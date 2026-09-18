"""After a restart, at shutdown, while a new Codex build waits (design §4.4, §4.10, §6.3).

What RAVIS is held to:
- **a restart ends only recorded processes**, by pid and start time, leaves a task that was
  mid-turn `uncertain` — or `leftover`, naming what wouldn't stop — and answers no route until then;
- **the restart adoption rule**, case by case as `lock-rule-cases.json` → `adoption_cases` names
  them: a lock file naming RAVIS's previous instance is rewritten and kept; one naming a window —
  alive, unresponsive or gone — is left byte for byte and the lock is `superseded`, so the task's
  `settle-claim` and `turns` get 409 `LOCK_SUPERSEDED`; a missing file is created; a create race
  lost to a window is `superseded`;
- a superseded lock is RAVIS's again once the window lets go of its file; a lock whose task is gone
  is dropped, deleting only a file that names RAVIS;
- shutdown interrupts every running turn, then ends what the tasks' commands left;
- a turn only waiting for an answer is paused ten minutes after a new Codex build appears, and no
  new task starts meanwhile (review AL5);
- a running task's processes are recorded, and written into its checkout lock file as
  `{pid, start, comm}`;
- Codex's own threads are deleted after 90 days unused, unless a checkpoint still names them;
- **a restart while a task's thread was reopening** resumes the thread when its next turn starts,
  with the profile, roots and the task's effort, and a turn that waited for the reopen is uncertain.

The restart cases build the previous RAVIS's rows in a throwaway database and start a new
`AgentSessions` on it, with a pretend process table: nothing real is signalled.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import os
import subprocess
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.agent_rig import TASK_ID, FakeClock, project, ready_rig, refused, serving
from tests.codex_rig import eventually

from ravis.agent import locks as locks_module
from ravis.agent.calibration_dependent import thread_resume_params, turn_start_params
from ravis.agent.cleanup import CleanupTimings
from ravis.agent.roots import root_hash
from ravis.agent.session import SessionTimings
from ravis.agent.sessions import AgentSessions, AgentTimings
from ravis.agent.store import AgentStore
from ravis.codex.lock_file import Holder, encode, iso, lock_content, lock_file_path, read_lock_file
from ravis.codex.lock_rule import own_start, probe_process
from ravis.codex.process_table import ProcessRow
from ravis.codex.refusals import CodexRefusalError
from ravis.codex.rpc import CodexUnavailableError
from ravis.config import Settings
from ravis.storage.database import prepare_database

LOCK_RULE_CASES = Path(__file__).resolve().parent / "fixtures" / "lock-rule-cases.json"
#: RAVIS's previous instance, as the database recorded it.
PREVIOUS = (999_991, "Sun Sep 13 04:00:00 2026")
WINDOW_ID = "win-desktop-1c9e4d"
FAST = AgentTimings(session=SessionTimings(
    cleanup=CleanupTimings(terminals_seconds=0.2, term_wait_seconds=0.0, confirm_seconds=0.2)))


class FakeHost:
    """RAVIS's Codex as a restart finds it: not running yet, and every request noted."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, Any]] = []
        self.answers: dict[str, Any] = {}
        self.pending = False

    async def request(self, method: str, params: dict[str, Any] | None = None, *,
                      timeout: float) -> Any:  # noqa: ARG002 — the host's signature
        self.asked.append((method, params))
        if method in self.answers:
            return self.answers[method]
        raise CodexUnavailableError("Codex isn't running")

    def hold(self, thread_id: str, inbox: Any) -> None:  # noqa: ARG002 — the host's signature
        return None

    def release(self, thread_id: str) -> None:  # noqa: ARG002 — the host's signature
        return None

    def readiness(self) -> tuple[str, str] | None:
        return None

    def profile_name(self) -> str | None:
        return None

    def models(self) -> tuple[Any, ...]:
        return ()

    def account_fingerprint(self) -> str | None:
        return None

    def running_sha256(self) -> str | None:
        return None

    def app_server_pid(self) -> int | None:
        return None

    def update_pending(self) -> bool:
        return self.pending


