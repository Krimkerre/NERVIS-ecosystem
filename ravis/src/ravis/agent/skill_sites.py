"""Websites that publish an Agent Skills index — the agentskills.io discovery standard (0.28.0).

**Why** (owner decisions, 15 September 2026). agentskills.io has no catalogue of its own; what it
has is a way for any website to list its skills, which is how it becomes a source: the owner adds
a website's address, and RAVIS reads that website's index. The standard's rules as the owner's
coordinator confirmed them from `vercel-labs/skills` (`src/providers/wellknown.ts`), 15 September
2026. The schema address below didn't resolve while this was built, so the rules here are that
description's, not the schema's own text.

**Where the index is**, in this order (`candidates`):
`<address>/.well-known/agent-skills/index.json`, then the older
`<address>/.well-known/skills/index.json`; for an address with a path
(`https://example.com/docs`) both of those first, then the same two at the website's root. The
first that answers with an index RAVIS reads is the index. (The CLI interleaves the two levels;
RAVIS follows the order the owner was given.)

**Version 0.2.0** carries `"$schema": SCHEMA_V2` and `skills: [{name, type, description, url,
digest}]`: `type` is `skill-md` (one `SKILL.md`) or `archive` (a zip, or a `.tar.gz`); `url` is
read relative to the index's own address; `digest` is `sha256:<64 hex>` of the bytes, and **a
download whose hash differs is refused**. An entry that breaks a rule is left out and counted.
**Version 0.1.0** has no `$schema`: `skills: [{name, description, files}]`, each file at
`<index's folder>/<name>/<file>`; there is no digest, so an update compares the files themselves.
An index of 0.1.0 with any entry that breaks a rule isn't read at all.

**Hosts**: `https` only, and exactly the host the owner added, redirects included. An entry whose
`url` lies on another host is listed as not installable here and never fetched.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

from ravis.agent.skill_catalog import MOST_FILE_BYTES as MOST_SKILL_MD_BYTES
from ravis.agent.skill_catalog import SKILL_FILE, one_line
from ravis.agent.skill_package import (
    MOST_ARCHIVE_BYTES,
    MOST_DESCRIPTION_CHARACTERS,
    MOST_FILE_BYTES,
    MOST_FILES,
    NAME,
    Incoming,
    PackageRefusedError,
    from_archive,
    one_skill,
    plain_path,
)
from ravis.agent.skill_web import NotFoundError, Web, host_of

SCHEMA_V2 = "https://schemas.agentskills.io/discovery/0.2.0/schema.json"
WELL_KNOWN = (".well-known/agent-skills", ".well-known/skills")
INDEX_FILE = "index.json"
MOST_INDEX_BYTES = 1024 * 1024
MOST_ENTRIES = 300
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
TYPES = frozenset({"skill-md", "archive"})
AT_ONCE = 8


@dataclass(frozen=True)
class Site:
    """A website the owner added: its address without a trailing slash, and its host."""

    url: str
    host: str

    @property
    def hosts(self) -> frozenset[str]:
        return frozenset({self.host})


def site_of(value: object) -> Site | None:
    """A website's address as RAVIS keeps it: `https`, a host, a path, no query or fragment."""
    if not isinstance(value, str) or len(value) > 500:
        return None
    text = value.strip()
    host = host_of(text)
    parts = urlsplit(text)
    if host is None or parts.query or parts.fragment or "." not in host.split(":")[0]:
        return None
    path = parts.path.rstrip("/")
    if path and (any(part in (".", "..") for part in path.split("/")[1:])
                 or any(ord(c) < 33 for c in path)):
        return None
    return Site(f"https://{host}{path}", host)


def candidates(site: Site) -> list[str]:
    """The index's possible addresses, in the order they are tried."""
    root = f"https://{site.host}"
    bases = [site.url, root] if site.url != root else [root]
    return [f"{base}/{known}/{INDEX_FILE}" for base in bases for known in WELL_KNOWN]


@dataclass(frozen=True)
class SiteEntry:
    name: str
    description: str
    #: `skill-md` or `archive` (0.2.0), or `files` (0.1.0).
    type: str
    #: The artifact's absolute address (0.2.0); the skill's folder address (0.1.0).
    url: str
    digest: str | None
    files: tuple[str, ...]
    #: Why it can't be installed from here, in plain words; None when it can.
    problem: str | None

    def view(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "type": self.type,
                "url": self.url, "digest": self.digest, "files": list(self.files),
                "problem": self.problem}

    @classmethod
    def from_view(cls, raw: dict[str, Any]) -> SiteEntry:
        return cls(str(raw["name"]), str(raw["description"]), str(raw["type"]), str(raw["url"]),
                   raw.get("digest"), tuple(raw.get("files") or ()), raw.get("problem"))


@dataclass(frozen=True)
class SiteIndex:
    index_url: str
    version: str
    entries: tuple[SiteEntry, ...]
    #: Entries left out because they broke a rule (0.2.0 only).
    skipped: int


