"""The relay's HTTP edge: `/api/v1/agent-sessions…` (RAVIS.md §15.1.2; design §3.5.3, §3.5.5).

**Two routers, never mixed.** `router` carries `require_agent_client` for every route, GETs
included; `owner_router` carries only `POST /{sid}/owner-stop`, with `require_owner_stop_caller`
(`identity.py`). The owner Stop stops a task and does nothing else: no route on `owner_router`
answers, approves, steers, starts, continues, settles, ends, reads or reissues anything, and
`tests/test_agent_sessions_identity.py` holds both routers to that.

**Each route's order:** the caller (the router's dependency); the task and its token (404); the
`Idempotency-Key` where one is required (428), and a kept answer replayed; the body (422
`INVALID_REQUEST_BODY` for a malformed one); then the action, under the task's action lock for
every mutating route but `presence` (review AM7).

Shapes and codes are the fixtures': `agent-sessions.json`, `event-stream.json`, `owner-stop.json`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ravis.agent import calibration_dependent as calibrated
from ravis.agent import refusals
from ravis.agent.events import HEARTBEAT, RETRY, Replay, Subscriber, frame
from ravis.agent.idempotency import body_sha256, required_key, scope
from ravis.agent.identity import TOKEN_HEADER, require_agent_client, require_owner_stop_caller
from ravis.agent.requests import decision_from, mapping
from ravis.agent.session import AgentSession
from ravis.agent.sessions import AgentSessions
from ravis.api.management import audit
from ravis.codex.lock_file import iso
from ravis.codex.refusals import CodexRefusalError

# The prefix is written out, not named: `tools/knowledge_check.py` reads it from the source.
router = APIRouter(prefix="/api/v1/agent-sessions", tags=["agent-sessions"],
                   dependencies=[Depends(require_agent_client)])
owner_router = APIRouter(prefix="/api/v1/agent-sessions", tags=["agent-sessions"],
                         dependencies=[Depends(require_owner_stop_caller)])

TURN_KINDS = frozenset({"continue", "carry_on", "catch_up"})
INTERRUPT_REASONS = frozenset({"stop", "switch", "scope_change"})
SETTLE_NEXT = frozenset({"idle", "end", "transfer"})
OWNER_SOURCES = frozenset({"menu_bar", "dashboard"})


def _sessions(request: Request) -> AgentSessions:
    return request.app.state.codex_service.agents  # type: ignore[no-any-return]


def _invalid(message: str) -> CodexRefusalError:
    return CodexRefusalError("INVALID_REQUEST_BODY", 422, message)


async def _body(request: Request) -> tuple[bytes, dict[str, Any]]:
    """The raw body and its JSON object; an empty body is `{}`."""
    raw = await request.body()
    if not raw.strip():
        return raw, {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise _invalid("The body must be a JSON object.") from None
    if not isinstance(parsed, dict):
        raise _invalid("The body must be a JSON object.")
    return raw, parsed


def _session(request: Request, sid: str) -> AgentSession:
    return _sessions(request).session_for(sid, request.headers.get(TOKEN_HEADER))


def _text(body: dict[str, Any], name: str) -> str | None:
    value = body.get(name)
    return value if isinstance(value, str) and value else None


# ── Create, list, read ───────────────────────────────────────────────────────


@router.post("")
async def create_session(request: Request) -> JSONResponse:
    sessions = _sessions(request)
    key = required_key(request)
    raw, body = await _body(request)
    window = mapping(body.get("window"))
    where = scope("create", body.get("workspace_root"), window.get("id"))
    kept = sessions.kept.find(where, key, body_sha256(raw))
    if kept is not None:
        return JSONResponse(_with_token(sessions, where, key, kept[1],
                                        kept[1]["session"]["id"], body.get("workspace_root")),
                            status_code=kept[0])
    answer = await sessions.create(request.state.identity.application_id, body)
    sessions.kept.keep(where, key, body_sha256(raw), 201, answer)
    return JSONResponse(answer, status_code=201)


def _with_token(sessions: AgentSessions, where: str, key: str, body: dict[str, Any],
                session_id: str, root: object) -> dict[str, Any]:
    """A replayed answer's token: the one kept in memory, or — after a restart — a reissued one."""
    token = sessions.kept.token(where, key)
    if token is None:
        token = sessions.reissue(session_id, root)
        sessions.kept.remember(where, key, token)
    return {**body, "session_token": token}


