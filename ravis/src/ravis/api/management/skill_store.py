"""The skill store's routes: installs and the marketplace, for NERVIS's Skills page (RAVIS 0.28.0).

The shapes, codes and messages are the fixture `tests/fixtures/skill-store/contract.json`, NERVIS's
alone (`agent/store_refusals.py` says why it isn't in `relay-contract/`). This module is the edge of
`agent/skill_installs.py` and `agent/skill_market.py`.

**Who may call** (owner decisions, 15 September 2026): the two reads, `GET /api/v1/skills/installs`
and `GET /api/v1/skills/market`, take NERVIS's GET relay or an admin credential, as
`GET /api/v1/skills` does; everything else takes an admin credential — NERVIS's Skills page,
through its control routes. Never Clarvis.

**Bodies carry names, never addresses**: a skill's name, a preview's id or a source's id travels in
the body, so NERVIS forwards to fixed paths and nothing the page sends changes where a call goes. A
zip file is the whole body of `POST /api/v1/skills/previews/zip`, at most 8 MB.

**Retries need no `Idempotency-Key`**: confirming a preview again answers what the first confirm
did; removing a skill already removed is 404 `SKILL_NOT_INSTALLED`; hiding and showing a source set
a state; adding a source twice is 409 `MARKET_SOURCE_EXISTS`.

**Audited without contents**: `ravis.skill_installed` and `ravis.skill_updated` (where from, the
commit or digest, the hash, how many files and scripts, and for an update whether the switches were
reset), `ravis.skill_removed` (whether a folder went to the Trash), and `ravis.skill_source_added`,
`ravis.skill_source_hidden` and `ravis.skill_source_removed`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from ravis.agent import store_refusals as refusals
from ravis.agent.skill_github import Repository, clean_folder, clean_ref
from ravis.agent.skill_installs import Preview, SkillInstalls
from ravis.agent.skill_market import MOST_QUERY_CHARACTERS, SKILL_ID, SkillMarket
from ravis.agent.skill_package import NAME
from ravis.api.management import audit
from ravis.api.management.codex import _json_object, require_admin
from ravis.api.management.skills import require_board_reader

router = APIRouter(prefix="/api/v1/skills", tags=["skill store"])

PREVIEW_BODY = (
    "The body must carry origin: github, with url, or with repository and perhaps folder and "
    "ref, or with repository and skill; or website, with source and name."
)
PREVIEW_ID_BODY = "The body must carry preview_id, as a review gave it."
NAME_BODY = "The body must carry name, the name of a skill RAVIS installed."
SOURCE_BODY = "The body must carry source, the id of a source in the list."
MOST_URL_CHARACTERS = 2000
MOST_LINKS_NAMED = 50


def _installs(request: Request) -> SkillInstalls:
    return request.app.state.skill_installs  # type: ignore[no-any-return]


def _market(request: Request) -> SkillMarket:
    return request.app.state.skill_market  # type: ignore[no-any-return]


# ── Reads ────────────────────────────────────────────────────────────────────


@router.get("/installs", dependencies=[Depends(require_board_reader)])
async def read_installs(request: Request) -> dict[str, Any]:
    """Every skill RAVIS installed: where from, when, and whether its folder is still as it was."""
    return await _installs(request).installs()


@router.get("/market", dependencies=[Depends(require_board_reader)])
async def read_market(request: Request) -> dict[str, Any]:
    """The sources and what their cached listings hold. Nothing is fetched here."""
    return await _market(request).view()


# ── Reviews, installs, updates and removals ─────────────────────────────────


@router.post("/previews", dependencies=[Depends(require_admin)])
async def preview_skill(request: Request) -> dict[str, Any]:
    """Fetch a skill from GitHub or a website's index, check it and stage it: its review."""
    body = await _json_object(request)
    with refusals.translated():
        preview = await _preview(request, body)
    return preview.view()


