"""Route decisions outlive the process that made them.

Kept in memory only until 18 September 2026, when the owner chose to store the
whole explanation. The cases here are what "stored" has to mean to be worth the
table: a decision found again after a restart, found again after the in-memory
bound has moved past it, carrying what happened when it ran, and deleted on
time and under a ceiling so the cost stays finite.
"""

from __future__ import annotations

import json

from ravis.api.management.decisions import DecisionLog
from ravis.routing.explain import ExcludedCandidate, RouteDecision
from ravis.storage import prepare_database


def _decision() -> RouteDecision:
    """A decision with every part of an explanation filled in."""
    return RouteDecision(
        requested="ravis/auto",
        pool_id="coders",
        selected="coder-a",
        fallbacks=["coder-b"],
        reason="cheapest build that passes the pool's requirements",
        considered=["coder-a", "coder-b", "coder-c"],
        excluded=[
            ExcludedCandidate(model="coder-c", reasons=["no tool support"]),
            ExcludedCandidate(model="coder-d", reasons=["circuit open"], circuit_open=True),
        ],
        requirements=["tools"],
        unverified=["context length not measured"],
    )


def test_a_decision_is_found_again_after_a_restart(tmp_path) -> None:
    """The reason the table exists.

    A trace links to a decision by id. Before this, following that link the next
    morning reached a 404 whatever the id was, because the process holding the
    explanation had been restarted — and the link is offered by NERVIS on every
    route event.
    """
    path = str(tmp_path / "ravis.db")
    first = DecisionLog(database=prepare_database(path))
    made = first.record(_decision(), application_id="clarvis", request_id="req-1",
                        trace_id="trace-1")

    restarted = DecisionLog(database=prepare_database(path))
    found = restarted.find(made.decision_id)

    assert found is not None, "the decision did not survive the restart"
    assert found.as_dict() == made.as_dict()
    # The published shape leaves out which exclusions lift by themselves, so the
    # equality above would hold even if that were lost. It is a different fact
    # about the pool and it is checked on its own.
    resting = [entry.model for entry in found.decision.excluded if entry.circuit_open]
    assert resting == ["coder-d"]


def test_a_decision_past_the_memory_bound_is_still_served(tmp_path) -> None:
    """Memory is a working set, not the record.

    With a bound of two, the first decision is out of memory after the third.
    It is still an answerable question.
    """
    log = DecisionLog(capacity=2, database=prepare_database(str(tmp_path / "ravis.db")))
    first = log.record(_decision(), application_id="clarvis", request_id="req-1")
    for number in range(2, 6):
        log.record(_decision(), application_id="clarvis", request_id=f"req-{number}")

    assert log.find(first.decision_id) is not None
    # And a page deeper than memory holds is answered from the table rather than
    # stopping at whatever happened to be resident.
    assert len(log.recent(10)) == 5
    assert [entry.request_id for entry in log.recent(10)][:2] == ["req-5", "req-4"]


def test_what_happened_when_it_ran_reaches_the_table(tmp_path) -> None:
    """The attempt chain is written long after the decision, sometimes minutes.

    A stored decision that says what was chosen but not what happened when it
    was called keeps the half that matters least during an incident.
    """
    path = str(tmp_path / "ravis.db")
    log = DecisionLog(database=prepare_database(path))
    made = log.record(_decision(), application_id="clarvis", request_id="req-1")
    made.attempts = {"attempts": [{"target": "coder-a", "outcome": "succeeded"}]}
    made.execution_path = "TRANSPARENT_OPENAI"
    made.stored()

    found = DecisionLog(database=prepare_database(path)).find(made.decision_id)

    assert found is not None
    assert found.attempts == made.attempts
    assert found.execution_path == "TRANSPARENT_OPENAI"


def test_a_decision_still_running_is_stored_as_unfinished(tmp_path) -> None:
    """`None` attempts means "never ran to completion" and must survive as that.

    Stored as an empty chain instead, it would read as a request that finished
    without trying anything — the runbook §14.4 distinction, in a column.
    """
    path = str(tmp_path / "ravis.db")
    log = DecisionLog(database=prepare_database(path))
    made = log.record(_decision(), application_id="clarvis", request_id="req-1")

    found = DecisionLog(database=prepare_database(path)).find(made.decision_id)

    assert found is not None
    assert found.attempts is None


def test_decisions_are_deleted_at_the_end_of_the_window(tmp_path) -> None:
    """Retention, on the clock the log is given rather than the wall's."""
    now = [1_000_000.0]
    path = str(tmp_path / "ravis.db")
    log = DecisionLog(database=prepare_database(path), retention_seconds=60.0,
                      clock=lambda: now[0])
    old = log.record(_decision(), application_id="clarvis", request_id="req-old")
    now[0] += 61.0
    fresh = log.record(_decision(), application_id="clarvis", request_id="req-new")

    assert log.enforce_retention() == 1
    # Asked of a log that has not cached either of them, so the answer comes
    # from the table rather than from memory that outlived the row.
    reader = DecisionLog(database=prepare_database(path), retention_seconds=60.0,
                         clock=lambda: now[0])
    assert reader.find(old.decision_id) is None
    assert reader.find(fresh.decision_id) is not None


def test_an_explanation_is_stored_packed(tmp_path) -> None:
    """Measured, not assumed: a real explanation is 87 KB of repeated ids.

    The first ceiling this table shipped with allowed several gigabytes, because
    nobody had looked at a row. Deflate takes the measured rows twelve to one,
    and the column holds bytes so that saving cannot quietly stop happening.
    """
    path = str(tmp_path / "ravis.db")
    log = DecisionLog(database=prepare_database(path))
    wide = _decision()
    wide.considered = [f"vendor/model-{number}" for number in range(500)]
    made = log.record(wide, application_id="clarvis", request_id="req-1")

    stored = prepare_database(path).connection.execute(
        "SELECT explanation FROM route_decision WHERE decision_id = ?", (made.decision_id,)
    ).fetchone()["explanation"]
    assert isinstance(stored, bytes), "the explanation was written as text"
    assert len(stored) * 4 < len(json.dumps(wide.as_dict())), "packing bought almost nothing"

    # And it is still the decision it was.
    found = DecisionLog(database=prepare_database(path)).find(made.decision_id)
    assert found is not None
    assert found.decision.considered == wide.considered


def test_a_busy_night_is_bounded_by_the_row_ceiling(tmp_path) -> None:
    """The window bounds a quiet month; the ceiling bounds a runaway loop.

    The soak test makes twenty-eight thousand requests in a night, every one of
    them inside any sensible retention window.
    """
    path = str(tmp_path / "ravis.db")
    log = DecisionLog(database=prepare_database(path), row_ceiling=10)
    for number in range(25):
        log.record(_decision(), application_id="clarvis", request_id=f"req-{number}")

    assert log.enforce_retention() == 15
    kept = DecisionLog(database=prepare_database(path), row_ceiling=10).recent(50)
    assert len(kept) == 10
    assert kept[0].request_id == "req-24", "the ceiling kept the oldest rows, not the newest"
