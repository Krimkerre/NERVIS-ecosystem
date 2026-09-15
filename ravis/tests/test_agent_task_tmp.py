"""A Codex task's temp folder is removed once the task is over (RAVIS 0.26.2).

RAVIS made `<project>/.clarvis/tmp/<session id>` — the `TMPDIR` a task's commands use — when a Codex
task started, and nothing removed it: on 15 September 2026 the live test's project `live-test-a`
still held `.clarvis/tmp/as_…/tmp…` after its task had ended. These tests hold the removal to its
rules: gone once the task is over, with `.clarvis/tmp` and `.clarvis` when RAVIS made them and
nothing else is in them; never through a symbolic link and never outside the project; Clarvis's own
lock and checkpoint files kept; a folder another unfinished task of the project uses kept; a resumed
task's folder made again; a failure logged, never raised; and what an ended task still owes removed
at RAVIS's next start.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest
from tests.agent_rig import SESSIONS, clarvis_lock, project, ready_rig, serving

from ravis.agent import refusals, task_tmp
from ravis.agent.roots import root_hash
from ravis.agent.session import AgentSession
from ravis.agent.store import AgentStore
from ravis.storage import database as storage
from ravis.storage.database import prepare_database, restore_backup


def a_project(tmp_path: Path, name: str = "project") -> Path:
    root = Path(os.path.realpath(tmp_path)) / name
    root.mkdir()
    return root


def removed(root: Path, folder: str, made: str, *, in_use: frozenset[str] = frozenset(),
            busy: bool = False) -> bool:
    return task_tmp.remove(root, folder, made=made, folders_in_use=in_use, project_busy=busy)


def a_row(session_id: str, **changes: Any) -> dict[str, Any]:
    """An `agent_session` row with what the table requires, as `test_agent_store` builds one."""
    return {
        "id": session_id, "application_id": "clarvis", "workspace_root": "/p",
        "workspace_root_hash": "sha256:x", "workspace_name": "p", "clarvis_task_id": "t",
        "mode": "agent", "file_rules": "strict", "state": "running", "token_sha256": "0" * 64,
        "trace_id": "t", "created_at": "2026-09-14T10:00:00Z",
        "updated_at": "2026-09-14T10:00:00Z", "ended_at": None, "last_event_id": 0, **changes,
    }


# ── The folder and its parents ─────────────────────────────────────────────


def test_a_finished_task_s_folder_goes_with_the_parents_ravis_made(tmp_path: Path) -> None:
    root = a_project(tmp_path)
    made = task_tmp.make(root, "as_one")
    assert made == ".clarvis,tmp"
    scratch = root / ".clarvis" / "tmp" / "as_one" / "tmpq3x9"
    scratch.mkdir()
    (scratch / "build.log").write_text("what a command left")

    assert removed(root, "as_one", made)

    assert list(root.iterdir()) == []


def test_parents_that_were_already_there_are_kept(tmp_path: Path) -> None:
    root = a_project(tmp_path)
    (root / ".clarvis").mkdir()
    made = task_tmp.make(root, "as_one")
    assert made == "tmp"

    assert removed(root, "as_one", made)

    assert (root / ".clarvis").is_dir()
    assert not (root / ".clarvis" / "tmp").exists()

    # And a `.clarvis/tmp` that was already there — calibration's, say — stays too.
    both = a_project(tmp_path, "both-already-there")
    (both / ".clarvis" / "tmp").mkdir(parents=True)
    assert task_tmp.make(both, "as_one") == ""
    assert removed(both, "as_one", "")
    assert (both / ".clarvis" / "tmp").is_dir()


def test_a_clarvis_folder_ravis_s_own_lock_file_made_is_ravis_s_to_remove(tmp_path: Path) -> None:
    """Without git RAVIS writes its lock file into `.clarvis` a moment before the task's folder."""
    root = a_project(tmp_path)
    existed = task_tmp.clarvis_exists(root)
    (root / ".clarvis").mkdir()

    assert task_tmp.make(root, "as_one", clarvis_existed=existed) == ".clarvis,tmp"