@router.get("")
async def list_sessions(request: Request) -> dict[str, Any]:
    return _sessions(request).listed(request.query_params.get("workspace_root"))


@router.get("/{sid}")
async def read_session(sid: str, request: Request) -> dict[str, Any]:
    return _session(request, sid).view()


@router.get("/{sid}/transcript")
async def read_transcript(sid: str, request: Request) -> dict[str, Any]:
    session = _session(request, sid)
    raw = request.query_params.get("limit", "50")
    limit = int(raw) if raw.isdigit() else 50
    return await session.transcript(max(1, min(limit, 200)))


# ── The event stream ─────────────────────────────────────────────────────────


def _cursor(request: Request) -> int | None:
    """`?after=` or `Last-Event-ID`; the two are equivalent (C1's notes)."""
    for value in (request.query_params.get("after"), request.headers.get("last-event-id")):
        if value is not None and value.strip().isdigit():
            return int(value.strip())
    return None


@router.get("/{sid}/events")
async def stream_events(sid: str, request: Request) -> StreamingResponse:
    session = _session(request, sid)
    cursor = _cursor(request)
    subscriber = session.events.subscribe()
    if cursor is None:
        view = session.view()
        first = [frame(view["last_event_id"], "snapshot", {"session_id": session.id, **view})]
        sent = view["last_event_id"]
    else:
        replay = session.events.replay(cursor)
        if not isinstance(replay, Replay):
            session.events.unsubscribe(subscriber)
            raise refusals.event_cursor_expired(replay)
        first = _replayed(session, replay, cursor)
        sent = replay.events[-1].id if replay.events else cursor
    heartbeat = _sessions(request).timings.stream_heartbeat_seconds
    return StreamingResponse(
        _stream(request, session, subscriber, first, sent, heartbeat),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _replayed(session: AgentSession, replay: Replay, cursor: int) -> list[bytes]:
    frames = [event.frame for event in replay.events]
    if replay.deltas_skipped:
        marker = frame(None, "deltas_skipped", {"session_id": session.id, "after": cursor})
        frames.insert(0, marker)
    return frames


async def _stream(
    request: Request, session: AgentSession, subscriber: Subscriber, first: list[bytes],
    sent: int, heartbeat: float,
) -> AsyncIterator[bytes]:
    """`retry:`, the snapshot or the replay, then live frames and heartbeats until it closes."""
    try:
        yield RETRY
        for chunk in first:
            yield chunk
        while True:
            frames = await subscriber.take(heartbeat)
            if not frames:
                if subscriber.closed or await request.is_disconnected():
                    return
                yield HEARTBEAT
                continue
            for event_id, data in frames:
                if event_id > sent:
                    sent = event_id
                    yield data
            if subscriber.closed:
                return
    finally:
        session.events.unsubscribe(subscriber)


# ── Turns, steering, requests ────────────────────────────────────────────────


async def _replay_or(request: Request, session: AgentSession, name: str, *extra: object
                     ) -> tuple[str, str, bytes, dict[str, Any], JSONResponse | None]:
    """The key, scope and body of a keyed route — and the kept answer, when this is a retry."""
    key = required_key(request)
    raw, body = await _body(request)
    where = scope(name, session.id, *extra)
    kept = _sessions(request).kept.find(where, key, body_sha256(raw))
    replayed = JSONResponse(kept[1], status_code=kept[0]) if kept is not None else None
    return key, where, raw, body, replayed


@router.post("/{sid}/turns")
async def start_turn(sid: str, request: Request) -> JSONResponse:
    session = _session(request, sid)
    key, where, raw, body, replayed = await _replay_or(request, session, "turns")
    if replayed is not None:
        return replayed
    kind, text = body.get("kind"), body.get("text", "")
    if kind not in TURN_KINDS or not isinstance(text, str):
        raise _invalid("A turn needs text and a kind: continue, carry_on or catch_up.")
    lock = mapping(body.get("lock"))
    async with session.action_lock:
        answer = await session.turn(kind, text, _text(lock, "transfer_token"))
    _sessions(request).kept.keep(where, key, body_sha256(raw), 202, answer)
    return JSONResponse(answer, status_code=202)


@router.post("/{sid}/steer")
async def steer(sid: str, request: Request) -> JSONResponse:
    session = _session(request, sid)
    key, where, raw, body, replayed = await _replay_or(request, session, "steer")
    if replayed is not None:
        return replayed
    text = body.get("text")
    if not isinstance(text, str):
        raise refusals.empty_steer()
    async with session.action_lock:
        answer = await session.steer(text, _text(body, "expected_turn_id"))
    _sessions(request).kept.keep(where, key, body_sha256(raw), 202, answer)
    return JSONResponse(answer, status_code=202)


@router.post("/{sid}/interrupt")
async def interrupt(sid: str, request: Request) -> JSONResponse:
    session = _session(request, sid)
    _, body = await _body(request)
    if body.get("reason", "stop") not in INTERRUPT_REASONS:
        raise _invalid("reason must be stop, switch or scope_change.")
    async with session.action_lock:
        state = session.stop("window")
    return JSONResponse({"state": state}, status_code=202)


@router.post("/{sid}/requests/{rid}/answer")
async def answer_request(sid: str, rid: str, request: Request) -> JSONResponse:
    session = _session(request, sid)
    # The key names the window (`<rid>:<window_id>`), so another window's retry is never replayed
    # this window's 200: it reaches the resolution check and gets REQUEST_ALREADY_RESOLVED.
    key, where, raw, body, replayed = await _replay_or(request, session, "answer", rid)
    if replayed is not None:
        return replayed
    decision = decision_from(body)
    if decision is None:
        raise _invalid("The body must carry decision: {kind: once, skip, stop or answer}.")
    async with session.action_lock:
        host = session.site_host(rid)
        answer = await session.answer(rid, decision)
    if host is not None:
        # The owner's decision about a site Codex's proxy blocked: the host, who and when; no more.
        audit.record(request, "ravis.agent_session.site_decided", session_id=sid, request_id=rid,
                     host=host, decision=answer["decision_kind"],
                     decided_at=iso(datetime.now(UTC)))
    _sessions(request).kept.keep(where, key, body_sha256(raw), 200, answer)
    return JSONResponse(answer)


# ── Presence, mode, leftovers ────────────────────────────────────────────────


@router.post("/{sid}/presence")
async def presence(sid: str, request: Request) -> Response:
    """The panel's heartbeat. Deliberately not serialised: it changes no task (design §3.5.3)."""
    session = _session(request, sid)
    _, body = await _body(request)
    window, host, connected = body.get("window_id"), body.get("host"), body.get("panel_connected")
    if not isinstance(window, str) or not window or not isinstance(connected, bool):
        raise _invalid("presence needs window_id, host and panel_connected.")
    session.presence(window, str(host or ""), connected)
    return Response(status_code=204)


@router.post("/{sid}/mode")
async def change_mode(sid: str, request: Request) -> dict[str, Any]:
    session = _session(request, sid)
    _, body = await _body(request)
    if body.get("mode") not in calibrated.MODES:
        raise _invalid(f"mode must be one of {', '.join(calibrated.MODES)}.")
    async with session.action_lock:
        return session.set_mode(str(body["mode"]))


@router.post("/{sid}/leftover")
async def stop_leftovers(sid: str, request: Request) -> dict[str, Any]:
    session = _session(request, sid)
    _, body = await _body(request)
    if body.get("action") != "stop_them":
        raise _invalid("action must be stop_them.")
    async with session.action_lock:
        return await session.stop_leftovers()


# ── Settle, cancel, end, tokens ──────────────────────────────────────────────


@router.post("/{sid}/settle-claim")
async def claim_settle(sid: str, request: Request) -> dict[str, Any]:
    session = _session(request, sid)
    _, body = await _body(request)
    window = _text(body, "window_id")
    if window is None:
        raise _invalid("settle-claim needs the claiming window's window_id.")
    async with session.action_lock:
        return session.claim_settle(window)


@router.post("/{sid}/settle")
async def settle(sid: str, request: Request) -> JSONResponse:
    session = _session(request, sid)
    key, where, raw, body, replayed = await _replay_or(request, session, "settle")
    if replayed is not None:
        return replayed
    claim, next_step = _text(body, "claim_id"), body.get("next")
    if claim is None or next_step not in SETTLE_NEXT or body.get("checkpoint_saved") is not True:
        raise _invalid("settle needs claim_id, checkpoint_saved: true and next: idle, end or "
                       "transfer.")
    async with session.action_lock:
        answer = await session.settle(claim, str(next_step))
    _sessions(request).kept.keep(where, key, body_sha256(raw), 200, answer)
    return JSONResponse(answer)


@router.post("/{sid}/cancel")
async def cancel(sid: str, request: Request) -> JSONResponse:
    session = _session(request, sid)
    await _body(request)
    async with session.action_lock:
        answer = await session.cancel()
    return JSONResponse(answer, status_code=202)


@router.delete("/{sid}")
async def end_session(sid: str, request: Request) -> dict[str, Any]:
    session = _session(request, sid)
    async with session.action_lock:
        return await session.delete()


@router.post("/{sid}/reissue-token")
async def reissue_token(sid: str, request: Request) -> JSONResponse:
    """A new token for a window that lost its file: the client credential, no token (§3.5.1)."""
    sessions = _sessions(request)
    key = required_key(request)
    raw, body = await _body(request)
    where = scope("reissue-token", sid)
    kept = sessions.kept.find(where, key, body_sha256(raw))
    if kept is not None:
        return JSONResponse(_with_token(sessions, where, key, kept[1], sid,
                                        body.get("workspace_root")))
    token = sessions.reissue(sid, body.get("workspace_root"))
    sessions.kept.keep(where, key, body_sha256(raw), 200, {"session_token": token})
    audit.record(request, "ravis.agent_session.token_reissued", session_id=sid)
    return JSONResponse({"session_token": token})


# ── The owner Stop: its own router ───────────────────────────────────────────


@owner_router.post("/{sid}/owner-stop")
async def owner_stop(sid: str, request: Request) -> JSONResponse:
    """Stop a task from the menu bar or the dashboard, after the owner confirmed it (§3.5.5)."""
    sessions = _sessions(request)
    application_id = request.state.identity.application_id
    key = required_key(request)
    sessions.count_owner_stop(application_id)
    raw, body = await _body(request)
    where = scope("owner-stop", sid, application_id)
    kept = sessions.kept.find(where, key, body_sha256(raw))
    if kept is not None:
        return JSONResponse(kept[1], status_code=kept[0])
    source = body.get("source")
    confirm = mapping(body.get("confirm"))
    if source not in OWNER_SOURCES:
        raise _invalid("source must be menu_bar or dashboard.")
    session = sessions.find(sid)
    if session is None:
        raise refusals.session_not_found()
    async with session.action_lock:
        _confirmed(session, confirm)
        state = session.stop(str(source))
    audit.record(request, "ravis.agent_session.owner_stopped", session_id=sid, source=source)
    answer = {"state": state}
    sessions.kept.keep(where, key, body_sha256(raw), 202, answer)
    return JSONResponse(answer, status_code=202)


def _confirmed(session: AgentSession, confirm: dict[str, Any]) -> None:
    """The confirmation names this task's folder and its current or last turn, and it runs."""
    turn = session.active_turn_id or session.last_turn_id
    if confirm.get("project") != session.name or confirm.get("turn_id") != turn:
        raise refusals.confirmation_mismatch()
    if session.state not in ("starting", "running", "waiting_on_you", "stopping"):
        raise refusals.nothing_running(session.state)
