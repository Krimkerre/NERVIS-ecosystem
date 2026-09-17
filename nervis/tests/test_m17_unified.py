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


def stamp(seconds: float) -> str:
    """Epoch seconds as a log line's `time`, the shape ecosystem-protocol 0.2.3 writes."""
    from datetime import datetime, timezone

    moment = datetime.fromtimestamp(seconds, timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


STARTED = 1_789_565_917.0  # 16 September 2026, 13:38:37 UTC


def test_lines_matched_only_by_time_say_so_in_the_link() -> None:
    """A line written in the same second as a request is evidence about the
    second, not about the request. Presenting the two identically turns a
    coincidence into a finding."""
    linked = unified.correlate_logs(
        [{"time": stamp(STARTED + 0.5), "message": "something else entirely"}],
        "t" * 32, STARTED,
    )
    assert linked.how == unified.BY_WINDOW
    assert "may belong to something else" in linked.reason
    assert "within 2 s" in linked.reason


def test_only_lines_written_near_the_trace_are_offered() -> None:
    """17 September 2026: the fallback offered each log's newest lines as "written during the same
    window" for a trace from the day before. A line counts only if its own time is within the
    window around the trace — before its start, through its end, and after."""
    lines = [
        {"time": stamp(STARTED - 2.5), "message": "too early"},
        {"time": stamp(STARTED - 1.5), "message": "just before"},
        {"time": stamp(STARTED + 2.9), "message": "during a 3 s trace"},
        {"time": stamp(STARTED + 4.5), "message": "just after it ended"},
        {"time": stamp(STARTED + 5.5), "message": "too late"},
        {"time": stamp(STARTED + 86_400), "message": "a day later"},
        {"message": "no time at all"},
        {"time": "not a time", "message": "an unreadable time"},
    ]
    linked = unified.correlate_logs(lines, "t" * 32, STARTED, STARTED + 3.0)
    assert [one["message"] for one in linked.items] == [
        "just before", "during a 3 s trace", "just after it ended",
    ]
    only_start = unified.correlate_logs(lines, "t" * 32, STARTED)
    assert [one["message"] for one in only_start.items] == ["just before"]


def test_an_empty_window_says_why_it_is_empty() -> None:
    """None of the lines read has a time (older logs), or none falls near the trace: two different
    answers, and neither lists lines. No lines at all is the second, not the first."""
    nothing = unified.correlate_logs([], "t" * 32, STARTED)
    assert nothing.items == []
    assert "none of the lines read was written within 2 s" in nothing.reason
    untimed = unified.correlate_logs([{"message": "old"}], "t" * 32, STARTED)
    assert untimed.items == []
    assert "says when it was written" in untimed.reason
    far = unified.correlate_logs(
        [{"time": stamp(STARTED + 86_400), "message": "tomorrow"}], "t" * 32, STARTED,
    )
    assert far.items == []
    assert "none of the lines read was written within 2 s" in far.reason


def test_the_unified_view_passes_the_trace_s_end_to_the_window() -> None:
    lines = [{"time": stamp(STARTED + 3.5), "message": "near the end of a 3 s trace"}]
    view = unified.unify(
        {"trace_id": "t" * 32, "started": STARTED, "window_ms": 3000.0, "spans": [],
         "events": [], "warnings": []},
        [], [], lines,
    )
    assert [one["message"] for one in view["logs"]["items"]] == ["near the end of a 3 s trace"]


def test_the_log_reader_keeps_only_lines_written_in_the_window(tmp_path: Any) -> None:
    """The I/O half: `logs.read(between=…)` searches back and keeps lines by their own time."""
    import json as _json

    from nervis import logs

    written = [
        {"time": stamp(STARTED - 100), "level": "INFO", "logger": "x", "message": "earlier"},
        {"time": stamp(STARTED + 1), "level": "INFO", "logger": "x", "message": "inside"},
        {"level": "INFO", "logger": "x", "message": "no time"},
        *({"time": stamp(STARTED + 500 + n), "level": "INFO", "logger": "x",
           "message": f"later {n}"} for n in range(50)),
    ]
    (tmp_path / "ravis.log").write_text("\n".join(_json.dumps(one) for one in written) + "\n")
    found = logs.read(tmp_path, "ravis", limit=8, between=unified.window_around(STARTED))
    assert [one["message"] for one in found["items"]] == ["inside"]
    assert found["filtered"] is True


def test_an_exact_hit_wins_over_the_window() -> None:
    """Otherwise the strong link is diluted by whatever else was happening."""
    trace_id = "t" * 32
    linked = unified.correlate_logs(
        [{"message": "noise"}, {"message": f"handled {trace_id}"}, {"message": "more noise"}],
        trace_id, 1000.0,
    )
    assert linked.how == unified.BY_TRACE
    assert len(linked.items) == 1


def test_reading_a_trace_is_not_part_of_it() -> None:
    """NERVIS's access log names the trace id in every request to view that trace, and those
    lines were shown as the trace's own — found on 17 September 2026 in a day-old trace whose
    only "correlated" lines were the dashboard's reads of it. A line carrying the id as its own
    trace field, or naming it any other way, still counts."""
    trace_id = "7886440ed00d054668280d32321ca6e9"
    lookups = [
        {"message": f'127.0.0.1:52913 - "GET /api/v1/traces/{trace_id} HTTP/1.1" 200'},
        {"message": f'127.0.0.1:52915 - "GET /api/v1/traces/{trace_id}/unified HTTP/1.1" 200'},
        {"message": f'127.0.0.1:1 - "HEAD /api/v1/events?trace_id={trace_id} HTTP/1.1" 200'},
    ]
    real = [
        {"trace_id": trace_id, "message": f'"GET /api/v1/traces/{trace_id} HTTP/1.1" 200'},
        {"message": f"route selected for {trace_id}"},
        {"message": f'"POST /api/v1/chat?trace={trace_id} HTTP/1.1" 200'},
    ]
    linked = unified.correlate_logs([*lookups, *real], trace_id, 1000.0)
    assert linked.how == unified.BY_TRACE
    assert linked.items == real

    only_lookups = unified.correlate_logs(lookups, trace_id, 1000.0)
    assert only_lookups.how == unified.BY_WINDOW
    assert only_lookups.items == [], "and they are not offered as the window's lines either"


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


def test_the_route_offers_lines_from_the_trace_s_moment_not_the_newest(tmp_path: Any) -> None:
    """End to end through `GET /api/v1/traces/{id}/unified`, with a real log file whose newest
    lines are from long after the trace — the case that showed today's lines for yesterday's."""
    import json as _json

    from fastapi.testclient import TestClient

    from nervis.app import create_app
    from nervis.config import Settings

    trace_id = "ab" * 16
    run = tmp_path / "run"
    run.mkdir()
    lines = [
        {"time": stamp(STARTED + 1), "level": "INFO", "logger": "x", "message": "while it ran"},
        # Somebody opened this trace before: the id is in the line, and it is not part of it.
        {"time": stamp(STARTED + 7200), "level": "INFO", "logger": "uvicorn.access",
         "message": f'127.0.0.1:1 - "GET /api/v1/traces/{trace_id}/unified HTTP/1.1" 200'},
        *({"time": stamp(STARTED + 3600 + n), "level": "INFO", "logger": "x",
           "message": f"an hour later {n}"} for n in range(20)),
    ]
    (run / "ravis.log").write_text("\n".join(_json.dumps(one) for one in lines) + "\n")
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"), workspace_path=str(tmp_path),
        run_directory=str(run), _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        client.app.state.hub.ingest({  # type: ignore[attr-defined]
            "event_id": "trace-event-1", "event_type": "ravis.route.selected",
            "event_version": "1.0.0", "occurred_at": stamp(STARTED),
            "source": {"service_type": "ravis", "service_id": "ravis-1"},
            "trace_id": trace_id, "severity": "info", "data": {},
        })
        logs = client.get(f"/api/v1/traces/{trace_id}/unified").json()["logs"]
    assert logs["how"] == unified.BY_WINDOW
    assert [one["message"] for one in logs["items"]] == ["while it ran"]
