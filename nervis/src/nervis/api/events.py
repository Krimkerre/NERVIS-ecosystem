"""`/api/v1/events` — ingest, query, stream (§11.1).

Three verbs on one idea. Ingestion is HTTP POST, which §11.1 lists first and
which needs nothing of a producer beyond being able to make a request. The
stream is SSE, carrying `id:` so a reconnect resumes exactly where it stopped.

**Ingestion never returns a 5xx for a producer's mistake.** A malformed event is
quarantined and answered `202` with the reason — §11.1 requires the gate to pass
*"without blocking producers"*, and a hub that returned 500 would have every
producer's retry logic hammering it over an event that will never parse.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from nervis.errors import InvalidConfigurationError
from nervis.events import Rejected, heartbeat, sse_frame

router = APIRouter(prefix="/api/v1/events", tags=["events"])

# §4.1: a comment heartbeat at least every 15 seconds. Twelve leaves room for a
# slow hop without a proxy deciding the connection is idle.
HEARTBEAT_SECONDS = 12.0

# How much context a *new* subscriber gets. Enough to show the screen is alive,
# far short of the retained history — which is what an unbounded replay handed
# to anyone who connected without a cursor.
FRESH_TAIL = 25


@router.post("", status_code=202)
async def ingest(request: Request) -> dict[str, Any]:
    """One envelope, or a batch of them.

    A list is accepted because a producer reconnecting after an outage has a
    backlog, and making it send them one at a time would turn one outage into a
    thundering herd.

    **Always 202.** The body says what happened to each. A rejected event is a
    fact about the producer, not a failure of the hub, and answering 4xx would
    invite a retry of something that cannot parse.
    """
    hub = request.app.state.hub
    try:
        body = json.loads(await request.body() or b"null")
    except ValueError as failure:
        # The one case that is NERVIS's own boundary rather than a producer's
        # envelope: the request was not JSON at all, so there is nothing to
        # quarantine and nothing to describe.
        raise InvalidConfigurationError(f"body is not valid JSON: {failure}") from failure

    payloads = body if isinstance(body, list) else [body]
    accepted, rejected = 0, []
    for payload in payloads:
        outcome = hub.ingest(payload)
        if isinstance(outcome, Rejected):
            rejected.append({"reason": outcome.reason, "detail": outcome.detail})
        else:
            accepted += 1
    return {"accepted": accepted, "rejected": rejected}


@router.get("")
async def read_events(request: Request) -> dict[str, Any]:
    """§11.2's filters: service, severity, event type, trace, free text.

    `after` is the same cursor the stream's `id:` carries, so a client can page
    history and then subscribe from where it stopped without a second vocabulary
    for "where I am".
    """
    query = request.query_params
    hub = request.app.state.hub
    # No cursor means "show me what has happened", which is the newest window.
    # With a cursor this is replay and must stay oldest-first from that point.
    after = _int(query.get("after"), 0)
    items = hub.query(
        after=after,
        latest=after == 0,
        limit=_int(query.get("limit"), 100),
        service=query.get("service", ""),
        severity=query.get("severity", ""),
        event_type=query.get("event_type", ""),
        trace_id=query.get("trace_id", ""),
        text=query.get("text", ""),
    )
    return {
        "items": items,
        "next_cursor": items[-1]["_sequence"] if items else _int(query.get("after"), 0),
        "latest_sequence": hub.latest_sequence(),
    }


@router.get("/quarantine")
async def read_quarantine(request: Request) -> dict[str, Any]:
    """What was refused, and why.

    §11.1 asks for quarantine *with safe diagnostics*. A hub that silently
    dropped malformed events would make "nobody is sending anything" and "one
    producer is sending rubbish" look identical, and only one of those is
    somebody's bug.
    """
    return {"items": request.app.state.hub.quarantined(_int(request.query_params.get("limit"), 50))}


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    """Live events, with replay from `Last-Event-ID`.

    The replay is read from storage before the subscription is drained, so an
    event that arrived between the reconnect and the subscribe is delivered
    once rather than missed — the queue holds it, and the cursor filters it out
    of the replay if storage already had it.
    """
    hub = request.app.state.hub
    resume = request.headers.get("last-event-id") or request.query_params.get("after") or ""
    # **A subscriber with no cursor is new, not resuming from zero.** `_int("")`
    # is 0, and `query(after=0)` returns the *oldest* 500 events — so one
    # connection with no `Last-Event-ID` dumped the start of the retained
    # history to whoever asked. A new subscriber wants what happens next, and a
    # short tail for context; a resuming one says where it stopped.
    fresh = not resume
    start = max(0, hub.latest_sequence() - FRESH_TAIL) if fresh else _int(resume, 0)

    async def frames() -> AsyncIterator[bytes]:
        queue = hub.subscribe()
        try:
            for missed in hub.query(after=start, limit=FRESH_TAIL if fresh else 500):
                yield sse_frame(missed)
            # Tells a client the backlog is done and everything after this is
            # live. Without it, a burst of replay and a burst of new events are
            # indistinguishable.
            yield b"event: ecosystem.stream.live\ndata: {}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield heartbeat()
                    continue
                yield sse_frame(event)
        finally:
            # Runs on client disconnect, which is the ordinary way this ends. A
            # subscriber left registered is a queue filling until it is dropped
            # for falling behind — a slow leak that presents as gap warnings
            # about a tab somebody closed an hour ago.
            hub.unsubscribe(queue)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"cache-control": "no-store", "x-accel-buffering": "no"},
    )


def _int(value: Any, fallback: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return fallback
