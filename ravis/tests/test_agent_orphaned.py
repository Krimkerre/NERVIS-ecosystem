"""A resting task whose project has been removed is ended, at start and hourly.

Found on 16 September 2026: the live-test projects went to the Trash and three `idle` Codex
tasks stayed in RAVIS, reloaded at every start with no window left to end them and no owner
control that could. RAVIS now ends such a task itself, the way a window's `DELETE` would.

Built on `test_agent_reconcile.py`'s restart rig: a previous RAVIS's rows, then `AgentSessions`
started on them with a Codex that isn't running.
"""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

from tests.test_agent_reconcile import (
    FAST,
    FakeHost,
    FakeTable,
    previous_ravis,
    reopened,
    restart,
    run,
    session_row,
    settings_for,
    workspace,
)

from ravis.agent.sessions import AgentSessions
from ravis.agent.store import AgentStore
from ravis.storage.database import prepare_database


def _state(path: Path, session_id: str) -> tuple[str, bool]:
    row = reopened(path).session(session_id)
    assert row is not None
    return str(row["state"]), bool(row["ended_at"])


def test_a_resting_task_whose_project_was_removed_is_ended_at_start(tmp_path: Path) -> None:
    gone, gone_git = workspace(tmp_path, "removed-project")
    kept, kept_git = workspace(tmp_path, "kept-project")

    def build(store: AgentStore) -> None:
        store.insert_session(session_row("as_gone", gone, gone_git, "idle"))
        store.insert_session(session_row("as_kept", kept, kept_git, "idle"))

    path = previous_ravis(tmp_path, build)
    shutil.rmtree(gone)
    run(restart(path, settings_for(tmp_path), FakeTable([])))

    assert _state(path, "as_gone") == ("ended", True)
    assert _state(path, "as_kept") == ("idle", False), "a project still there keeps its task"


def test_a_project_whose_parent_is_missing_is_not_taken_for_removed(tmp_path: Path) -> None:
    """A disk that isn't mounted loses the whole path; its tasks wait for it to come back."""
    root, git_dir = workspace(tmp_path, "on-a-disk")
    elsewhere = tmp_path / "unmounted" / "on-a-disk"

    def build(store: AgentStore) -> None:
        store.insert_session({**session_row("as_disk", root, git_dir, "idle"),
                              "workspace_root": str(elsewhere)})

    path = previous_ravis(tmp_path, build)
    run(restart(path, settings_for(tmp_path), FakeTable([])))

    assert _state(path, "as_disk") == ("idle", False)


def test_a_task_with_something_owed_is_left_alone(tmp_path: Path) -> None:
    """Only a resting task: one waiting for its settle is the window's or the reconciler's."""
    root, git_dir = workspace(tmp_path, "owed-project")

    def build(store: AgentStore) -> None:
        store.insert_session(session_row("as_owed", root, git_dir, "completed_needs_review"))

    path = previous_ravis(tmp_path, build)
    shutil.rmtree(root)
    run(restart(path, settings_for(tmp_path), FakeTable([])))

    assert _state(path, "as_owed") == ("completed_needs_review", False)


def test_a_project_removed_while_ravis_runs_is_ended_on_the_hourly_pass(tmp_path: Path) -> None:
    root, git_dir = workspace(tmp_path, "removed-later")

    def build(store: AgentStore) -> None:
        store.insert_session(session_row("as_later", root, git_dir, "idle"))

    path = previous_ravis(tmp_path, build)
    table = FakeTable([])
    sessions = AgentSessions(
        FakeHost(), prepare_database(str(path)), settings_for(tmp_path),
        emit=lambda *_args, **_kwargs: None,
        # Retention due on every tick, so one tick is the hourly pass.
        timings=dataclasses.replace(FAST, retention_seconds=0.0),
        take_snapshot=table.snapshot, kill=table.kill,
    )

    async def remove_then_tick() -> None:
        assert _state(path, "as_later") == ("idle", False), "still there at start"
        shutil.rmtree(root)
        await sessions.tick()

    run(sessions, remove_then_tick)

    assert _state(path, "as_later") == ("ended", True)