def test_clarvis_is_kept_while_it_holds_the_lock_or_the_checkpoint(tmp_path: Path) -> None:
    """In a project without git, Clarvis's checkout lock and task checkpoint live in `.clarvis`."""
    root = a_project(tmp_path)
    made = task_tmp.make(root, "as_one")
    (root / ".clarvis" / "engine.lock").write_text('{"holder": {}}')
    (root / ".clarvis" / "task-checkpoint.json").write_text("{}")

    assert removed(root, "as_one", made)

    assert sorted(path.name for path in (root / ".clarvis").iterdir()) == [
        "engine.lock", "task-checkpoint.json"]
    assert (root / ".clarvis" / "engine.lock").read_text() == '{"holder": {}}'


def test_a_link_inside_the_folder_is_never_followed(tmp_path: Path) -> None:
    root = a_project(tmp_path)
    outside = a_project(tmp_path, "outside")
    (outside / "keep.txt").write_text("the owner's")
    made = task_tmp.make(root, "as_one")
    folder = root / ".clarvis" / "tmp" / "as_one"
    (folder / "to-a-folder").symlink_to(outside, target_is_directory=True)
    (folder / "deeper").mkdir()
    (folder / "deeper" / "to-a-file").symlink_to(outside / "keep.txt")

    assert removed(root, "as_one", made)

    assert not folder.exists()
    assert (outside / "keep.txt").read_text() == "the owner's"


def test_a_link_in_place_of_the_folder_or_a_parent_leads_nowhere(tmp_path: Path) -> None:
    outside = a_project(tmp_path, "outside")
    (outside / "as_one").mkdir()
    (outside / "as_one" / "keep.txt").write_text("the owner's")
    (outside / "keep.txt").write_text("the owner's too")

    folder_link = a_project(tmp_path, "the-folder-is-a-link")
    (folder_link / ".clarvis" / "tmp").mkdir(parents=True)
    (folder_link / ".clarvis" / "tmp" / "as_one").symlink_to(outside, target_is_directory=True)
    assert removed(folder_link, "as_one", "tmp")
    assert not os.path.lexists(folder_link / ".clarvis" / "tmp"), "the link itself is removed"

    tmp_link = a_project(tmp_path, "tmp-is-a-link")
    (tmp_link / ".clarvis").mkdir()
    (tmp_link / ".clarvis" / "tmp").symlink_to(outside, target_is_directory=True)
    assert removed(tmp_link, "as_one", ".clarvis,tmp")
    assert (tmp_link / ".clarvis" / "tmp").is_symlink()

    clarvis_link = a_project(tmp_path, "clarvis-is-a-link")
    (clarvis_link / ".clarvis").symlink_to(outside, target_is_directory=True)
    assert removed(clarvis_link, "as_one", ".clarvis,tmp")
    assert (clarvis_link / ".clarvis").is_symlink()

    assert (outside / "as_one" / "keep.txt").read_text() == "the owner's"
    assert (outside / "keep.txt").read_text() == "the owner's too"


def test_a_folder_another_task_uses_and_a_busy_project_s_parents_are_kept(tmp_path: Path) -> None:
    root = a_project(tmp_path)
    first = task_tmp.make(root, "as_one")
    task_tmp.make(root, "as_two")
    tmp = root / ".clarvis" / "tmp"

    assert removed(root, "as_two", "", in_use=frozenset({"as_two"}), busy=True)
    assert (tmp / "as_two").is_dir()
    assert removed(root, "as_one", first, in_use=frozenset({"as_two"}), busy=True)
    assert not (tmp / "as_one").exists()
    assert (tmp / "as_two").is_dir()

    # Once nothing else holds the project, the parents go with the last folder.
    assert removed(root, "as_two", first)
    assert list(root.iterdir()) == []

    # Busy with no other folder in it — a Clarvis run, say — the empty parents still stay.
    lone = a_project(tmp_path, "lone")
    lone_made = task_tmp.make(lone, "as_one")
    assert removed(lone, "as_one", lone_made, busy=True)
    assert (lone / ".clarvis" / "tmp").is_dir()
    assert not (lone / ".clarvis" / "tmp" / "as_one").exists()
    no_tmp = a_project(tmp_path, "no-tmp-left")
    (no_tmp / ".clarvis").mkdir()
    assert removed(no_tmp, "as_one", ".clarvis,tmp", busy=True)
    assert (no_tmp / ".clarvis").is_dir()


