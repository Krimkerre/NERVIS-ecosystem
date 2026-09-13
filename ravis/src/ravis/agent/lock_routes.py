"""The project lock's HTTP edge: `/api/v1/project-locks…` (RAVIS.md §15.1.2; design §3.6).

Every route carries `require_agent_client`, GETs included (`conventions.json` → `identity_rule`);
`tests/test_agent_sessions_identity.py` holds the route table to it. The decisions are
`lock_api.py`'s; this module reads the headers and keys, replays a retry and audits a takeover.

**Each route's order:** the caller (the router's dependency); the `Idempotency-Key` where one is
required (428), and a kept answer replayed; the body (422 `INVALID_REQUEST_BODY`); then the action.
A lease travels in `X-Lock-Lease`; a Codex session's transfer may instead present its token in
`X-Agent-Session-Token`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ravis.agent import refusals
from ravis.agent.idempotency import body_sha256, required_key, scope
from ravis.agent.identity import TOKEN_HEADER, require_agent_client
from ravis.agent.locks import CODEX_SESSION
from ravis.agent.requests import mapping
from ravis.agent.routes import json_body, sessions_of
from ravis.agent.sessions import AgentSessions
from ravis.api.management import audit

# The prefix is written out, not named: `tools/knowledge_check.py` reads it from the source.
router = APIRouter(prefix="/api/v1/project-locks", tags=["project-locks"],
                   dependencies=[Depends(require_agent_client)])

LEASE_HEADER = "x-lock-lease"


def _kept_token(sessions: AgentSessions, where: str, key: str, reissue: Callable[[], str]) -> str:
    """A replay's lease or token: the one held in memory, or — after a restart — a new one."""
    token = sessions.kept.token(where, key)
    if token is None:
        token = reissue()
        sessions.kept.remember(where, key, token)
    return token


@router.get("")
async def read_lock(request: Request) -> dict[str, Any]:
    windows = sessions_of(request).windows
    return {"lock": windows.current(request.query_params.get("workspace_root"))}


@router.post("")
async def acquire_lock(request: Request) -> JSONResponse:
    sessions = sessions_of(request)
    key = required_key(request)
    raw, body = await json_body(request)
    window = mapping(body.get("holder")).get("window_id")
    where = scope("lock-create", body.get("workspace_root"), window)
    kept = sessions.kept.find(where, key, body_sha256(raw))
    if kept is not None:
        lock_id = str(mapping(kept[1].get("lock")).get("id"))
        lease = _kept_token(sessions, where, key,
                            lambda: sessions.windows.reissue_lease(lock_id, window))
        return JSONResponse({**kept[1], "lease_token": lease}, status_code=kept[0])
    lock, lease = sessions.windows.acquire(body)
    answer = {"lock": lock, "lease_token": lease}
    sessions.kept.keep(where, key, body_sha256(raw), 201, answer)
    return JSONResponse(answer, status_code=201)


@router.post("/{lid}/heartbeat")
async def heartbeat(lid: str, request: Request) -> dict[str, Any]:
    sessions = sessions_of(request)
    _, body = await json_body(request)
    return {"lock": sessions.windows.heartbeat(lid, request.headers.get(LEASE_HEADER), body)}


@router.post("/{lid}/release")
async def release(lid: str, request: Request) -> dict[str, Any]:
    sessions = sessions_of(request)
    _, body = await json_body(request)
    sessions.windows.release(lid, request.headers.get(LEASE_HEADER), body)
    return {"lock": None}


@router.post("/{lid}/takeover")
async def take_over(lid: str, request: Request) -> JSONResponse:
    sessions = sessions_of(request)
    key = required_key(request)
    raw, body = await json_body(request)
    window = mapping(body.get("window")).get("id")
    where = scope("lock-takeover", lid, window)
    kept = sessions.kept.find(where, key, body_sha256(raw))
    if kept is not None:
        lease = _kept_token(sessions, where, key,
                            lambda: sessions.windows.reissue_lease(lid, window))
        return JSONResponse({**kept[1], "lease_token": lease}, status_code=kept[0])
    lock, lease, previous = await sessions.windows.takeover(lid, body)
    # Window ids and the lock's id: never the project's path.
    audit.record(request, "ravis.project_lock.taken_over", lock_id=lid,
                 taken_over_from=previous, taken_over_by=window)
    answer = {"lock": lock, "lease_token": lease}
    sessions.kept.keep(where, key, body_sha256(raw), 200, answer)
    return JSONResponse(answer)


@router.post("/{lid}/transfer")
async def transfer(lid: str, request: Request) -> JSONResponse:
    sessions = sessions_of(request)
    key = required_key(request)
    raw, body = await json_body(request)
    where = scope("lock-transfer", lid)
    kept = sessions.kept.find(where, key, body_sha256(raw))
    if kept is not None:
        token = _kept_token(sessions, where, key, lambda: sessions.windows.reissue_transfer(lid))
        return JSONResponse({**kept[1], "transfer_token": token}, status_code=kept[0])
    row = _transfer_authority(sessions, lid, request)
    answer = sessions.windows.transfer(row, body.get("to"))
    sessions.kept.keep(where, key, body_sha256(raw), 200, answer)
    return JSONResponse(answer)


def _transfer_authority(sessions: AgentSessions, lid: str, request: Request) -> dict[str, Any]:
    """The lock a transfer may move: by its lease, or by the holding Codex session's token."""
    lease, token = request.headers.get(LEASE_HEADER), request.headers.get(TOKEN_HEADER)
    if lease:
        return sessions.windows.leased(lid, lease)
    if not token:
        raise refusals.lease_required()
    row = sessions.store.lock(lid)
    if row is None or row["holder_kind"] != CODEX_SESSION:
        raise refusals.session_not_found()
    session = sessions.session_for(str(row["holder_session_id"]), token)
    if session.state == "leftover":
        raise refusals.run_processes_not_confirmed_gone()
    return row
