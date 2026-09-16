"""M9 — the Clarvis diagnostics surface (`CLARVIS.md` §6.3, §6.4, §6.6, §6.7).

The folds are pure and are tested as folds: a task's state is whatever its own
events say, and the interesting cases are the ones where the events are
incomplete, out of order, or belong to somebody else's editor window.

**Isolation gets a test of its own with a live hub**, because §6.6's rule is
about a query and not about a function — a fold that is perfectly correct still
shows the wrong window if the rows handed to it came from the wrong one.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis import clarvis
from nervis.app import create_app
from nervis.config import Settings


@pytest.fixture()
def client(tmp_path: Any) -> Any:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"),
        workspace_path=str(tmp_path),
        _env_file=None,
    )
    with TestClient(create_app(settings)) as made:
        yield made


def event(event_type: str, occurred_at: str, **data: Any) -> dict[str, Any]:
    return {"event_type": event_type, "occurred_at": occurred_at, "data": data}


# ── Tasks ───────────────────────────────────────────────────────────────────


def test_a_task_that_started_and_did_not_finish_is_running() -> None:
    """Nothing said otherwise, and that is the only claim the evidence supports.

    Deliberately not "stalled": NERVIS was told a task began and was told
    nothing since, which is a different fact from knowing it is stuck.
    """
    found = clarvis.tasks([event(clarvis.TASK_STARTED, "2026-09-02T10:00:00Z", task_id="t1")])
    assert found == [{"task_id": "t1", "state": "running", "stage": "",
                      "started_at": "2026-09-02T10:00:00Z", "completed_at": "",
                      "updated_at": "2026-09-02T10:00:00Z"}]


def test_a_task_appears_once_however_many_events_it_has() -> None:
    """Started and completed are one task, not two rows."""
    found = clarvis.tasks([
        event(clarvis.TASK_STARTED, "2026-09-02T10:00:00Z", task_id="t1"),
        event(clarvis.TASK_COMPLETED, "2026-09-02T10:00:09Z", task_id="t1", outcome="ok"),
    ])
    assert len(found) == 1
    assert found[0]["state"] == "completed"
    assert found[0]["outcome"] == "ok"


def test_an_outcome_is_copied_only_when_clarvis_sent_one() -> None:
    """§6.3: unknown values stay unknown, and a result class is a value."""
    found = clarvis.tasks([
        event(clarvis.TASK_STARTED, "2026-09-02T10:00:00Z", task_id="t1"),
        event(clarvis.TASK_COMPLETED, "2026-09-02T10:00:09Z", task_id="t1"),
    ])
    assert found[0]["state"] == "completed"
    assert "outcome" not in found[0]


def test_a_completion_for_a_task_nobody_saw_start_is_still_a_task() -> None:
    """The window may have registered mid-run, and half a record beats none."""
    found = clarvis.tasks([
        event(clarvis.TASK_COMPLETED, "2026-09-02T10:00:09Z", task_id="t9", outcome="failed"),
    ])
    assert found[0]["state"] == "completed"
    assert found[0]["started_at"] == ""


def test_an_event_with_no_task_id_is_ignored_rather_than_invented() -> None:
    found = clarvis.tasks([event(clarvis.TASK_STARTED, "2026-09-02T10:00:00Z")])
    assert found == []


# ── The agent run ───────────────────────────────────────────────────────────


def test_a_step_does_not_end_a_run() -> None:
    """The newest event is not the outcome.

    Treating it as one would end every run at whatever it last did, which reads
    as "completed: step" on a run still going.
    """
    run = clarvis.agent_run([
        event(clarvis.AGENT_STARTED, "2026-09-02T10:00:00Z"),
        event("clarvis.agent.step", "2026-09-02T10:00:01Z"),
        event("clarvis.agent.step", "2026-09-02T10:00:02Z"),
    ])
    assert run["state"] == "running"
    assert run["steps"] == 2


def test_two_runs_in_one_window_do_not_pool_their_steps() -> None:
    """A second run replaces the first rather than merging into it."""
    run = clarvis.agent_run([
        event(clarvis.AGENT_STARTED, "2026-09-02T10:00:00Z"),
        event("clarvis.agent.step", "2026-09-02T10:00:01Z"),
        event("clarvis.agent.completed", "2026-09-02T10:00:02Z"),
        event(clarvis.AGENT_STARTED, "2026-09-02T10:05:00Z"),
        event("clarvis.agent.step", "2026-09-02T10:05:01Z"),
    ])
    assert run["steps"] == 1
    assert run["state"] == "running"
    assert run["started_at"] == "2026-09-02T10:05:00Z"


def test_steps_before_any_run_started_are_not_a_run() -> None:
    """A window that registered mid-run reports no run rather than a partial one.

    Counting orphan steps would produce a run with no start time, which the
    screen would draw as though NERVIS had watched it begin.
    """
    assert clarvis.agent_run([event("clarvis.agent.step", "2026-09-02T10:00:01Z")]) == {}


def test_a_failed_run_says_failed_rather_than_ended() -> None:
    run = clarvis.agent_run([
        event(clarvis.AGENT_STARTED, "2026-09-02T10:00:00Z"),
        event("clarvis.agent.failed", "2026-09-02T10:00:04Z"),
    ])
    assert run["state"] == "failed"
    assert run["ended_at"] == "2026-09-02T10:00:04Z"


# ── The approval gate ───────────────────────────────────────────────────────


def test_a_requested_gate_is_shown_with_its_category() -> None:
    """§6.7 permits displaying *that* a gate awaits the user."""
    waiting = clarvis.gate([event(clarvis.GATE_REQUESTED, "2026-09-02T10:00:00Z",
                                  category="command")])
    assert waiting["category"] == "command"


def test_a_resolved_gate_stops_being_shown() -> None:
    """A stale "waiting for you" is how somebody learns to ignore the real one."""
    waiting = clarvis.gate([
        event(clarvis.GATE_REQUESTED, "2026-09-02T10:00:00Z", category="command"),
        event(clarvis.GATE_RESOLVED, "2026-09-02T10:00:20Z"),
    ])
    assert waiting == {}


def test_nothing_in_this_module_can_resolve_a_gate() -> None:
    """§6.7 by construction rather than by policy.

    `CLARVIS.md` §6.7 says an agent asked to add such control must stop. The
    enforcement here is that the module imports nothing that could make a
    request, so no code path exists to resolve anything — asserted rather than
    trusted, because the next person to add a convenience import is what this
    guards against.
    """
    import ast
    from pathlib import Path

    source = Path(clarvis.__file__).read_text(encoding="utf-8")
    imported = {
        node.module.split(".")[0] if isinstance(node, ast.ImportFrom) and node.module else
        node.names[0].name.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
    }
    assert imported <= {"__future__", "typing"}, f"clarvis.py reached for {imported}"


# ── Isolation, against a real hub ───────────────────────────────────────────


def test_one_windows_events_never_appear_under_another(client: TestClient) -> None:
    """§6.6, and the reason the filter is `json_extract` rather than a substring.

    Both windows publish the same event family. Selecting on a substring of the
    envelope would match either, and the screen would show one editor's agent
    run under the other editor's name.
    """
    hub = client.app.state.hub  # type: ignore[attr-defined]
    for window, task in (("win-a", "t-a"), ("win-b", "t-b")):
        hub.ingest({
            "event_id": f"ev-{window}",
            "event_type": clarvis.TASK_STARTED,
            "occurred_at": "2026-09-02T10:00:00Z",
            "source": {"service_type": "clarvis", "instance_id": window},
            "data": {"task_id": task},
        })

    only_a = hub.query(instance_id="win-a", latest=True)
    assert [e["data"]["task_id"] for e in only_a] == ["t-a"]
    assert clarvis.tasks(only_a)[0]["task_id"] == "t-a"


def test_a_window_that_published_nothing_gets_nothing(client: TestClient) -> None:
    """Not the hub's recent traffic.

    An unfiltered fallback is how a quiet window would show somebody else's
    editor as its own — the failure §6.6 exists to prevent, and the one an empty
    result set makes impossible.
    """
    hub = client.app.state.hub  # type: ignore[attr-defined]
    hub.ingest({
        "event_id": "ev-loud",
        "event_type": clarvis.TASK_STARTED,
        "occurred_at": "2026-09-02T10:00:00Z",
        "source": {"service_type": "clarvis", "instance_id": "loud"},
        "data": {"task_id": "t1"},
    })
    assert hub.query(instance_id="quiet", latest=True) == []


def test_diagnostics_for_an_unknown_window_is_a_404(client: TestClient) -> None:
    """An expired lease and an id that never existed are both "not held here"."""
    found = client.get("/api/v1/registry/instances/clarvis/nobody/diagnostics")
    assert found.status_code == 404
