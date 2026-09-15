"""The skills marketplace over HTTP (`agent/skill_market.py`, RAVIS 0.28.0).

Against the fake GitHub, fake websites and a fake skills.sh answering with the one real search
recorded while this was built (`fixtures/skill-store/skills-sh-search.json`). What RAVIS is held to:

- **the sources it comes with**, each with its note: openai deprecated by its owner, the link list
  and skills.sh uncurated; a read of the marketplace fetches nothing;
- **a GitHub source's listing**: its skill folders with name, description and license (front
  matter, or a license file), a problem where an install would refuse, one API call per source;
- **kept a day**: read again when stale or forced, and a source that can't be read keeps its last
  listing and says why, rate limits with the time;
- **a link list**: its links listed from its README without an API call, a link elsewhere shown as
  not installable and never fetched, links that aren't skills left out; **resolved only when
  shown**, a skill's own folder by a raw read alone, kept a day, and stopped with a plain note when
  GitHub rate-limits or the hourly allowance runs low;
- **installing through a list installs from the repository the skill lives in**, which Update
  reads later, and **installed** matched by repository folder;
- **the owner's own sources**: checked, added, hidden and removed, RAVIS's own hidden only, at
  most thirty; **a website's index** listed and installed with its digest checked;
- **skills.sh**: most installed first, only its host fetched, and every failure a plain line.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.agent_rig import refused, serving
from tests.skill_store_rig import ADMIN, StoreRig, body_of, skill_md, store_rig

from ravis.agent import store_refusals
from ravis.agent.skill_package import SPECIFICATION
from ravis.agent.skill_sites import SCHEMA_V2

MARKET = "/api/v1/skills/market"
REFRESH, RESOLVE = f"{MARKET}/refresh", f"{MARKET}/resolve"
SEARCH, SOURCES = f"{MARKET}/search", f"{MARKET}/sources"
PREVIEWS, INSTALLS = "/api/v1/skills/previews", "/api/v1/skills/installs"
FIXTURES = Path(__file__).parent / "fixtures" / "skill-store"
PDF_LINK = "https://github.com/anthropics/skills/tree/main/skills/pdf"
VERCEL_LINK = "https://github.com/vercel-labs/agent-skills"
SENTRY_LINK = "https://github.com/getsentry/skills/tree/main/skills/code-review"
SEARCH_URL = "https://skills.sh/api/search?q=pdf&limit=20"
KNOWN = "https://example.com/.well-known/agent-skills/index.json"


def call(relay: Any, method: str, path: str, body: Any = None, caller: str = ADMIN) -> Any:
    return relay.call(method, path, caller=caller, body=body)


def post(relay: Any, path: str, body: Any = None) -> dict[str, Any]:
    return body_of(call(relay, "POST", path, body))  # type: ignore[no-any-return]


def market(relay: Any) -> dict[str, Any]:
    return body_of(call(relay, "GET", MARKET))  # type: ignore[no-any-return]


def entries(view: dict[str, Any], source: str) -> list[dict[str, Any]]:
    return [entry for entry in view["entries"] if entry["source"] == source]


def named(view: dict[str, Any], source: str) -> dict[str, dict[str, Any]]:
    return {entry["name"]: entry for entry in entries(view, source)}


def source(view: dict[str, Any], source_id: str) -> dict[str, Any]:
    return next(item for item in view["sources"] if item["id"] == source_id)  # type: ignore[no-any-return]


def api_calls(store: StoreRig) -> list[str]:
    return store.github.api_calls()


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StoreRig:
    rig = store_rig(tmp_path, monkeypatch)
    github = rig.github
    github.repository("anthropics/skills", {
        "README.md": b"# Skills",
        "skills/pdf/SKILL.md": skill_md("pdf", extra="license: Proprietary. LICENSE.txt has "
                                                     "complete terms\n"),
        "skills/docx/SKILL.md": skill_md("docx"), "skills/docx/LICENSE.txt": b"Apache License",
        "skills/broken/SKILL.md": b"no front matter at all",
    })
    github.repository("openai/skills", {
        "skills/.curated/gh-fix/SKILL.md": skill_md("gh-fix"),
        "skills/.experimental/notion/SKILL.md": skill_md("notion"),
        "skills/.system/skill-installer/SKILL.md": skill_md("skill-installer"),
    })
    github.repository("ComposioHQ/awesome-claude-skills", {
        "README.md": b"# Awesome", "changelog-generator/SKILL.md": skill_md("changelog-generator"),
        "file-organizer/SKILL.md": skill_md("file-organizer"),
        "document-skills/docx/SKILL.md": skill_md("docx"),
    })
    github.repository("VoltAgent/awesome-agent-skills", {
        "README.md": (FIXTURES / "link-list-README.md").read_bytes()})
    github.repository("vercel-labs/agent-skills", {
        "skills/react-best/SKILL.md": skill_md("react-best"),
        "skills/deploy/SKILL.md": skill_md("deploy")})
    github.repository("getsentry/skills", {
        "skills/code-review/SKILL.md": skill_md("code-review")})
    return rig


# ── The sources RAVIS comes with ─────────────────────────────────────────────


def test_the_sources_ravis_comes_with_and_a_read_that_fetches_nothing(store: StoreRig) -> None:
    with serving(store.rig) as relay:
        view = market(relay)

    assert [item["id"] for item in view["sources"]] == [
        "anthropics", "openai", "composio", "voltagent", "skills_sh"]
    assert view["checked_against"] == SPECIFICATION and view["entries"] == []
    assert source(view, "openai")["deprecated"] is True
    assert "Deprecated by its owner" in source(view, "openai")["note"]
    assert (source(view, "voltagent")["uncurated"], source(view, "voltagent")["kind"]) == (
        True, "link_list")
    assert source(view, "skills_sh")["note"] == "Uncurated, ranked by installs."
    assert [item["stale"] for item in view["sources"]] == [True, True, True, True, False]
    assert all(item["default"] and not item["hidden"] for item in view["sources"])
    assert store.internet.calls == []


# ── GitHub sources ───────────────────────────────────────────────────────────


def test_a_refresh_lists_each_github_sources_skills_with_their_licenses(store: StoreRig) -> None:
    with serving(store.rig) as relay:
        view = post(relay, REFRESH, {})

    anthropics = named(view, "anthropics")
    assert set(anthropics) == {"pdf", "docx", "broken"}
    assert anthropics["pdf"]["license"] == "Proprietary. LICENSE.txt has complete terms"
    assert anthropics["docx"]["license"] == "LICENSE.txt in its folder"
    assert (anthropics["broken"]["state"], anthropics["pdf"]["state"]) == ("problem", "ready")
    assert anthropics["pdf"]["install"] == {"origin": "github", "repository": "anthropics/skills",
                                            "folder": "skills/pdf", "ref": None,
                                            "via": "anthropics"}
    assert {name: entry["label"] for name, entry in named(view, "openai").items()} == {
        "gh-fix": "curated", "notion": "experimental"}
    assert {entry["folder"] for entry in entries(view, "composio")} == {
        "changelog-generator", "file-organizer", "document-skills/docx"}
    assert (source(view, "anthropics")["count"], source(view, "anthropics")["stale"],
            source(view, "anthropics")["fetched_at"]) == (3, False, "2026-09-15T20:00:00Z")
    assert len(api_calls(store)) == 3


def test_listings_are_kept_a_day_and_read_again_when_stale_or_forced(store: StoreRig) -> None:
    with serving(store.rig) as relay:
        post(relay, REFRESH, {})
        first = len(api_calls(store))
        post(relay, REFRESH, {})
        kept = len(api_calls(store))
        post(relay, REFRESH, {"source": "anthropics", "force": True})
        forced = len(api_calls(store))
        store.clock.advance(hours=25)
        stale = [item["stale"] for item in market(relay)["sources"]]
        post(relay, REFRESH, {})

    assert (first, kept, forced) == (3, 3, 4)
    assert stale == [True, True, True, True, False]
    assert len(api_calls(store)) == 7


def test_a_source_that_cant_be_read_keeps_its_last_listing_and_says_why(store: StoreRig) -> None:
    with serving(store.rig) as relay:
        post(relay, REFRESH, {"source": "anthropics"})
        store.github.limit_api_after = 0
        view = post(relay, REFRESH, {"source": "anthropics", "force": True})
        unread = post(relay, REFRESH, {"source": "openai"})

    lifts = store_refusals.local_time(store.github.reset_at)
    assert len(entries(view, "anthropics")) == 3
    assert source(view, "anthropics")["problem"] == (
        f"GitHub is rate-limiting RAVIS; try again at {lifts}.")
    assert source(view, "anthropics")["fetched_at"] == "2026-09-15T20:00:00Z"
    assert (source(unread, "openai")["stale"], entries(unread, "openai")) == (True, [])
    assert "rate-limiting" in source(unread, "openai")["problem"]


# ── A link list ──────────────────────────────────────────────────────────────


def test_a_link_lists_links_are_listed_from_its_readme_without_an_api_call(store: StoreRig
                                                                           ) -> None:
    with serving(store.rig) as relay:
        view = post(relay, REFRESH, {"source": "voltagent"})

    listed = entries(view, "voltagent")
    assert [(entry["name"], entry["state"]) for entry in listed] == [
        ("anthropics/pdf", "unresolved"), ("vercel-labs/agent-skills", "unresolved"),
        ("Hosted elsewhere", "not_installable"), ("sentry/code-review", "unresolved"),
        ("gone/missing", "unresolved")]
    assert [entry["description"] for entry in listed[:4]] == [
        "Read and write PDFs.", "Vercel's collection", "A skill kept on another site.",
        "Reviews code the Sentry way"]
    assert listed[0]["lives_in"] == "anthropics/skills"
    assert "can't be installed here" in listed[2]["problem"] and listed[2]["install"] is None
    assert api_calls(store) == []
    assert not [url for url in store.internet.calls if "skills.example.org" in url]


def test_links_are_resolved_only_when_shown_and_kept_a_day(store: StoreRig) -> None:
    with serving(store.rig) as relay:
        post(relay, REFRESH, {"source": "voltagent"})
        folder = post(relay, RESOLVE, {"source": "voltagent", "links": [PDF_LINK]})
        after_folder = len(api_calls(store))
        repository = post(relay, RESOLVE, {"source": "voltagent", "links": [VERCEL_LINK]})
        after_repository = len(api_calls(store))
        post(relay, RESOLVE, {"source": "voltagent", "links": [PDF_LINK, VERCEL_LINK]})
        missing = post(relay, RESOLVE, {"source": "voltagent", "links": [
            "https://github.com/gone/missing/tree/main/skills/x",
            "https://skills.example.org/cool"]})

    pdf = named(folder, "voltagent")["pdf"]
    assert (pdf["state"], pdf["repository"], pdf["folder"], pdf["ref"], pdf["link"]) == (
        "ready", "anthropics/skills", "skills/pdf", "main", PDF_LINK)
    assert pdf["install"] == {"origin": "github", "repository": "anthropics/skills",
                              "folder": "skills/pdf", "ref": "main", "via": "voltagent"}
    assert after_folder == 0 and after_repository == 1
    assert {entry["folder"] for entry in entries(repository, "voltagent")
            if entry["repository"] == "vercel-labs/agent-skills"} == {
        "skills/react-best", "skills/deploy"}
    assert len(api_calls(store)) == after_repository + 3
    gone = named(missing, "voltagent")["gone/missing"]
    assert gone["state"] == "problem" and "gone/missing" in gone["problem"]
    assert not [url for url in store.internet.calls if "skills.example.org" in url]


def test_resolving_stops_with_a_note_when_github_rate_limits_or_the_allowance_runs_low(
    store: StoreRig,
) -> None:
    with serving(store.rig) as relay:
        post(relay, REFRESH, {"source": "voltagent"})
        store.github.limit_raw = True
        limited = post(relay, RESOLVE, {"source": "voltagent", "links": [PDF_LINK]})
        store.github.limit_raw = False
        store.clock.advance(minutes=3)
        store.github.api_remaining = 11
        post(relay, REFRESH, {"source": "anthropics"})
        low = post(relay, RESOLVE, {"source": "voltagent", "links": [SENTRY_LINK]})

    assert limited["note"].startswith("GitHub is rate-limiting RAVIS; try again at ")
    assert named(limited, "voltagent")["anthropics/pdf"]["state"] == "unresolved"
    assert "hourly allowance" in low["note"] and "kept for installs" in low["note"]
    assert named(low, "voltagent")["sentry/code-review"]["state"] == "unresolved"


def test_a_skill_found_through_a_list_installs_from_the_repository_it_lives_in(store: StoreRig
                                                                              ) -> None:
    with serving(store.rig) as relay:
        post(relay, REFRESH, {})
        resolved = post(relay, RESOLVE, {"source": "voltagent", "links": [SENTRY_LINK, PDF_LINK]})
        preview = post(relay, PREVIEWS, named(resolved, "voltagent")["code-review"]["install"])
        post(relay, INSTALLS, {"preview_id": preview["preview_id"]})
        readme_reads = sum("awesome-agent-skills" in url for url in store.internet.calls)
        store.github.push("getsentry/skills", {
            "skills/code-review/SKILL.md": skill_md("code-review", body="Stricter.\n")})
        update = post(relay, "/api/v1/skills/installs/update-preview", {"name": "code-review"})
        pdf_preview = post(relay, PREVIEWS, named(resolved, "anthropics")["pdf"]["install"])
        post(relay, INSTALLS, {"preview_id": pdf_preview["preview_id"]})
        view = market(relay)

    assert (preview["source"]["repository"], preview["source"]["via"]) == (
        "getsentry/skills", "voltagent")
    assert update["kind"] == "update" and update["source"]["repository"] == "getsentry/skills"
    assert sum("awesome-agent-skills" in url for url in store.internet.calls) == readme_reads
    assert named(view, "voltagent")["code-review"]["installed"] is True
    assert named(view, "voltagent")["pdf"]["installed"] is True
    assert named(view, "anthropics")["pdf"]["installed"] is True
    assert named(view, "anthropics")["docx"]["installed"] is False


# ── The owner's own sources ──────────────────────────────────────────────────


def test_the_owners_sources_are_checked_added_hidden_and_removed(store: StoreRig) -> None:
    with serving(store.rig) as relay:
        added = post(relay, SOURCES, {"kind": "github", "repository": "https://github.com/me/mine",
                                      "path": "skills", "ref": "dev"})
        again = call(relay, "POST", SOURCES, {"kind": "github", "repository": "me/mine",
                                              "path": "skills/", "ref": "dev"})
        listed = post(relay, SOURCES, {"kind": "link_list", "repository": "me/list"})
        site = post(relay, SOURCES, {"kind": "website", "url": "https://example.com/docs"})
        bad = [call(relay, "POST", SOURCES, body) for body in (
            {"kind": "github", "repository": "nope"}, {"kind": "website", "url": "http://x.com"},
            {"kind": "ftp"}, {"kind": "github", "repository": "me/mine", "ref": "../x"})]
        hidden = post(relay, f"{SOURCES}/hide", {"source": "anthropics", "hidden": True})
        search_hidden = post(relay, f"{SOURCES}/hide", {"source": "skills_sh", "hidden": True})
        searching = call(relay, "POST", SEARCH, {"query": "pdf"})
        default = call(relay, "POST", f"{SOURCES}/remove", {"source": "openai"})
        own_id = next(item["id"] for item in added["sources"] if not item["default"])
        removed = post(relay, f"{SOURCES}/remove", {"source": own_id})
        unknown = call(relay, "POST", f"{SOURCES}/remove", {"source": own_id})

    own = [item for item in added["sources"] if not item["default"]]
    assert [(item["kind"], item["label"], item["repository"], item["folders"], item["ref"])
            for item in own] == [("github", "me/mine/skills at dev", "me/mine", ["skills"], "dev")]
    refused(again, 409, "MARKET_SOURCE_EXISTS")
    list_source = next(item for item in listed["sources"] if item["kind"] == "link_list"
                       and not item["default"])
    assert (list_source["path"], list_source["uncurated"]) == ("README.md", True)
    assert next(item for item in site["sources"] if item["kind"] == "website")["site"] == (
        "https://example.com/docs")
    for answer in bad:
        refused(answer, 422, "INVALID_REQUEST_BODY")
    assert source(hidden, "anthropics")["hidden"] is True
    assert source(search_hidden, "skills_sh")["hidden"] is True
    refused(searching, 404, "MARKET_SOURCE_NOT_FOUND")
    refused(default, 409, "MARKET_SOURCE_IS_DEFAULT")
    assert own_id not in [item["id"] for item in removed["sources"]]
    refused(unknown, 404, "MARKET_SOURCE_NOT_FOUND")
    assert [event["kind"] for event in store.published("ravis.skill_source_added")] == [
        "github", "link_list", "website"]
    assert len(store.published("ravis.skill_source_removed")) == 1


def test_a_hidden_source_lists_nothing(store: StoreRig) -> None:
    with serving(store.rig) as relay:
        post(relay, REFRESH, {})
        post(relay, f"{SOURCES}/hide", {"source": "composio", "hidden": True})
        view = market(relay)
        shown = post(relay, f"{SOURCES}/hide", {"source": "composio", "hidden": False})

    assert entries(view, "composio") == [] and source(view, "composio")["count"] == 0
    assert len(entries(shown, "composio")) == 3


def test_at_most_thirty_sources_of_the_owners_own(store: StoreRig) -> None:
    with serving(store.rig) as relay:
        for count in range(30):
            post(relay, SOURCES, {"kind": "github", "repository": f"me/repository-{count}"})
        answer = call(relay, "POST", SOURCES, {"kind": "github", "repository": "me/one-more"})

    refused(answer, 409, "MARKET_SOURCES_FULL", most=30)


# ── A website with an Agent Skills index ─────────────────────────────────────


def digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def publish(store: StoreRig, skill: bytes, said: bytes | None = None) -> None:
    store.internet.page(KNOWN, {"$schema": SCHEMA_V2, "skills": [
        {"name": "pdf", "type": "skill-md", "description": "PDFs.", "url": "pdf/SKILL.md",
         "digest": digest(said if said is not None else skill)},
        {"name": "cdn", "type": "archive", "description": "Elsewhere.",
         "url": "https://cdn.other.example/cdn.zip", "digest": digest(b"x")}]})
    store.internet.page("https://example.com/.well-known/agent-skills/pdf/SKILL.md", skill)


def test_a_websites_index_is_listed_and_installed_with_its_digest_checked(store: StoreRig
                                                                         ) -> None:
    publish(store, skill_md("pdf"))
    with serving(store.rig) as relay:
        added = post(relay, SOURCES, {"kind": "website", "url": "https://example.com"})
        site_id = next(item["id"] for item in added["sources"] if item["kind"] == "website")
        view = post(relay, REFRESH, {"source": site_id})
        listed = named(view, site_id)
        preview = post(relay, PREVIEWS, listed["pdf"]["install"])
        post(relay, INSTALLS, {"preview_id": preview["preview_id"]})
        same = post(relay, "/api/v1/skills/installs/update-preview", {"name": "pdf"})
        publish(store, skill_md("pdf", body="Newer.\n"))
        newer = post(relay, "/api/v1/skills/installs/update-preview", {"name": "pdf"})
        publish(store, skill_md("pdf", body="Tampered.\n"), said=skill_md("pdf", body="Other.\n"))
        tampered = call(relay, "POST", "/api/v1/skills/installs/update-preview", {"name": "pdf"})

    assert (listed["pdf"]["state"], listed["cdn"]["state"]) == ("ready", "not_installable")
    assert listed["pdf"]["install"] == {"origin": "website", "source": site_id, "name": "pdf"}
    said = preview["source"]
    assert (said["origin"], said["site"], said["digest"]) == (
        "website", "https://example.com", digest(skill_md("pdf")))
    assert same["up_to_date"] is True
    assert newer["kind"] == "update" and newer["update"]["switches"] == "reset"
    refused(tampered, 422, "SKILL_REFUSED", reason="digest_mismatch")
    assert not [url for url in store.internet.calls if "cdn.other.example" in url]


# ── skills.sh ────────────────────────────────────────────────────────────────


def recorded() -> dict[str, Any]:
    return json.loads((FIXTURES / "skills-sh-search.json").read_text())["answer"]  # type: ignore[no-any-return]


def test_skills_sh_results_come_most_installed_first_from_its_host_alone(store: StoreRig) -> None:
    store.internet.page(SEARCH_URL, recorded())
    with serving(store.rig) as relay:
        found = post(relay, SEARCH, {"query": "  pdf "})
        preview = post(relay, PREVIEWS, found["results"][0]["install"])
        post(relay, INSTALLS, {"preview_id": preview["preview_id"]})
        again = post(relay, SEARCH, {"query": "pdf"})

    assert (found["note"], found["problem"], found["query"]) == (
        "Uncurated, ranked by installs.", None, "pdf")
    assert [(result["repository"], result["skill"], result["installs"])
            for result in found["results"]] == [
        ("anthropics/skills", "pdf", 196349), ("openai/skills", "pdf", 12434),
        ("github/awesome-copilot", "pdftk-server", 10034),
        ("anthropics/knowledge-work-plugins", "view-pdf", 6024),
        ("vercel-labs/json-render", "react-pdf", 2079)]
    assert found["results"][0]["install"] == {"origin": "github", "repository": "anthropics/skills",
                                              "skill": "pdf", "via": "skills_sh"}
    assert [result["installed"] for result in again["results"]] == [True, False, False, False,
                                                                     False]
    assert [url for url in store.internet.calls if "skills.sh" in url] == [SEARCH_URL, SEARCH_URL]


@pytest.mark.parametrize("answer, said", [
    ({"status": 500}, "skills.sh couldn't be searched: skills.sh answered HTTP 500."),
    ({"body": {"skills": [{"unexpected": True}]}}, "skills.sh answered in a shape RAVIS doesn't "
                                                   "read, so there are no results to show."),
    ({"body": "not json"}, "skills.sh answered in a shape RAVIS doesn't read, so there are no "
                           "results to show."),
    ({"status": 302, "headers": {"location": "https://evil.example/api/search"}},
     "skills.sh couldn't be searched: skills.sh sent RAVIS on to evil.example, which this source "
     "doesn't allow."),
])
def test_a_skills_sh_failure_is_a_plain_line_never_an_error(store: StoreRig,
                                                          answer: dict[str, Any], said: str
                                                          ) -> None:
    store.internet.page(SEARCH_URL, answer.get("body", b""), status=answer.get("status", 200),
                        headers=answer.get("headers"))
    with serving(store.rig) as relay:
        found = post(relay, SEARCH, {"query": "pdf"})

    assert (found["results"], found["problem"]) == ([], said)


def test_skills_sh_rate_limiting_says_when(store: StoreRig) -> None:
    store.internet.page(SEARCH_URL, status=429, headers={"retry-after": "600"})
    with serving(store.rig) as relay:
        found = post(relay, SEARCH, {"query": "pdf"})

    lifts = store_refusals.local_time(store.clock() + timedelta(minutes=10))
    assert found["problem"] == (f"skills.sh couldn't be searched: skills.sh is rate-limiting "
                                f"RAVIS; try again at {lifts}.")


def test_skills_sh_address_is_a_setting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = store_rig(tmp_path, monkeypatch, skills_sh_url="https://search.example/")
    store.internet.page("https://search.example/api/search?q=pdf&limit=20", recorded())
    with serving(store.rig) as relay:
        found = post(relay, SEARCH, {"query": "pdf"})

    assert len(found["results"]) == 5 and found["problem"] is None


@pytest.mark.parametrize("body", [{}, {"query": "   "}, {"query": "q" * 101}, {"query": 7}])
def test_a_search_needs_a_query(store: StoreRig, body: dict[str, Any]) -> None:
    with serving(store.rig) as relay:
        refused(call(relay, "POST", SEARCH, body), 422, "INVALID_REQUEST_BODY")


@pytest.mark.parametrize("path, body", [
    (REFRESH, {"source": 7}), (REFRESH, {"force": "yes"}), (RESOLVE, {"source": "voltagent"}),
    (RESOLVE, {"source": "voltagent", "links": []}), (f"{SOURCES}/hide", {"source": "openai"}),
    (f"{SOURCES}/remove", {}),
])
def test_marketplace_bodies_that_arent_what_a_route_takes(store: StoreRig, path: str,
                                                         body: dict[str, Any]) -> None:
    with serving(store.rig) as relay:
        refused(call(relay, "POST", path, body), 422, "INVALID_REQUEST_BODY")