class FakeTable:
    """The process table a restart sees. A signalled process ends, unless it is stubborn."""

    def __init__(self, rows: list[ProcessRow], stubborn: frozenset[int] = frozenset()) -> None:
        self.rows = list(rows)
        self.stubborn = stubborn
        self.signalled: list[int] = []

    def snapshot(self) -> list[ProcessRow]:
        return list(self.rows)

    def kill(self, pid: int, _signal: int) -> None:
        self.signalled.append(pid)
        if pid not in self.stubborn:
            self.rows = [row for row in self.rows if row.pid != pid]


def settings_for(tmp_path: Path) -> Settings:
    coding = Path(os.path.realpath(tmp_path)) / "coding"
    coding.mkdir(exist_ok=True)
    return Settings(database_path=":memory:", _env_file=None,  # type: ignore[call-arg]
                    agent_allowed_roots=[str(coding)])


def workspace(tmp_path: Path, name: str = "add-utc-demo") -> tuple[Path, Path]:
    root = Path(os.path.realpath(tmp_path)) / "coding" / name
    (root / ".git").mkdir(parents=True, exist_ok=True)
    return root, root / ".git"


def session_row(session_id: str, root: Path, git_dir: Path, state: str) -> dict[str, Any]:
    stamp = "2026-09-13T01:00:00Z"
    return {
        "id": session_id, "application_id": "clarvis", "workspace_root": str(root),
        "workspace_root_hash": root_hash(root), "workspace_name": root.name,
        "git_dir": str(git_dir), "clarvis_task_id": TASK_ID, "mode": "agent",
        "file_rules": "strict", "state": state, "token_sha256": "0" * 64,
        "trace_id": uuid.uuid4().hex, "codex_thread_id": "thread-1",
        "active_turn_id": "turn-1" if state == "running" else None, "last_turn_id": "turn-1",
        "created_at": stamp, "updated_at": stamp,
    }


def lock_row(lock_id: str, session_id: str, root: Path) -> dict[str, Any]:
    stamp = iso(datetime.now(UTC))
    return {
        "id": lock_id, "workspace_root": str(root), "root_hash": root_hash(root),
        "holder_kind": "codex_session", "holder_session_id": session_id,
        "holder_window_id": None, "holder_host": "ravis", "holder_pid": PREVIOUS[0],
        "holder_pid_start": PREVIOUS[1], "state": "running", "waiting_on_you": 0,
        "heartbeat_at": stamp, "acquired_at": stamp,
    }


def ravis_file(root: Path, git_dir: Path, session_id: str, lock_id: str) -> Path:
    """The lock file the previous RAVIS wrote for a task."""
    holder = Holder(kind="codex_session", session_id=session_id, pid=PREVIOUS[0],
                    pid_start=PREVIOUS[1])
    path = lock_file_path(root, git_dir)
    path.write_bytes(encode({**lock_content(holder, task_id=TASK_ID), "ravis_lock_id": lock_id}))
    return path


def window_file(root: Path, git_dir: Path, pid: int, start: str,
                heartbeat: datetime | None = None) -> Path:
    """A Clarvis window's lock file, written as the window writes it (replacing any)."""
    holder = Holder(kind="clarvis_run", session_id=None, pid=pid, pid_start=start,
                    window_id=WINDOW_ID, host="desktop")
    path = lock_file_path(root, git_dir)
    part = path.with_name("window.part")
    part.write_bytes(encode(lock_content(holder, task_id=TASK_ID, engine="clarvis", now=heartbeat)))
    os.replace(part, path)
    return path


def previous_ravis(tmp_path: Path, build: Callable[[AgentStore], None]) -> Path:
    """The database a previous RAVIS left: its instance recorded, and whatever `build` adds."""
    path = tmp_path / "ravis.db"
    store = AgentStore(prepare_database(str(path)))
    store.record_instance(*PREVIOUS)
    build(store)
    return path


Emitted = list[tuple[str, dict[str, Any]]]


def restart(path: Path, settings: Settings, table: FakeTable, host: FakeHost | None = None,
            events: Emitted | None = None, timings: AgentTimings = FAST) -> AgentSessions:
    def emit(event: str, *, trace_id: str, data: Any = None, **_: Any) -> None:
        assert trace_id
        if events is not None:
            events.append((event, dict(data or {})))

    return AgentSessions(host or FakeHost(), prepare_database(str(path)), settings, emit=emit,
                         timings=timings, take_snapshot=table.snapshot, kill=table.kill)