async def read_index(web: Web, site: Site) -> SiteIndex:
    """The website's index, from the first address that holds one RAVIS reads."""
    unreadable: str | None = None
    for url in candidates(site):
        try:
            answer = await web.get(url, hosts=site.hosts, most_bytes=MOST_INDEX_BYTES,
                                   accept="application/json")
        except NotFoundError:
            continue
        parsed = parse_index(answer.body, answer.url, site)
        if isinstance(parsed, SiteIndex):
            return parsed
        unreadable = f"{parsed} ({url})"
    raise NotFoundError(unreadable or f"{site.host} doesn't publish an Agent Skills index at "
                                      ".well-known/agent-skills/index.json or "
                                      ".well-known/skills/index.json")


def parse_index(body: bytes, index_url: str, site: Site) -> SiteIndex | str:
    """An index RAVIS reads, or why it isn't one, in plain words."""
    try:
        data = json.loads(body)
    except ValueError:
        return f"{site.host}'s index isn't JSON"
    skills = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(skills, list):
        return f"{site.host}'s index has no list of skills"
    schema = data.get("$schema")
    if schema == SCHEMA_V2:
        return _version_2(skills[:MOST_ENTRIES], index_url, site)
    if schema is None:
        return _version_1(skills[:MOST_ENTRIES], index_url, site)
    return f"{site.host}'s index uses a version of the standard RAVIS doesn't read ({schema})"


def _version_2(skills: list[Any], index_url: str, site: Site) -> SiteIndex:
    entries, skipped = [], 0
    for raw in skills:
        name, description = _named(raw)
        kind, url, digest = (raw.get(field) if isinstance(raw, dict) else None
                             for field in ("type", "url", "digest"))
        if (name is None or description is None or kind not in TYPES or not isinstance(url, str)
                or not isinstance(digest, str) or not DIGEST.fullmatch(digest)):
            skipped += 1
            continue
        absolute = urljoin(index_url, url)
        problem = (None if host_of(absolute) == site.host
                   else "it is kept on another website, so it can't be installed from here")
        entries.append(SiteEntry(name, description, str(kind), absolute, digest, (), problem))
    return SiteIndex(index_url, "0.2.0", tuple(entries), skipped)


def _version_1(skills: list[Any], index_url: str, site: Site) -> SiteIndex | str:
    entries = []
    folder = index_url.rsplit("/", 1)[0]
    for raw in skills:
        name, description = _named(raw)
        files = raw.get("files") if isinstance(raw, dict) else None
        if (name is None or description is None or not isinstance(files, list) or not files
                or len(files) > MOST_FILES or not all(_plain(file) for file in files)):
            return f"{site.host}'s index (version 0.1.0) has an entry that breaks the standard"
        problem = None if SKILL_FILE in files else "its files don't include a SKILL.md"
        entries.append(SiteEntry(name, description, "files", f"{folder}/{quote(name)}/", None,
                                 tuple(files), problem))
    return SiteIndex(index_url, "0.1.0", tuple(entries), 0)


def _named(raw: object) -> tuple[str | None, str | None]:
    """An entry's name, when the specification allows it, and its one-line description."""
    if not isinstance(raw, dict):
        return None, None
    name, description = raw.get("name"), raw.get("description")
    if not isinstance(name, str) or len(name) > 64 or not NAME.fullmatch(name):
        return None, None
    if (not isinstance(description, str) or not description.strip()
            or len(description) > MOST_DESCRIPTION_CHARACTERS):
        return name, None
    return name, one_line(description, 300)


def _plain(file: object) -> bool:
    if not isinstance(file, str):
        return False
    try:
        plain_path(file)
    except PackageRefusedError:
        return False
    return True


async def fetch_skill(web: Web, site: Site, entry: SiteEntry) -> tuple[list[Incoming], int]:
    """An entry's files, checked against its digest where it has one, and how many Finder-added
    files an archive's unpacking left out."""
    if entry.problem is not None:
        raise NotFoundError(f"{entry.name} can't be installed from {site.host}: {entry.problem}")
    if entry.type == "files":
        return await _files(web, site, entry), 0
    most = MOST_SKILL_MD_BYTES if entry.type == "skill-md" else MOST_ARCHIVE_BYTES
    answer = await web.get(entry.url, hosts=site.hosts, most_bytes=most)
    actual = f"sha256:{hashlib.sha256(answer.body).hexdigest()}"
    if actual != entry.digest:
        raise PackageRefusedError("digest_mismatch", f"What {site.host} sent for {entry.name} "
                                  "doesn't match the digest its index lists, so it isn't what "
                                  "the index describes.")
    if entry.type == "skill-md":
        return [Incoming(SKILL_FILE, answer.body)], 0
    files, left = from_archive(answer.body)
    _, inner = one_skill(files)
    return inner, left


async def _files(web: Web, site: Site, entry: SiteEntry) -> list[Incoming]:
    limit = asyncio.Semaphore(AT_ONCE)

    async def one(file: str) -> Incoming:
        async with limit:
            answer = await web.get(f"{entry.url}{quote(file)}", hosts=site.hosts,
                                   most_bytes=MOST_FILE_BYTES)
        return Incoming(file, answer.body)

    return list(await asyncio.gather(*(one(file) for file in entry.files)))
