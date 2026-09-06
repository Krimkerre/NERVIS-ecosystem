"""M18b — RAVIS publishes what it decided, and never at its own expense.

Runbook Stage 7 asks for one `trace_id` per logical operation across the
services, and NERVIS's `traces.assemble` groups events into spans by
`source.service_type`. So RAVIS's whole obligation is: emit under the caller's
trace_id, emit enough to bound an interval rather than a point, and never let
the collector matter.

The last of those is the one worth testing hardest. RAVIS routes requests; a
dashboard being down may not slow one down, fail one, or change one.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any

import httpx
from fastapi.testclient import TestClient
from tests.test_fallback import TWO_CODERS, ScriptedUpstream

from ravis.app import create_app
from ravis.config import Settings

TRACE = "a" * 32


def a_client(**overrides: Any) -> TestClient:
    """The real application, transport replaced, pointed at a hub.

    Mirrors `test_fallback._app_with` — returned un-entered so the caller uses
    it as a context manager and the lifespan warms the catalogue. Without that
    every pool resolves to "no models available" and these tests would fail for
    a reason that has nothing to do with events.
    """
    upstream = ScriptedUpstream(TWO_CODERS)
    settings = Settings(
        database_path=":memory:",
        upstream_base_url="http://upstream.invalid",
        model_capabilities=upstream.catalogue,
        _env_file=None,  # type: ignore[call-arg]
        **{"nervis_base_url": "http://127.0.0.1:8790", **overrides},
    )
    app = create_app(settings)
    fake_client = httpx.AsyncClient(transport=upstream.transport())
    app.app.state.upstream_client = fake_client
    app.app.state.model_registry.use_client(fake_client)
    return TestClient(app)


def ask(client: TestClient, *, trace: str = TRACE, model: str = "ravis/auto") -> Any:
    headers = {"traceparent": f"00-{trace}-{'b' * 16}-01"} if trace else {}
    return client.post(
        "/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
        headers=headers,
    )


def publisher(client: TestClient) -> Any:
    """RAVIS's publisher.

    `create_app` returns the body-size middleware wrapping the application, so
    the state is one level further in than a plain FastAPI app — the same
    `app.app.state` the fallback tests use.
    """
    return client.app.app.state.events  # type: ignore[attr-defined]


def queued(client: TestClient) -> list[dict[str, Any]]:
    """Whatever the publisher is holding, without sending it anywhere."""
    return list(publisher(client)._pending)


# ── What is published ───────────────────────────────────────────────────────


def test_a_routed_request_publishes_a_selection_and_a_completion() -> None:
    """Two events, and the count is the point rather than the content.

    `traces.Span.duration_ms` returns None below two events, so a service that
    emits once is drawn as a point at the moment it happened to finish. Two is
    what turns RAVIS into a bar on the waterfall.
    """
    with a_client() as client:
        ask(client)

        types = [event["event_type"] for event in queued(client)]

    assert "ravis.route.selected" in types
    assert "ravis.request.completed" in types


def test_every_event_carries_the_caller_s_trace_id() -> None:
    """One trace_id per logical operation is the whole of Stage 7."""
    with a_client() as client:
        ask(client)
        events = queued(client)

    assert events, "nothing was published"
    assert {event["trace_id"] for event in events} == {TRACE}


def test_the_lane_is_named_so_a_waterfall_can_draw_it() -> None:
    """`assemble` groups by `source.service_type`; anything else lands in a lane
    called "unknown" together with everybody else who got it wrong."""
    with a_client() as client:
        ask(client)
        events = queued(client)

    assert events
    assert all(event["source"]["service_type"] == "ravis" for event in events)


def test_a_caller_with_no_traceparent_publishes_nothing() -> None:
    """An event with no trace is stored by the hub, counted against retention,
    and then dropped by `summarise` — stored and invisible, which is worse than
    either sending it or not."""
    with a_client() as client:
        ask(client, trace="")

        assert queued(client) == []


def test_the_selection_points_at_the_explanation_rather_than_carrying_it() -> None:
    """`considered` against an OpenRouter catalogue is hundreds of model ids.
    NERVIS fetches the explanation itself; the event carries the pointer."""
    with a_client() as client:
        ask(client)
        selected = [
            event for event in queued(client)
            if event["event_type"] == "ravis.route.selected"
        ]

    assert selected, "no selection was published"
    data = selected[0]["data"]
    assert isinstance(data["considered"], int), "the count, not the list"
    assert "decision_id" in data, "the pointer NERVIS follows"


def test_the_completion_says_whether_it_worked() -> None:
    """Severity is what `assemble` promotes onto the span, so a failed request
    has to make RAVIS's lane visibly the unhappy one."""
    with a_client() as client:
        ask(client)
        completed = [
            event for event in queued(client)
            if event["event_type"] == "ravis.request.completed"
        ]

    assert completed
    assert completed[0]["severity"] in ("info", "error")
    assert "succeeded" in completed[0]["data"]