async def _preview(request: Request, body: dict[str, Any]) -> Preview:
    if body.get("origin") == "website":
        source = _market(request).source(body.get("source"), "website")
        name = body.get("name")
        if not isinstance(name, str) or not NAME.fullmatch(name) or source.site is None:
            raise refusals.invalid_body(PREVIEW_BODY)
        return await _installs(request).preview_website(source.site, name, source.id)
    if body.get("origin") != "github":
        raise refusals.invalid_body(PREVIEW_BODY)
    return await _github_preview(request, body)


async def _github_preview(request: Request, body: dict[str, Any]) -> Preview:
    installs, via = _installs(request), _via(request, body.get("via"))
    url = body.get("url")
    if url is not None:
        if not isinstance(url, str) or len(url) > MOST_URL_CHARACTERS:
            raise refusals.invalid_body(PREVIEW_BODY)
        return await installs.preview_github(url=url, via=via)
    repository, folder = Repository.parse(body.get("repository")), clean_folder(body.get("folder"))
    ref, skill = body.get("ref"), body.get("skill")
    if (repository is None or folder is None or (ref is not None and clean_ref(ref) is None)
            or (skill is not None and (not isinstance(skill, str)
                                       or not SKILL_ID.fullmatch(skill)))):
        raise refusals.invalid_body(PREVIEW_BODY)
    return await installs.preview_github(repository=repository, folder=folder, ref=ref,
                                         skill=skill, via=via)


def _via(request: Request, value: object) -> str | None:
    """The marketplace source a review was opened from: a source in the list, or none."""
    if value is None:
        return None
    if not any(source.id == value for source in _market(request).sources()):
        raise refusals.invalid_body("via must be the id of a source in the list.")
    return str(value)


@router.post("/previews/zip", dependencies=[Depends(require_admin)])
async def preview_zip(request: Request) -> dict[str, Any]:
    """Unpack a zip file sent as the body, check it and stage it: its review."""
    data = await request.body()
    if not data:
        raise refusals.invalid_body("The body must be the zip file itself.")
    with refusals.translated():
        preview = await _installs(request).preview_zip(data)
    return preview.view()


@router.post("/previews/discard", dependencies=[Depends(require_admin)])
async def discard_preview(request: Request) -> dict[str, Any]:
    """The owner closed a review: its staging goes now rather than when it expires."""
    preview_id = _preview_id(await _json_object(request))
    return {"discarded": _installs(request).discard(preview_id)}


@router.post("/installs", dependencies=[Depends(require_admin)])
async def confirm_preview(request: Request) -> dict[str, Any]:
    """Install, or update, from a review the owner confirmed."""
    preview_id = _preview_id(await _json_object(request))
    with refusals.translated():
        result, facts = await _installs(request).confirm(preview_id)
    if facts is not None:
        updated = "switches_reset" in facts
        audit.record(request, "ravis.skill_updated" if updated else "ravis.skill_installed",
                     **facts)
    return result


@router.post("/installs/update-preview", dependencies=[Depends(require_admin)])
async def preview_update(request: Request) -> dict[str, Any]:
    """An installed skill's update, fetched from where it came from, with what changed."""
    name = _name(await _json_object(request))
    with refusals.translated():
        answer = await _installs(request).preview_update(name)
    return answer.view() if isinstance(answer, Preview) else answer


@router.post("/installs/remove", dependencies=[Depends(require_admin)])
async def remove_skill(request: Request) -> dict[str, Any]:
    """Move a skill RAVIS installed to the Trash, and forget its switches and its record."""
    name = _name(await _json_object(request))
    result, facts = await _installs(request).remove(name)
    audit.record(request, "ravis.skill_removed", **facts)
    return result


def _preview_id(body: dict[str, Any]) -> str:
    value = body.get("preview_id")
    if not isinstance(value, str) or not value.startswith("sp_") or len(value) > 100:
        raise refusals.invalid_body(PREVIEW_ID_BODY)
    return value


