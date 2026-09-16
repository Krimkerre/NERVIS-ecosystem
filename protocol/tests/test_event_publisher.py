"""Stage 7's second exit clause: "collector outage leaves every product healthy".

The publisher exists to make a NERVIS outage a non-event for RAVIS and SIRVIS,
and every test here is one way that could fail to be true. The redaction tests
are the other half — §9 puts the obligation on the producer because a collector
cannot un-leak a secret it has already received.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ecosystem_protocol import EventPublisher, envelope, redact_deep, stable_event_id
from ecosystem_protocol.publisher import DEFAULT_BUFFER, MAX_BACKOFF_SECONDS


class Collector:
    """A NERVIS that answers, or does not."""

    def __init__(self, *, status: int = 202, fail: bool = False) -> None:
        self.status, self.fail, self.batches = status, fail, []

    async def post(self, url: str, *, json: Any, timeout: float) -> Any:
        # Named to match the Protocol the publisher borrows a client through;
        # this double only cares about the body.
        del url, timeout
        if self.fail:
            raise ConnectionError("nervis is not running")
        self.batches.append(json)
        return type("R", (), {"status_code": self.status})()


def a_publisher(**kwargs: Any) -> EventPublisher:
    return EventPublisher(
        service_type="ravis", service_id="ravis-1", machine_id="m1",
        base_url="http://127.0.0.1:8790", **kwargs,
    )


# ── The outage ──────────────────────────────────────────────────────────────


def test_emitting_never_raises_when_the_collector_is_gone() -> None:
    """The product may not learn that the dashboard is down."""
    publisher = a_publisher()

    for n in range(10):
        publisher.emit("ravis.route.selected", trace_id="t1", data={"n": n})

    assert publisher.snapshot()["queued"] == 10


def test_a_failed_flush_keeps_the_events_and_says_so() -> None:
    publisher = a_publisher()
    publisher.emit("ravis.route.selected", trace_id="t1")

    sent = asyncio.run(publisher.flush(Collector(fail=True)))

    assert sent == 0
    state = publisher.snapshot()
    assert state["queued"] == 1, "a failed publish must not lose the event"
    assert state["failures"] == 1
    assert "nervis is not running" in state["last_error"]


def test_a_failed_flush_preserves_order() -> None:
    """A timeline assembled from events that arrived out of order is one a
    reader has to distrust."""
    publisher = a_publisher()
    for n in range(3):
        publisher.emit("ravis.route.selected", trace_id="t1", data={"n": n})

    asyncio.run(publisher.flush(Collector(fail=True)))
    collector = Collector()
    asyncio.run(publisher.flush(collector))

    assert [event["data"]["n"] for event in collector.batches[0]] == [0, 1, 2]


def test_the_buffer_is_bounded_and_a_drop_is_counted() -> None:
    """An unbounded queue is the outage taking the product down by a slower
    route. A drop nobody counted is a hole in a timeline nothing can explain."""
    publisher = a_publisher(buffer=4)

    for n in range(10):
        publisher.emit("ravis.route.selected", trace_id="t1", data={"n": n})

    state = publisher.snapshot()
    assert state["queued"] == 4
    assert state["dropped"] == 6


def test_the_oldest_is_dropped_not_the_newest() -> None:
    """The recent history is the half somebody looking at a live problem wants."""
    publisher = a_publisher(buffer=3)
    for n in range(6):
        publisher.emit("ravis.route.selected", trace_id="t1", data={"n": n})

    collector = Collector()
    asyncio.run(publisher.flush(collector))

    assert [event["data"]["n"] for event in collector.batches[0]] == [3, 4, 5]


def test_a_5xx_is_retried_and_a_202_is_not() -> None:
    publisher = a_publisher()
    publisher.emit("ravis.route.selected", trace_id="t1")

    asyncio.run(publisher.flush(Collector(status=503)))
    assert publisher.snapshot()["queued"] == 1, "a hub error is worth retrying"

    asyncio.run(publisher.flush(Collector(status=202)))
    assert publisher.snapshot()["queued"] == 0
    assert publisher.snapshot()["published"] == 1


def test_no_collector_configured_is_a_no_op_rather_than_a_failure() -> None:
    """Running RAVIS on its own is the ordinary case, not a degraded one."""
    publisher = EventPublisher(service_type="ravis")

    publisher.emit("ravis.route.selected", trace_id="t1")

    assert not publisher.enabled
    assert publisher.snapshot()["queued"] == 0
    assert publisher.snapshot()["dropped"] == 0, "a no-op is not a loss"


def test_the_drain_is_bounded_when_the_collector_hangs() -> None:
    """A shutdown that hangs on a collector is the outage taking the product
    down at the last possible moment."""

    class Hanging:
        async def post(self, url: str, *, json: Any, timeout: float) -> Any:
            del url, json, timeout
            await asyncio.sleep(30)

    publisher = a_publisher()
    publisher.emit("ravis.route.selected", trace_id="t1")

    async def exercise() -> float:
        loop = asyncio.get_running_loop()
        started = loop.time()
        await publisher.drain(Hanging(), deadline=0.2)
        return loop.time() - started

    assert asyncio.run(exercise()) < 3.0


# ── What must never leave the process ───────────────────────────────────────


def test_redaction_reaches_nested_values() -> None:
    """`redact` is a comprehension over top-level keys, which is right for a log
    record and wrong for an event: a prompt one level down walks through it."""
    payload = {"spec": {"tests": [{"prompt": "the secret question", "id": "t1"}]}}

    cleaned = redact_deep(payload)

    assert cleaned["spec"]["tests"][0]["prompt"] == "[redacted]"
    assert cleaned["spec"]["tests"][0]["id"] == "t1", "only the named keys go"


def test_an_event_redacts_on_the_way_in() -> None:
    built = envelope(
        event_type="ravis.route.selected", service_type="ravis", trace_id="t1",
        data={"api_key": "sk-live-1234", "messages": [{"content": "hello"}]},
    )

    assert built["data"]["api_key"] == "[redacted]"
    assert built["data"]["messages"] == "[redacted]"


def test_a_long_string_is_truncated_and_marked() -> None:
    """A stack trace or a pasted document must not ride out inside a message,
    and a silently shortened string is one a reader quotes back as complete."""
    built = envelope(
        event_type="x.y", service_type="ravis", trace_id="t1",
        data={"detail": "a" * 5000},
    )

    assert len(built["data"]["detail"]) < 400
    assert built["data"]["detail"].endswith("… [truncated]")


def test_redaction_survives_a_cycle_rather_than_taking_the_process_down() -> None:
    """The one thing telemetry may never do is kill its producer."""
    cyclic: dict[str, Any] = {}
    cyclic["self"] = cyclic

    assert redact_deep(cyclic) is not None


# ── The envelope ────────────────────────────────────────────────────────────


def test_an_event_with_no_trace_is_not_queued() -> None:
    """NERVIS accepts it, stores it, counts it against retention — and
    `summarise` drops it, so it is stored and invisible."""
    publisher = a_publisher()

    publisher.emit("ravis.route.selected", trace_id="")

    assert publisher.snapshot()["queued"] == 0


def test_the_service_type_is_what_makes_a_lane() -> None:
    """`traces.assemble` groups events into spans by exactly this field."""
    built = envelope(event_type="x.y", service_type="ravis", trace_id="t1")

    assert built["source"]["service_type"] == "ravis"


def test_a_stable_id_makes_a_retry_one_event_rather_than_two() -> None:
    """The hub dedupes on `event_id`, so a uuid4 per attempt is stored twice and
    inflates `span.events` — the number that decides whether a span is drawn as
    an interval or as a point."""
    first = stable_event_id("run_1", "sirvis.benchmark.started")
    again = stable_event_id("run_1", "sirvis.benchmark.started")
    other = stable_event_id("run_2", "sirvis.benchmark.started")

    assert first == again
    assert first != other


def test_an_unknown_severity_is_refused_at_the_producer() -> None:
    """Otherwise it is discovered as a quarantined event, hours later."""
    with pytest.raises(ValueError):
        envelope(event_type="x.y", service_type="ravis", trace_id="t1", severity="warn")


def test_a_built_event_carries_every_field_the_hub_requires() -> None:
    built = envelope(event_type="x.y", service_type="ravis", trace_id="t1")

    for field in ("event_id", "event_type", "occurred_at"):
        assert str(built.get(field) or "").strip(), f"{field} is required by the hub"


def test_the_default_buffer_matches_the_hub_s_own() -> None:
    assert DEFAULT_BUFFER == 256


# ── Publishing that stopped has to be visible, and cost nothing ─────────────


def test_a_drop_is_reported_without_touching_the_product_s_health(caplog: Any) -> None:
    """Reported as a log line, deliberately, and this was a readiness check
    first — which made a dead collector turn `ready` false and the service
    advertise itself as degraded. That is exactly the coupling Stage 7 forbids:
    *collector outage leaves every product healthy*. The thing built to prove
    the clause broke it.
    """
    import logging

    publisher = a_publisher(buffer=2)
    with caplog.at_level(logging.WARNING, logger="ecosystem.publisher"):
        for n in range(5):
            publisher.emit("ravis.route.selected", trace_id="t1", data={"n": n})

    assert publisher.snapshot()["dropped"] == 3
    assert any("dropped" in record.message for record in caplog.records)


def test_the_drop_log_does_not_grow_with_the_outage(caplog: Any) -> None:
    """A line per lost event would bury the first one, which is the useful one."""
    import logging

    publisher = a_publisher(buffer=2)
    with caplog.at_level(logging.WARNING, logger="ecosystem.publisher"):
        for n in range(200):
            publisher.emit("ravis.route.selected", trace_id="t1", data={"n": n})

    assert publisher.snapshot()["dropped"] == 198
    assert len(caplog.records) < 12, "one line per drop buries the first one"


def test_a_disabled_publisher_reports_nothing() -> None:
    """No collector configured is an ordinary state, not a fault to log about."""
    import logging

    publisher = EventPublisher(service_type="ravis")
    publisher.emit("ravis.route.selected", trace_id="t1")

    assert publisher.snapshot()["dropped"] == 0
    del logging


# ── Backing off, because §10 asks for it ────────────────────────────────────


def test_a_healthy_collector_is_polled_at_the_plain_interval() -> None:
    """Backoff is a response to failure, not a tax on the ordinary case."""
    publisher = a_publisher()
    assert publisher.wait(every=2.0) == 2.0


def test_each_consecutive_failure_waits_longer() -> None:
    """§10: "Retriable operations are bounded and jittered."

    The publisher re-posted a failed batch every two seconds forever — bounded in
    memory by the buffer, unbounded in attempts, and at a fixed interval. With
    the collector down, two producers hammered it in step for as long as the
    outage lasted, and hit it hardest at the moment it was coming back up.
    """
    publisher = a_publisher(jitter=lambda: 1.0)
    waits = []
    for _ in range(5):
        publisher.note_failure()
        waits.append(publisher.wait(every=2.0))
    assert waits == sorted(waits)
    assert waits[0] > 2.0
    assert waits != [waits[0]] * len(waits)


def test_the_wait_is_capped_so_recovery_is_not_hours_away() -> None:
    """Doubling without a ceiling means an outage of an hour is answered by a
    producer that has stopped checking. The cap is what keeps recovery prompt."""
    publisher = a_publisher(jitter=lambda: 1.0)
    for _ in range(50):
        publisher.note_failure()
    assert publisher.wait(every=2.0) <= MAX_BACKOFF_SECONDS


def test_jitter_separates_two_producers_that_failed_together() -> None:
    """RAVIS and SIRVIS lose the same collector at the same instant.

    Without jitter their retries stay in lockstep for the whole outage and
    arrive together — which is the thundering herd §10's word guards against,
    and is worst exactly when the collector is coming back.
    """
    one, two = a_publisher(jitter=lambda: 0.5), a_publisher(jitter=lambda: 1.0)
    one.note_failure()
    two.note_failure()
    assert one.wait(every=2.0) != two.wait(every=2.0)


def test_a_success_puts_the_interval_back() -> None:
    """The producer that recovers stops apologising for the outage."""
    publisher = a_publisher(jitter=lambda: 1.0)
    for _ in range(4):
        publisher.note_failure()
    assert publisher.wait(every=2.0) > 2.0
    publisher.note_success()
    assert publisher.wait(every=2.0) == 2.0


# ── The sender's secret (NERVIS 0.34.18; the security review's S7) ─────────


class HeaderCollector(Collector):
    """A NERVIS that also records the headers each batch arrived with."""

    def __init__(self, *, status: int = 202) -> None:
        super().__init__(status=status)
        self.headers: list[dict[str, str]] = []

    async def post(
        self, url: str, *, json: Any, timeout: float, headers: dict[str, str] | None = None
    ) -> Any:
        self.headers.append(dict(headers or {}))
        return await super().post(url, json=json, timeout=timeout)


def test_the_secret_rides_on_every_batch_and_nothing_rides_without_one() -> None:
    held = a_publisher(secret="the-ravis-events-secret")
    held.emit("ravis.route.selected", trace_id="t1")
    collector = HeaderCollector()
    assert asyncio.run(held.flush(collector)) == 1
    assert collector.headers == [{"Authorization": "Bearer the-ravis-events-secret"}]

    bare = a_publisher()
    bare.emit("ravis.route.selected", trace_id="t1")
    plain = Collector()  # a client with no headers parameter still works
    assert asyncio.run(bare.flush(plain)) == 1


def test_a_refused_secret_is_logged_once_per_power_of_two_and_the_events_kept(
    caplog: pytest.LogCaptureFixture,
) -> None:
    publisher = a_publisher(secret="the-ravis-events-secret")
    publisher.emit("ravis.route.selected", trace_id="t1")
    refusing = HeaderCollector(status=401)
    with caplog.at_level("WARNING", logger="ecosystem.publisher"):
        for _ in range(4):
            assert asyncio.run(publisher.flush(refusing)) == 0
    lines = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert len(lines) == 3, lines  # the 1st, 2nd and 4th refusal
    assert "not the one the collector expects" in lines[0]
    assert "the-ravis-events-secret" not in " ".join(lines)
    assert publisher.snapshot()["queued"] == 1


def test_a_refusal_without_a_secret_says_to_use_the_launcher(
    caplog: pytest.LogCaptureFixture,
) -> None:
    publisher = a_publisher()
    publisher.emit("ravis.route.selected", trace_id="t1")
    with caplog.at_level("WARNING", logger="ecosystem.publisher"):
        asyncio.run(publisher.flush(Collector(status=401)))
    assert any("start it with the ecosystem launcher" in r.getMessage() for r in caplog.records)
