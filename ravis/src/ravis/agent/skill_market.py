"""The skills marketplace: the sources the Skills page browses, their listings, and search (0.28.0).

**The owner's sources** (decisions of 15 September 2026; `DEFAULT_SOURCES`, each can be hidden, none
removed):
- **anthropics/skills** — skill folders under `skills/`; some carry their own license terms, so each
  entry shows its license, from its front matter or a license file in its folder.
- **openai/skills** — `skills/.curated` and `skills/.experimental`; its README says the repository
  is deprecated in favour of OpenAI's plugins repository, so it is marked "deprecated by its owner".
- **ComposioHQ/awesome-claude-skills** — skill folders at the repository's top (only folders that
  hold a `SKILL.md`; its links to other repositories aren't listed).
- **VoltAgent/awesome-agent-skills** — a **link list**: a README linking to skills kept in other
  repositories, over a thousand. Uncurated.
- **skills.sh** — a **search** source (Vercel's open directory), asked only when the owner searches.
  Uncurated, ranked by installs. Not a documented public API: a failure is a plain line. It refuses
  a search shorter than two characters (measured 15 September 2026: HTTP 400, "Query must be at
  least 2 characters"), so RAVIS never sends one and says so instead.
And the owner's own: a **GitHub** repository (optional folder, branch or tag), a **link list** (a
repository's README or another Markdown file in it), or a **website with an Agent Skills index**
(`skill_sites.py`). At most `MOST_OWN_SOURCES`.

**What is fetched when, and kept a day** (`skill_market_cache`, `CACHE_HOURS`):
- A GitHub source: its repository's tree (one API call) and each skill folder's `SKILL.md` front
  matter (raw files, not counted by GitHub's API limit), at most `MOST_SKILLS_PER_SOURCE`: the
  folders nearest the top first, then alphabetically, and the problem line says how many were left
  out (0.28.1). The cap stays 300: each skill listed is one raw read a day, and ComposioHQ's
  hundreds of generated `composio-skills/<app>-automation` folders come after its own skills now.
- A link list: its file, raw. **Its links are resolved lazily** (`resolve`): only the ones the page
  is showing, at most `MOST_RESOLVE_PER_CALL` per call, each kept a day. A link to a skill's own
  folder costs one raw read and no API call; a folder of skills or a repository costs one API call
  more. Resolving stops while GitHub's hourly allowance is down to `API_RESERVE`, leaving the rest
  to installs, and says so. A link to any host but GitHub is listed as not installable here and
  never fetched.
- A website: its index, from its own host only.
- skills.sh: a query's results are kept in memory `SEARCH_KEPT_MINUTES`, whatever its letter case
  (0.28.1), since the Skills page now searches as the owner types; nothing is written to disk, and
  a failed search asks again next time.
`refresh` reads a source again when its listing is older than a day, or always when forced; a
source that can't be read keeps its last listing, with why. GETs never fetch: they answer from the
cache, so the Skills page's reads stay quick.

**Installing from any of them goes through the same review** (`skill_installs.py`): each entry
carries the preview request that installs it. A link list's or skills.sh's entry installs from the
repository the skill really lives in, so Update later fetches from there, not from the list. An
entry is installed when RAVIS installed a skill from the same repository folder or the same
website.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlsplit

from ravis.agent import store_refusals as refusals
from ravis.agent.skill_catalog import SKILL_FILE, one_line
from ravis.agent.skill_github import (
    MOST_HEAD_BYTES,
    Head,
    Repository,
    clean_folder,
    clean_ref,
    head_of,
    parse_link,
    skill_folders,
)
from ravis.agent.skill_installs import Install, SkillInstalls
from ravis.agent.skill_package import SPECIFICATION
from ravis.agent.skill_sites import SiteEntry, read_index, site_of
from ravis.agent.skill_web import NotFoundError, RateLimitedError, WebError, host_of
from ravis.codex.lock_file import iso
from ravis.config import Settings
from ravis.storage.database import Database

CACHE_HOURS = 24
MOST_SKILLS_PER_SOURCE = 300
MOST_LINKS = 2000
MOST_README_BYTES = 1024 * 1024
MOST_RESOLVE_PER_CALL = 10
MOST_SKILLS_PER_LINK = 50
API_RESERVE = 10
MOST_OWN_SOURCES = 30
MOST_QUERY_CHARACTERS = 100
#: skills.sh refuses a shorter search (HTTP 400, measured 15 September 2026), so none is sent.
LEAST_QUERY_CHARACTERS = 2
SHORT_QUERY = "skills.sh needs a search of at least 2 letters."
SEARCH_LIMIT = 20
MOST_SEARCH_BYTES = 256 * 1024
#: How long a query's results are kept, whatever its letter case (0.28.1): the Skills page searches
#: as the owner types, and typing back to words just searched shouldn't ask skills.sh again.
SEARCH_KEPT_MINUTES = 5
MOST_KEPT_SEARCHES = 100
#: A cached listing that never was read: stale at once, so the next refresh tries again.
NEVER = datetime(1970, 1, 1, tzinfo=UTC)
UNCURATED_LIST = "Uncurated: a list of links to skills kept in other people's repositories."
SKILL_ID = re.compile(r"[A-Za-z0-9._-]{1,100}")


@dataclass(frozen=True)
class Source:
    id: str
    #: `github`, `link_list`, `search` or `website`.
    kind: str
    label: str
    repository: str | None = None
    #: A GitHub source's folders, each with a word the page shows beside its skills.
    roots: tuple[tuple[str, str | None], ...] = ()
    #: A link list's file in its repository.
    path: str = ""
    ref: str | None = None
    site: str | None = None
    default: bool = False
    deprecated: bool = False
    uncurated: bool = False
    note: str | None = None

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "label": self.label,
                "repository": self.repository, "folders": [root for root, _ in self.roots],
                "path": self.path or None, "ref": self.ref, "site": self.site,
                "default": self.default, "deprecated": self.deprecated,
                "uncurated": self.uncurated, "note": self.note}


DEFAULT_SOURCES = (
    Source("anthropics", "github", "anthropics/skills", repository="anthropics/skills",
           roots=(("skills", None),), default=True,
           note="Some skills carry their own license terms: read each one's license."),
    Source("openai", "github", "openai/skills", repository="openai/skills",
           roots=(("skills/.curated", "curated"), ("skills/.experimental", "experimental")),
           default=True, deprecated=True,
           note="Deprecated by its owner, in favour of OpenAI's plugins repository."),
    Source("composio", "github", "ComposioHQ/awesome-claude-skills",
           repository="ComposioHQ/awesome-claude-skills", roots=(("", None),), default=True,
           note="Apache-2.0; a skill may carry its own license."),
    Source("voltagent", "link_list", "VoltAgent/awesome-agent-skills",
           repository="VoltAgent/awesome-agent-skills", path="README.md", default=True,
           uncurated=True, note=UNCURATED_LIST),
    Source("skills_sh", "search", "skills.sh", default=True, uncurated=True,
           note="Uncurated, ranked by installs."),
)


class SkillMarket:
    """The sources, their cached listings, lazy link resolution and skills.sh search."""

    def __init__(self, database: Database, settings: Settings, installs: SkillInstalls) -> None:
        self._database = database
        self._settings = settings
        self._installs = installs
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        #: skills.sh's parsed results by query (case-folded), with when they were asked: kept in
        #: memory only, `SEARCH_KEPT_MINUTES`, at most `MOST_KEPT_SEARCHES`, oldest dropped first.
        self._searches: dict[str, tuple[datetime, list[dict[str, Any]]]] = {}

    @property
    def _web(self) -> Any:
        return self._installs.web

    def _now(self) -> datetime:
        """The install service's clock, so a test moves both with one hand."""
        return self._installs.now()

    # ── Sources ──────────────────────────────────────────────────────────────

    def sources(self) -> list[Source]:
        rows = self._database.connection.execute(
            "SELECT * FROM skill_market_source ORDER BY added_at, id").fetchall()
        return [*DEFAULT_SOURCES, *(own_source(row) for row in rows)]

    def source(self, source_id: object, kind: str | None = None) -> Source:
        """A source shown on the page, of `kind` when named; `MARKET_SOURCE_NOT_FOUND` otherwise."""
        found = next((source for source in self.sources() if source.id == source_id), None)
        if found is None or found.id in self._hidden() or (kind and found.kind != kind):
            raise refusals.source_not_found(str(source_id))
        return found

    def _hidden(self) -> set[str]:
        rows = self._database.connection.execute("SELECT source_id FROM skill_market_hidden")
        return {row["source_id"] for row in rows}

    def add_source(self, kind: object, repository: object, path: object, ref: object,
                   url: object) -> Source:
        """One of the owner's own sources, checked; `INVALID_REQUEST_BODY` when it can't be one."""
        source = _own_from_body(kind, repository, path, ref, url)
        if any(existing.id == source.id for existing in self.sources()):
            raise refusals.source_exists(source.id)
        count = self._database.connection.execute(
            "SELECT COUNT(*) AS count FROM skill_market_source").fetchone()["count"]
        if count >= MOST_OWN_SOURCES:
            raise refusals.sources_full(MOST_OWN_SOURCES)
        self._database.connection.execute(
            "INSERT INTO skill_market_source (id, kind, repository, path, ref, site, added_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (source.id, source.kind, source.repository,
             source.roots[0][0] if source.kind == "github" else source.path,
             source.ref, source.site, iso(self._now())),
        )
        return source

    def set_hidden(self, source_id: str, hidden: bool) -> Source:
        found = next((source for source in self.sources() if source.id == source_id), None)
        if found is None:
            raise refusals.source_not_found(source_id)
        if hidden:
            self._database.connection.execute(
                "INSERT OR IGNORE INTO skill_market_hidden (source_id, hidden_at) VALUES (?, ?)",
                (source_id, iso(self._now())))
        else:
            self._database.connection.execute(
                "DELETE FROM skill_market_hidden WHERE source_id = ?", (source_id,))
        return found

    def remove_source(self, source_id: str) -> Source:
        found = next((source for source in self.sources() if source.id == source_id), None)
        if found is None:
            raise refusals.source_not_found(source_id)
        if found.default:
            raise refusals.source_is_default(source_id)
        for statement in ("DELETE FROM skill_market_source WHERE id = ?",
                          "DELETE FROM skill_market_hidden WHERE source_id = ?",
                          "DELETE FROM skill_market_cache WHERE key = 'index:' || ?"):
            self._database.connection.execute(statement, (source_id,))
        return found

    # ── The cache ────────────────────────────────────────────────────────────

    def _cached(self, key: str) -> tuple[datetime, dict[str, Any]] | None:
        row = self._database.connection.execute(
            "SELECT fetched_at, value FROM skill_market_cache WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        fetched = datetime.strptime(row["fetched_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        return fetched, json.loads(row["value"])

    def _store(self, key: str, value: dict[str, Any], fetched_at: datetime) -> None:
        self._database.connection.execute(
            "INSERT OR REPLACE INTO skill_market_cache (key, fetched_at, value) VALUES (?, ?, ?)",
            (key, iso(fetched_at), json.dumps(value)))

    def _fresh(self, fetched_at: datetime) -> bool:
        return self._now() - fetched_at < timedelta(hours=CACHE_HOURS)

    # ── What the page shows ──────────────────────────────────────────────────

    async def view(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._view)

    def _view(self) -> dict[str, Any]:
        records, hidden = self._installs.records(), self._hidden()
        sources, entries = [], []
        for source in self.sources():
            cached = self._cached(f"index:{source.id}") if source.kind != "search" else None
            value = cached[1] if cached else {}
            listed = self._entries(source, value, records) if source.id not in hidden else []
            sources.append({
                **source.view(), "hidden": source.id in hidden,
                "fetched_at": iso(cached[0]) if cached and cached[0] != NEVER else None,
                "stale": source.kind != "search" and (cached is None
                                                      or not self._fresh(cached[0])),
                "problem": value.get("problem"), "count": len(listed),
            })
            entries += listed
        web = self._web
        return {"checked_against": SPECIFICATION, "sources": sources, "entries": entries,
                "github": {"api_remaining": web.api_remaining,
                           "resets_at": iso(web.api_resets_at) if web.api_resets_at else None}}

    def _entries(self, source: Source, value: dict[str, Any], records: list[Install]
                 ) -> list[dict[str, Any]]:
        if source.kind == "github":
            return [_github_entry(source, source.repository or "", source.ref, skill, None,
                                  records) for skill in value.get("skills", [])]
        if source.kind == "link_list":
            return [entry for link in value.get("links", [])
                    for entry in self._link_entries(source, link, records)]
        if source.kind == "website":
            return [_site_entry(source, SiteEntry.from_view(raw), records)
                    for raw in value.get("entries", [])]
        return []

    def _link_entries(self, source: Source, link: dict[str, Any], records: list[Install]
                      ) -> list[dict[str, Any]]:
        url = str(link["url"])
        base = {"source": source.id, "link": url, "name": link["name"],
                "description": link["description"], "license": None, "label": None,
                "repository": None, "folder": None, "ref": None, "site": None,
                "installed": False, "install": None, "problem": None}
        if not link["github"]:
            host = urlsplit(url).hostname or url
            return [{**base, "id": f"{source.id}:{url}", "state": "not_installable",
                     "lives_in": host,
                     "problem": f"It is kept on {host}, not GitHub, so it can't be installed "
                                "here."}]
        parsed = parse_link(url)
        cached = self._cached(f"link:{url}")
        lives_in = parsed.repository.full if parsed else None
        if cached is None or not self._fresh(cached[0]):
            return [{**base, "id": f"{source.id}:{url}", "state": "unresolved",
                     "lives_in": lives_in}]
        skills = cached[1].get("skills", [])
        if not skills:
            return [{**base, "id": f"{source.id}:{url}", "state": "problem",
                     "lives_in": lives_in, "problem": cached[1].get("problem")}]
        return [_github_entry(source, skill["repository"], skill["ref"], skill, url, records)
                for skill in skills]

    # ── Reading sources again ────────────────────────────────────────────────

    def _one_at_a_time(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock

    async def refresh(self, source_id: str | None, force: bool) -> dict[str, Any]:
        """Read again every source shown (or the one named) whose listing is a day old, or all of
        them when forced; the page's view afterwards."""
        wanted = [self.source(source_id)] if source_id is not None else [
            source for source in self.sources() if source.id not in self._hidden()]
        async with self._one_at_a_time():
            for source in wanted:
                if source.kind != "search":
                    await self._refresh_one(source, force)
        return await self.view()

    async def _refresh_one(self, source: Source, force: bool) -> None:
        key = f"index:{source.id}"
        cached = self._cached(key)
        if cached is not None and not force and self._fresh(cached[0]):
            return
        try:
            value = await self._read(source)
        except WebError as failure:
            kept = cached[1] if cached else {}
            self._store(key, {**kept, "problem": failure_words(failure)},
                        cached[0] if cached else NEVER)
            return
        self._store(key, value, self._now())

    async def _read(self, source: Source) -> dict[str, Any]:
        if source.kind == "website":
            return await self._read_site(source)
        repository = Repository.parse(source.repository)
        if repository is None:
            raise NotFoundError(f"{source.repository} isn't a GitHub repository RAVIS reads")
        ref = source.ref or "HEAD"
        github = self._installs.github
        if source.kind == "link_list":
            data = await github.raw(repository, ref, source.path, MOST_README_BYTES)
            links, cut = parse_links(data.decode("utf-8", "replace"))
            return {"links": links,
                    "problem": f"Only the first {MOST_LINKS} links are listed." if cut else None}
        entries, truncated = await github.tree(repository, ref)
        labels = dict(source.roots)
        folders, left_out = skill_folders(entries, list(labels), MOST_SKILLS_PER_SOURCE)
        heads = await github.heads(repository, ref, folders)
        skills = [{**_head_view(head), "label": _label(head.folder, labels)} for head in heads]
        problem = ("GitHub cut the repository's list of files short, so some skills may be "
                   "missing." if truncated else
                   left_out_words(MOST_SKILLS_PER_SOURCE, left_out, "") if left_out else None)
        return {"skills": skills, "problem": problem}

    async def _read_site(self, source: Source) -> dict[str, Any]:
        site = site_of(source.site)
        if site is None:
            raise NotFoundError(f"{source.site} isn't a website address RAVIS reads")
        index = await read_index(self._web, site)
        skipped = index.skipped
        return {"version": index.version, "index_url": index.index_url,
                "entries": [entry.view() for entry in index.entries],
                "problem": (f"{skipped} entr{'y' if skipped == 1 else 'ies'} in its index broke "
                            "the standard and are left out." if skipped else None)}

    # ── A link list's links, resolved as they are shown ──────────────────────

    async def resolve(self, source_id: str, links: list[str]) -> dict[str, Any]:
        """Resolve the named links of a link list (the ones the page shows), lazily and capped;
        the page's view afterwards, with `note` saying why some weren't, when any weren't."""
        source = self.source(source_id, "link_list")
        cached = self._cached(f"index:{source.id}")
        known = {link["url"] for link in (cached[1].get("links", []) if cached else [])
                 if link["github"]}
        wanted = [url for url in dict.fromkeys(links) if url in known][:MOST_RESOLVE_PER_CALL]
        note = None
        async with self._one_at_a_time():
            for url in wanted:
                hit = self._cached(f"link:{url}")
                if hit is not None and self._fresh(hit[0]):
                    continue
                note = self._allowance_note()
                if note is not None:
                    break
                try:
                    value = await self._resolve_link(url)
                except RateLimitedError as failure:
                    note = failure_words(failure)
                    break
                except WebError as failure:
                    value = {"skills": [], "problem": refusals.sentence(failure.why)}
                self._store(f"link:{url}", value, self._now())
        return {**await self.view(), "note": note}

    def _allowance_note(self) -> str | None:
        web = self._web
        if web.api_remaining is None or web.api_remaining > API_RESERVE:
            return None
        resets = web.api_resets_at
        if resets is not None and resets <= self._now():
            return None
        when = f" after {refusals.local_time(resets)}" if resets else " later"
        return ("GitHub's hourly allowance for RAVIS is nearly used up, so the rest of these "
                f"links are looked up{when}; what is left is kept for installs.")

    async def _resolve_link(self, url: str) -> dict[str, Any]:
        """The skills a link leads to: its own folder's `SKILL.md` when it is a skill (no API
        call), else the skill folders below it (one API call per ref it may be)."""
        link = parse_link(url)
        if link is None:
            return {"skills": [], "problem": "That isn't a link to a GitHub folder."}
        github, repository = self._installs.github, link.repository
        splits = link.splits()
        try:
            data = await github.raw(repository, "/".join(link.rest) or "HEAD", SKILL_FILE,
                                    MOST_HEAD_BYTES)
        except NotFoundError:
            data = None
        if data is not None:
            ref, folder = splits[0]
            head = head_of(folder or repository.name, folder, data, None)
            return {"skills": [_link_skill(repository, ref, head)], "problem": None}
        for ref, folder in splits:
            try:
                entries, _ = await github.tree(repository, ref or "HEAD")
            except NotFoundError:
                continue
            folders, left_out = skill_folders(entries, [folder], MOST_SKILLS_PER_LINK)
            if not folders:
                return {"skills": [], "problem": "There is no folder holding a SKILL.md at that "
                                                 "link."}
            heads = await github.heads(repository, ref or "HEAD", folders)
            return {"skills": [_link_skill(repository, ref, head) for head in heads],
                    "problem": (left_out_words(MOST_SKILLS_PER_LINK, left_out, " at that link")
                                if left_out else None)}
        return {"skills": [], "problem": f"GitHub has no such branch, tag or folder in "
                                         f"{repository.full}."}

    # ── skills.sh ────────────────────────────────────────────────────────────

    async def search(self, query: str) -> dict[str, Any]:
        """skills.sh's results for a query, most installed first; a failure is a plain line.

        Never sent when shorter than `LEAST_QUERY_CHARACTERS`, and answered from the same query
        kept `SEARCH_KEPT_MINUTES` when there is one (0.28.1). Whether each result is installed is
        worked out on every answer, kept or not, so an install shows at once."""
        source = self.source("skills_sh", "search")
        answer: dict[str, Any] = {"source": source.id, "query": query, "note": source.note,
                                  "results": [], "problem": None}
        if len(query) < LEAST_QUERY_CHARACTERS:
            return {**answer, "problem": SHORT_QUERY}
        results, problem = await self._results(query)
        if problem is not None:
            return {**answer, "problem": problem}
        records = await asyncio.to_thread(self._installs.records)
        # Copies, so what is kept is never marked: the next answer works installed out again.
        return {**answer, "results": [
            {**result, "installed": any(_found_installed(record, result) for record in records)}
            for result in results]}

    async def _results(self, query: str) -> tuple[list[dict[str, Any]], str | None]:
        """A query's results, kept or asked for now; or none, with why in plain words. Only an
        answer RAVIS could read is kept: a failure asks again next time."""
        key, now = query.casefold(), self._now()
        kept = self._searches.get(key)
        if kept is not None and now - kept[0] < timedelta(minutes=SEARCH_KEPT_MINUTES):
            return kept[1], None
        base = self._settings.skills_sh_url.rstrip("/")
        host = host_of(base)
        if host is None:
            return [], "RAVIS's skills.sh address isn't an https address, so it can't search."
        try:
            fetched = await self._web.get(
                f"{base}/api/search?q={quote(query)}&limit={SEARCH_LIMIT}",
                hosts=frozenset({host}), most_bytes=MOST_SEARCH_BYTES, accept="application/json",
                readable=frozenset({400}))
        except WebError as failure:
            return [], f"skills.sh couldn't be searched: {failure_words(failure)}"
        if fetched.status == 400:
            return [], refused_words(fetched.body)
        results = search_results(fetched.body)
        if results is None:
            return [], UNREADABLE_SEARCH
        self._keep(key, now, results)
        return results, None

    def _keep(self, key: str, now: datetime, results: list[dict[str, Any]]) -> None:
        """A query's results kept: expired queries dropped first, then the oldest while there are
        `MOST_KEPT_SEARCHES` (a dict keeps the order queries were added in)."""
        fresh = timedelta(minutes=SEARCH_KEPT_MINUTES)
        self._searches = {kept: value for kept, value in self._searches.items()
                          if now - value[0] < fresh and kept != key}
        while len(self._searches) >= MOST_KEPT_SEARCHES:
            del self._searches[next(iter(self._searches))]
        self._searches[key] = (now, results)


# ── Shapes ───────────────────────────────────────────────────────────────────


def own_source(row: Any) -> Source:
    """One of the owner's sources, as a `Source`, from its row."""
    kind = row["kind"]
    if kind == "website":
        host = urlsplit(row["site"]).netloc
        return Source(row["id"], kind, host, site=row["site"])
    ref_words = f" at {row['ref']}" if row["ref"] else ""
    if kind == "link_list":
        return Source(row["id"], kind, f"{row['repository']}/{row['path']}{ref_words}",
                      repository=row["repository"], path=row["path"], ref=row["ref"],
                      uncurated=True, note=UNCURATED_LIST)
    folder_words = f"/{row['path']}" if row["path"] else ""
    return Source(row["id"], kind, f"{row['repository']}{folder_words}{ref_words}",
                  repository=row["repository"], roots=((row["path"], None),), ref=row["ref"])


def _own_from_body(kind: object, repository: object, path: object, ref: object, url: object
                   ) -> Source:
    if kind == "website":
        site = site_of(url)
        if site is None:
            raise refusals.invalid_body("A website must be an https address, like "
                                        "https://example.com or https://example.com/docs.")
        return Source(_own_id("website", site.url), "website", site.host, site=site.url)
    parsed = Repository.parse(repository)
    folder = clean_folder(path)
    branch = None if ref in (None, "") else clean_ref(ref)
    if kind not in ("github", "link_list") or parsed is None or folder is None or (
            ref not in (None, "") and branch is None):
        raise refusals.invalid_body(
            "A source needs kind github, link_list or website; a GitHub source or link list "
            "needs repository as owner/name, and may have path (a folder, or the list's file) "
            "and ref (a branch or tag); a website needs url.")
    if kind == "link_list":
        folder = folder or "README.md"
        return Source(_own_id(kind, parsed.key, folder, branch or ""), kind, "",
                      repository=parsed.full, path=folder, ref=branch)
    return Source(_own_id(kind, parsed.key, folder, branch or ""), kind, "",
                  repository=parsed.full, roots=((folder, None),), ref=branch)


def _own_id(*parts: str) -> str:
    return "own-" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def _head_view(head: Head) -> dict[str, Any]:
    return {"folder": head.folder, "name": head.name, "description": head.description,
            "license": head.license, "problem": head.problem}


def _label(folder: str, labels: dict[str, str | None]) -> str | None:
    return next((label for root, label in labels.items()
                 if root == "" or folder == root or folder.startswith(f"{root}/")), None)


def _link_skill(repository: Repository, ref: str | None, head: Head) -> dict[str, Any]:
    return {**_head_view(head), "repository": repository.full, "ref": ref, "label": None}


def _github_entry(source: Source, repository: str, ref: str | None, skill: dict[str, Any],
                  link: str | None, records: list[Install]) -> dict[str, Any]:
    folder = str(skill["folder"])
    installed = any(record.origin.kind == "github"
                    and (record.origin.repository or "").lower() == repository.lower()
                    and (record.origin.folder or "") == folder for record in records)
    tail = f"/tree/{quote(ref or 'HEAD', safe='/')}/{quote(folder)}" if folder else ""
    return {
        "id": f"{source.id}:{link or ''}:{repository}/{folder}",
        "source": source.id, "state": "problem" if skill.get("problem") else "ready",
        "name": skill["name"], "description": skill["description"],
        "license": skill.get("license"), "label": skill.get("label"),
        "problem": skill.get("problem"), "lives_in": repository,
        "repository": repository, "folder": folder, "ref": ref, "site": None,
        "link": link or f"https://github.com/{repository}{tail}",
        "installed": installed,
        "install": {"origin": "github", "repository": repository, "folder": folder, "ref": ref,
                    "via": source.id},
    }


def _site_entry(source: Source, entry: SiteEntry, records: list[Install]) -> dict[str, Any]:
    installed = any(record.origin.kind == "website" and record.origin.site == source.site
                    and record.name == entry.name for record in records)
    return {
        "id": f"{source.id}:{entry.name}", "source": source.id,
        "state": "not_installable" if entry.problem else "ready",
        "name": entry.name, "description": entry.description, "license": None, "label": None,
        "problem": entry.problem, "lives_in": source.label, "repository": None, "folder": None,
        "ref": None, "site": source.site, "link": entry.url, "installed": installed,
        "install": None if entry.problem else {"origin": "website", "source": source.id,
                                               "name": entry.name},
    }


def failure_words(failure: WebError) -> str:
    if isinstance(failure, RateLimitedError):
        return (f"{refusals.host_label(failure.host)} is rate-limiting RAVIS; try again at "
                f"{refusals.local_time(failure.retry_at)}.")
    return refusals.sentence(failure.why)


def left_out_words(listed: int, left_out: int, where: str) -> str:
    """A capped listing's problem line: how many it lists of how many, in which order, and how many
    it leaves out (0.28.1). `where` is `""` for a source, `" at that link"` for a link."""
    verb = "is" if left_out == 1 else "are"
    return (f"Only the first {listed} of {listed + left_out} skills{where} are listed, top folders "
            f"before sub-folders; {left_out} {verb} left out.")


UNREADABLE_SEARCH = ("skills.sh answered in a shape RAVIS doesn't read, so there are no results "
                     "to show.")


def refused_words(body: bytes) -> str:
    """skills.sh's refusal of a search (HTTP 400), in its own words when its answer carries some:
    `{"error": "Query must be at least 2 characters"}`, as recorded on 15 September 2026."""
    try:
        said = json.loads(body).get("error")
    except (ValueError, AttributeError):
        said = None
    if not isinstance(said, str) or not said.strip():
        return "skills.sh refused the search without saying why."
    return f"skills.sh refused the search: {refusals.sentence(one_line(said, 200))}"


def _found_installed(record: Install, result: dict[str, Any]) -> bool:
    """Whether an install came from a search result: the same repository, and a folder named as
    the result's skill, whatever the letter case of either (skills.sh's `source` comes in lower
    case, and GitHub's own name may not)."""
    folder = (record.origin.folder or "").rsplit("/", 1)[-1]
    return (record.origin.kind == "github"
            and (record.origin.repository or "").lower() == str(result["repository"]).lower()
            and folder.lower() == str(result["skill"]).lower())


ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?P<rest>.+)$")
ROW = re.compile(r"^\s*\|(?P<rest>.+)\|\s*$")
LINK = re.compile(r"(?<!!)\[(?P<text>[^\]]+)\]\((?P<url>[^)\s]+)(?:\s+\"[^\"]*\")?\)")


def parse_links(text: str) -> tuple[list[dict[str, Any]], bool]:
    """A link list's entries: each list item or table row whose first link is a web address, with
    the link's words as its name and the rest of the line as its description; whether the list was
    cut at `MOST_LINKS`. A github.com link that can't be a skill (an issue, a person's page) is left
    out; any other host's link is kept, to be shown as not installable here."""
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        match = ITEM.match(line) or ROW.match(line)
        found = next((link for link in LINK.finditer(match["rest"])
                      if link["url"].startswith(("https://", "http://"))), None) if match else None
        if match is None or found is None or found["url"] in seen:
            continue
        name = one_line(re.sub(r"[*_`]", "", found["text"]), 100)
        host = (urlsplit(found["url"]).hostname or "").lower()
        github = host in ("github.com", "www.github.com")
        if not name or (github and parse_link(found["url"]) is None):
            continue
        if len(links) >= MOST_LINKS:
            return links, True
        seen.add(found["url"])
        rest = LINK.sub(lambda inner: inner["text"], match["rest"][found.end():])
        description = one_line(re.sub(r"[*_`|]", " ", rest).strip(" -–—:"), 300)
        links.append({"url": found["url"], "name": name, "description": description,
                      "github": github})
    return links, False


def search_results(body: bytes) -> list[dict[str, Any]] | None:
    """skills.sh's answer as results, most installed first; None when it isn't one RAVIS reads.

    The shape, read from one real search on 15 September 2026: `{query, searchType,
    searchVersion, skills: [{id, skillId, name, installs, source}], count, duration_ms}`, where
    `source` is the GitHub repository (`anthropics/skills`) and `skillId` the skill's folder name.
    """
    try:
        data = json.loads(body)
    except ValueError:
        return None
    skills = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(skills, list):
        return None
    results = [result for result in map(_search_result, skills) if result is not None]
    if skills and not results:
        return None
    return sorted(results, key=lambda result: -result["installs"])


def _search_result(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    repository = Repository.parse(raw.get("source"))
    name, installs = raw.get("name"), raw.get("installs")
    skill = raw.get("skillId") or name
    if (repository is None or not isinstance(name, str) or not isinstance(skill, str)
            or not SKILL_ID.fullmatch(skill)):
        return None
    count = installs if isinstance(installs, int) and installs >= 0 else 0
    return {"name": one_line(name, 100), "skill": skill, "repository": repository.full,
            "installs": count, "installed": False,
            "install": {"origin": "github", "repository": repository.full, "skill": skill,
                        "via": "skills_sh"}}