def run(sessions: AgentSessions, then: Callable[[], Awaitable[None]] | None = None) -> None:
    async def main() -> None:
        await sessions.start()
        try:
            if then is not None:
                await then()
        finally:
            await sessions.stop()

    asyncio.run(main())


def reopened(path: Path) -> AgentStore:
    return AgentStore(prepare_database(str(path)))


@contextlib.contextmanager
def live_process() -> Iterator[tuple[int, str]]:
    process = subprocess.Popen(["sleep", "60"])
    try:
        probe = eventually(lambda: probe_process(process.pid), what="the sleep started")
        assert probe.lstart is not None
        yield process.pid, probe.lstart
    finally:
        process.kill()
        process.wait(10)


# ── Processes after a restart ─────────────────────────────────────────────────


@pytest.mark.parametrize("stubborn", [False, True])
def test_a_restart_ends_only_recorded_processes_and_the_task_needs_settling(
    tmp_path: Path, stubborn: bool
) -> None:
    root, git_dir = workspace(tmp_path)
    recorded = ProcessRow(5001, 1, "Sun Sep 13 05:20:00 2026", "sleep 600", 5001)
    reused = ProcessRow(5002, 1, "Sun Sep 13 06:00:00 2026", "sleep 5", 5002)
    stranger = ProcessRow(5003, 1, "Sun Sep 13 05:20:00 2026", "node vite", 5003)

    def build(store: AgentStore) -> None:
        store.insert_session(session_row("as_1", root, git_dir, "running"))
        store.record_process("as_1", "turn-1", recorded.identity, "sleep", "command_cwd")
        store.record_process("as_1", "turn-1", (5002, "Sun Sep 13 05:20:01 2026"), "sleep",
                             "parent")

    path = previous_ravis(tmp_path, build)
    table = FakeTable([recorded, reused, stranger],
                      stubborn=frozenset({5001}) if stubborn else frozenset())
    sessions = restart(path, settings_for(tmp_path), table)
    assert sessions.ready is False

    run(sessions)

    assert sessions.ready is True
    assert set(table.signalled) == {5001}
    store = reopened(path)
    if stubborn:
        assert store.session("as_1")["state"] == "leftover"  # type: ignore[index]
        session = sessions.find("as_1")
        assert session is not None and [row.pid for row in session.processes.leftover] == [5001]
    else:
        assert store.session("as_1")["state"] == "uncertain"  # type: ignore[index]
        assert store.live_processes("as_1") == []


# ── The restart adoption rule ─────────────────────────────────────────────────


def test_a_lock_file_naming_ravis_previous_instance_is_rewritten_and_kept(tmp_path: Path) -> None:
    root, git_dir = workspace(tmp_path)
    path = previous_ravis(tmp_path, lambda store: (
        store.insert_session(session_row("as_1", root, git_dir, "completed_needs_review")),
        store.insert_lock(lock_row("pl_1", "as_1", root))))
    ravis_file(root, git_dir, "as_1", "pl_1")
    sessions = restart(path, settings_for(tmp_path), FakeTable([]))
    held: list[bool] = []

    async def check() -> None:
        session = sessions.find("as_1")
        assert session is not None
        held.append(sessions.locks.holds(session))

    run(sessions, check)

    holder = read_lock_file(lock_file_path(root, git_dir)).content["holder"]
    assert (holder["session_id"], holder["pid"], holder["pid_start"]) == (
        "as_1", os.getpid(), own_start())
    row = reopened(path).lock("pl_1")
    assert row is not None and (row["state"], row["holder_pid"]) == ("running", os.getpid())
    assert held == [True]