@pytest.mark.parametrize("name", ["..", ".", "", "as_one/..", "../outside", "as one"])
def test_a_name_ravis_could_not_have_given_removes_nothing(tmp_path: Path, name: str) -> None:
    root = a_project(tmp_path)
    made = task_tmp.make(root, "as_one")

    assert task_tmp.make(root, name) == ""
    assert removed(root, name, made)

    assert (root / ".clarvis" / "tmp" / "as_one").is_dir()


def test_a_project_that_is_no_longer_its_own_real_path_is_left_alone(tmp_path: Path) -> None:
    real = a_project(tmp_path, "real")
    made = task_tmp.make(real, "as_one")
    moved = Path(os.path.realpath(tmp_path)) / "moved"
    moved.symlink_to(real, target_is_directory=True)

    assert removed(moved, "as_one", made)

    assert (real / ".clarvis" / "tmp" / "as_one").is_dir()


def test_a_removal_that_fails_is_logged_and_reported_never_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = a_project(tmp_path)
    made = task_tmp.make(root, "as_one")
    stuck = root / ".clarvis" / "tmp" / "as_one" / "stuck"
    stuck.mkdir()
    (stuck / "file.txt").write_text("a command's")
    stuck.chmod(0o500)
    try:
        with caplog.at_level(logging.WARNING, logger="ravis"):
            assert removed(root, "as_one", made) is False
    finally:
        stuck.chmod(0o700)

    assert "couldn't remove the task temp folder as_one" in caplog.text
    assert (root / ".clarvis").is_dir()


# ── The record (migration 12) ──────────────────────────────────────────────


