"""Every Codex task RAVIS keeps: create, look up, list, the tick, and the Codex process ending.

**Creating a task** (design §3.5.3, `CreateSession`), in order:
1. the body's shape, then the folder and its git folder (`roots.py`) — 422;
2. **Codex must be ready**: `signed_in`, with its strict file rules `proven` and no new build
   waiting to run — else 409 `CODEX_NOT_READY {state, reason}`, the reason
   `strict_file_rules_unproven` while calibration or the re-test hasn't proven them (owner
   decision D2), `codex_update_pending` while a new build waits (review AL5);
3. a named model must be one Codex's `model/list` offers (422 `MODEL_NOT_OFFERED`), and a named
   effort one of that model's efforts — the default model's when none is named (422
   `EFFORT_NOT_OFFERED`); while Codex hasn't listed its models, either is 503 (R5);
4. the live-session limit (default 3) — 409 `CODEX_SESSION_LIMIT`;
5. the project lock, taken atomically, or with a transfer token (`locks.py`) — 409;
6. the row, then `thread/start` (or `thread/unarchive` and `thread/resume`) — 503 if Codex
   doesn't answer, and then the lock is let go and the row marked failed;
7. `session.state starting` (event 1), the answer rendered — so a window following from the
   create's `last_event_id` still gets `turn.started` — and only then the first turn.

Steps 2 to 6 run under one lock, so two creates can't both slip under the limit.

**Starting** (design §4.4, "RAVIS restart"), before any agent-session or project-lock route
answers (`ready`; until then they answer 503): RAVIS records its own instance; every task a
previous RAVIS left mid-turn becomes `uncertain` and **only its recorded processes** are ended and
confirmed; then the restart adoption rule settles each lock a Codex session holds (`reconcile.py`).

**The tick** (every 5 s): windows whose panel went quiet stop counting as attached; the
unanswered-request policy (30 minutes with no window attached, 2 hours with one); a turn only
waiting for an answer is paused 10 minutes after a new Codex build appears (`paused_for_update`);
leftover processes looked at again; lock heartbeats, and superseded locks re-checked, every 15 s;
ended tasks' event logs dropped 30 minutes after they end; retention, and the 90-day sweep of
Codex's own threads, hourly.

**Temp folders** (`task_tmp.py`). A task's `<root>/.clarvis/tmp/<folder>` is removed — with
`.clarvis/tmp` and `.clarvis` when RAVIS made them and they are empty — whenever the task has
nothing running: when it rests (`idle`, 0.26.3), under its action lock, and when it is over for
good (ended, or its start failed, 0.26.2). It is made again before Codex's next step in the task
(`session.py`). Clarvis never ends a task, only settles it to `idle` for follow-ups, which is why
resting matters. Never removed: a folder or parent another unfinished task of the project needs —
any that isn't resting, or that has an action under way (a turn starting, say) — or parents while a
lock holds the project. A task's records keep its folder's name and the parents RAVIS made while it
rests. Each start first removes every resting task's folder, then what ended tasks still owe.

**The processes** (design §4.5): every 2 s while a turn runs, Codex's processes are looked at and
attributed (`attribution.py`), recorded, and written into each task's checkout lock file.

**Stopping** (the lifespan's `finally`): every running turn is interrupted at once, the tasks
become `uncertain`, Codex's process ends (`codex/service.py`), and then every recorded process still
running is ended and confirmed. The interrupts and kills are kept short, so RAVIS ends inside the
launcher's six-second wait; whatever that cuts off is ended by the next start's reconciliation.

**The owner Stop's rate limit** (design §3.5.5): 10 calls a minute per application, so the menu
bar (`owner_cli`) and the dashboard (`launcher`) are counted apart.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ravis.agent import calibration_dependent as calibrated
from ravis.agent import refusals, roots, task_tmp
from ravis.agent.attribution import CwdReader, ProcessSampler, SampledTask
from ravis.agent.cleanup import CleanupTimings, ProcessCleanup, lock_file_leftover
from ravis.agent.group_kill import GroupKill, GroupKillTimings
from ravis.agent.idempotency import KeptResponses
from ravis.agent.lock_api import WindowLocks
from ravis.agent.locks import ProjectLocks
from ravis.agent.reconcile import LockReconciler
from ravis.agent.redact import Redactor
from ravis.agent.requests import PathContext, mapping
from ravis.agent.session import (
    RESTING,
    TURN_STATES,
    AgentSession,
    CodexHost,
    Context,
    SessionTimings,
)
from ravis.agent.sites import SiteAllowlist
from ravis.agent.store import AgentStore
from ravis.agent.tokens import new_id, new_token, token_matches, token_sha256
from ravis.codex.lock_rule import own_start
from ravis.codex.process_table import Kill, Snapshot, read_cwds, snapshot
from ravis.codex.refusals import CodexRefusalError
from ravis.codex.rpc import CodexRpcError, CodexUnavailableError
from ravis.codex.state import ModelFacts
from ravis.config import Settings
from ravis.storage.database import Database

logger = logging.getLogger("ravis")

START_KINDS = frozenset({"brief", "resume"})
OWNER_STOPS_PER_MINUTE = 10
#: How long `thread/delete` may take in the hourly sweep.
THREAD_DELETE_SECONDS = 10.0


@dataclass(frozen=True)
class AgentTimings:
    tick_seconds: float = 5.0
    heartbeat_seconds: float = 15.0
    retention_seconds: float = 3600.0
    #: How long an ended task's event log is kept for a window reconnecting (design §3.5.4).
    ended_kept_seconds: float = 1800.0
    #: The SSE heartbeat comment's interval.
    stream_heartbeat_seconds: float = 15.0
    #: How often Codex's processes are looked at while a turn runs (design §4.5).
    sample_seconds: float = 2.0
    #: How long a turn only waiting for an answer may hold back a new Codex build (review AL5).
    update_pause_seconds: float = 600.0
    #: Shutdown's interrupts, all at once; short, to end inside the launcher's six-second wait.
    shutdown_interrupt_seconds: float = 1.5
    shutdown_cleanup: CleanupTimings = CleanupTimings(term_wait_seconds=1.0, confirm_seconds=1.0)
    #: The takeover's kill of a Clarvis window's running command (design §6.3).
    takeover_kill: GroupKillTimings = GroupKillTimings()
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
    #: The effort named, or None for the model's own default (R5).
    effort: str | None
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
        mode=mode, model=str(body.get("model") or ""), effort=_effort(body.get("effort")),
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


def _effort(value: object) -> str | None:
    """The effort a create names, as text; absent, null or empty leaves it to the model (R5)."""
    if value is None or isinstance(value, str):
        return value or None
    raise _invalid("effort must be text: one of the model's efforts.")


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _invalid(message: str) -> CodexRefusalError:
    return CodexRefusalError("INVALID_REQUEST_BODY", 422, message)


def _chosen_model(models: tuple[ModelFacts, ...], named: str) -> ModelFacts | None:
    """The model a create names; naming none, the one Codex uses by default (else its first)."""
    if named:
        return next((model for model in models if model.id == named), None)
    return next((model for model in models if model.is_default), models[0])


def checkpoint_names_thread(row: dict[str, Any]) -> bool:
    """Whether a Clarvis checkpoint in the project still names this thread (design §4.10).

    The checkpoint is `<git_dir>/clarvis-task-checkpoint.json`, or `<root>/.clarvis/
    task-checkpoint.json` without git (§6.1). Only its `codexSession.threadId` is looked at, in
    memory; nothing of it is kept. **A checkpoint that can't be read keeps the thread**: not
    knowing is never permission to delete someone's history.
    """
    folder, root = row["git_dir"], Path(row["workspace_root"])
    path = (Path(folder) / "clarvis-task-checkpoint.json" if folder
            else root / ".clarvis" / "task-checkpoint.json")
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    named = mapping(mapping(content).get("codexSession")).get("threadId")
    return bool(named == row["thread_id"])


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
        read_folders: CwdReader = read_cwds,
    ) -> None:
        self.settings = settings
        self.timings = timings
        self.clock = clock
        self._codex = codex
        self._emit = emit
        self.store = AgentStore(database)
        self.kept = KeptResponses(self.store, clock)
        self.locks = ProjectLocks(self.store, emit=self._emit_lock, own_start=own_start)
        self.sampler = ProcessSampler(self.store, app_server_pid=codex.app_server_pid,
                                      terminals=self._terminals, take_snapshot=take_snapshot,
                                      cwds=read_folders)
        cleanup = ProcessCleanup(codex.request, self.sampler, self.store, self._sampled_tasks,
                                 timings.session.cleanup, take_snapshot=take_snapshot, kill=kill)
        self.windows = WindowLocks(
            self.store, self.locks, settings, turn_running=self._turn_running,
            group_kill=GroupKill(timings.takeover_kill, take_snapshot=take_snapshot, kill=kill),
        )
        self._reconciler = LockReconciler(self.store, self.locks)
        denied = roots.denied_paths(settings)
        redactor = Redactor(denied, roots.realpath(Path.home()))
        self.context = Context(
            codex=codex, store=self.store, locks=self.locks, cleanup=cleanup,
            sites=SiteAllowlist(codex.request, codex.profile_name),
            paths=lambda root: PathContext(root, denied, redactor), settings=settings,
            timings=timings.session, clock=clock, emit=emit, task_over=self._task_over,
            task_resting=self._task_resting,
        )
        #: False until a restart's reconciliation has run: the routes answer 503 until then.
        self.ready = False
        self._sessions: dict[str, AgentSession] = {}
        self._ended_at: dict[str, float] = {}
        self._create_lock = asyncio.Lock()
        self._owner_stops: dict[str, deque[float]] = {}
        self._tick: asyncio.Task[None] | None = None
        self._sampling: asyncio.Task[None] | None = None
        self._last_heartbeat = 0.0
        self._last_retention = 0.0
        self._update_noticed: float | None = None

    def _emit_lock(self, event: str, trace_id: str, data: dict[str, Any]) -> None:
        self._emit(event, trace_id=trace_id, data=data)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Recover what a previous RAVIS left, reconcile the locks, then serve and tick."""
        # Before retention drops the records that say which temp folders are still owed.
        self._sweep_task_folders()
        self.store.enforce_retention()
        self._last_retention = self.clock()
        instances = [(int(row["pid"]), str(row["pid_start"])) for row in self.store.instances()]
        begun = self.locks.pid_start()
        if begun is not None:
            self.store.record_instance(self.locks.pid, begun)
        for row in self.store.live_sessions():
            session = AgentSession(self.context, row, attached_until=self.clock())
            self._sessions[session.id] = session
            await session.recover()
        self._reconciler.adopt(self._sessions, instances)
        self.ready = True
        self._tick = asyncio.create_task(self._tick_loop())
        self._sampling = asyncio.create_task(self._sample_loop())

    async def stop(self) -> None:
        """Shutdown's first half, while Codex still runs: interrupt every running turn."""
        for task in (self._tick, self._sampling):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self._interrupt_running_turns()
        for session in self._sessions.values():
            session.shutdown()

    async def end_recorded_processes(self) -> None:
        """Shutdown's second half, once Codex's process has ended: what its commands left."""
        confirmation = await self.context.cleanup.end_recorded(None, self.timings.shutdown_cleanup)
        if not confirmation.confirmed_gone:
            logger.warning("agent: %d of Codex's command processes were still running at shutdown; "
                           "the next start ends them", len(confirmation.leftover))

    async def _interrupt_running_turns(self) -> None:
        running = [s for s in self._sessions.values()
                   if s.active_turn_id is not None and s.thread_loaded]
        if not running:
            return
        seconds = self.timings.shutdown_interrupt_seconds
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(seconds + 0.5):
                await asyncio.gather(*(self._interrupt(s, seconds) for s in running),
                                     return_exceptions=True)

    async def _interrupt(self, session: AgentSession, seconds: float) -> None:
        with contextlib.suppress(CodexRpcError, CodexUnavailableError):
            await self._codex.request(
                "turn/interrupt", {"threadId": session.thread_id, "turnId": session.active_turn_id},
                timeout=seconds)

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
            self._offered(wanted)
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
            # A start that failed had already made its temp folder, which goes like any other.
            self._task_over(session)
            raise
        self._sessions[session.id] = session

    def _offered(self, wanted: CreateRequest) -> None:
        """A named model must be one Codex offers, and a named effort one that model offers."""
        if not wanted.model and wanted.effort is None:
            return
        models = self._codex.models()
        if not models:
            raise refusals.runtime_unavailable(
                "Codex hasn't listed its models yet, so the model and effort can't be checked; "
                "try again in a moment.")
        chosen = _chosen_model(models, wanted.model)
        if chosen is None:
            raise refusals.model_not_offered(wanted.model, [model.id for model in models])
        if wanted.effort is not None and wanted.effort not in chosen.efforts:
            raise refusals.effort_not_offered(chosen.id, wanted.effort, list(chosen.efforts))

    def _row(self, application_id: str, wanted: CreateRequest, workspace: roots.Workspace,
             token: str) -> dict[str, Any]:
        stamp = self.store.stamp()
        return {
            "id": new_id("as_"), "application_id": application_id,
            "workspace_root": str(workspace.root), "workspace_root_hash": workspace.root_hash,
            "workspace_name": workspace.name,
            "git_dir": str(workspace.git_dir) if workspace.git_dir else None,
            "clarvis_task_id": wanted.task_id, "codex_thread_id": None, "active_turn_id": None,
            "last_turn_id": None, "model": wanted.model or None, "effort": wanted.effort,
            "mode": wanted.mode,
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
            "clarvis_task_id": session.task_id, "model": session.model, "effort": session.effort,
            "mode": session.mode,
            "file_rules": "strict", "state": session.state, "token_sha256": session.token_sha256,
            "trace_id": session.trace_id, "runtime_sha256": session.runtime_sha256,
            "account_fingerprint": session.account_fingerprint,
            "branch_name": session.branch_name, "head_commit_at_start": session.head_commit,
            "max_steps": session.max_steps, "last_event_id": 0,
            "created_at": session.created_at, "updated_at": session.updated_at,
        }

    # ── Temp folders (`task_tmp.py`) ─────────────────────────────────────────

    def _task_resting(self, session: AgentSession) -> None:
        """A task has nothing running: its temp folder goes until Codex's next step in it (0.26.3).

        Called under the task's action lock, just after it settled to `idle` or its thread was
        resumed with nothing to run, so no turn of its own can be starting. Its records stay: the
        folder's name, made again before the next step, and the parents RAVIS made, which it may
        remove again. A failure is logged, never the task's error, and tried again at its next rest
        or RAVIS's next start.
        """
        row = self.store.session(session.id)
        if row is not None:
            self._rest_folder(row)

    def _rest_folder(self, row: dict[str, Any], *, parents: bool = True) -> None:
        """Remove a resting task's folder, as the project's tasks that still need theirs allow.

        When those tasks keep the parents RAVIS made for this one, the first of them may remove
        them too once it stops needing them, so the last task to stop is the one that tidies up.
        `parents=False` removes only the folder: the start's first pass.
        """
        if not row["tmp_folder_id"]:
            return
        project = row["workspace_root_hash"]
        needing = [other for other in self._others(project, row["id"]) if not self._rests(other)]
        made = str(row["tmp_parents_made"] or "") if parents else ""
        task_tmp.remove(Path(row["workspace_root"]), str(row["tmp_folder_id"]), made=made,
                        folders_in_use=self._folders(needing),
                        project_busy=self._locked(project) or bool(needing))
        if made and needing:
            self._inherit_parents(needing[0], made)

    def _task_over(self, session: AgentSession) -> None:
        """A task is over for good: its temp folder goes, unless another task still needs it."""
        self._release_tmp(session.id)

    def _sweep_task_folders(self) -> None:
        """At start: every resting task's folder, then what tasks that ended still owe.

        No task has an action under way yet. Resting tasks include every one Clarvis settled and
        kept for follow-ups before RAVIS removed a resting task's folder (0.26.3) — the live tests'
        projects among them. Ended ones: a crash between a task's end and its folder's removal, a
        removal that failed, and tasks that ended before 0.26.2. Every resting folder goes first,
        then the resting tasks' parents, then what ended tasks owe — so no task's parents are kept
        for a folder that is about to go, whichever order the tasks come in.
        """
        resting = [row for row in self.store.live_sessions() if row["state"] in RESTING]
        for row in resting:
            self._rest_folder(row, parents=False)
        for row in resting:
            self._rest_folder(row)
        for row in self.store.ended_with_tmp():
            self._release_tmp(row["id"])

    def _release_tmp(self, session_id: str) -> None:
        """Remove one ended task's temp folder, as the project's other unfinished tasks allow.

        The folders of the project's tasks that still need theirs are kept, and while any of them
        does, or a lock holds the project, so are `.clarvis/tmp` and `.clarvis`. The right to
        remove the parents passes to an unfinished task of the project — one that needs its folder
        first — so it isn't lost with the ended task's records. What couldn't be done stays
        recorded, for the next start's sweep.
        """
        ended = self.store.session(session_id)
        if ended is None or not ended["tmp_folder_id"]:
            return
        project = ended["workspace_root_hash"]
        others = self._others(project, session_id)
        needing = [row for row in others if not self._rests(row)]
        locked = self._locked(project)
        made = str(ended["tmp_parents_made"] or "")
        done = task_tmp.remove(
            Path(ended["workspace_root"]), str(ended["tmp_folder_id"]), made=made,
            folders_in_use=self._folders(needing), project_busy=locked or bool(needing),
        )
        if made and others:
            self._inherit_parents((needing or others)[0], made)
            made = ""
        if done and not (made and locked):
            self.store.record_tmp(session_id, None, "")

    def _others(self, project: str, but: str) -> list[dict[str, Any]]:
        """The project's other unfinished tasks, as recorded — one still being created included."""
        return [row for row in self.store.live_sessions()
                if row["workspace_root_hash"] == project and row["id"] != but]

    def _rests(self, row: dict[str, Any]) -> bool:
        """Whether another task of the project doesn't need its temp folder or the parents now.

        Only a resting task with no action under way: its action lock held means a turn, a resume
        or a settle may be using or making the folder this moment. Before the start has loaded the
        tasks, the record is all there is, and nothing acts on any of them.
        """
        if row["state"] not in RESTING:
            return False
        session = self._sessions.get(row["id"])
        return session is None or (session.resting and not session.action_lock.locked())

    def _locked(self, project: str) -> bool:
        return any(lock["root_hash"] == project for lock in self.store.locks())

    @staticmethod
    def _folders(rows: list[dict[str, Any]]) -> frozenset[str]:
        return frozenset(str(row["tmp_folder_id"]) for row in rows if row["tmp_folder_id"])

    def _inherit_parents(self, heir: dict[str, Any], made: str) -> None:
        """An unfinished task of the project takes over removing the parents RAVIS made."""
        self.store.record_tmp(heir["id"], heir["tmp_folder_id"],
                              task_tmp.merge(str(heir["tmp_parents_made"] or ""), made))

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
        """`GET /api/v1/codex` → `runs`: live tasks, then Clarvis's own writing runs."""
        now = datetime.now(UTC)
        entries = [s.run_entry(named=named, now=now) for s in self._sessions.values()]
        runs = [entry for entry in entries if entry is not None]
        for row in self.locks.clarvis_runs():
            entry: dict[str, Any] = {"id": None, "turn_id": None} if named else {}
            # Clarvis's own engine runs no Codex thread: no model, effort or reopen (R5b).
            entry.update(project=Path(row["workspace_root"]).name, state="clarvis_engine",
                         since=row["acquired_at"], age_minutes=None, waiting_minutes=None,
                         attached_windows=1, paused_reason=None, model=None, effort=None,
                         reopening=None)
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

    # ── Processes ────────────────────────────────────────────────────────────

    def _turn_running(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        return session is not None and (session.state in TURN_STATES or session.state == "stopping")

    def _sampled_tasks(self) -> list[SampledTask]:
        return [s.sampled_task() for s in self._sessions.values() if self._turn_running(s.id)]

    async def _terminals(self, thread_id: str) -> list[dict[str, Any]]:
        return await self.context.cleanup.terminals(thread_id)

    async def _sample_loop(self) -> None:
        while True:
            await asyncio.sleep(self.timings.sample_seconds)
            try:
                await self.sample()
            except Exception:  # noqa: BLE001 — sampling must outlive one bad look
                logger.exception("agent: looking at Codex's processes failed")

    async def sample(self) -> None:
        """One look at every running task's processes: recorded, and in each task's lock file."""
        tasks = self._sampled_tasks()
        if not tasks:
            return
        look = await self.sampler.look(tasks)
        if look is None:
            return
        for task in tasks:
            family = look.family(task.session_id)
            self.locks.record_family(task.session_id,
                                     lock_file_leftover(a.row for a in family.attributed))

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
        await self._pause_for_update(now)
        if now - self._last_heartbeat >= self.timings.heartbeat_seconds:
            self._last_heartbeat = now
            self.locks.heartbeat({sid: s.state == "waiting_on_you"
                                  for sid, s in self._sessions.items()})
            self._reconciler.recheck(self._sessions)
        if now - self._last_retention >= self.timings.retention_seconds:
            self._last_retention = now
            self.store.enforce_retention()
            await self.sweep_threads()

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

    async def _pause_for_update(self, now: float) -> None:
        """Review AL5: a turn only waiting for an answer doesn't hold a new Codex build back."""
        if not self._codex.update_pending():
            self._update_noticed = None
            return
        if self._update_noticed is None:
            self._update_noticed = now
        if now - self._update_noticed < self.timings.update_pause_seconds:
            return
        for session in list(self._sessions.values()):
            if session.state == "waiting_on_you":
                async with session.action_lock:
                    session.pause_for_update()

    async def sweep_threads(self) -> None:
        """Codex's own history of a task, deleted after its retention unused (design §4.10)."""
        unused_for = timedelta(days=self.settings.agent_history_retention_days)
        held = {s.thread_id for s in self._sessions.values() if not s.over}
        for row in self.store.unused_threads(unused_for):
            if row["thread_id"] in held or checkpoint_names_thread(row):
                continue
            try:
                await self._codex.request("thread/delete", {"threadId": row["thread_id"]},
                                          timeout=THREAD_DELETE_SECONDS)
            except CodexUnavailableError:
                return  # Codex isn't running: the next sweep tries again
            except CodexRpcError as refusal:
                logger.info("agent: Codex didn't delete an old thread: %s", refusal)
                continue
            self.store.thread_deleted(row["thread_id"])
