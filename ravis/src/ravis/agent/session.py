"""One Codex task: its state, turns, requests, steering, Stop, settle and presence (design §3.5).

**The states** (`agent-sessions.json` → `session_states`):

| state | meaning |
|---|---|
| `starting` | created, or a turn asked for and not yet begun |
| `running` | a turn is active |
| `waiting_on_you` | a turn is active and a request is open |
| `stopping` | a Stop: the interrupt and process clean-up are under way |
| `stopped` | interrupted, processes confirmed gone — **needs settle** |
| `leftover` | processes not confirmed gone; the lock stays held |
| `paused_unanswered` | the unanswered-request policy fired — **needs settle** |
| `paused_for_update` | a new Codex binary while the turn only waited — **needs settle** (R4) |
| `completed_needs_review` | the turn ended, processes confirmed gone — **needs settle** |
| `idle` | settled; the thread is kept and can continue |
| `uncertain` | Codex's process died mid-turn, or RAVIS restarted — **needs settle** |
| `failed`, `ended` | over |

**After every turn a settle state follows** — completed, failed or capped alike (C2a: "the runner
settles only on that") — unless a steer queued during the turn starts its follow-on turn.

**Serialisation** (review AM7, F-A7). Every mutating route but `presence` runs under this task's
one action lock (`routes.py` takes it). Stop sets `stopping` and answers every open request with
its stop response **inside** the lock, before the interrupt is even sent, so an answer arriving at
the same moment finds `SESSION_STOPPING` and is never forwarded to Codex.

**The end sequence** (design §4.5) runs outside the lock, so a slow clean-up never blocks a
window's routes: interrupt the turn (5 s) and wait up to 3 s for Codex to end it, then end this
task's processes and confirm (`cleanup.py`), then emit `turn.completed` with the confirmation, and
the settle state — or `leftover`, naming what still runs.

**Content** passes through memory only: request payloads until answered, and the event log
(`events.py`). The database gets ids, states and kinds (`store.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ravis.agent import calibration_dependent as calibrated
from ravis.agent import refusals
from ravis.agent.attribution import SampledTask
from ravis.agent.cleanup import (
    CleanupTimings,
    Confirmation,
    ProcessCleanup,
    leftover_view,
    lock_file_leftover,
)
from ravis.agent.events import EventLog
from ravis.agent.locks import ProjectLocks
from ravis.agent.redact import command_hidden
from ravis.agent.requests import (
    KINDS,
    PathContext,
    is_secret_question,
    mapping,
    payload_and_decisions,
)
from ravis.agent.sites import DECISIONS as SITE_DECISIONS
from ravis.agent.sites import SiteAllowlist, blocked_hosts, protocol_for, site_payload
from ravis.agent.store import AgentStore
from ravis.agent.tokens import new_id
from ravis.agent.translate import (
    RUNTIME_CRASHED,
    STEP_TYPES,
    normalise_item,
    plan_steps,
    step_cap_error,
    turn_error,
    turn_start_error,
)
from ravis.codex.lock_file import iso
from ravis.codex.process_table import ProcessRow
from ravis.codex.refusals import CodexRefusalError
from ravis.codex.routing import ELICITATION_DECLINED, Inbox, InboxItem
from ravis.codex.rpc import (
    METHOD_NOT_FOUND,
    CodexRpcError,
    CodexTimeoutError,
    CodexUnavailableError,
)
from ravis.config import Settings

logger = logging.getLogger("ravis")

TURN_STATES = frozenset({"starting", "running", "waiting_on_you"})
SETTLE_STATES = frozenset({
    "stopped", "completed_needs_review", "paused_unanswered", "paused_for_update", "uncertain",
})
OVER = frozenset({"ended", "failed"})
#: How each way a turn can be ended lands (`None` is a turn that ended on its own).
FINAL_STATE = {
    "stop": "stopped", "policy_timeout": "paused_unanswered", "update": "paused_for_update",
    "crash": "uncertain", "turn_start_timeout": "uncertain",
}
#: `GET /api/v1/codex` → `runs[].state` (`codex-state.json` → `run_states`) for each session state.
RUN_STATES = {
    "starting": "running", "running": "running", "stopping": "running",
    "waiting_on_you": "waiting_on_you", "stopped": "completed_needs_review",
    "completed_needs_review": "completed_needs_review", "paused_unanswered": "paused_unanswered",
    "paused_for_update": "paused_for_update", "uncertain": "uncertain", "leftover": "leftover",
}


class CodexHost(Protocol):
    """What a task needs of RAVIS's one Codex process (`codex/service.py` provides it)."""

    async def request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float
    ) -> Any: ...
    def hold(self, thread_id: str, inbox: Inbox) -> None: ...
    def release(self, thread_id: str) -> None: ...
    def readiness(self) -> tuple[str, str] | None: ...
    def profile_name(self) -> str | None: ...
    def account_fingerprint(self) -> str | None: ...
    def running_sha256(self) -> str | None: ...
    def app_server_pid(self) -> int | None: ...
    def update_pending(self) -> bool: ...


@dataclass(frozen=True)
class SessionTimings:
    """Design §4.8's timeouts and §3.5.4's attachment; tests shorten them."""

    thread_start_seconds: float = 30.0
    turn_start_seconds: float = 30.0
    steer_seconds: float = 10.0
    interrupt_seconds: float = 5.0
    interrupt_wait_seconds: float = 3.0
    archive_seconds: float = 10.0
    turns_list_seconds: float = 15.0
    #: A window counts as attached while its last `panel_connected: true` is younger than this.
    attached_seconds: float = 45.0
    #: `reissue-token` waits until no window has been attached for this long.
    reissue_after_seconds: float = 60.0
    claim_seconds: float = 300.0
    #: How long a file-change approval waits for its item to say what it writes (K12).
    file_item_wait_seconds: float = 2.0
    cleanup: CleanupTimings = CleanupTimings()


@dataclass(frozen=True)
class Context:
    """What every task shares, built once by `AgentSessions`."""

    codex: CodexHost
    store: AgentStore
    locks: ProjectLocks
    cleanup: ProcessCleanup
    sites: SiteAllowlist
    paths: Callable[[Path], PathContext]
    settings: Settings
    timings: SessionTimings
    clock: Callable[[], float]
    #: The MEP publisher's `emit(event, *, trace_id, data)`.
    emit: Callable[..., None]