@pytest.mark.parametrize("verdict", ["alive", "unresponsive", "gone"])
def test_a_window_holding_the_file_leaves_it_untouched_and_the_lock_superseded(
    tmp_path: Path, verdict: str
) -> None:
    root, git_dir = workspace(tmp_path)
    path = previous_ravis(tmp_path, lambda store: (
        store.insert_session(session_row("as_1", root, git_dir, "completed_needs_review")),
        store.insert_lock(lock_row("pl_1", "as_1", root))))
    events: Emitted = []
    codes: list[str] = []
    with live_process() as (pid, start):
        if verdict == "gone":
            pid, start = 999_993, "Sun Sep 13 03:00:00 2026"
        old = datetime.now(UTC) - timedelta(seconds=600 if verdict == "unresponsive" else 0)
        before = window_file(root, git_dir, pid, start, old).read_bytes()
        sessions = restart(path, settings_for(tmp_path), FakeTable([]), events=events)

        async def check() -> None:
            session = sessions.find("as_1")
            assert session is not None
            with pytest.raises(CodexRefusalError) as refusal:
                session.claim_settle("win-code-server-7f3a2b")
            codes.append(refusal.value.code)

        run(sessions, check)

    assert lock_file_path(root, git_dir).read_bytes() == before
    row = reopened(path).lock("pl_1")
    assert row is not None and row["state"] == "superseded"
    assert codes == ["LOCK_SUPERSEDED"]
    assert {"lock_id": "pl_1", "state": "superseded", "holder_kind": "codex_session"} in [
        data for name, data in events if name == "ravis.project_lock.changed"]


def test_a_restart_while_a_thread_reopened_resumes_it_when_the_next_turn_starts(
    tmp_path: Path,
) -> None:
    root, git_dir = workspace(tmp_path)
    other_root, other_git = workspace(tmp_path, "waited")

    def build(store: AgentStore) -> None:
        # Settled while Codex let go of its thread; a site ask still open. RAVIS keeps no record
        # of the reopen itself: a restart restarts Codex, and every thread with it.
        store.insert_session({**session_row("as_1", root, git_dir, "idle"), "effort": "medium"})
        store.insert_lock(lock_row("pl_1", "as_1", root))
        store.insert_request("rq_1", "as_1", "turn-1", "", "site", host="download.pytorch.org")
        # A turn asked for while the thread reopened, not yet begun.
        store.insert_session(session_row("as_2", other_root, other_git, "starting"))

    path = previous_ravis(tmp_path, build)
    ravis_file(root, git_dir, "as_1", "pl_1")
    host = FakeHost()
    host.answers.update({"thread/resume": {"thread": {"id": "thread-1"}},
                         "turn/start": {"turn": {"id": "turn-2"}}})
    sessions = restart(path, settings_for(tmp_path), FakeTable([]), host)
    seen: list[Any] = []

    async def carry_on() -> None:
        session = sessions.find("as_1")
        assert session is not None
        seen.append((session.view()["codex"]["reopening"], session.thread_loaded))
        await session.turn("carry_on", "The owner allowed download.pytorch.org.", None)
        for _ in range(200):
            if any(method == "turn/start" for method, _ in host.asked):
                break
            await asyncio.sleep(0.01)

    run(sessions, carry_on)

    assert seen == [(None, False)]
    methods = [method for method, _ in host.asked]
    assert methods.index("thread/resume") < methods.index("turn/start")
    asked = dict(host.asked)
    assert asked["thread/resume"] == thread_resume_params("thread-1", root, "agent", "")
    assert asked["turn/start"] == turn_start_params(
        "thread-1", root, "agent", "The owner allowed download.pytorch.org.", "medium")
    assert reopened(path).session("as_2")["state"] == "uncertain"  # type: ignore[index]


def test_a_superseded_task_cant_start_a_turn(tmp_path: Path) -> None:
    root, git_dir = workspace(tmp_path)
    path = previous_ravis(tmp_path, lambda store: (
        store.insert_session(session_row("as_1", root, git_dir, "idle")),
        store.insert_lock(lock_row("pl_1", "as_1", root))))
    window_file(root, git_dir, 999_993, "Sun Sep 13 03:00:00 2026")
    sessions = restart(path, settings_for(tmp_path), FakeTable([]))
    codes: list[str] = []

    async def check() -> None:
        session = sessions.find("as_1")
        assert session is not None
        with pytest.raises(CodexRefusalError) as refusal:
            await session.turn("continue", "carry on", None)
        codes.append(refusal.value.code)

    run(sessions, check)

    assert codes == ["LOCK_SUPERSEDED"]


