"""M17 — unified diagnostics (§11.2, §11.3).

The spans answer *what happened*. This answers *what was going on*, and the
whole design is §11.3's rule that a service-reported fact, a NERVIS observation
and an operator inference are three different claims — so every linked thing
carries how it was linked, and a caller cannot present a coincidence as a
finding without ignoring a field.

M17's exit clause about partial traces gets the most attention, because the
thing being diagnosed is usually the thing that is broken: a unified view that
failed whole when one input was missing would be useless exactly when needed.
"""

from __future__ import annotations

from typing import Any

from nervis import unified


def a_trace(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "trace_id": "t" * 32, "started": 1000.0, "window_ms": 500.0,
        "spans": [], "warnings": [],
        "events": [{"event_type": "ravis.route.selected",
                    "data": {"selected": "qwen/qwen3-1.7b"}}],
    }
    base.update(over)
    return base


def a_service(key: str, state: str) -> dict[str, Any]:
    return {"key": key, "state": state}


def a_transition(service: str, to: str, at: str) -> dict[str, Any]:
    return {"event_type": "nervis.service.state_changed", "occurred_at": at,
            "data": {"service": service, "to": to}}


# ── The health overlay is about *then*, not now ─────────────────────────────


def test_health_is_reconstructed_at_the_moment_of_the_trace() -> None:
    """Showing today's health beside yesterday's failure is how somebody
    concludes the failure is unexplained."""
    transitions = [
        a_transition("ravis", "unreachable", "2026-09-02T10:00:00Z"),
        a_transition("ravis", "healthy", "2026-09-02T12:00:00Z"),
    ]
    from nervis.traces import _moment
    when = _moment("2026-09-02T11:00:00Z")

    overlay = unified.health_at([a_service("ravis", "healthy")], transitions, when)

    assert overlay[0]["state_then"] == "unreachable"
    assert overlay[0]["state_now"] == "healthy"
    assert overlay[0]["changed_since"] is True


def test_a_service_with_no_recorded_transition_is_unknown_not_healthy() -> None:
    """`unknown` and `healthy` are different claims, and only one is supported.

    NERVIS started five minutes ago and has recorded nothing about a service it
    has been watching happily ever since — that is no evidence about an hour
    ago, and reporting the current state as the historical one invents a
    reading.
    """
    overlay = unified.health_at([a_service("sirvis", "healthy")], [], 1000.0)

    assert overlay[0]["state_then"] == "unknown"
    assert overlay[0]["known"] is False


def test_a_transition_after_the_trace_does_not_describe_it() -> None:
    """A service that broke *after* the request did not break it."""
    later = [a_transition("ravis", "unreachable", "2026-09-02T23:00:00Z")]
    from nervis.traces import _moment

    overlay = unified.health_at([a_service("ravis", "unreachable")], later,
                                _moment("2026-09-02T09:00:00Z"))
    assert overlay[0]["state_then"] == "unknown"


# ── Log correlation, and the strength of the claim ──────────────────────────


def test_a_line_carrying_the_trace_id_is_linked_as_a_fact() -> None:
    trace_id = "t" * 32
    linked = unified.correlate_logs(
        [{"message": "ordinary"}, {"trace_id": trace_id, "message": "the one"}],
        trace_id, 1000.0,
    )
    assert linked.how == unified.BY_TRACE
    assert [one["message"] for one in linked.items] == ["the one"]


def test_lines_matched_only_by_time_say_so_in_the_link() -> None:
    """A line written in the same second as a request is evidence about the
    second, not about the request. Presenting the two identically turns a
    coincidence into a finding."""
    linked = unified.correlate_logs(
        [{"message": "something else entirely"}], "t" * 32, 1000.0,
    )
    assert linked.how == unified.BY_WINDOW
    assert "may belong to something else" in linked.reason


def test_an_exact_hit_wins_over_the_window() -> None:
    """Otherwise the strong link is diluted by whatever else was happening."""
    trace_id = "t" * 32
    linked = unified.correlate_logs(
        [{"message": "noise"}, {"message": f"handled {trace_id}"}, {"message": "more noise"}],
        trace_id, 1000.0,
    )
    assert linked.how == unified.BY_TRACE
    assert len(linked.items) == 1


# ── Runtime evidence, where available ──────────────────────────────────────


def test_a_model_with_no_measurement_says_so_rather_than_borrowing_one() -> None:
    """M17's "where available" is a statement about how often this is empty.

    §13 keeps SIRVIS's identity for a build whole precisely so a consumer cannot
    decide two builds are the same thing — so an absent measurement is never
    filled in from a similar one.
    """
    found = unified.runtime_context(["some-hosted-model"], None)
    assert found["available"] is False
    assert "published no evidence" in found["reason"]


def test_evidence_is_carried_when_there_is_some() -> None:
    found = unified.runtime_context(
        ["qwen/qwen3-1.7b"], {"qwen/qwen3-1.7b": {"evidence_type": "PARTIALLY_MEASURED"}})
    assert found["available"] is True
    assert "qwen/qwen3-1.7b" in found["evidence"]


def test_the_models_are_read_from_the_events_that_name_them() -> None:
    assert unified.models_in(a_trace()) == ["qwen/qwen3-1.7b"]
    assert unified.models_in(a_trace(events=[])) == []


# ── A broken link still produces a partial trace ────────────────────────────


def test_every_section_is_independently_absent_rather_than_fatal() -> None:
    """M17's exit clause, and the ordinary case: the thing being diagnosed is
    usually the thing that is broken."""
    found = unified.unify(a_trace(), [], [], [], evidence=None)

    assert found["trace"]["trace_id"] == "t" * 32
    assert found["health"] == []
    assert found["logs"]["items"] == []
    assert found["runtime"]["available"] is False


def test_a_trace_carrying_warnings_is_reported_as_partial() -> None:
    """The waterfall already marks a missing span; this says so at the top, so
    a reader knows the picture is incomplete before reading it as complete."""
    assert unified.unify(a_trace(warnings=["a peer published nothing"]),
                         [], [], [])["partial"] is True
    assert unified.unify(a_trace(), [], [], [])["partial"] is False


def test_a_trace_with_no_start_cannot_take_a_window_and_says_that() -> None:
    """Rather than taking a window from nothing and calling it correlation."""
    linked = unified.correlate_logs([{"message": "x"}], "t" * 32, None)
    assert linked.items == []
    assert "no start time" in linked.reason
