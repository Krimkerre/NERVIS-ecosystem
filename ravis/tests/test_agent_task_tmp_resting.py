"""A task's temp folder goes whenever the task rests, and is back before Codex's next step.

RAVIS 0.26.2 removed a task's `<project>/.clarvis/tmp/<folder>` only once the task was over for
good. Live, on 15 September 2026, that never happened: Clarvis settles every finished Codex task to
`idle`, kept for follow-ups, and never ends one, so `live-test-a` and `live-test-c` kept their
folders. The owner decided RAVIS removes the folder whenever a task has nothing running and makes it
again when Codex takes the next step (RAVIS 0.26.3). What RAVIS is held to, against the fake Codex:

- **gone at rest** — settled to `idle`, every process confirmed gone — with the parents RAVIS made,
  its records kept so the folder and those parents are made again, and recorded again;
- **kept** while a turn runs or stops, while processes aren't confirmed gone (`leftover`) and while
  a settle is owed — the task's own folder, a folder another task of the project uses, and at start;
- **back before every step**: a turn, which finds it (`tmpdir`), a resume, a site reopen's resume;
- **under the action lock**: a settle and a turn arriving together, or a task ending while another
  makes their shared folder, never leave a turn without it;
- **never through a link**, never Clarvis's own files, and the parents kept for a task that still
  needs them until the last one rests;
- **a folder that can't be made refuses the step** — a turn, a steer's follow-on turn, a create —
  and no turn starts without it;
- **at start**, every resting task's folder goes, and every other unfinished task's stays.

The relay cases serve RAVIS with the fake app-server; the restart cases build a previous RAVIS's
rows and start `AgentSessions` on them with a pretend Codex (`test_agent_reconcile.py`'s rig).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import stat
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from tests.agent_rig import (
    SESSIONS,
    Frame,
    Task,
    clarvis_lock,
    has,
    named,
    project,
    ready_rig,
    refused,
    serving,
)
from tests.codex_rig import eventually
from tests.test_agent_reconcile import (
    FakeHost,
    FakeTable,
    lock_row,
    previous_ravis,
    reopened,
    restart,
    run,
    session_row,
    settings_for,
    workspace,
)

from ravis.agent.session import AgentSession, Processes
from ravis.agent.sessions import AgentSessions
from ravis.agent.store import AgentStore

TMP = Path(".clarvis") / "tmp"
ONE_SITE = "RELAY\nfetch download.pytorch.org\nsay I need download.pytorch.org."
#: Every unfinished state but `idle`: something runs or is under way, or a settle is owed.
NOT_RESTING = ("starting", "running", "waiting_on_you", "stopping", "stopped", "leftover",
               "completed_needs_review", "paused_unanswered", "paused_for_update", "uncertain")


# ── Helpers ──────────────────────────────────────────────────────────────────


def outputs(frames: list[Frame]) -> list[str]:
    """What each completed command printed, in order."""
    return [data["item"]["output_tail"] for data in named(frames, "item.completed")
            if data["item"]["type"] == "commandExecution"]


def turn_after(task: Task, text: str) -> list[Frame]:
    """A carry-on once the task rests, read until it completes."""
    last = task.view()["last_event_id"]
    started = task.post("turns", {"text": text, "kind": "carry_on"}, keyed=True)
    assert started.status_code == 202, started.text
    return task.frames(after=last, until=has("turn.completed"), seconds=15)


def row(store: AgentStore, session_id: str) -> dict[str, Any]:
    found = store.session(session_id)
    assert found is not None, session_id
    return found


class Host(FakeHost):
    """The restart's Codex, answering a resume and a turn, and noting whether the folder was there.

    With `hold_resume`, `thread/resume` waits until `go_on` is set — so a test can act while a task
    is between making its folder and sending its turn.
    """

    def __init__(self) -> None:
        super().__init__()
        self.answers.update({"thread/resume": {"thread": {"id": "thread-1"}},
                             "turn/start": {"turn": {"id": "turn-2"}}})
        self.folder: Path | None = None
        self.there_at: list[tuple[str, bool]] = []
        self.hold_resume = False
        self.resuming = asyncio.Event()
        self.go_on = asyncio.Event()

    async def request(self, method: str, params: dict[str, Any] | None = None, *,
                      timeout: float) -> Any:
        if method in ("thread/resume", "turn/start") and self.folder is not None:
            self.there_at.append((method, self.folder.is_dir()))
        if method == "thread/resume" and self.hold_resume:
            self.resuming.set()
            await self.go_on.wait()
        return await super().request(method, params, timeout=timeout)


def a_restart(tmp_path: Path, tasks: list[tuple[str, str, str, str]],
              host: FakeHost | None = None) -> tuple[AgentSessions, Path]:
    """RAVIS starting on one project's tasks as a previous RAVIS left them.

    Each task is (id, state, the folder it uses, the parents RAVIS made for it); the folders exist.
    """
    root, git_dir = workspace(tmp_path)

    def build(store: AgentStore) -> None:
        for session_id, state, folder, made in tasks:
            store.insert_session(session_row(session_id, root, git_dir, state))
            store.record_tmp(session_id, folder, made)

    path = previous_ravis(tmp_path, build)
    for _, _, folder, _ in tasks:
        (root / TMP / folder / "tmpq3x9").mkdir(parents=True, exist_ok=True)
    return restart(path, settings_for(tmp_path), FakeTable([]), host), root


def found(sessions: AgentSessions, session_id: str) -> AgentSession:
    session = sessions.find(session_id)
    assert session is not None, session_id
    return session


async def until(condition: Callable[[], bool], what: str) -> None:
    for _ in range(500):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"never happened: {what}")


async def carry_on(session: AgentSession) -> None:
    """`POST …/turns` as the route runs it: under the task's action lock."""
    async with session.action_lock:
        await session.turn("carry_on", "Carry on.", None)


