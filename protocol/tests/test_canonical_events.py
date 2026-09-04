"""`/ecosystem/events` carries real events where a service has them (§16 item 9).

**The route was a heartbeat and nothing else**, on all three services, while
§4.1 describes it as *the* canonical stream: `id`/`event`/`data` frames,
`Last-Event-ID` replay, `409 EVENT_CURSOR_EXPIRED` past retention, bounded
subscriber buffers and `ecosystem.stream.gap` on overflow. A consumer following
the specification would have connected, held the line, and received nothing but
comments for ever.

NERVIS already implements every one of those clauses — on `/api/v1/events/stream`,
its own private path. So the fix is not to build a second stream: it is to let a
service that *has* a stream supply it, which is what §16 item 9 means by "an
injected event source". A service with nothing to publish keeps the heartbeat,
which is honest rather than empty: the connection is real and the traffic is not
there yet.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from ecosystem_protocol import router as ecosystem_router
from ecosystem_protocol.events import envelope


def _app(source: Any = None) -> FastAPI:
    api = FastAPI()
    api.include_router(ecosystem_router)
    if source is not None:
        api.state.ecosystem_events = source
    return api


@pytest.mark.asyncio()
async def test_a_service_with_no_source_still_heartbeats() -> None:
    """The unchanged case, asserted so the injection cannot quietly break it.

    RAVIS and SIRVIS publish by pushing to NERVIS rather than by being polled,
    so neither has a stream to offer yet. A heartbeat says "connected, nothing
    to say", which is true; inventing an empty event stream would not be.

    Driven against the generator rather than over HTTP, because the stream is
    endless by design: a client asked for the whole body would wait for a stream
    that is working correctly to finish, which it never does. The first attempt
    at this test did exactly that and hung.
    """
    from ecosystem_protocol.routes import heartbeat_stream

    stream = heartbeat_stream()
    assert (await anext(stream)).startswith(b"retry:"), (
        "the reconnect interval is the server's decision, not each client's guess"
    )
    await stream.aclose()


def test_an_injected_source_is_what_the_client_receives() -> None:
    """The canonical route carries the service's real events, not a copy of them.

    A second implementation beside NERVIS's would be a second set of replay and
    gap semantics to keep in step — which §4.1's own list makes expensive to get
    wrong twice.
    """
    async def source(request: Request) -> AsyncIterator[bytes]:
        del request
        yield b"id: ev-1\nevent: nervis.service.state_changed\ndata: {}\n\n"

    with TestClient(_app(source)) as client:
        body = client.get("/ecosystem/events").text

    assert "id: ev-1" in body
    assert "event: nervis.service.state_changed" in body


def test_the_source_is_given_the_request_so_it_can_resume() -> None:
    """`Last-Event-ID` is the whole of the reconnect contract.

    Handing the source the request rather than a parsed cursor keeps the parsing
    where the semantics live: the service that owns the retention window is the
    one that can say whether a cursor is still inside it.
    """
    seen: list[str] = []

    async def source(request: Request) -> AsyncIterator[bytes]:
        seen.append(request.headers.get("last-event-id", ""))
        yield b": ok\n\n"

    with TestClient(_app(source)) as client:
        client.get("/ecosystem/events", headers={"Last-Event-ID": "ev-41"})

    assert seen == ["ev-41"]


def test_the_envelope_carries_every_field_4_4_names() -> None:
    """§4.4 lists twelve fields; the producer envelope shipped six of them.

    `event_version`, `subject`, `request_id`, `session_id` and `privacy` were
    absent from every event any service published — so a consumer reading the
    specification and the wire saw two different shapes, and the fields §4.3
    fixes the vocabulary for could not be joined on because they were not there.
    """
    from ecosystem_protocol.observability import carrying

    with carrying(request_id="req-9", trace_id="trace-9", session_id="sess-9"):
        body = envelope(
            event_type="ravis.route.selected",
            service_type="ravis",
            subject={"type": "session", "id": "sess-9"},
            data={"model": "qwen", "api_key": "sk-should-not-survive"},
        )
    assert body["event_version"] == "1.0.0"
    assert body["subject"] == {"type": "session", "id": "sess-9"}
    assert body["request_id"] == "req-9"
    assert body["session_id"] == "sess-9"
    assert body["trace_id"] == "trace-9"


def test_the_privacy_block_names_what_was_actually_removed() -> None:
    """`redactions` is a record, not a decoration.

    §4.4 asks producers to declare it; a constant empty list would say "nothing
    was removed" while `redact_deep` was removing things, which is worse than
    omitting the field.
    """
    body = envelope(
        event_type="ravis.route.selected",
        service_type="ravis",
        trace_id="t",
        data={"model": "qwen", "api_key": "sk-x", "nested": {"prompt": "hello"}},
    )
    assert body["privacy"]["classification"] == "operational"
    assert sorted(body["privacy"]["redactions"]) == ["api_key", "prompt"]
    assert body["data"]["api_key"] == "[redacted]"
    assert body["data"]["nested"]["prompt"] == "[redacted]"


def test_an_event_with_nothing_to_redact_says_so_rather_than_omitting_it() -> None:
    body = envelope(event_type="a.b", service_type="ravis", trace_id="t", data={"n": 1})
    assert body["privacy"] == {"classification": "operational", "redactions": []}


def test_correlation_ids_are_absent_rather_than_empty_outside_a_request() -> None:
    """Absent means "not applicable"; empty means "this had none", which differs."""
    body = envelope(event_type="a.b", service_type="sirvis", trace_id="t", data={})
    assert "request_id" not in body
    assert "session_id" not in body
    assert "subject" not in body