def test_migration_12_records_whose_folder_each_thread_uses_and_rolls_back_to_11(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resume keeps its thread's folder, so the folder's owner is the task that started it."""
    path = tmp_path / "ravis.db"
    with monkeypatch.context() as patched:
        patched.setattr(storage, "MIGRATIONS", storage.MIGRATIONS[:11])
        before = prepare_database(str(path))
        assert before.schema_version == 11
        old = AgentStore(before)
        old.insert_session(a_row("as_started", codex_thread_id="th_1", state="ended",
                                 ended_at="2026-09-14T10:30:00Z"))
        old.insert_session(a_row("as_resumed", codex_thread_id="th_1",
                                 created_at="2026-09-14T11:00:00Z"))
        old.insert_session(a_row("as_failed", state="failed", ended_at="2026-09-14T12:00:00Z",
                                 created_at="2026-09-14T12:00:00Z"))
        before.connection.execute(
            "INSERT INTO agent_thread (thread_id, workspace_root, git_dir, last_used_at) "
            "VALUES ('th_1', '/p', NULL, '2026-09-14T11:00:00Z')")

    store = AgentStore(prepare_database(str(path)))

    assert (tmp_path / "ravis.db.v11.bak").exists()
    assert store.thread_tmp_session("th_1") == "as_started"
    rows = [store.session(name) for name in ("as_started", "as_resumed", "as_failed")]
    assert [(row["tmp_folder_id"], row["tmp_parents_made"]) for row in rows if row] == [
        ("as_started", ""), ("as_started", ""), ("as_failed", "")]
    assert [row["id"] for row in store.ended_with_tmp()] == ["as_started", "as_failed"]
    store.remember_thread("th_1", "/p", None, "as_resumed")
    assert store.thread_tmp_session("th_1") == "as_started", "a thread's folder owner changed"
    assert restore_backup(path, 11) == 11
    columns = {row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(agent_session)")}
    assert "tmp_folder_id" not in columns and "effort" in columns


# ── Through the relay, with the fake Codex ─────────────────────────────────


def test_an_ended_task_s_folder_goes_and_its_record_is_cleared(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nsay done")
        folder = root / ".clarvis" / "tmp" / task.id
        assert folder.is_dir()
        (folder / "tmpcodex").mkdir()
        (folder / "tmpcodex" / "scratch.txt").write_text("what a command left")
        task.reaches("completed_needs_review")
        assert task.settle("idle").json()["state"] == "idle"
        # 0.26.3: a task kept for follow-ups keeps only the folder's record while it rests.
        assert not folder.exists()
        assert rig.service.agents.store.session(task.id)["tmp_folder_id"] == task.id

        ended = relay.call("DELETE", f"{SESSIONS}/{task.id}", token=task.token)

        assert ended.json() == {"state": "ended"}
        assert not (root / ".clarvis").exists()
        assert rig.service.agents.store.session(task.id)["tmp_folder_id"] is None
        assert (root / ".git").is_dir()


def test_without_git_clarvis_s_own_files_keep_its_folder(tmp_path: Path) -> None:
    """`.clarvis` with Clarvis's checkpoint in it stays; one only RAVIS's lock made goes."""
    rig = ready_rig(tmp_path)
    kept, _ = project(rig, "has-a-checkpoint", git=False)
    (kept / ".clarvis").mkdir()
    (kept / ".clarvis" / "task-checkpoint.json").write_text("{}")
    fresh, _ = project(rig, "nothing-yet", git=False)
    with serving(rig) as relay:
        relay.ready()
        for root in (kept, fresh):
            task = relay.started(root, None, text="RELAY\nsay done", task_id=str(uuid.uuid4()))
            assert (root / ".clarvis" / "engine.lock").is_file()
            assert (root / ".clarvis" / "tmp" / task.id).is_dir()
            task.reaches("completed_needs_review")
            assert task.settle("end").json()["state"] == "ended"

    assert sorted(path.name for path in (kept / ".clarvis").iterdir()) == ["task-checkpoint.json"]
    assert not (fresh / ".clarvis").exists()


def test_a_concurrent_task_keeps_its_folder_and_inherits_the_parents(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    tmp = root / ".clarvis" / "tmp"
    with serving(rig) as relay:
        relay.ready()
        first = relay.started(root, git_dir, text="RELAY\nsay one")
        first.reaches("completed_needs_review")
        assert first.settle("idle").json()["state"] == "idle"
        second = relay.started(root, git_dir, text="RELAY\nwait", task_id=str(uuid.uuid4()))
        second.reaches("running")

        ended = relay.call("DELETE", f"{SESSIONS}/{first.id}", token=first.token)

        assert ended.json() == {"state": "ended"}
        assert not (tmp / first.id).exists()
        assert (tmp / second.id).is_dir(), "the running task's folder was removed"
        assert second.post("cancel").status_code == 202
        second.reaches("stopped")
        assert second.settle("idle").json()["state"] == "ended"
        assert not (root / ".clarvis").exists(), "the last task left the parents behind"


def test_a_resumed_task_makes_its_thread_s_folder_again_and_removes_it(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        first = relay.started(root, git_dir, text="RELAY\nsay done")
        first.reaches("completed_needs_review")
        assert first.settle("end").json()["state"] == "ended"
        threads_folder = root / ".clarvis" / "tmp" / first.id
        assert not threads_folder.exists()
        started = rig.server.received("thread/start")[-1]["params"]
        assert started["config"]["shell_environment_policy.set.TMPDIR"] == str(threads_folder)

        thread = first.created["session"]["codex"]["thread_id"]
        start = {"kind": "resume", "thread_id": thread, "catch_up_text": "RELAY\nsay caught up"}
        second = relay.started(root, git_dir, start=start)

        assert threads_folder.is_dir(), "the resumed thread's commands would find no temp folder"
        assert not (root / ".clarvis" / "tmp" / second.id).exists()
        second.reaches("completed_needs_review")
        assert second.settle("end").json()["state"] == "ended"
        assert not (root / ".clarvis").exists()


def test_a_task_whose_start_fails_has_its_folder_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The folder is made just before `thread/start`, so a start Codex refuses would leave it
    behind. The fake Codex has no refusal for a start, so the step is replaced by one that makes the
    folder, as the real one does, and then is refused."""
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)

    async def refused_after_the_folder(self: AgentSession, _start: dict[str, Any]) -> None:
        self._prepare_tmp(self.id)
        assert (root / ".clarvis" / "tmp" / self.id).is_dir()
        raise refusals.runtime_unavailable("Codex didn't start: refused for this test.")

    monkeypatch.setattr(AgentSession, "open_thread", refused_after_the_folder)
    with serving(rig) as relay:
        relay.ready()
        answer = relay.create(root, git_dir)

    assert answer.status_code == 503, answer.text
    assert not (root / ".clarvis").exists()
    assert rig.service.agents.store.ended_with_tmp() == []


