"""Every Codex task RAVIS keeps: create, look up, list, the tick, and the Codex process ending.

**Creating a task** (design §3.5.3, `CreateSession`), in order:
1. the body's shape, then the folder and its git folder (`roots.py`) — 422;
2. **Codex must be ready**: `signed_in`, with its strict file rules `proven` — else 409
   `CODEX_NOT_READY {state, reason}`, the reason `strict_file_rules_unproven` while calibration or
   the re-test hasn't proven them (owner decision D2). This is what keeps every real task paused
   until calibration passes;
3. the live-session limit (default 3) — 409 `CODEX_SESSION_LIMIT`;
4. the project lock, taken atomically, or with a transfer token (`locks.py`) — 409;
5. the row, then `thread/start` (or `thread/unarchive` and `thread/resume`) — 503 if Codex
   doesn't answer, and then the lock is let go and the row marked failed;
6. `session.state starting` (event 1), the answer rendered — so a window following from the
   create's `last_event_id` still gets `turn.started` — and only then the first turn.

Steps 2 to 5 run under one lock, so two creates can't both slip under the limit.

**The tick** (every 5 s): windows whose panel went quiet stop counting as attached; the
unanswered-request policy (30 minutes with no window attached, 2 hours with one); leftover
processes looked at again; lock heartbeats every 15 s; ended tasks' event logs dropped 30 minutes
after they end; retention hourly.

**The owner Stop's rate limit** (design §3.5.5): 10 calls a minute per application, so the menu
bar (`owner_cli`) and the dashboard (`launcher`) are counted apart.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ravis.agent import calibration_dependent as calibrated
from ravis.agent import refusals, roots
from ravis.agent.cleanup import ProcessCleanup
from ravis.agent.idempotency import KeptResponses
from ravis.agent.locks import ProjectLocks
from ravis.agent.redact import Redactor
from ravis.agent.requests import PathContext
from ravis.agent.session import AgentSession, CodexHost, Context, SessionTimings
from ravis.agent.sites import SiteAllowlist
from ravis.agent.store import AgentStore
from ravis.agent.tokens import new_id, new_token, token_matches, token_sha256
from ravis.codex.lock_rule import own_start
from ravis.codex.process_table import Kill, Snapshot, snapshot
from ravis.codex.refusals import CodexRefusalError
from ravis.config import Settings
from ravis.storage.database import Database

logger = logging.getLogger("ravis")

START_KINDS = frozenset({"brief", "resume"})
OWNER_STOPS_PER_MINUTE = 10


@dataclass(frozen=True)
class AgentTimings:
    tick_seconds: float = 5.0
    heartbeat_seconds: float = 15.0
    retention_seconds: float = 3600.0
    #: How long an ended task's event log is kept for a window reconnecting (design §3.5.4).
    ended_kept_seconds: float = 1800.0
    #: The SSE heartbeat comment's interval.
    stream_heartbeat_seconds: float = 15.0
    session: SessionTimings = SessionTimings()


@dataclass(frozen=True)
class CreateRequest:
    """A `CreateSession` body whose shape has been checked."""

    workspace_root: str
    git_dir: object
    task_id: str
    window_id: str
    window_host: str
    mode: str
    model: str
    branch_name: str | None
    head_commit: str | None
    start: dict[str, Any]
    transfer_token: str | None
    max_steps: int | None


def create_request(body: dict[str, Any]) -> CreateRequest:
    """The create body, or 422 `INVALID_REQUEST_BODY` naming what is wrong."""
    window = _mapping(body.get("window"))
    branch, limits = _mapping(body.get("branch")), _mapping(body.get("limits"))
    start = _start(body.get("start"))
    task_id, mode = body.get("clarvis_task_id"), body.get("mode", "agent")
    if not isinstance(task_id, str) or not task_id:
        raise _invalid("clarvis_task_id must be the task's id.")
    if not isinstance(window.get("id"), str) or not window["id"]:
        raise _invalid("window must carry the window's id and host.")
    if mode not in calibrated.MODES:
        raise _invalid(f"mode must be one of {', '.join(calibrated.MODES)}.")
    max_steps = limits.get("max_steps")
    if max_steps is not None and (not isinstance(max_steps, int) or max_steps < 1):
        raise _invalid("limits.max_steps must be a positive whole number.")
    return CreateRequest(
        workspace_root=str(body.get("workspace_root") or ""), git_dir=body.get("git_dir"),
        task_id=task_id, window_id=window["id"], window_host=str(window.get("host") or ""),
        mode=mode, model=str(body.get("model") or ""),
        branch_name=_text(branch.get("name")), head_commit=_text(branch.get("head_commit")),
        start=start, transfer_token=_text(_mapping(body.get("lock")).get("transfer_token")),
        max_steps=max_steps,
    )


def _start(value: object) -> dict[str, Any]:
    start = _mapping(value)
    kind = start.get("kind")
    if kind not in START_KINDS:
        raise _invalid("start.kind must be brief or resume.")
    if kind == "resume":
        if not isinstance(start.get("thread_id"), str) or not start["thread_id"]:
            raise _invalid("A resume must name the thread to resume.")
        return {"kind": kind, "thread_id": start["thread_id"],
                "text": str(start.get("catch_up_text") or "")}
    return {"kind": kind, "text": str(start.get("text") or "")}


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _invalid(message: str) -> CodexRefusalError:
    return CodexRefusalError("INVALID_REQUEST_BODY", 422, message)


class AgentSessions:
    """The relay's tasks, and what they share."""

    def __init__(
        self,
        codex: CodexHost,
        database: Database,
        settings: Settings,
        *,
        emit: Callable[..., None],
        timings: AgentTimings = AgentTimings(),
        clock: Callable[[], float] = time.monotonic,
        take_snapshot: Snapshot = snapshot,
        kill: Kill = os.kill,
    ) -> None:
        self.settings = settings
        self.timings = timings
        self.clock = clock
        self._codex = codex
        self._emit = emit
        self.store = AgentStore(database)
        self.kept = KeptResponses(self.store, clock)
        self.locks = ProjectLocks(self.store, emit=self._emit_lock, own_start=own_start)
        cleanup = ProcessCleanup(codex.request, timings.session.cleanup,
                                 take_snapshot=take_snapshot, kill=kill)
        denied = roots.denied_paths(settings)
        redactor = Redactor(denied, roots.realpath(Path.home()))
        self.context = Context(
            codex=codex, store=self.store, locks=self.locks, cleanup=cleanup,
            sites=SiteAllowlist(codex.request, codex.profile_name),
            paths=lambda root: PathContext(root, denied, redactor), settings=settings,
            timings=timings.session, clock=clock, emit=emit,
        )
        self._sessions: dict[str, AgentSession] = {}
        self._ended_at: dict[str, float] = {}
        self._create_lock = asyncio.Lock()
        self._owner_stops: dict[str, deque[float]] = {}
        self._tick: asyncio.Task[None] | None = None
        self._last_heartbeat = 0.0
        self._last_retention = 0.0

    def _emit_lock(self, event: str, trace_id: str, data: dict[str, Any]) -> None:
        self._emit(event, trace_id=trace_id, data=data)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Load the tasks a previous RAVIS left, recover them, and start the tick."""
        self.store.enforce_retention()
        self._last_retention = self.clock()
        for row in self.store.live_sessions():
            session = AgentSession(self.context, row, attached_until=self.clock())
            self._sessions[session.id] = session
            await session.recover()
        self._tick = asyncio.create_task(self._tick_loop())

    async def stop(self) -> None:
        if self._tick is not None:
            self._tick.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tick
        for session in self._sessions.values():
            session.shutdown()

    def codex_process_ended(self) -> None:
        for session in self._sessions.values():
            session.codex_ended()

    def usage_changed(self, windows: list[Any], limit_reached: object) -> None:
        """`usage.updated` for each task whose turn is running (design §3.5.4)."""
        for session in self._sessions.values():
            if session.active_turn_id is not None:
                session.emit("usage.updated", windows=windows, limit_reached=limit_reached)

    def settle_pending(self) -> bool:
        """A turn runs or a settle waits: sign-in, sign-out and the re-test must wait too."""
        return any(session.live for session in self._sessions.values())

    # ── Create ───────────────────────────────────────────────────────────────

    async def create(self, application_id: str, body: dict[str, Any]) -> dict[str, Any]:
        wanted = create_request(body)
        workspace = roots.workspace(wanted.workspace_root, wanted.git_dir, self.settings)
        token = new_token()
        async with self._create_lock:
            ready = self._codex.readiness()
            if ready is not None:
                raise refusals.codex_not_ready(*ready)
            live = sum(1 for known in self._sessions.values() if known.live)
            if live >= self.settings.agent_session_limit:
                raise refusals.session_limit()
            row = self._row(application_id, wanted, workspace, token)
            session = AgentSession(self.context, row, attached_until=self.clock())
            self.locks.acquire(session, wanted.transfer_token)
            await self._open(session, wanted)
        session.attach_creator(wanted.window_id, wanted.window_host)
        session.announce_start()
        self._emit("ravis.agent_session.started", trace_id=session.trace_id,
                   data={"session_id": session.id, "mode": session.mode})
        answer = {"session": session.view(), "session_token": token,
                  "events_url": f"/api/v1/agent-sessions/{session.id}/events"}
        session.start_turn(wanted.start["kind"], wanted.start["text"])
        return answer

    async def _open(self, session: AgentSession, wanted: CreateRequest) -> None:
        self.store.insert_session(self._stored(session))
        try:
            await session.open_thread(wanted.start)
        except BaseException:
            self.locks.release(session)
            stamp = self.store.stamp()
            self.store.update_session(session.id, state="failed", ended_at=stamp)
            raise
        self._sessions[session.id] = session

    def _row(self, application_id: str, wanted: CreateRequest, workspace: roots.Workspace,
             token: str) -> dict[str, Any]:
        stamp = self.store.stamp()
        return {
            "id": new_id("as_"), "application_id": application_id,
            "workspace_root": str(workspace.root), "workspace_root_hash": workspace.root_hash,
            "workspace_name": workspace.name,
            "git_dir": str(workspace.git_dir) if workspace.git_dir else None,
            "clarvis_task_id": wanted.task_id, "codex_thread_id": None, "active_turn_id": None,
            "last_turn_id": None, "model": wanted.model or None, "mode": wanted.mode,
            "state": "starting", "token_sha256": token_sha256(token), "trace_id": uuid.uuid4().hex,
            "runtime_sha256": self._codex.running_sha256(),
            "account_fingerprint": self._codex.account_fingerprint(),
            "branch_name": wanted.branch_name, "head_commit_at_start": wanted.head_commit,
            "max_steps": wanted.max_steps, "last_event_id": 0, "created_at": stamp,
            "updated_at": stamp,
        }

    @staticmethod
    def _stored(session: AgentSession) -> dict[str, Any]:
        return {
            "id": session.id, "application_id": session.application_id,
            "workspace_root": str(session.root), "workspace_root_hash": session.root_hash,
            "workspace_name": session.name,
            "git_dir": str(session.git_dir) if session.git_dir else None,
            "clarvis_task_id": session.task_id, "model": session.model, "mode": session.mode,
            "file_rules": "strict", "state": session.state, "token_sha256": session.token_sha256,
            "trace_id": session.trace_id, "runtime_sha256": session.runtime_sha256,
            "account_fingerprint": session.account_fingerprint,
            "branch_name": session.branch_name, "head_commit_at_start": session.head_commit,
            "max_steps": session.max_steps, "last_event_id": 0,
            "created_at": session.created_at, "updated_at": session.updated_at,
        }

    # ── Looking tasks up ─────────────────────────────────────────────────────

    def session_for(self, session_id: str, token: str | None) -> AgentSession:
        """The task this token opens, or 404 — unknown, missing and wrong all look alike."""
        session = self._sessions.get(session_id)
        if session is None or not token_matches(token, session.token_sha256):
            raise refusals.session_not_found()
        return session

    def find(self, session_id: str) -> AgentSession | None:
        return self._sessions.get(session_id)

    def listed(self, raw_root: object) -> dict[str, Any]:
        root = roots.workspace_root(raw_root, self.settings)
        return {"items": [s.list_item() for s in self._sessions.values()
                          if s.root == root and not s.over]}

    def runs(self, *, named: bool) -> list[dict[str, Any]]:
        """`GET /api/v1/codex` → `runs`: live tasks, then Clarvis's own writing runs (R4's rows)."""
        now = datetime.now(UTC)
        entries = [s.run_entry(named=named, now=now) for s in self._sessions.values()]
        runs = [entry for entry in entries if entry is not None]
        for row in self.locks.clarvis_runs():
            entry: dict[str, Any] = {"id": None, "turn_id": None} if named else {}
            entry.update(project=Path(row["workspace_root"]).name, state="clarvis_engine",
                         since=row["acquired_at"], age_minutes=None, waiting_minutes=None,
                         attached_windows=1, paused_reason=None)
            runs.append(entry)
        return runs

    # ── Tokens ───────────────────────────────────────────────────────────────

    def reissue(self, session_id: str, raw_root: object) -> str:
        """A new token for a task's own root, once no window has been attached for 60 s."""
        session = self._sessions.get(session_id)
        if session is None or session.over or not isinstance(raw_root, str):
            raise refusals.session_not_found()
        if roots.realpath(raw_root) != session.root:
            raise refusals.session_not_found()
        if session.attached_recently():
            raise refusals.window_attached()
        token = new_token()
        session.token_sha256 = token_sha256(token)
        self.store.update_session(session.id, token_sha256=session.token_sha256)
        return token

    # ── The owner Stop ───────────────────────────────────────────────────────

    def count_owner_stop(self, application_id: str) -> None:
        now = self.clock()
        calls = self._owner_stops.setdefault(application_id, deque())
        while calls and calls[0] <= now - 60.0:
            calls.popleft()
        if len(calls) >= OWNER_STOPS_PER_MINUTE:
            raise refusals.stop_rate_limited()
        calls.append(now)

    # ── The tick ─────────────────────────────────────────────────────────────

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(self.timings.tick_seconds)
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 — the tick must outlive one bad pass
                logger.exception("agent: the sessions' tick failed")

    async def tick(self) -> None:
        now = self.clock()
        for session in list(self._sessions.values()):
            await self._tick_session(session, now)
        if now - self._last_heartbeat >= self.timings.heartbeat_seconds:
            self._last_heartbeat = now
            self.locks.heartbeat({sid: s.state == "waiting_on_you"
                                  for sid, s in self._sessions.items()})
        if now - self._last_retention >= self.timings.retention_seconds:
            self._last_retention = now
            self.store.enforce_retention()

    async def _tick_session(self, session: AgentSession, now: float) -> None:
        if session.over:
            ended = self._ended_at.setdefault(session.id, now)
            if now - ended >= self.timings.ended_kept_seconds:
                session.events.close_streams()
                del self._sessions[session.id]
                del self._ended_at[session.id]
            return
        session.refresh_attachment()
        session.open_overdue_file_changes()
        if session.unanswered_due():
            async with session.action_lock:
                session.policy_fires()
        await session.recheck_leftovers()