@dataclass
class Window:
    host: str
    since: str
    last_seen: float


@dataclass
class OpenRequest:
    id: str
    kind: str
    view: dict[str, Any]
    codex_params: dict[str, Any]
    codex_id: Any
    connection: Any
    opened: float


@dataclass
class Claim:
    id: str
    window: str
    expires: float
    expires_at: str


@dataclass
class Processes:
    attributed: int = 0
    confirmed_gone: bool = False
    leftover: tuple[ProcessRow, ...] = ()


@dataclass
class _Turn:
    """What the task knows of its current turn."""

    kind: str = "brief"
    started_at: datetime | None = None
    #: Set when Codex's `turn/completed` arrived (or Codex's process is gone).
    done: asyncio.Event = field(default_factory=asyncio.Event)
    began: asyncio.Event = field(default_factory=asyncio.Event)
    status: str | None = None
    error: Any = None
    steps: int = 0
    #: Why RAVIS ended it, or None while it runs on its own ("completed" once it ended itself).
    end_reason: str | None = None


class AgentSession:
    """One task. Built from its database row; everything else lives in memory."""

    def __init__(self, context: Context, row: dict[str, Any], *, attached_until: float) -> None:
        self._context = context
        self.id: str = row["id"]
        self.application_id: str = row["application_id"]
        self.root = Path(row["workspace_root"])
        self.root_hash: str = row["workspace_root_hash"]
        self.name: str = row["workspace_name"]
        self.git_dir = Path(row["git_dir"]) if row["git_dir"] else None
        self.task_id: str = row["clarvis_task_id"]
        self.trace_id: str = row["trace_id"]
        self.token_sha256: str = row["token_sha256"]
        self.state: str = row["state"]
        self.mode: str = row["mode"]
        self.model: str | None = row["model"]
        self.thread_id: str | None = row["codex_thread_id"]
        self.active_turn_id: str | None = row["active_turn_id"]
        self.last_turn_id: str | None = row["last_turn_id"]
        self.runtime_sha256: str | None = row["runtime_sha256"]
        self.account_fingerprint: str | None = row["account_fingerprint"]
        self.branch_name: str | None = row["branch_name"]
        self.head_commit: str | None = row["head_commit_at_start"]
        self.max_steps: int | None = row["max_steps"]
        self.created_at: str = row["created_at"]
        self.updated_at: str = row["updated_at"]
        self.events = EventLog(self.id, after=int(row["last_event_id"]), reserve=self._reserve)
        self.action_lock = asyncio.Lock()
        self.inbox = Inbox()
        self.thread_loaded = False
        self.stopped_by: str | None = None
        self.stop_requested = False
        self.end_after_settle = False
        self.queued: list[str] = []
        self.windows: dict[str, Window] = {}
        self.last_attached = attached_until
        self.claim: Claim | None = None
        self.processes = Processes()
        self._after_leftover = "completed_needs_review"
        self._turn = _Turn()
        self._open: dict[str, OpenRequest] = {}
        #: File-change approvals waiting for their item (calibration K12), by item id.
        self._awaiting: dict[str, OpenRequest] = {}
        #: Sites Codex's proxy blocked in this task: every host seen, and those still to ask.
        self._sites_seen: set[str] = set()
        self._sites_waiting: list[tuple[str, str | None, Any, Any]] = []
        self._resolved: dict[str, str] = {}
        self._file_changes: dict[str, list[dict[str, Any]]] = {}
        self._hidden_output: dict[str, bool] = {}
        self._announced: tuple[str, ...] = ()
        self._tasks: set[asyncio.Task[None]] = set()
        self._consumer: asyncio.Task[None] | None = None
        self._paths = context.paths(self.root)

    # ── Small facts ──────────────────────────────────────────────────────────

    @property
    def needs_settle(self) -> bool:
        return self.state in SETTLE_STATES

    @property
    def over(self) -> bool:
        return self.state in OVER

    @property
    def live(self) -> bool:
        """Counted against the live-session limit: working, or waiting to be settled."""
        return self.state not in OVER and self.state != "idle"

    def _reserve(self, through: int) -> None:
        self._context.store.update_session(self.id, last_event_id=through)

    def _task(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("agent: session %s's background work failed", self.id,
                         exc_info=task.exception())

    def emit(self, name: str, **fields: Any) -> None:
        self.events.append(name, fields)

    def _meta(self, event: str, **data: Any) -> None:
        self._context.emit(event, trace_id=self.trace_id, data={"session_id": self.id, **data})

    # ── Views ────────────────────────────────────────────────────────────────

    def view(self) -> dict[str, Any]:
        """`SessionView` (`agent-sessions.json`), for token holders only."""
        claim = self.claim if self.claim and self.claim.expires > self._context.clock() else None
        return {
            "id": self.id,
            "state": self.state,
            "workspace": {"root": str(self.root), "name": self.name},
            "clarvis_task_id": self.task_id,
            "mode": self.mode,
            "file_rules": "strict",
            "codex": {
                "thread_id": self.thread_id, "active_turn_id": self.active_turn_id,
                "model": self.model, "runtime_sha256": self.runtime_sha256,
                "account_fingerprint": self.account_fingerprint,
            },
            "branch": {"name": self.branch_name, "head_commit_at_start": self.head_commit},
            "pending_requests": [request.view for request in self._open.values()],
            "queued_feedback": len(self.queued),
            "attached_windows": self.attached_windows(),
            "processes": self._processes_view(),
            "lock": self._context.locks.state_for(self.id),
            "settle": {"needed": self.needs_settle, "claimed_by": claim.window if claim else None},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_event_id": self.events.last_id,
        }

    def _processes_view(self) -> dict[str, Any]:
        processes = self.processes
        return {"attributed": processes.attributed, "confirmed_gone": processes.confirmed_gone,
                "leftover": leftover_view(processes.leftover)}

    def list_item(self) -> dict[str, Any]:
        """One entry of `GET /api/v1/agent-sessions`: no token, no payload, windows counted."""
        return {
            "id": self.id, "state": self.state, "clarvis_task_id": self.task_id,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "waiting_on_you": self.state == "waiting_on_you",
            "attached_windows": len(self.attached_windows()),
        }

    def run_entry(self, *, named: bool, now: datetime) -> dict[str, Any] | None:
        """This task in `GET /api/v1/codex` → `runs`: ids and the turn only for named callers."""
        if self.state not in RUN_STATES:
            return None
        created = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        waiting = min((request.opened for request in self._open.values()), default=None)
        entry: dict[str, Any] = {}
        if named:
            entry.update(id=self.id, turn_id=self.active_turn_id or self.last_turn_id)
        entry.update(
            project=self.name,
            state=RUN_STATES[self.state],
            since=self.created_at,
            age_minutes=int((now - created).total_seconds() // 60),
            waiting_minutes=(
                None if waiting is None else int((self._context.clock() - waiting) // 60)
            ),
            attached_windows=len(self.attached_windows()),
            paused_reason=self.state if self.state in SETTLE_STATES or self.state == "leftover"
            else None,
        )
        return entry

    # ── Presence (not serialised: design §3.5.3) ─────────────────────────────

    def presence(self, window_id: str, host: str, connected: bool) -> None:
        now = self._context.clock()
        if connected:
            window = self.windows.get(window_id)
            if window is None:
                self.windows[window_id] = Window(host, self._context.store.stamp(), now)
            else:
                window.host, window.last_seen = host, now
        else:
            self.windows.pop(window_id, None)
        self.refresh_attachment()

    def attach_creator(self, window_id: str, host: str) -> None:
        """The window that created the task is attached from the start, with no event of its own.

        The create's answer already lists it, and its `last_event_id` must be the task's first
        event (`agent-sessions.json`: 1), so a window following from there misses nothing.
        """
        now = self._context.clock()
        self.windows[window_id] = Window(host, self._context.store.stamp(), now)
        self._announced, self.last_attached = (window_id,), now

    def refresh_attachment(self) -> None:
        """Forget windows whose panel went quiet; announce the attached set when it changes."""
        now = self._context.clock()
        limit = self._context.timings.attached_seconds
        for window_id in [key for key, w in self.windows.items() if now - w.last_seen >= limit]:
            del self.windows[window_id]
        if self.windows:
            self.last_attached = now
        current = tuple(sorted(self.windows))
        if current != self._announced:
            self._announced = current
            self.emit("attached", windows=self.attached_windows())

    def attached_windows(self) -> list[dict[str, Any]]:
        return [{"id": key, "host": w.host, "since": w.since} for key, w in self.windows.items()]

    def attached_recently(self) -> bool:
        return (self._context.clock() - self.last_attached
                < self._context.timings.reissue_after_seconds)

    # ── State ────────────────────────────────────────────────────────────────

    def set_state(self, state: str, *, reason: str | None = None) -> None:
        if state == self.state:
            return
        previous, self.state = self.state, state
        self.updated_at = self._context.store.stamp()
        self._context.store.update_session(
            self.id, state=state, active_turn_id=self.active_turn_id,
            last_turn_id=self.last_turn_id, updated_at=self.updated_at,
            ended_at=self.updated_at if state in OVER else None,
        )
        self._announce_state(reason)
        owner = self.stopped_by in ("menu_bar", "dashboard") and state == "stopping"
        extra = {"reason_code": "owner_stop"} if owner else {}
        self._meta("ravis.agent_session.state_changed", **{"from": previous, "to": state}, **extra)

    def _announce_state(self, reason: str | None = None) -> None:
        data: dict[str, Any] = {"state": self.state}
        if reason is not None:
            data["reason"] = reason
        if self.stopped_by is not None and self.state in ("stopping", "stopped", "leftover"):
            data["stopped_by"] = self.stopped_by
        data["processes"] = {"confirmed_gone": self.processes.confirmed_gone,
                             "leftover": leftover_view(self.processes.leftover)}
        self.emit("session.state", **data)

    def announce_start(self) -> None:
        """The first event of a new task: `session.state starting`, before the create answers."""
        self._announce_state()

    # ── The thread ───────────────────────────────────────────────────────────

    async def open_thread(self, start: dict[str, Any]) -> None:
        """`thread/start` for a brief; `thread/unarchive` then `thread/resume` for a resume."""
        codex, timings = self._context.codex, self._context.timings
        profile = codex.profile_name() or ""
        try:
            if start["kind"] == "resume":
                with contextlib.suppress(CodexRpcError):
                    await codex.request("thread/unarchive", {"threadId": start["thread_id"]},
                                        timeout=timings.thread_start_seconds)
                params = calibrated.thread_resume_params(
                    start["thread_id"], self.root, self.mode, profile)
                result = await codex.request("thread/resume", params,
                                             timeout=timings.thread_start_seconds)
            else:
                with contextlib.suppress(OSError):
                    calibrated.tmp_folder(self.root, self.id).mkdir(0o700, parents=True,
                                                                     exist_ok=True)
                params = calibrated.thread_start_params(
                    self.root, self.mode, profile, self.model or "", self.id)
                result = await codex.request("thread/start", params,
                                             timeout=timings.thread_start_seconds)
        except (CodexRpcError, CodexUnavailableError) as failure:
            raise refusals.runtime_unavailable(f"Codex didn't start: {failure}") from None
        self._adopt_thread(result)

    def _adopt_thread(self, result: object) -> None:
        answer = result if isinstance(result, dict) else {}
        thread = mapping(answer.get("thread"))
        if not isinstance(thread.get("id"), str):
            raise refusals.runtime_unavailable("Codex didn't start: its thread had no id.")
        self.thread_id = thread["id"]
        if isinstance(answer.get("model"), str):
            self.model = answer["model"]
        self._context.store.update_session(
            self.id, codex_thread_id=self.thread_id, model=self.model
        )
        self._remember_thread()
        self._hold_thread()

    def _remember_thread(self) -> None:
        """The thread and when it was last used, kept past the task's records (90-day sweep)."""
        if self.thread_id is not None:
            self._context.store.remember_thread(
                self.thread_id, str(self.root), str(self.git_dir) if self.git_dir else None)

    def _hold_thread(self) -> None:
        if self.thread_id is None:
            return
        self.inbox = Inbox()
        self._context.codex.hold(self.thread_id, self.inbox)
        self.thread_loaded = True
        if self._consumer is not None:
            self._consumer.cancel()
        self._consumer = asyncio.create_task(self._consume())

    async def _resume_thread(self) -> None:
        """After Codex's process restarted, or RAVIS did: `thread/resume` in the new process."""
        timings = self._context.timings
        params = calibrated.thread_resume_params(
            str(self.thread_id), self.root, self.mode, self._context.codex.profile_name() or "")
        try:
            await self._context.codex.request("thread/resume", params,
                                              timeout=timings.thread_start_seconds)
        except (CodexRpcError, CodexUnavailableError) as failure:
            raise refusals.runtime_unavailable(
                f"Codex couldn't resume the task: {failure}"
            ) from None
        self._hold_thread()

    # ── Turns ────────────────────────────────────────────────────────────────

    async def turn(self, kind: str, text: str, transfer_token: str | None) -> dict[str, Any]:
        """`POST …/turns`, under the action lock: the checks in the contract's order, then start."""
        self._refuse_if_over()
        if self.state == "stopping" or (self.stop_requested and self.state in TURN_STATES):
            raise refusals.session_stopping()
        if self.state in TURN_STATES:
            raise refusals.turn_active()
        if self.needs_settle or self.state == "leftover":
            raise refusals.settle_first()
        ready = self._context.codex.readiness()
        if ready is not None:
            raise refusals.codex_not_ready(*ready)
        self._context.locks.take_for_turn(self, transfer_token)
        if not self.thread_loaded:
            await self._resume_thread()
        self.start_turn(kind, text)
        return {"turn": {"state": "starting"}}

    def _refuse_if_over(self) -> None:
        if self.over:
            raise refusals.session_not_found()

    def start_turn(self, kind: str, text: str) -> None:
        """Begin a turn: queued feedback goes in front of the text, and is delivered in it."""
        queued, self.queued = self.queued, []
        for feedback in queued:
            self.emit("feedback", text=feedback, how="delivered_in_turn")
        self._turn = _Turn(kind=kind, started_at=datetime.now(UTC))
        self._remember_thread()
        self.stop_requested, self.stopped_by = False, None
        self.processes = Processes()
        self.set_state("starting")
        full = "\n\n".join(part for part in (*queued, text) if part)
        self._task(self._send_turn_start(full))

    async def _send_turn_start(self, text: str) -> None:
        codex, timings = self._context.codex, self._context.timings
        params = calibrated.turn_start_params(str(self.thread_id), self.root, self.mode, text)
        try:
            result = await codex.request("turn/start", params, timeout=timings.turn_start_seconds)
        except CodexTimeoutError:
            self._end_turn_as("turn_start_timeout", "turn_ended")
            return
        except (CodexRpcError, CodexUnavailableError) as failure:
            self._turn.end_reason = "completed"
            self._turn.status, self._turn.error = "failed", turn_start_error(str(failure))
            await self._conclude_turn()
            return
        turn = result.get("turn") if isinstance(result, dict) else None
        if isinstance(turn, dict) and isinstance(turn.get("id"), str):
            self._turn_began(turn["id"])

    def _turn_began(self, turn_id: str) -> None:
        if self.active_turn_id == turn_id or self._turn.began.is_set():
            return
        self.active_turn_id = self.last_turn_id = turn_id
        self._turn.began.set()
        self._context.store.insert_turn(self.id, turn_id, self._turn.kind)
        self.emit("turn.started", turn_id=turn_id, kind=self._turn.kind)
        if self.state == "starting":
            self.set_state("running")
        else:
            self._context.store.update_session(self.id, active_turn_id=turn_id,
                                               last_turn_id=turn_id)

    # ── Codex's messages for this thread ─────────────────────────────────────

    async def _consume(self) -> None:
        while True:
            item = await self.inbox.get()
            try:
                await self._handle(item)
            except Exception:  # noqa: BLE001 — one bad message must not stop the task's stream
                logger.exception("agent: session %s couldn't handle %s", self.id, item.method)

    async def _handle(self, item: InboxItem) -> None:
        if item.request_id is not None:
            self._codex_request(item)
            return
        params = item.params
        if item.method == "turn/completed":
            await self._turn_completed(params)
            return
        handler = self._NOTIFICATIONS.get(item.method)
        if handler is not None:
            handler(self, params)

    def _turn_started(self, params: dict[str, Any]) -> None:
        turn = mapping(params.get("turn"))
        if isinstance(turn.get("id"), str):
            self._turn_began(turn["id"])

    def _item_started(self, params: dict[str, Any]) -> None:
        item = mapping(params.get("item"))
        if item.get("type") == "fileChange":
            self._file_changes[str(item.get("id"))] = list(item.get("changes") or [])
            waiting = self._awaiting.pop(str(item.get("id")), None)
            if waiting is not None:
                self._open_request(waiting)
        if item.get("type") == "commandExecution":
            self._hidden_output[str(item.get("id"))] = command_hidden(self._paths.redactor, item)
        relayed = normalise_item(item, self._paths, completed=False)
        if relayed is not None:
            self.emit("item.started", turn_id=params.get("turnId"), item=relayed)

    def _item_completed(self, params: dict[str, Any]) -> None:
        item = mapping(params.get("item"))
        relayed = normalise_item(item, self._paths, completed=True)
        if relayed is None:
            return
        self.emit("item.completed", turn_id=params.get("turnId"), item=relayed)
        if item.get("type") == "commandExecution":
            self._site_blocks(params.get("turnId"), item)
        if item.get("type") not in STEP_TYPES or self._turn.end_reason is not None:
            return
        self._turn.steps += 1
        if self.max_steps and self._turn.steps >= self.max_steps:
            # Enforced here, so the cap holds while no window is attached (design §5.5).
            self._end_turn_as("step_cap", "turn_ended")

    def _agent_delta(self, params: dict[str, Any]) -> None:
        self.emit("agent.delta", turn_id=params.get("turnId"), item_id=params.get("itemId"),
                  text=params.get("delta") or "")

    def _output_delta(self, params: dict[str, Any]) -> None:
        hidden = self._hidden_output.get(str(params.get("itemId")), False)
        text = self._paths.redactor.output(params.get("delta"), hidden=hidden)
        self.emit("command.output", turn_id=params.get("turnId"), item_id=params.get("itemId"),
                  text=text)

    def _plan_updated(self, params: dict[str, Any]) -> None:
        self.emit("plan.updated", turn_id=params.get("turnId"), steps=plan_steps(params))

    def _model_rerouted(self, params: dict[str, Any]) -> None:
        self.emit("model.rerouted", **{"from": params.get("fromModel"), "to": params.get("toModel"),
                                       "reason": params.get("reason")})

    def _retrying(self, params: dict[str, Any]) -> None:
        if params.get("willRetry") is True:
            error = turn_error(params.get("error")) or {"message": "Codex hit a problem."}
            self.emit("warning", message=f"{error['message']} Codex is trying again.")

    def _request_resolved_by_codex(self, params: dict[str, Any]) -> None:
        """Codex let go of a request itself — its turn ended — before any window answered it."""
        for request in list(self._open.values()):
            if request.codex_id == params.get("requestId"):
                self._resolve(request, "turn_ended", None)

    def _site_blocks(self, turn_id: Any, item: dict[str, Any]) -> None:
        """A command Codex's proxy blocked names a site: ask the owner about it (`sites.py`)."""
        for host in blocked_hosts(item.get("aggregatedOutput")):
            if host in self._sites_seen:
                continue
            self._sites_seen.add(host)
            protocol = protocol_for(host, item.get("command"))
            self.emit("site.blocked", turn_id=turn_id, item_id=item.get("id"), host=host,
                      protocol=protocol)
            self._sites_waiting.append((host, protocol, turn_id, item.get("id")))
        self._open_next_site()

    def _open_next_site(self) -> None:
        """One site ask open at a time; the next opens once the owner decided the last."""
        if not self._sites_waiting or any(r.kind == "site" for r in self._open.values()):
            return
        host, protocol, turn_id, item_id = self._sites_waiting.pop(0)
        request_id = new_id("rq_")
        store = self._context.store
        store.insert_request(request_id, self.id, turn_id, "", "site", host=host)
        view = {
            "id": request_id, "kind": "site", "turn_id": turn_id, "item_id": item_id,
            "opened_at": store.stamp(), "payload": site_payload(host, protocol),
            "allowed_decisions": list(SITE_DECISIONS),
        }
        self._open[request_id] = OpenRequest(request_id, "site", view, {"host": host}, None, None,
                                             self._context.clock())
        self.emit("request.opened", request=view)
        self._meta("ravis.agent_session.request_opened", request_id=request_id, kind="site")

    async def _decide_site(self, request: OpenRequest, word: str) -> dict[str, Any]:
        """The owner's decision about a blocked site: added to Codex's list now, or kept blocked."""
        if word not in SITE_DECISIONS:
            raise refusals.decision_not_allowed(list(SITE_DECISIONS))
        host = str(request.codex_params["host"])
        if word == "allow_site":
            await self._context.sites.add(host)
            # So Clarvis can have Codex retry the step the proxy blocked.
            self.emit("site.allowed", request_id=request.id, host=host)
        self._resolve(request, "window", word)
        self._open_next_site()
        return {"resolved": True, "decision_kind": word}

    def site_host(self, request_id: str) -> str | None:
        """The host an open site ask names, for the route's audit; None for any other request."""
        request = self._open.get(request_id)
        if request is None or request.kind != "site":
            return None
        return str(request.codex_params["host"])

    def _elicitation_declined(self, _params: dict[str, Any]) -> None:
        """An MCP server asked for input: RAVIS's router declined it (review AL2); say so."""
        request_id = new_id("rq_")
        store = self._context.store
        store.insert_request(request_id, self.id, self.active_turn_id, "", "elicitation")
        store.resolve_request(request_id, "policy_elicitation", "stop")
        self._resolved[request_id] = "policy_elicitation"
        self.emit("request.resolved", request_id=request_id, by="policy_elicitation",
                  decision_kind="stop")

    _NOTIFICATIONS: dict[str, Callable[[AgentSession, dict[str, Any]], None]] = {
        "turn/started": _turn_started,
        "item/started": _item_started,
        "item/completed": _item_completed,
        "item/agentMessage/delta": _agent_delta,
        "item/commandExecution/outputDelta": _output_delta,
        "turn/plan/updated": _plan_updated,
        "model/rerouted": _model_rerouted,
        "error": _retrying,
        "serverRequest/resolved": _request_resolved_by_codex,
        ELICITATION_DECLINED: _elicitation_declined,
    }

    # ── Codex's requests: approvals and questions ────────────────────────────

    def _codex_request(self, item: InboxItem) -> None:
        kind, codex_id, connection = KINDS.get(item.method), item.request_id, item.connection
        if codex_id is None or connection is None:
            return
        if kind is None:
            connection.refuse(codex_id, METHOD_NOT_FOUND,
                              f"RAVIS does not answer {item.method} for a task")
            return
        if self.stop_requested or self.state not in TURN_STATES:
            connection.respond(codex_id, calibrated.STOP_RESPONSES[kind])
            return
        request_id = new_id("rq_")
        store = self._context.store
        store.insert_request(request_id, self.id, item.params.get("turnId"), str(codex_id), kind)
        if kind == "question" and is_secret_question(item.params):
            # Never offered to a window (design §3.5.2): RAVIS answers it empty itself.
            connection.respond(codex_id, calibrated.STOP_RESPONSES["question"])
            store.resolve_request(request_id, "policy_secret", "stop")
            self._resolved[request_id] = "policy_secret"
            self.emit("request.resolved", request_id=request_id, by="policy_secret",
                      decision_kind="stop")
            return
        request = OpenRequest(request_id, kind, {}, item.params, codex_id, connection,
                              self._context.clock())
        item_id = str(item.params.get("itemId"))
        if kind == "fileChange" and item_id not in self._file_changes:
            # Calibration K12: events can arrive out of order. A file change is offered once its
            # item says what it would write — or, if it never does, offered only to decline.
            self._awaiting[item_id] = request
            return
        self._open_request(request)

    def _open_request(self, request: OpenRequest) -> None:
        """Offer a request to the windows, with the decisions RAVIS allows for it."""
        params = request.codex_params
        changes = self._file_changes.get(str(params.get("itemId")), [])
        payload, allowed = payload_and_decisions(request.kind, params, self._paths, changes)
        request.view = {
            "id": request.id, "kind": request.kind, "turn_id": params.get("turnId"),
            "item_id": params.get("itemId"), "opened_at": self._context.store.stamp(),
            "payload": payload, "allowed_decisions": allowed,
        }
        self._open[request.id] = request
        self.emit("request.opened", request=request.view)
        self._meta("ravis.agent_session.request_opened", request_id=request.id, kind=request.kind)
        if self.state == "running":
            self.set_state("waiting_on_you")

    def open_overdue_file_changes(self) -> None:
        """A file change whose item never said what it writes is offered after a moment (K12)."""
        now = self._context.clock()
        for item_id, request in list(self._awaiting.items()):
            if now - request.opened >= self._context.timings.file_item_wait_seconds:
                del self._awaiting[item_id]
                self._open_request(request)

    async def answer(self, request_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        """`POST …/requests/{rid}/answer`, under the action lock."""
        self._refuse_if_over()
        request = self._open.get(request_id)
        if request is None and request_id not in self._resolved:
            row = self._context.store.request(request_id)
            if row is None or row["session_id"] != self.id:
                raise refusals.request_not_found()
            self._resolved[request_id] = row["resolved_by"] or "turn_ended"
        if request is not None and request.kind == "site":
            # Not Codex's request: the command already failed, so no Stop can make it late.
            return await self._decide_site(request, decision["kind"])
        # A late answer never starts a step: Stop, the policy or the cap got there first.
        if self.stop_requested or self.state == "stopping":
            raise refusals.session_stopping()
        if request is None:
            raise refusals.request_already_resolved(self._resolved[request_id])
        allowed = request.view["allowed_decisions"]
        if decision["kind"] not in allowed:
            raise refusals.decision_not_allowed(allowed)
        answer = calibrated.codex_answer(request.kind, decision, request.codex_params)
        request.connection.respond(request.codex_id, answer)
        self._resolve(request, "window", decision["kind"])
        return {"resolved": True, "decision_kind": decision["kind"]}

    def _resolve(self, request: OpenRequest, by: str, decision_kind: str | None) -> None:
        self._open.pop(request.id, None)
        self._resolved[request.id] = by
        self._context.store.resolve_request(request.id, by, decision_kind or "none")
        self.emit("request.resolved", request_id=request.id, by=by, decision_kind=decision_kind)
        self._meta("ravis.agent_session.request_resolved", request_id=request.id, by=by,
                   decision_kind=decision_kind)
        if self.state == "waiting_on_you" and not any(
            open_request.kind != "site" for open_request in self._open.values()
        ):
            self.set_state("running")

    def _resolve_all(self, by: str) -> None:
        """Every open request answered with its stop response — before any interrupt is sent.

        Also when Codex ended the turn itself: calibration K7 found an interrupted turn leaves its
        open request unresolved, so nothing but this answers it, and no late accept can follow.
        """
        waiting, self._awaiting = list(self._awaiting.values()), {}
        # Site asks aren't Codex's requests and outlive the turn: the owner still decides them.
        codex_asks = [request for request in self._open.values() if request.kind != "site"]
        for request in [*waiting, *codex_asks]:
            request.connection.respond(request.codex_id, calibrated.STOP_RESPONSES[request.kind])
            self._resolve(request, by, "stop")

    # ── Steering ─────────────────────────────────────────────────────────────

    async def steer(self, text: str, expected_turn_id: str | None) -> dict[str, Any]:
        """`POST …/steer`, under the action lock (design §5.4)."""
        self._refuse_if_over()
        if not text.strip():
            raise refusals.empty_steer()
        if self.stop_requested or self.state == "stopping":
            raise refusals.session_stopping()
        active = self.active_turn_id if self.state in TURN_STATES else None
        if active is None and self.state not in TURN_STATES:
            # No turn to steer into: the text waits for the next one, which needs the lock (F-A1).
            self._context.locks.take_for_turn(self, None)
        elif active is not None and expected_turn_id in (None, active) and await self._steered(
            active, text
        ):
            self.emit("feedback", text=text, how="steered")
            return {"delivered": "steered"}
        self.queued.append(text)
        self.emit("feedback", text=text, how="queued")
        return {"delivered": "queued"}

    async def _steered(self, turn_id: str, text: str) -> bool:
        params = {"threadId": self.thread_id, "input": [{"type": "text", "text": text}],
                  "expectedTurnId": turn_id}
        try:
            await self._context.codex.request("turn/steer", params,
                                              timeout=self._context.timings.steer_seconds)
        except (CodexRpcError, CodexUnavailableError):
            return False
        return True

    def set_mode(self, mode: str) -> dict[str, Any]:
        self._refuse_if_over()
        self.mode = mode
        self._context.store.update_session(self.id, mode=mode)
        return {"mode": mode, "applies_from": "next_turn"}

    # ── Ending a turn: Stop, the step cap, the policy, a crash ───────────────

    def stop(self, by: str) -> str:
        """A Stop from a window (interrupt, cancel) or the owner (owner-stop), under the lock."""
        if self.state == "stopping" or self.state not in TURN_STATES:
            return self.state
        if self._turn.end_reason is not None:
            return self.state
        self.stopped_by = by
        self.set_state("stopping")
        self._end_turn_as("stop", "stop" if by == "window" else "owner_stop")
        return "stopping"

    def policy_fires(self) -> None:
        """The unanswered-request policy (design §4.8): decline, end the turn, keep the thread."""
        self._end_turn_as("policy_timeout", "policy_timeout")

    def pause_for_update(self) -> None:
        """A new Codex build, and this turn only waits for an answer: paused like the policy (AL5).

        Its open requests get their stop responses, the turn ends, the thread is kept, and the task
        reads `paused_for_update` until a window settles it — so Codex can be swapped once idle.
        """
        if self.state == "waiting_on_you":
            self._end_turn_as("update", "turn_ended")

    def sampled_task(self) -> SampledTask:
        """This task as a look at Codex's processes needs it (`attribution.py`)."""
        return SampledTask(self.id, self.active_turn_id or self.last_turn_id, self.root,
                           self._turn.started_at, self.thread_id if self.thread_loaded else None)

    def _end_turn_as(self, reason: str, resolved_by: str) -> None:
        if self._turn.end_reason is not None:
            return
        self._turn.end_reason = reason
        self.stop_requested = True
        self._resolve_all(resolved_by)
        self._task(self._end_sequence())

    def unanswered_due(self) -> bool:
        """Whether an open request has waited past the policy's limit (design §4.8)."""
        if not self._open or self._turn.end_reason is not None:
            return False
        now, settings = self._context.clock(), self._context.settings
        attached = bool(self.windows)
        for request in self._open.values():
            if request.kind == "site":
                continue  # a site ask never holds Codex up, so it never pauses the task
            if attached and now - request.opened >= settings.agent_unanswered_attached_seconds:
                return True
            detached_for = now - max(request.opened, self.last_attached)
            if not attached and detached_for >= settings.agent_unanswered_detached_seconds:
                return True
        return False

    async def _end_sequence(self) -> None:
        """Interrupt (5 s), wait for Codex to end the turn (3 s), then conclude it."""
        codex, timings = self._context.codex, self._context.timings
        if not self._turn.began.is_set():
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(timings.interrupt_wait_seconds):
                    await self._turn.began.wait()
        if self.active_turn_id is not None and not self._turn.done.is_set():
            with contextlib.suppress(CodexRpcError, CodexUnavailableError):
                await codex.request("turn/interrupt",
                                    {"threadId": self.thread_id, "turnId": self.active_turn_id},
                                    timeout=timings.interrupt_seconds)
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(timings.interrupt_wait_seconds):
                    await self._turn.done.wait()
        await self._conclude_turn()

    async def _turn_completed(self, params: dict[str, Any]) -> None:
        turn = mapping(params.get("turn"))
        if turn.get("id") not in (None, self.active_turn_id) and self.active_turn_id is not None:
            return
        self._turn.status = str(turn.get("status") or "completed")
        self._turn.error = turn.get("error")
        self._turn.done.set()
        if self.active_turn_id is not None:
            self._context.store.end_turn(self.id, self.active_turn_id, self._turn.status,
                                         uncertain=False)
        if self._turn.end_reason is not None:
            return  # the end sequence already running concludes it
        self._turn.end_reason = "completed"
        self._resolve_all("turn_ended")
        await self._conclude_turn()

    def codex_ended(self) -> None:
        """Codex's process stopped: a running turn is cut off, and its thread must be resumed."""
        if self.thread_id is not None:
            self._context.codex.release(self.thread_id)
        self.thread_loaded = False
        if self.state not in TURN_STATES and self.state != "stopping":
            return
        self._turn.done.set()
        self._turn.began.set()
        if self._turn.end_reason is None:
            self._end_turn_as("crash", "turn_ended")

    async def _conclude_turn(self) -> None:
        """Steps 2-6 of the end sequence, `turn.completed`, then the state that follows."""
        confirmation = await self._context.cleanup.end(self.sampled_task())
        self._set_processes(confirmation)
        status, error = self._outcome()
        completed: dict[str, Any] = {"turn_id": self.active_turn_id, "status": status}
        if error is not None:
            completed["error"] = error
        completed["processes_confirmed_gone"] = confirmation.confirmed_gone
        self.emit("turn.completed", **completed)
        if self.active_turn_id is not None and self._turn.end_reason != "completed":
            self._context.store.end_turn(self.id, self.active_turn_id, status,
                                         uncertain=self._turn.end_reason == "crash")
        self.active_turn_id = None
        target = FINAL_STATE.get(str(self._turn.end_reason), "completed_needs_review")
        if not confirmation.confirmed_gone:
            self._after_leftover = target
            self._not_delivered()
            self.set_state("leftover")
            return
        if await self._follow_on(status):
            return
        self.set_state(target)

    def _set_processes(self, confirmation: Confirmation) -> None:
        self.processes = Processes(confirmation.attributed, confirmation.confirmed_gone,
                                   confirmation.leftover)
        # What still runs, in the checkout lock file too: a window ends it if RAVIS is down (§6.3).
        self._context.locks.record_family(self.id, lock_file_leftover(confirmation.leftover))

    def _outcome(self) -> tuple[str, dict[str, Any] | None]:
        turn = self._turn
        if turn.end_reason == "step_cap":
            return "interrupted", step_cap_error(self.max_steps or 0)
        if turn.end_reason == "crash":
            return "failed", dict(RUNTIME_CRASHED)
        if turn.end_reason == "turn_start_timeout":
            return "failed", turn_start_error("Codex didn't answer in time.")
        if turn.end_reason == "completed":
            return turn.status or "completed", (
                turn.error if isinstance(turn.error, dict) and "kind" in turn.error
                else turn_error(turn.error)
            )
        return (turn.status if turn.status == "completed" else "interrupted"), None

    async def _follow_on(self, status: str) -> bool:
        """A steer queued during the turn starts the next one — if the task still holds the lock."""
        if not self.queued:
            return False
        if self._turn.end_reason != "completed" or status != "completed":
            self._not_delivered()
            return False
        async with self.action_lock:
            try:
                self._context.locks.take_for_turn(self, None)
            except CodexRefusalError:
                self._not_delivered()
                return False
            self.start_turn("continue", "")
        return True

    def _not_delivered(self) -> None:
        queued, self.queued = self.queued, []
        for text in queued:
            self.emit("feedback", text=text, how="not_delivered")

    # ── Leftover processes ───────────────────────────────────────────────────

    async def stop_leftovers(self) -> dict[str, Any]:
        """`POST …/leftover {action: stop_them}`, under the action lock."""
        if self.state != "leftover":
            raise refusals.no_leftover()
        # The owner pressed Stop them: the leftovers the chat named are signalled too.
        confirmation = await self._context.cleanup.end(self.sampled_task(),
                                                       also=self.processes.leftover)
        self._set_processes(confirmation)
        if confirmation.confirmed_gone:
            self.set_state(self._after_leftover)
        else:
            self._announce_state()
        return {"processes_confirmed_gone": confirmation.confirmed_gone}

    async def recheck_leftovers(self) -> None:
        """Every tick while `leftover`: once every survivor has gone, the task can be settled."""
        if self.state != "leftover":
            return
        alive = await self._context.cleanup.still_alive(self.processes.leftover)
        if alive:
            return
        self.processes = Processes(self.processes.attributed, True, ())
        self.set_state(self._after_leftover)

    # ── Settle ───────────────────────────────────────────────────────────────

    def claim_settle(self, window_id: str) -> dict[str, Any]:
        """`POST …/settle-claim`, under the action lock: one window commits (design §3.5.3)."""
        self._refuse_if_over()
        lock = self._context.store.lock_for_session(self.id)
        if lock is not None and lock["state"] == "superseded":
            raise refusals.lock_superseded()
        if self.state == "leftover":
            raise refusals.processes_not_confirmed_gone()
        if not self.needs_settle:
            raise refusals.nothing_to_settle()
        if not self.processes.confirmed_gone:
            raise refusals.processes_not_confirmed_gone()
        now = self._context.clock()
        claim = self.claim
        if claim is not None and claim.expires > now:
            if claim.window != window_id:
                raise refusals.settle_claimed(claim.window)
            return {"claim_id": claim.id, "expires_at": claim.expires_at}
        seconds = self._context.timings.claim_seconds
        expires_at = datetime.fromtimestamp(datetime.now(UTC).timestamp() + seconds, UTC)
        self.claim = Claim(f"sc_{uuid.uuid4().hex}", window_id, now + seconds, iso(expires_at))
        return {"claim_id": self.claim.id, "expires_at": self.claim.expires_at}

    async def settle(self, claim_id: str, next_step: str) -> dict[str, Any]:
        """`POST …/settle`, under the action lock: the window committed; idle, end or transfer."""
        self._refuse_if_over()
        claim = self.claim
        if claim is None or claim.id != claim_id or claim.expires <= self._context.clock():
            raise refusals.claim_invalid()
        if self.state == "leftover" or not self.processes.confirmed_gone:
            raise refusals.processes_not_confirmed_gone()
        if not self.needs_settle:
            raise refusals.claim_invalid()
        self.claim = None
        self.processes = Processes(0, True, ())
        if next_step == "end" or self.end_after_settle:
            await self.end()
        elif next_step == "transfer":
            # The lock stays, reserved for the destination's transfer token (design §6.2).
            self.set_state("idle")
        else:
            self._context.locks.release(self)
            self.set_state("idle")
        return self.view()

    # ── Ending the task ──────────────────────────────────────────────────────

    async def cancel(self) -> dict[str, Any]:
        """`POST …/cancel`: Stop now if a turn runs, and end once the work is settled."""
        self._refuse_if_over()
        self.end_after_settle = True
        if self.state in TURN_STATES:
            self.stop("window")
        elif self.state == "idle":
            await self.end()
        return {"state": self.state, "end_after_settle": True}

    async def delete(self) -> dict[str, Any]:
        """`DELETE …/{sid}`: end a settled task, keeping Codex's thread (owner decision D3)."""
        if self.state in TURN_STATES or self.state == "stopping":
            raise refusals.turn_active("A turn is still running.")
        if self.needs_settle or self.state == "leftover":
            raise refusals.settle_first()
        if not self.over:
            await self.end()
        return {"state": "ended"}

    async def end(self) -> None:
        """Archive the thread (kept, not deleted), let the lock go, and say the task is over."""
        if self.thread_id is not None and self.thread_loaded:
            with contextlib.suppress(CodexRpcError, CodexUnavailableError):
                await self._context.codex.request(
                    "thread/archive", {"threadId": self.thread_id},
                    timeout=self._context.timings.archive_seconds)
            self._context.codex.release(self.thread_id)
        self.thread_loaded = False
        for site in [request for request in self._open.values() if request.kind == "site"]:
            self._resolve(site, "turn_ended", "keep_blocked")
        self._context.locks.release(self)
        self.set_state("ended")
        self.emit("session.ended", final_state="ended")
        if self._consumer is not None:
            self._consumer.cancel()

    def shutdown(self) -> None:
        """RAVIS is stopping, and Codex's process with it: a running turn's outcome is unknown."""
        for task in list(self._tasks):
            task.cancel()
        if self._consumer is not None:
            self._consumer.cancel()
        self.events.close_streams()
        if self.state in TURN_STATES or self.state == "stopping":
            self._context.store.update_session(self.id, state="uncertain")
            self._context.store.mark_uncertain(self.id)

    # ── The transcript ───────────────────────────────────────────────────────

    async def transcript(self, limit: int) -> dict[str, Any]:
        """`GET …/transcript`: Codex's own turns, read through and redacted; nothing is stored."""
        try:
            result = await self._context.codex.request(
                "thread/turns/list", {"threadId": self.thread_id, "limit": limit},
                timeout=self._context.timings.turns_list_seconds)
        except (CodexRpcError, CodexUnavailableError):
            raise refusals.runtime_unavailable("Codex didn't answer.") from None
        data = result.get("data") if isinstance(result, dict) else None
        turns = [self._turn_summary(turn) for turn in data or [] if isinstance(turn, dict)]
        return {"turns": turns}

    def _turn_summary(self, turn: dict[str, Any]) -> dict[str, Any]:
        items = [normalise_item(item, self._paths, completed=True)
                 for item in turn.get("items") or [] if isinstance(item, dict)]
        return {"id": turn.get("id"), "status": turn.get("status"),
                "error": turn_error(turn.get("error")),
                "items": [item for item in items if item is not None]}

    # ── After a restart ──────────────────────────────────────────────────────

    async def recover(self) -> None:
        """A task the previous RAVIS left mid-turn: uncertain, its processes ended and confirmed."""
        if self.state in TURN_STATES or self.state == "stopping":
            self._context.store.mark_uncertain(self.id)
            self.active_turn_id = None
            self.set_state("uncertain")
        if self.needs_settle or self.state == "leftover":
            # Codex's process died with the last RAVIS: only what was recorded can be found.
            confirmation = await self._context.cleanup.end_recorded(self.id)
            self._set_processes(confirmation)
            if not confirmation.confirmed_gone:
                self._after_leftover = self.state if self.needs_settle else "uncertain"
                self.set_state("leftover")
        elif self.state == "idle":
            self.processes = Processes(0, True, ())
