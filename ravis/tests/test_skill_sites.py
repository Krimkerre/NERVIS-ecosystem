"""Websites that publish an Agent Skills index (`agent/skill_sites.py`, RAVIS 0.28.0).

What RAVIS is held to, against fake websites:

- **where the index is**: for an address with a path, both path-relative indexes first
  (`agent-skills`, then the older `skills`), then the same at the root, stopping at the first;
- **version 0.2.0**: `url` read against the index's own address, and **a download whose digest
  differs is refused**; an archive unpacked by the same rules as an upload; an entry breaking the
  standard left out and counted; an entry on another host listed but never fetched;
- **version 0.1.0**: files beside the index, under the skill's name; an index with a bad entry
  isn't read at all;
- **hosts**: only the website's own, redirects included; and what counts as a website's address.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from tests.skill_store_rig import FakeInternet, internet, skill_md, zip_of

from ravis.agent.skill_package import PackageRefusedError
from ravis.agent.skill_sites import (
    SCHEMA_V2,
    Site,
    candidates,
    fetch_skill,
    parse_index,
    read_index,
    site_of,
)
from ravis.agent.skill_web import NotFoundError, RefusedError, Web

KNOWN = "https://example.com/.well-known/agent-skills/index.json"
OLDER = "https://example.com/.well-known/skills/index.json"
SITE = Site("https://example.com", "example.com")


def web_on(fake: FakeInternet) -> Web:
    return Web(transport=fake.transport(), now=fake.github.clock)


def digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def entry(name: str, url: str, data: bytes, kind: str = "skill-md") -> dict[str, Any]:
    return {"name": name, "type": kind, "description": f"The {name} skill.", "url": url,
            "digest": digest(data)}


def version_2(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"$schema": SCHEMA_V2, "skills": list(entries)}


async def test_the_path_relative_index_comes_first_and_urls_are_read_against_it() -> None:
    fake = internet()
    here = "https://example.com/docs/.well-known/agent-skills/index.json"
    fake.page(here, version_2(entry("pdf", "pdf/SKILL.md", skill_md("pdf"))))
    fake.page(KNOWN, version_2())
    site = site_of("https://example.com/docs/")
    assert site is not None

    index = await read_index(web_on(fake), site)

    assert (index.index_url, index.version) == (here, "0.2.0")
    assert index.entries[0].url == "https://example.com/docs/.well-known/agent-skills/pdf/SKILL.md"
    assert fake.calls == [here]


async def test_the_older_path_before_the_root_and_version_1_files_beside_the_index() -> None:
    fake = internet()
    fake.page(OLDER, {"skills": [{"name": "pdf", "description": "PDFs.",
                                  "files": ["SKILL.md", "scripts/run.sh"]}]})
    fake.page("https://example.com/.well-known/skills/pdf/SKILL.md", skill_md("pdf"))
    fake.page("https://example.com/.well-known/skills/pdf/scripts/run.sh", b"echo")
    site = site_of("https://example.com/docs")
    assert site is not None
    web = web_on(fake)

    index = await read_index(web, site)
    files, _ = await fetch_skill(web, site, index.entries[0])

    assert fake.calls[:4] == [
        "https://example.com/docs/.well-known/agent-skills/index.json",
        "https://example.com/docs/.well-known/skills/index.json", KNOWN, OLDER]
    assert (index.version, index.entries[0].type) == ("0.1.0", "files")
    assert {file.path: file.data for file in files} == {"SKILL.md": skill_md("pdf"),
                                                        "scripts/run.sh": b"echo"}


async def test_a_download_whose_digest_differs_is_refused() -> None:
    fake = internet()
    fake.page(KNOWN, version_2(entry("pdf", "pdf/SKILL.md", b"what the index described")))
    fake.page("https://example.com/.well-known/agent-skills/pdf/SKILL.md", skill_md("pdf"))
    web = web_on(fake)
    index = await read_index(web, SITE)

    with pytest.raises(PackageRefusedError) as caught:
        await fetch_skill(web, SITE, index.entries[0])

    assert caught.value.reason == "digest_mismatch"


async def test_an_archive_is_unpacked_by_the_same_rules_as_an_upload() -> None:
    good = zip_of({"pdf/SKILL.md": skill_md("pdf"), "pdf/notes.md": b"notes"})
    broken = b"PK\x03\x04 not really a zip"
    slipping = zip_of({"SKILL.md": skill_md("slip"), "../outside.txt": b"x"})
    fake = internet()
    fake.page(KNOWN, version_2(entry("pdf", "pdf.zip", good, "archive"),
                               entry("broken", "broken.zip", broken, "archive"),
                               entry("slip", "slip.zip", slipping, "archive")))
    for name, data in (("pdf", good), ("broken", broken), ("slip", slipping)):
        fake.page(f"https://example.com/.well-known/agent-skills/{name}.zip", data)
    web = web_on(fake)
    index = await read_index(web, SITE)

    files, _ = await fetch_skill(web, SITE, index.entries[0])
    assert {file.path for file in files} == {"SKILL.md", "notes.md"}
    for position, reason in ((1, "not_archive"), (2, "unsafe_path")):
        with pytest.raises(PackageRefusedError) as caught:
            await fetch_skill(web, SITE, index.entries[position])
        assert caught.value.reason == reason


async def test_a_redirect_to_another_host_is_refused() -> None:
    fake = internet()
    fake.page(KNOWN, status=302, headers={"location": "https://evil.example/index.json"})

    with pytest.raises(RefusedError) as caught:
        await read_index(web_on(fake), SITE)

    assert caught.value.reason == "redirect_elsewhere"
    assert not [call for call in fake.calls if "evil.example" in call]


async def test_an_entry_kept_on_another_host_is_listed_but_never_fetched() -> None:
    fake = internet()
    fake.page(KNOWN, version_2(entry("pdf", "https://cdn.other.example/pdf.zip", b"x",
                                     "archive")))
    web = web_on(fake)
    index = await read_index(web, SITE)

    with pytest.raises(NotFoundError, match="another website"):
        await fetch_skill(web, SITE, index.entries[0])

    assert index.entries[0].problem and fake.calls == [KNOWN]


async def test_no_index_anywhere_says_where_it_looked() -> None:
    with pytest.raises(NotFoundError, match="doesn't publish an Agent Skills index"):
        await read_index(web_on(internet()), SITE)


def test_entries_and_indexes_that_break_the_standard() -> None:
    good = entry("pdf", "pdf/SKILL.md", b"x")
    parsed = parse_index(
        json.dumps(version_2(
            good, {**good, "name": "PDF"}, {**good, "type": "folder"},
            {**good, "digest": "md5:abc"}, {key: value for key, value in good.items()
                                            if key != "url"})).encode(), KNOWN, SITE)

    assert not isinstance(parsed, str) and (len(parsed.entries), parsed.skipped) == (1, 4)
    assert isinstance(parse_index(b'{"skills": [{"name": "pdf", "description": "d", '
                                  b'"files": ["../x"]}]}', OLDER, SITE), str)
    assert isinstance(parse_index(b'{"$schema": "https://example.com/9.9", "skills": []}',
                                  KNOWN, SITE), str)
    assert isinstance(parse_index(b"not json", KNOWN, SITE), str)


def test_what_counts_as_a_websites_address() -> None:
    assert site_of("https://Example.com/docs/") == Site("https://example.com/docs", "example.com")
    assert all(site_of(bad) is None for bad in (
        "http://example.com", "https://example.com/?q=1", "https://example.com/#top",
        "https://localhost/", "https://example.com/../x", "https://owner@example.com/", 7))
    assert candidates(SITE) == [KNOWN, OLDER]