def test_a_missing_file_is_created_and_a_create_race_lost_to_a_window_supersedes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (root_a, git_a), (root_b, git_b) = workspace(tmp_path, "a"), workspace(tmp_path, "b")
    path = previous_ravis(tmp_path, lambda store: (
        store.insert_session(session_row("as_a", root_a, git_a, "idle")),
        store.insert_session(session_row("as_b", root_b, git_b, "idle")),
        store.insert_lock(lock_row("pl_a", "as_a", root_a)),
        store.insert_lock(lock_row("pl_b", "as_b", root_b))))
    real = locks_module.create_lock_file

    def racing(where: Path, content: dict[str, Any]) -> Any:
        if where == lock_file_path(root_b, git_b):
            window_file(root_b, git_b, os.getpid(), own_start() or "")
        return real(where, content)

    monkeypatch.setattr(locks_module, "create_lock_file", racing)

    run(restart(path, settings_for(tmp_path), FakeTable([])))

    created = read_lock_file(lock_file_path(root_a, git_a)).content["holder"]
    assert (created["session_id"], created["pid"]) == ("as_a", os.getpid())
    lost = read_lock_file(lock_file_path(root_b, git_b)).content["holder"]
    assert lost["window_id"] == WINDOW_ID
    store = reopened(path)
    assert (store.lock("pl_a") or {})["state"] == "running"
    assert (store.lock("pl_b") or {})["state"] == "superseded"


def test_a_superseded_lock_is_ravis_again_once_the_window_lets_go_of_its_file(
    tmp_path: Path,
) -> None:
    root, git_dir = workspace(tmp_path)
    path = previous_ravis(tmp_path, lambda store: (
        store.insert_session(session_row("as_1", root, git_dir, "idle")),
        store.insert_lock(lock_row("pl_1", "as_1", root))))
    window_file(root, git_dir, 999_993, "Sun Sep 13 03:00:00 2026")
    # **A heartbeat due on every tick.** The recheck runs on the heartbeat, every 15 s, and the
    # background tick loop `start()` begins could take that heartbeat just before the file below
    # is removed — the explicit tick then skips the recheck and the lock reads superseded. It did,
    # once in eleven full Linux runs on 18 September 2026; forcing that ordering failed every time.
    sessions = restart(path, settings_for(tmp_path), FakeTable([]),
                       timings=dataclasses.replace(FAST, heartbeat_seconds=0.0))
    states: list[str] = []

    async def check() -> None:
        states.append((sessions.store.lock("pl_1") or {})["state"])
        lock_file_path(root, git_dir).unlink()
        await sessions.tick()
        states.append((sessions.store.lock("pl_1") or {})["state"])

    run(sessions, check)

    assert states == ["superseded", "running"]
    assert read_lock_file(lock_file_path(root, git_dir)).content["holder"]["session_id"] == "as_1"


def test_a_lock_whose_task_is_gone_is_dropped_deleting_only_ravis_own_file(
    tmp_path: Path,
) -> None:
    (mine, mine_git), (theirs, theirs_git) = workspace(tmp_path, "mine"), workspace(tmp_path,
                                                                                   "theirs")
    path = previous_ravis(tmp_path, lambda store: (
        store.insert_lock(lock_row("pl_mine", "as_gone", mine)),
        store.insert_lock(lock_row("pl_theirs", "as_also_gone", theirs))))
    ravis_file(mine, mine_git, "as_gone", "pl_mine")
    window = window_file(theirs, theirs_git, 999_993, "Sun Sep 13 03:00:00 2026").read_bytes()

    run(restart(path, settings_for(tmp_path), FakeTable([])))

    assert not lock_file_path(mine, mine_git).exists()
    assert lock_file_path(theirs, theirs_git).read_bytes() == window
    assert reopened(path).locks() == []


# ── Shutdown ──────────────────────────────────────────────────────────────────


def test_shutdown_interrupts_running_turns_then_ends_recorded_processes(tmp_path: Path) -> None:
    root, git_dir = workspace(tmp_path)
    recorded = ProcessRow(5001, 1, "Sun Sep 13 05:20:00 2026", "sleep 600", 5001)
    path = previous_ravis(tmp_path, lambda store: (
        store.insert_session(session_row("as_1", root, git_dir, "idle")),
        store.record_process("as_1", "turn-2", recorded.identity, "sleep", "command_cwd")))
    host, table = FakeHost(), FakeTable([recorded])
    host.answers["turn/interrupt"] = {}
    sessions = restart(path, settings_for(tmp_path), table, host)

    async def main() -> None:
        await sessions.start()
        session = sessions.find("as_1")
        assert session is not None
        # As a task with a turn running looks in memory.
        session.state, session.active_turn_id = "running", "turn-2"
        session.thread_id, session.thread_loaded = "thread-1", True
        await sessions.stop()
        await sessions.end_recorded_processes()

    asyncio.run(main())

    assert ("turn/interrupt", {"threadId": "thread-1", "turnId": "turn-2"}) in host.asked
    assert (reopened(path).session("as_1") or {})["state"] == "uncertain"
    assert table.signalled == [5001, 5001] or set(table.signalled) == {5001}
    assert reopened(path).live_processes() == []