async def settled(session: AgentSession) -> None:
    """`settle-claim`, then `settle` with `next: idle`, each under the action lock."""
    async with session.action_lock:
        claim = session.claim_settle("win-test")
    async with session.action_lock:
        await session.settle(claim["claim_id"], "idle")
    assert session.state == "idle"


# ── Through the relay, with the fake Codex ─────────────────────────────────


def test_a_task_s_folder_stays_until_it_rests_and_is_back_for_its_next_turn(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    store = rig.service.agents.store
    spawned: list[int] = []
    try:
        with serving(rig) as relay:
            relay.ready()
            task = relay.started(root, git_dir, text="RELAY\nspawn\nwait")
            folder = root / TMP / task.id
            records = eventually(lambda: rig.server.records("relay_spawned"),
                                 what="its command's process started")
            spawned.extend(record["pid"] for record in records)
            task.reaches("running")
            assert folder.is_dir(), "removed while a turn runs"
            cleanup = rig.service.agents.context.cleanup
            cleanup._kill = lambda _pid, _signal: None  # a process that won't die, for now
            assert task.post("interrupt", {"reason": "stop"}).status_code == 202
            task.reaches("leftover")
            assert folder.is_dir(), "removed while its processes weren't confirmed gone"
            cleanup._kill = os.kill
            assert task.post("leftover", {"action": "stop_them"}).json() == {
                "processes_confirmed_gone": True}
            task.reaches("stopped")
            assert folder.is_dir(), "removed while a settle was owed"

            assert task.settle("idle").json()["state"] == "idle"

            assert not (root / ".clarvis").exists(), "a resting task's folder or parents stayed"
            assert (root / ".git").is_dir()
            kept = row(store, task.id)
            assert (kept["tmp_folder_id"], kept["tmp_parents_made"]) == (task.id, ".clarvis,tmp")

            frames = turn_after(task, "RELAY\ntmpdir")

            assert outputs(frames) == ["TMPDIR is a folder"]
            task.reaches("completed_needs_review")
            assert stat.S_IMODE(folder.stat().st_mode) == 0o700
            assert row(store, task.id)["tmp_parents_made"] == ".clarvis,tmp"
            assert task.settle("idle").json()["state"] == "idle"
            assert not (root / ".clarvis").exists()
    finally:
        for pid in spawned:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def test_tasks_taking_turns_in_one_project_each_have_a_folder_only_while_they_need_it(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    store, tmp = rig.service.agents.store, root / TMP
    with serving(rig) as relay:
        relay.ready()
        first = relay.started(root, git_dir, text="RELAY\nsay one")
        first.reaches("completed_needs_review")
        assert first.settle("idle").json()["state"] == "idle"

        second = relay.started(root, git_dir, text="RELAY\nwait", task_id=str(uuid.uuid4()))
        second.reaches("running")

        assert (tmp / second.id).is_dir() and not (tmp / first.id).exists()
        assert row(store, second.id)["tmp_parents_made"] == ".clarvis,tmp"
        # Ended while the other task rests: nothing in the project needs the parents any more.
        assert second.post("cancel").status_code == 202
        second.reaches("stopped")
        assert second.settle("idle").json()["state"] == "ended"
        assert not (root / ".clarvis").exists()

        assert outputs(turn_after(first, "RELAY\ntmpdir")) == ["TMPDIR is a folder"]
        first.reaches("completed_needs_review")
        assert first.settle("idle").json()["state"] == "idle"
        assert not (root / ".clarvis").exists()

        # A turn refused because a Clarvis run holds the project leaves no folder behind either.
        clarvis_lock(rig, root)
        locked = first.post("turns", {"text": "RELAY\ntmpdir", "kind": "carry_on"}, keyed=True)
        assert locked.status_code == 409, locked.text
        assert not (tmp / first.id).exists()


def test_a_resumed_or_reopened_thread_has_its_folder_before_codex_loads_it(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text=ONE_SITE)
        folder = root / TMP / task.id
        # The blocked site reopens the thread while the task is owed a settle.
        eventually(lambda: rig.server.records("thread_resumed"), what="the first reopen resumed")
        task.reaches("completed_needs_review")
        assert task.settle("idle").json()["state"] == "idle"
        assert not folder.exists()
        ask = task.view()["pending_requests"][0]
        assert ask["payload"]["host"] == "download.pytorch.org"

        # Allowed while the task rests: the thread is reopened at once, and resumed.
        assert task.answer(ask["id"], {"kind": "allow_site"}).status_code == 200

        resumed = eventually(lambda: (lambda seen: seen if len(seen) == 2 else None)(
            rig.server.records("thread_resumed")), what="the reopen while resting resumed")
        assert [record["tmpdir_there"] for record in resumed] == [True, True]
        eventually(lambda: not folder.exists(), what="the folder gone again, with nothing to run")
        assert task.view()["state"] == "idle"

        # A task resuming the thread gets the folder back before `thread/resume` too.
        ended = relay.call("DELETE", f"{SESSIONS}/{task.id}", token=task.token)
        assert ended.json() == {"state": "ended"}
        start = {"kind": "resume", "thread_id": task.created["session"]["codex"]["thread_id"],
                 "catch_up_text": "RELAY\ntmpdir"}
        again = relay.started(root, git_dir, start=start, task_id=str(uuid.uuid4()))

        frames = again.frames(after=0, until=has("turn.completed"), seconds=15)

        records = rig.server.records("thread_resumed")
        assert [record["tmpdir_there"] for record in records] == [True, True, True]
        assert outputs(frames) == ["TMPDIR is a folder"]


def test_no_link_leads_out_of_the_project_when_the_folder_goes_or_is_made_again(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    outside = Path(os.path.realpath(rig.folder)) / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("the owner's")
    turn = {"text": "RELAY\ntmpdir", "kind": "carry_on"}
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nsay done")
        folder = root / TMP / task.id
        (folder / "to-outside").symlink_to(outside, target_is_directory=True)
        task.reaches("completed_needs_review")

        assert task.settle("idle").json()["state"] == "idle"

        assert not os.path.lexists(folder)
        assert sorted(path.name for path in outside.iterdir()) == ["keep.txt"]

        # A link where `.clarvis/tmp` belongs: nothing is made through it, and the turn is refused.
        (root / ".clarvis").mkdir()
        (root / TMP).symlink_to(outside, target_is_directory=True)
        refused(task.post("turns", turn, keyed=True), 503, "CODEX_RUNTIME_UNAVAILABLE")
        assert sorted(path.name for path in outside.iterdir()) == ["keep.txt"]
        assert len(rig.server.received("turn/start")) == 1

        # A link in place of the task's own folder: refused too, never handed to Codex as TMPDIR.
        (root / TMP).unlink()
        (root / TMP).mkdir()
        folder.symlink_to(outside, target_is_directory=True)
        refused(task.post("turns", turn, keyed=True), 503, "CODEX_RUNTIME_UNAVAILABLE")
        assert len(rig.server.received("turn/start")) == 1

        folder.unlink()
        assert outputs(turn_after(task, "RELAY\ntmpdir")) == ["TMPDIR is a folder"]
        assert folder.is_dir() and not folder.is_symlink()
        assert sorted(path.name for path in outside.iterdir()) == ["keep.txt"]


def test_clarvis_s_own_files_keep_its_folder_while_a_task_rests(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    transferred, _ = project(rig, "transferred", git=False)
    kept, _ = project(rig, "has-a-checkpoint", git=False)
    (kept / ".clarvis").mkdir()
    (kept / ".clarvis" / "task-checkpoint.json").write_text("{}")
    with serving(rig) as relay:
        relay.ready()
        moving = relay.started(transferred, None, text="RELAY\nsay done")
        moving.reaches("completed_needs_review")

        assert moving.settle("transfer").json()["state"] == "idle"

        # The lock stays reserved for the transfer, its file in `.clarvis`: only the folder goes.
        assert not (transferred / TMP / moving.id).exists()
        assert (transferred / ".clarvis" / "engine.lock").is_file()
        assert (transferred / TMP).is_dir(), "a parent was removed while a lock held the project"

        checkpointed = relay.started(kept, None, text="RELAY\nsay done", task_id=str(uuid.uuid4()))
        checkpointed.reaches("completed_needs_review")
        assert checkpointed.settle("idle").json()["state"] == "idle"
        assert sorted(path.name for path in (kept / ".clarvis").iterdir()) == [
            "task-checkpoint.json"]


def test_a_folder_that_can_t_be_made_refuses_the_turn_the_follow_on_and_the_create(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path, scenario={"steer_refused": True})
    root, git_dir = project(rig)
    store = rig.service.agents.store
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nsay done")
        task.reaches("completed_needs_review")
        assert task.settle("idle").json()["state"] == "idle"
        root.chmod(0o500)  # nothing can be made in the project
        try:
            refusal = refused(task.post("turns", {"text": "RELAY\ntmpdir", "kind": "carry_on"},
                                        keyed=True), 503, "CODEX_RUNTIME_UNAVAILABLE")
            assert "temp folder" in refusal["message"]
            assert task.view()["state"] == "idle"
            assert store.lock_for_session(task.id) is None, "a refused turn kept the project"
            created = relay.create(root, git_dir, task_id=str(uuid.uuid4()))
            assert created.status_code == 503, created.text
            assert "temp folder" in created.json()["error"]["message"]
        finally:
            root.chmod(0o700)
        assert len(rig.server.received("turn/start")) == 1
        assert len(rig.server.received("thread/start")) == 1
        assert not (root / ".clarvis").exists()

        assert outputs(turn_after(task, "RELAY\ntmpdir")) == ["TMPDIR is a folder"]
        task.reaches("completed_needs_review")
        assert task.settle("idle").json()["state"] == "idle"

        # A steer queued during a turn starts the next one only with the folder there.
        last = task.view()["last_event_id"]
        asked = task.post("turns", {"text": "RELAY\nask npm test\nsay done", "kind": "carry_on"},
                          keyed=True)
        assert asked.status_code == 202, asked.text
        request = task.pending()
        queued = task.post("steer", {"text": "Also update the README."}, keyed=True)
        assert queued.json() == {"delivered": "queued"}
        shutil.rmtree(root / ".clarvis")  # something outside removed it mid-turn
        root.chmod(0o500)
        try:
            task.answer(request["id"], {"kind": "once"})
            task.frames(after=last, until=has("feedback", how="not_delivered"), seconds=15)
            task.reaches("completed_needs_review")
        finally:
            root.chmod(0o700)
        assert len(rig.server.received("turn/start")) == 3


# ── RAVIS's own restart rig ──────────────────────────────────────────────────


def test_the_start_removes_every_resting_task_s_folder_and_keeps_every_other(
    tmp_path: Path,
) -> None:
    """What the first start of 0.26.3 does to the live tests' projects, and to every other state."""
    coding = Path(os.path.realpath(tmp_path)) / "coding"

    def build(store: AgentStore) -> None:
        for name in (*NOT_RESTING, "idle", "transferred"):
            root, git_dir = workspace(tmp_path, name)
            session_id = f"as_{name}"
            store.insert_session(session_row(session_id, root, git_dir,
                                             "idle" if name == "transferred" else name))
            store.record_tmp(session_id, session_id, ".clarvis,tmp")
            (root / TMP / session_id / "tmpq3x9").mkdir(parents=True)
        # Settled for a transfer: the lock stays reserved, so the parents do too.
        store.insert_lock(lock_row("pl_transfer", "as_transferred", coding / "transferred"))
        # Two resting tasks of one project, the one that made the parents first: every folder goes
        # before either's parents are tried, so the parents go too.
        pair, pair_git = workspace(tmp_path, "two-resting")
        for session_id, made, created in (("as_pair_one", ".clarvis,tmp", "2026-09-13T01:00:00Z"),
                                          ("as_pair_two", "", "2026-09-13T02:00:00Z")):
            store.insert_session({**session_row(session_id, pair, pair_git, "idle"),
                                  "created_at": created})
            store.record_tmp(session_id, session_id, made)
            (pair / TMP / session_id / "tmpq3x9").mkdir(parents=True)

    path = previous_ravis(tmp_path, build)

    run(restart(path, settings_for(tmp_path), FakeTable([])))

    for name in NOT_RESTING:
        assert (coding / name / TMP / f"as_{name}" / "tmpq3x9").is_dir(), (
            f"a task left {name} lost its folder at start")
    assert not (coding / "idle" / ".clarvis").exists()
    assert (coding / "idle" / ".git").is_dir()
    assert not (coding / "transferred" / TMP / "as_transferred").exists()
    assert (coding / "transferred" / TMP).is_dir()
    assert not (coding / "two-resting" / ".clarvis").exists()
    store = reopened(path)
    assert (row(store, "as_idle")["tmp_folder_id"], row(store, "as_idle")["tmp_parents_made"]) == (
        "as_idle", ".clarvis,tmp")


@pytest.mark.parametrize("trigger", ["ends", "rests"])
@pytest.mark.parametrize(("state", "then"), [
    *((state, None) for state in ("leftover", "stopped", "completed_needs_review",
                                  "paused_unanswered", "paused_for_update", "uncertain")),
    ("idle", "running"),
    ("idle", "stopping"),
    ("idle", "unconfirmed"),
])
def test_a_folder_is_kept_while_the_task_using_it_runs_stops_is_left_over_or_is_owed_a_settle(
    tmp_path: Path, trigger: str, state: str, then: str | None,
) -> None:
    """A task resumed from another's thread uses that task's folder: the other one ending, or coming
    to rest, leaves it for as long as the task using it isn't resting."""
    first_state = "idle" if trigger == "ends" else "completed_needs_review"
    sessions, root = a_restart(tmp_path, [("as_first", first_state, "as_first", ""),
                                          ("as_using", state, "as_first", "")], Host())
    folder = root / TMP / "as_first"

    async def check() -> None:
        using, first = found(sessions, "as_using"), found(sessions, "as_first")
        if then == "unconfirmed":
            # `idle`, but its processes not confirmed gone: not resting, whatever the state says.
            # Its folder as the step before it left it (the start removed it, both tasks resting).
            using.processes = Processes(1, False, ())
            folder.mkdir(parents=True, exist_ok=True)
        elif then is not None:
            await carry_on(using)
            await until(lambda: using.state == "running", "the turn began")
            if then == "stopping":
                async with using.action_lock:
                    using.stop("window")
            assert using.state == then
        if trigger == "ends":
            async with first.action_lock:
                assert await first.delete() == {"state": "ended"}
        else:
            await settled(first)
        assert folder.is_dir(), f"removed while the task using it was {using.state} ({then})"

    run(sessions, check)


def test_the_parents_stay_for_a_task_that_needs_them_go_with_the_last_and_are_recorded_again(
    tmp_path: Path,
) -> None:
    sessions, root = a_restart(tmp_path, [
        ("as_first", "completed_needs_review", "as_first", ".clarvis,tmp"),
        ("as_second", "completed_needs_review", "as_second", ""),
        ("as_third", "idle", "as_third", "")], Host())
    tmp = root / TMP

    async def check() -> None:
        assert not (tmp / "as_third").exists(), "a resting task kept its folder at start"
        await settled(found(sessions, "as_first"))
        assert not (tmp / "as_first").exists() and (tmp / "as_second").is_dir()
        assert row(sessions.store, "as_second")["tmp_parents_made"] == ".clarvis,tmp"

        await settled(found(sessions, "as_second"))

        assert not (root / ".clarvis").exists(), "the last task to rest left the parents behind"

        # The third task found the parents there when it started; made again now, they are its.
        await carry_on(found(sessions, "as_third"))
        assert (tmp / "as_third").is_dir()
        assert row(sessions.store, "as_third")["tmp_parents_made"] == ".clarvis,tmp"

    run(sessions, check)


def test_a_settle_and_a_turn_arriving_together_leave_the_turn_its_folder(tmp_path: Path) -> None:
    host = Host()
    sessions, root = a_restart(
        tmp_path, [("as_1", "completed_needs_review", "as_1", ".clarvis,tmp")], host)
    host.folder = root / TMP / "as_1"

    async def race() -> None:
        task = found(sessions, "as_1")
        async with task.action_lock:
            claim = task.claim_settle("win-test")

        async def settle() -> None:
            async with task.action_lock:
                await task.settle(claim["claim_id"], "idle")

        await asyncio.gather(settle(), carry_on(task))
        await until(lambda: len(host.there_at) == 2, "the turn was sent")
        assert host.there_at == [("thread/resume", True), ("turn/start", True)]

    run(sessions, race)


def test_a_task_ending_while_another_makes_their_shared_folder_for_a_turn_leaves_it(
    tmp_path: Path,
) -> None:
    """The task starting the turn is still `idle` while Codex resumes its thread; only its held
    action lock tells the other task's removal that the folder is needed."""
    host = Host()
    host.hold_resume = True
    sessions, root = a_restart(tmp_path, [("as_first", "idle", "as_first", ".clarvis,tmp"),
                                          ("as_using", "idle", "as_first", "")], host)
    host.folder = root / TMP / "as_first"

    async def race() -> None:
        using, first = found(sessions, "as_using"), found(sessions, "as_first")
        turn = asyncio.create_task(carry_on(using))
        await asyncio.wait_for(host.resuming.wait(), 5)
        assert using.state == "idle"
        async with first.action_lock:
            assert await first.delete() == {"state": "ended"}
        host.go_on.set()
        await turn
        await until(lambda: len(host.there_at) == 2, "the turn was sent")
        assert host.there_at == [("thread/resume", True), ("turn/start", True)]
        assert row(sessions.store, "as_using")["tmp_parents_made"] == ".clarvis,tmp"

    run(sessions, race)
