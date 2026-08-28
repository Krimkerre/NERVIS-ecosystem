"""M7 — distributed tracing (§11.2).

M7's exit: *"One RAVIS request forms a trace; multiple events correlate; missing
spans render gracefully; filters work."*

The rule that shapes every test here is §11.2's, stated twice in one section:
**mark missing spans and clock skew — never synthesize a span as fact.** A
waterfall is a drawing of durations, and a drawing is exactly where an invented
number stops looking invented: a bar is a bar whether it was measured or guessed.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from nervis.app import create_app
from nervis.config import Settings
from nervis.events import Hub
from nervis.storage import prepare_database
from nervis.traces import assemble, summarise

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


def event(service: str, at: str, **extra: Any) -> dict[str, Any]:
    return {
        "event_id": extra.pop("event_id", f"{service}-{at}"),
        "event_type": extra.pop("event_type", f"{service}.something"),
        "event_version": "1.0.0",
        "occurred_at": at,
        "source": {"service_type": service},
        "trace_id": TRACE,
        "severity": extra.pop("severity", "info"),
        "data": {},
        **extra,
    }


def a_hub() -> Hub:
    return Hub(prepare_database(":memory:"))


# ── Never synthesize a span as fact ────────────────────────────────────────


def test_one_event_is_a_point_and_gets_no_duration() -> None:
    """A single event is a moment, not an interval.

    Giving it width would be the invented bar §11.2 forbids — and a bar is a bar
    whether it was measured or guessed.
    """
    trace = assemble(TRACE, [event("ravis", "2026-08-26T10:00:00.000Z")])

    span = trace.spans[0]
    assert span.duration_ms is None
    assert span.as_dict()["is_point"] is True


def test_two_events_give_a_real_duration() -> None:
    trace = assemble(TRACE, [
        event("ravis", "2026-08-26T10:00:00.000Z"),
        event("ravis", "2026-08-26T10:00:00.250Z"),
    ])

    assert trace.spans[0].duration_ms == 250.0
    assert trace.spans[0].as_dict()["is_point"] is False


def test_an_unparseable_timestamp_does_not_become_now() -> None:
    """Substituting the current time would place a span at the moment somebody
    opened the screen, which is the most confidently wrong a timeline can be."""
    trace = assemble(TRACE, [event("ravis", "whenever")])

    assert trace.spans[0].started is None
    assert trace.spans[0].duration_ms is None


def test_a_service_with_no_events_gets_no_span() -> None:
    """A hole in the record is the finding, not something to fill.

    RAVIS appearing with no caller means the caller does not publish events or
    its span was lost. Both are worth saying; neither is worth drawing.
    """
    trace = assemble(TRACE, [event("ravis", "2026-08-26T10:00:00.000Z")])

    assert [s.service for s in trace.spans] == ["ravis"]
    assert any("no calling service recorded" in w for w in trace.warnings)


def test_clock_skew_is_reported_and_never_corrected() -> None:
    """Correcting it would delete the only evidence the two clocks differ.

    A waterfall that has been quietly straightened is worse than one that
    visibly cannot be — the second makes somebody go and look.
    """
    hub = a_hub()
    hub.ingest(event("ravis", "2030-01-01T00:00:00.000Z"))
    stored = hub.query()

    trace = assemble(TRACE, stored)

    assert any("clock is ahead" in w for w in trace.warnings)
    # And the span is where the producer said, not where NERVIS would prefer.
    assert trace.spans[0].started is not None
    assert trace.spans[0].started > 1_800_000_000


# ── Correlation ────────────────────────────────────────────────────────────


def test_events_from_several_services_correlate_into_one_trace() -> None:
    """M7's exit: multiple events correlate."""
    trace = assemble(TRACE, [
        event("nervis", "2026-08-26T10:00:00.000Z"),
        event("ravis", "2026-08-26T10:00:00.100Z"),
        event("ravis", "2026-08-26T10:00:00.400Z"),
        event("sirvis", "2026-08-26T10:00:00.200Z"),
    ])

    assert [s.service for s in trace.spans] == ["nervis", "ravis", "sirvis"]
    assert [s.events for s in trace.spans] == [1, 2, 1]
    assert trace.window_ms == 400.0


def test_lanes_are_ordered_by_the_path_a_request_takes() -> None:
    """Not by arrival: a waterfall read top-to-bottom should follow the call."""
    trace = assemble(TRACE, [
        event("sirvis", "2026-08-26T10:00:00.000Z"),
        event("nervis", "2026-08-26T10:00:00.100Z"),
        event("ravis", "2026-08-26T10:00:00.200Z"),
    ])

    assert [s.service for s in trace.spans] == ["nervis", "ravis", "sirvis"]


def test_a_span_takes_the_worst_severity_it_saw() -> None:
    trace = assemble(TRACE, [
        event("ravis", "2026-08-26T10:00:00.000Z"),
        event("ravis", "2026-08-26T10:00:00.100Z", severity="error"),
    ])

    assert trace.spans[0].severity == "error"


def test_an_event_with_no_source_is_grouped_and_flagged() -> None:
    """Grouped as unknown rather than dropped, and said out loud — an event that
    vanishes because its envelope was thin is a gap nobody can see."""
    trace = assemble(TRACE, [{"event_id": "x", "event_type": "t",
                              "occurred_at": "2026-08-26T10:00:00.000Z", "trace_id": TRACE}])

    assert trace.spans[0].service == "unknown"
    assert any("no source service" in w for w in trace.warnings)