# ── A new Codex build ─────────────────────────────────────────────────────────


def test_a_turn_only_waiting_is_paused_ten_minutes_after_a_new_build_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    rig = ready_rig(tmp_path, clock=clock)
    (root, git_dir), (other, other_git) = project(rig), project(rig, "other-project")
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nask npm install left-pad\nwait")
        task.pending()
        agents = rig.service.agents
        monkeypatch.setattr(rig.service, "update_pending", lambda: True)
        eventually(lambda: agents._update_noticed is not None, what="the new build noticed")
        clock.advance(599)
        time.sleep(0.3)
        assert task.view()["state"] == "waiting_on_you"
        clock.advance(2)
        view = task.reaches("paused_for_update")
        assert view["pending_requests"] == []
        refused(relay.create(other, other_git, task_id=str(uuid.uuid4())), 409,
                "CODEX_NOT_READY", reason="codex_update_pending")
        runs = relay.call("GET", "/api/v1/codex", caller="client.nervis").json()["runs"]
        assert any(run.get("state") == "paused_for_update" for run in runs)


# ── Recording a running task's processes ──────────────────────────────────────


def test_a_running_tasks_processes_are_recorded_and_written_into_its_lock_file(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    lock_file = lock_file_path(root, git_dir)
    spawned: list[int] = []
    try:
        with serving(rig) as relay:
            relay.ready()
            task = relay.started(root, git_dir, text="RELAY\nspawn\nwait")
            record = eventually(lambda: rig.server.records("relay_spawned"), what="spawned")[0]
            spawned.append(record["pid"])
            entries = eventually(lambda: json.loads(lock_file.read_text())["leftover"], 15,
                                 "the task's processes in its lock file")
            shape = json.loads(LOCK_RULE_CASES.read_text())["lock_file_leftover"]["entry"]
            assert all(set(entry) == set(shape) for entry in entries)
            assert record["pid"] in {entry["pid"] for entry in entries}
            rows = rig.service.agents.store.live_processes(task.id)
            assert rows and {row["attribution"] for row in rows} <= {"command_cwd", "parent"}
            task.post("interrupt", {"reason": "stop"})
            task.reaches("stopped")
            assert json.loads(lock_file.read_text())["leftover"] == []
            assert rig.service.agents.store.live_processes(task.id) == []
    finally:
        for pid in spawned:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, 9)


# ── Codex's own threads ───────────────────────────────────────────────────────


def test_threads_unused_for_ninety_days_are_deleted_unless_a_checkpoint_names_them(
    tmp_path: Path,
) -> None:
    folders = {name: workspace(tmp_path, name) for name in ("plain", "kept", "broken")}
    (folders["kept"][1] / "clarvis-task-checkpoint.json").write_text(
        json.dumps({"codexSession": {"threadId": "thread-kept"}}))
    (folders["broken"][1] / "clarvis-task-checkpoint.json").write_text("{not json")
    path = tmp_path / "ravis.db"
    long_ago = AgentStore(prepare_database(str(path)),
                          now=lambda: datetime.now(UTC) - timedelta(days=91))
    for name, (root, git_dir) in folders.items():
        long_ago.remember_thread(f"thread-{name}", str(root), str(git_dir))
    reopened(path).remember_thread("thread-new", str(folders["plain"][0]),
                                   str(folders["plain"][1]))
    host = FakeHost()
    host.answers["thread/delete"] = {}
    sessions = restart(path, settings_for(tmp_path), FakeTable([]), host)

    asyncio.run(sessions.sweep_threads())

    assert [params for method, params in host.asked if method == "thread/delete"] == [
        {"threadId": "thread-plain"}]
    unused = {row["thread_id"] for row in reopened(path).unused_threads(timedelta(days=90))}
    assert unused == {"thread-kept", "thread-broken"}