def test_the_next_start_removes_what_ended_tasks_still_owe(tmp_path: Path) -> None:
    """A task that ended before 0.26.2, or whose removal failed, is cleaned when RAVIS starts — and
    a folder an unfinished task of the same project uses is not. What still can't be done stays
    owed for the start after: a removal that fails, and parents while a Clarvis run holds the
    project."""
    rig = ready_rig(tmp_path)
    agents = rig.service.agents
    root, _ = project(rig)
    quiet_root, _ = project(rig, "quiet")
    stuck_root, _ = project(rig, "stuck")
    locked_root, _ = project(rig, "locked")

    def task(session_id: str, where: Path, *, folder: str | None = None, made: str = "",
             unfinished: str | None = None) -> Path:
        # An unfinished task names its state: one owed a settle still needs its folder, while a
        # resting (idle) one doesn't (0.26.3, `test_agent_task_tmp_resting.py`).
        state = ({"state": unfinished} if unfinished
                 else {"state": "ended", "ended_at": agents.store.stamp()})
        agents.store.insert_session(a_row(session_id, workspace_root=str(where),
                                          workspace_root_hash=root_hash(where), **state))
        if folder is None:
            return where
        agents.store.record_tmp(session_id, folder, made)
        path = where / ".clarvis" / "tmp" / folder
        (path / "tmpq3x9").mkdir(parents=True, exist_ok=True)
        return path

    owed = task("as_before", root, folder="as_before")
    # A resumed task owed a settle uses the folder of the task that started its thread.
    shared = task("as_starter", root, folder="as_shared")
    task("as_resumer", root, folder="as_shared", unfinished="completed_needs_review")
    # A stopped task with no folder of its own still holds its project's parents.
    quiet = task("as_quiet_old", quiet_root, folder="as_quiet_old", made=".clarvis,tmp")
    task("as_quiet_live", quiet_root, unfinished="stopped")
    stuck = task("as_stuck", stuck_root, folder="as_stuck") / "tmpq3x9"
    (stuck / "left.txt").write_text("a command's")
    stuck.chmod(0o500)
    locked = task("as_locked", locked_root, folder="as_locked", made=".clarvis,tmp")
    clarvis_lock(rig, locked_root)
    try:
        with serving(rig) as relay:
            relay.ready()
    finally:
        stuck.chmod(0o700)

    assert not owed.exists()
    assert shared.is_dir(), "a folder an unfinished resumed task uses was removed"
    assert not quiet.exists() and (quiet_root / ".clarvis" / "tmp").is_dir()
    assert agents.store.session("as_quiet_live")["tmp_parents_made"] == ".clarvis,tmp"
    assert (stuck / "left.txt").exists()
    assert not locked.exists() and (locked_root / ".clarvis" / "tmp").is_dir()
    assert {row["id"] for row in agents.store.ended_with_tmp()} == {"as_stuck", "as_locked"}