# ── The index and the endpoints ────────────────────────────────────────────


def test_the_index_lists_traces_newest_first_without_assembling_them() -> None:
    hub = a_hub()
    hub.ingest(event("ravis", "2026-08-26T10:00:00.000Z", event_id="a"))
    hub.ingest(event("nervis", "2026-08-26T10:00:01.000Z", event_id="b"))
    hub.ingest({**event("ravis", "2026-08-26T11:00:00.000Z", event_id="c"),
                "trace_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"})

    rows = summarise(hub.query())

    assert [r["trace_id"] for r in rows] == ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", TRACE]
    assert sorted(rows[1]["services"]) == ["nervis", "ravis"]
    assert rows[1]["events"] == 2


def test_an_event_without_a_trace_appears_in_no_trace() -> None:
    """Registry transitions belong to no request. Inventing a trace for them
    would fill the index with traces of one service, which is a fact about
    nothing and makes the real ones harder to find."""
    hub = a_hub()
    hub.emit("nervis.service.state_changed", data={"service": "ravis"})

    assert summarise(hub.query()) == []


def an_api() -> TestClient:
    return TestClient(create_app(Settings(
        database_path=":memory:",
        ravis_base_url="http://127.0.0.1:9", sirvis_base_url="http://127.0.0.1:9",
        clarvis_base_url="http://127.0.0.1:9", lmstudio_base_url="http://127.0.0.1:9",
        ollama_base_url="http://127.0.0.1:9",
        _env_file=None,  # type: ignore[call-arg]
    )))


def test_an_unrecorded_trace_is_a_404_not_an_empty_timeline() -> None:
    """An empty waterfall reads as "this request did nothing", which is a much
    stronger claim than "nothing was recorded under this id"."""
    with an_api() as client:
        response = client.get(f"/api/v1/traces/{TRACE}")

    assert response.status_code == 404
    assert "nothing is recorded" in response.json()["error"]["message"]


def test_a_trace_reaches_the_api_with_its_spans_and_its_warnings() -> None:
    with an_api() as client:
        client.post("/api/v1/events", json=[
            event("nervis", "2026-08-26T10:00:00.000Z", event_id="n1"),
            event("ravis", "2026-08-26T10:00:00.100Z", event_id="r1"),
            event("ravis", "2026-08-26T10:00:00.300Z", event_id="r2"),
        ])

        body = client.get(f"/api/v1/traces/{TRACE}").json()
        index = client.get("/api/v1/traces").json()

    assert body["window_ms"] == 300.0
    assert [s["service"] for s in body["spans"]] == ["nervis", "ravis"]
    assert body["spans"][0]["is_point"] is True     # one event
    assert body["spans"][1]["duration_ms"] == 200.0  # two events
    assert index["items"][0]["trace_id"] == TRACE


def test_a_recent_trace_survives_a_hub_bigger_than_the_scan_limit() -> None:
    """The traces index is limited on *events scanned*, not on traces returned.

    It called `hub.query(limit=500)` and described itself as "newest first",
    but `query` scans `ORDER BY sequence` -- so it read the 500 *oldest* events
    in the hub. Once the hub passed that, every recent trace became invisible
    and the Overview card reported "no trace has been recorded yet" while the
    hub held several. The card is the honest-absence card, which is what makes
    this the worst place for a false absence.

    The limit is set small here rather than posting 500 events: the defect is
    the ordering, and a test that needs half a thousand rows to show it would
    be slow enough that nobody runs it.

    The first fix for this only moved the false absence to the other end --
    reading the newest N *events* reports no traces at all whenever the recent
    window happens to carry none, which is the ordinary state after a restart
    when every recent event is a registry transition. The limit has to bound
    traces; `test_a_trace_older_than_the_recent_events_is_still_listed` below is
    the half that catches that.
    """
    client = an_api()
    for n in range(12):
        client.post(
            "/api/v1/events",
            json=event("ravis", "2026-08-22T12:00:00Z",
                       event_id=f"old-{n}", trace_id=f"older-{n}"),
        )
    client.post(
        "/api/v1/events",
        json=event("ravis", "2026-08-22T12:00:05Z",
                   event_id="newest", trace_id="the-newest-trace"),
    )

    listed = client.get("/api/v1/traces?limit=5").json()["items"]

    assert any(item["trace_id"] == "the-newest-trace" for item in listed), (
        "a trace recorded a moment ago must appear before twelve older ones"
    )


def test_a_trace_older_than_the_recent_events_is_still_listed() -> None:
    """A quiet period must not erase the traces before it.

    Bounding the *event* scan rather than the trace count meant the answer
    depended on what had happened lately: after a restart the recent window is
    all registry transitions carrying no `trace_id`, so the index reported no
    traces while the hub held them, and the Overview printed "no trace has been
    recorded yet" -- the false absence that card exists to avoid, produced by
    the fix for the same false absence at the other end.
    """
    client = an_api()
    client.post("/api/v1/events", json=event(
        "ravis", "2026-08-22T12:00:00Z", event_id="traced", trace_id="an-old-trace"))
    for n in range(40):
        client.post("/api/v1/events", json=event(
            "nervis", "2026-08-22T12:00:05Z", event_id=f"untraced-{n}", trace_id=""))

    listed = client.get("/api/v1/traces?limit=10").json()["items"]

    assert [item["trace_id"] for item in listed] == ["an-old-trace"], (
        "forty untraced events after it do not make the trace disappear"
    )