def test_the_completion_names_the_execution_path_it_took() -> None:
    """§6's two paths, and the field a compatibility regression turns on.

    This published the empty string on every event, because it read
    `request.state.execution_path`, which nothing ever sets — the value lives on
    the recorded decision. No test caught it and none would have: "" is a
    plausible value for a field that is genuinely unset early in a request, so
    it was found by reading what actually arrived in the hub.
    """
    with a_client() as client:
        ask(client)
        completed = [
            event for event in queued(client)
            if event["event_type"] == "ravis.request.completed"
        ]

    assert completed
    assert completed[0]["data"]["execution_path"], (
        "the execution path is empty, so §6's fork is unreadable on the timeline"
    )


def test_a_successful_request_is_not_reported_as_cancelled() -> None:
    """`AttemptChain.cancelled` is a method, so `bool(chain.cancelled)` is always
    True — every successful request was published as cancelled.

    §9.7 calls this distinction out specifically: a cancelled request must not
    look like one that failed, and it must certainly not look like one that
    worked. The evidence for it is in the attempts, not in a truthy bound
    method.
    """
    with a_client() as client:
        ask(client)
        completed = [
            event for event in queued(client)
            if event["event_type"] == "ravis.request.completed"
        ]

    assert completed
    data = completed[0]["data"]
    assert data["succeeded"] is True
    assert data["cancelled"] is False, "a request that succeeded was called cancelled"


# ── What is never published ─────────────────────────────────────────────────


def test_no_event_carries_the_prompt_or_the_completion() -> None:
    """§9 forbids prompts and model output in telemetry, and the producer is
    where the obligation sits: a collector cannot un-leak what it was sent."""
    with a_client() as client:
        ask(client)
        events = queued(client)

    assert events
    for event in events:
        data = json.dumps(event["data"])
        assert '"hi"' not in data, "the prompt reached an event"
        for forbidden in ("messages", "content", "api_key", "authorization"):
            if f'"{forbidden}"' in data:
                assert f'"{forbidden}": "[redacted]"' in data


# ── The outage ──────────────────────────────────────────────────────────────


def test_routing_works_with_no_collector_configured() -> None:
    """The ordinary case for RAVIS on its own, and deliberately not degraded."""
    with a_client(nervis_base_url="") as client:
        answer = ask(client)

        assert answer.status_code == 200
        assert not publisher(client).enabled


def test_a_collector_that_refuses_does_not_reach_the_caller() -> None:
    """The product may not learn that the dashboard is down."""

    class Refusing:
        async def post(self, url: str, *, json: Any, timeout: float) -> Any:
            del url, json, timeout
            raise ConnectionError("no hub here")

    with a_client() as client:
        sink = publisher(client)
        ask(client)
        asyncio.run(sink.flush(Refusing()))

        assert ask(client).status_code == 200
        assert sink.snapshot()["failures"] >= 1
        assert sink.snapshot()["queued"] >= 1, "the events are kept, not lost"


def test_a_dead_collector_never_becomes_the_service_s_own_unreadiness() -> None:
    """Stage 7's clause, proven rather than left to a comment.

    This regressed once for real: a check that read the publisher's dropped
    count made `ready` false the moment a collector died, which is exactly
    the coupling *"collector outage leaves every product healthy"* forbids.
    The fix removed the check (`ravis_surface`'s own comment records why)
    rather than softening it — but nothing drove the failure end-to-end and
    read `/ecosystem/health` afterward. This does: overflow the buffer for
    real, fail to reach the collector for real, and confirm the service's
    own readiness never saw either.
    """

    class Refusing:
        async def post(self, url: str, *, json: Any, timeout: float) -> Any:
            del url, json, timeout
            raise ConnectionError("no hub here")

    with a_client() as client:
        sink = publisher(client)
        sink._pending = deque(maxlen=3)  # small on purpose: overflow in a few requests

        for _ in range(5):
            assert ask(client).status_code == 200
        asyncio.run(sink.flush(Refusing()))

        snapshot = sink.snapshot()
        assert snapshot["dropped"] > 0, "the buffer never actually overflowed"
        assert snapshot["failures"] >= 1, "the collector was never actually unreachable"

        health = client.get("/ecosystem/health").json()
        assert health["ready"] is True, "a dead collector must not fail the service's own readiness"
        assert health["status"] == "healthy"


def test_publishing_state_is_reportable() -> None:
    """Telemetry that stops quietly looks exactly like a quiet system."""
    with a_client() as client:
        ask(client)
        state = publisher(client).snapshot()

    for field in ("enabled", "queued", "published", "dropped", "failures", "last_error"):
        assert field in state


def test_the_capability_says_it_publishes() -> None:
    """Under-advertising fails silently — nothing breaks except integration, and
    this file's own capability module records three instances of it."""
    with a_client() as client:
        body = client.get("/ecosystem/capabilities").json()

    # The published id drops the `@major` suffix the declaration carries —
    # `wire_identifier` splits them, so the declared key and the wire id differ.
    states = {c["id"]: c["state"] for c in body["capabilities"]}
    assert states["ravis.events"] == "available"