def _name(body: dict[str, Any]) -> str:
    value = body.get("name")
    if not isinstance(value, str) or not NAME.fullmatch(value) or len(value) > 64:
        raise refusals.invalid_body(NAME_BODY)
    return value


# ── The marketplace ──────────────────────────────────────────────────────────


@router.post("/market/refresh", dependencies=[Depends(require_admin)])
async def refresh_market(request: Request) -> dict[str, Any]:
    """Read again the sources whose listings are a day old, or every one when forced."""
    body = await _json_object(request)
    source, force = body.get("source"), body.get("force", False)
    if (source is not None and not isinstance(source, str)) or not isinstance(force, bool):
        raise refusals.invalid_body("The body may carry source, the id of a source in the list, "
                                    "and force, true or false.")
    return await _market(request).refresh(source, force)


@router.post("/market/resolve", dependencies=[Depends(require_admin)])
async def resolve_links(request: Request) -> dict[str, Any]:
    """Look up the skills behind a link list's links the page is showing."""
    body = await _json_object(request)
    source, links = body.get("source"), body.get("links")
    if (not isinstance(source, str) or not isinstance(links, list)
            or not 0 < len(links) <= MOST_LINKS_NAMED
            or not all(isinstance(link, str) and len(link) <= MOST_URL_CHARACTERS
                       for link in links)):
        raise refusals.invalid_body("The body must carry source, a link list's id, and links, "
                                    f"1 to {MOST_LINKS_NAMED} of its links.")
    return await _market(request).resolve(source, links)


@router.post("/market/search", dependencies=[Depends(require_admin)])
async def search_market(request: Request) -> dict[str, Any]:
    """Search skills.sh: its results, most installed first, or a plain line why there are none."""
    query = (await _json_object(request)).get("query")
    if not isinstance(query, str) or not 0 < len(query.strip()) <= MOST_QUERY_CHARACTERS:
        raise refusals.invalid_body(f"The body must carry query, 1 to {MOST_QUERY_CHARACTERS} "
                                    "characters.")
    return await _market(request).search(query.strip())


@router.post("/market/sources", dependencies=[Depends(require_admin)])
async def add_source(request: Request) -> dict[str, Any]:
    """Add one of the owner's own sources: a GitHub repository, a link list or a website."""
    body = await _json_object(request)
    market = _market(request)
    source = market.add_source(body.get("kind"), body.get("repository"), body.get("path"),
                               body.get("ref"), body.get("url"))
    audit.record(request, "ravis.skill_source_added", source=source.id, kind=source.kind,
                 repository=source.repository, path=source.path or None, ref=source.ref,
                 site=source.site)
    return await market.view()


@router.post("/market/sources/hide", dependencies=[Depends(require_admin)])
async def hide_source(request: Request) -> dict[str, Any]:
    """Hide a source from the page, or show it again."""
    body = await _json_object(request)
    source, hidden = body.get("source"), body.get("hidden")
    if not isinstance(source, str) or not isinstance(hidden, bool):
        raise refusals.invalid_body("The body must carry source, the id of a source in the list, "
                                    "and hidden, true or false.")
    market = _market(request)
    found = market.set_hidden(source, hidden)
    audit.record(request, "ravis.skill_source_hidden", source=found.id, hidden=hidden)
    return await market.view()


@router.post("/market/sources/remove", dependencies=[Depends(require_admin)])
async def remove_source(request: Request) -> dict[str, Any]:
    """Remove one of the owner's own sources; RAVIS's own can only be hidden."""
    source = (await _json_object(request)).get("source")
    if not isinstance(source, str):
        raise refusals.invalid_body(SOURCE_BODY)
    market = _market(request)
    found = market.remove_source(source)
    audit.record(request, "ravis.skill_source_removed", source=found.id, kind=found.kind)
    return await market.view()
