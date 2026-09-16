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
import logging
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from nervis.enrollment import matches, presented_secret
from nervis.errors import InvalidConfigurationError, UnauthorizedError
from nervis.events import Rejected, ends_stream, heartbeat, sse_frame
from nervis.flood import GUARD_EVENT
from nervis.instances import Instances

# How far back the event read reports the flood guard's own events. A day,
# because "the guard kicked in last night" is still news the next morning, and
# a week of them would bury today's.
GUARD_RECENT_SECONDS = 86_400.0

# How far back a resuming subscriber is replayed in one connection. Named rather
# than a literal in the call, because it is a *policy*: past this the client is
# better served by the 409 above, and a bare 500 in the middle of a generator
# reads as an implementation detail nobody chose.
RESUME_LIMIT = 500

router = APIRouter(prefix="/api/v1/events", tags=["events"])

LOG = logging.getLogger("nervis.events")

#: The producers whose refused batches are worth saying out loud: the ones the launcher gives a
#: secret, and the editor windows. A refused batch naming anything else is a stranger, and a
#: stranger's claimed name is not repeated on screen.
KNOWN_SENDERS = ("ravis", "sirvis", "clarvis")

# §4.1: a comment heartbeat at least every 15 seconds. Twelve leaves room for a
# slow hop without a proxy deciding the connection is idle.
HEARTBEAT_SECONDS = 12.0

# How much context a *new* subscriber gets. Enough to show the screen is alive,
# far short of the retained history — which is what an unbounded replay handed
# to anyone who connected without a cursor.
FRESH_TAIL = 25


def _senders(
    presented: str, secrets: Mapping[str, str], instances: Instances
) -> set[tuple[str, str]]:
    """Who a batch's credential proves it comes from, as (service type, instance id) pairs.

    A service's own events secret proves that service, with any instance id (RAVIS and SIRVIS
    each stamp one of their own); a registered editor window's token proves that one window. The
    launcher gives each service a different secret, so a real credential proves one sender. Every
    candidate is compared, so the time taken does not say which one matched.
    """
    proven: set[tuple[str, str]] = set()
    if not presented:
        return proven
    for service, secret in secrets.items():
        if matches(presented, secret):
            proven.add((service, ""))
    for instance in instances.all():
        if matches(presented, instance.token):
            proven.add((instance.service, instance.instance_id))
    return proven


def _not_from(payload: Any, senders: set[tuple[str, str]]) -> dict[str, str] | None:
    """Refuse an event that names a sender its batch's credential did not prove (S7)."""
    source = payload.get("source") if isinstance(payload, Mapping) else None
    if not isinstance(source, Mapping):
        return None  # the hub quarantines an envelope with no source itself
    claimed_service = str(source.get("service_type") or "")
    claimed_instance = str(source.get("instance_id") or "")
    if any(
        claimed_service == service and (not instance or claimed_instance == instance)
        for service, instance in senders
    ):
        return None
    return {
        "reason": "unproven_source",
        "detail": "an event must name the sender its batch's credential proves",
    }


def _note_refusal(state: Any, payloads: list[Any]) -> None:
    """Remember that a known service's events were refused, so the events screen can say so.

    **A service started without its secret loses every event, and would otherwise do it
    quietly**: its own log says so, but nobody reads RAVIS's log to learn why a trace has one
    lane. Kept in memory — a restart starts over, and a service still refused is counted again
    within seconds. The claimed name is unproven, which is the point: it is only ever shown as
    "events claiming to come from", and only for the services NERVIS knows.
    """
    refused: dict[str, dict[str, Any]] | None = getattr(state, "refused_senders", None)
    if refused is None:
        refused = state.refused_senders = {}
    claimed = {
        str(payload["source"].get("service_type") or "")
        for payload in payloads
        if isinstance(payload, Mapping) and isinstance(payload.get("source"), Mapping)
    }
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    for service in sorted(claimed & set(KNOWN_SENDERS)):
        entry = refused.setdefault(service, {"batches": 0, "since": now, "last": now})
        entry["batches"] += 1
        entry["last"] = now
        if not entry["batches"] & (entry["batches"] - 1):
            LOG.warning("refused %d batch(es) of events claiming to come from %s, without a "
                        "valid sender's credential", entry["batches"], service)


def _unproven_instance(
    payload: Any, instances: Instances, presented: str
) -> dict[str, str] | None:
    """Refuse an event that claims a window it cannot prove it is.

    **`source.instance_id` is what §6.6's isolation is keyed on**, and until now
    nothing checked it: this route has no credential of its own, and a claimed
    id was stored exactly as sent. So anything that could reach the port could
    post an event naming somebody else's editor window, and it appeared under
    that window's diagnostics — which is the one thing "events and status from
    one never appear under another" promises will not happen.

    **Only a claim on a *registered* instance is checked**, which is narrower
    than it first looks and deliberately so. Every service publishes an
    `instance_id` of its own — RAVIS and SIRVIS put one in every envelope — and
    demanding a token for those would close the hub to the producers it exists
    for. What matters is misattribution: an id nobody registered cannot be
    mistaken for somebody's window, because every read of one 404s. An id the
    registry *does* hold is a window a person is looking at, and the registry
    already issued that window a token for exactly this kind of proof.
    """
    if not isinstance(payload, Mapping):
        return None
    source = payload.get("source")
    claimed = str((source or {}).get("instance_id") or "") if isinstance(source, Mapping) else ""
    if not claimed:
        return None
    service = str(source.get("service_type") or "") if isinstance(source, Mapping) else ""
    if instances.find(service, claimed) is None:
        return None
    if instances.holds(service, claimed, presented):
        return None
    return {
        "reason": "unproven_instance",
        # Deliberately not saying which half failed. The alternative is an
        # oracle for which instance ids exist, answerable by anything that can
        # reach the port.
        "detail": f"an event claiming {service or 'an'} instance must present that "
                  "instance's own token",
    }


@router.post("", status_code=202)
async def ingest(request: Request) -> dict[str, Any]:
    """One envelope, or a batch of them.

    A list is accepted because a producer reconnecting after an outage has a
    backlog, and making it send them one at a time would turn one outage into a
    thundering herd.

    **202 for anything readable as JSON.** The body says what happened to each.
    A rejected event is a fact about the producer, not a failure of the hub, and
    answering 4xx would invite a retry of something that cannot parse.

    The one exception is below and is the boundary rather than the envelope: a
    body that is not JSON at all has nothing to quarantine and nothing to
    describe, so it answers 422. This docstring said "always 202" and named no
    exception, which was wrong twice over -- the route also declared no status
    at all, so FastAPI answered 200 to everything.
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
    presented = presented_secret(request.headers.get("authorization"))
    # **A batch proves its sender, or is refused whole** (NERVIS 0.34.18; the security review's
    # S7). Until then anything able to reach the port could post events, which NERVIS stores,
    # shows and turns into notifications. 401 rather than a per-event rejection: this is not a
    # producer's malformed envelope but a caller with no standing, and nothing of it is stored.
    senders = _senders(
        presented, request.app.state.settings.event_producer_secrets, request.app.state.instances
    )
    if not senders:
        _note_refusal(request.app.state, payloads)
        raise UnauthorizedError(
            "events need a sender's credential: a service's events secret from the launcher, "
            "or a registered editor window's own token"
        )
    accepted, rejected = 0, []
    for payload in payloads:
        unproven = _not_from(payload, senders) or _unproven_instance(
            payload, request.app.state.instances, presented
        )
        if unproven is not None:
            rejected.append(unproven)
            continue
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
        "guard": _guard(hub),
        # Batches refused for want of a sender's credential, by the known service they claimed
        # (NERVIS 0.34.18). Empty while every producer proves itself.
        "refused_senders": dict(getattr(request.app.state, "refused_senders", {})),
    }


def _guard(hub: Any) -> dict[str, Any]:
    """The flood guard: on or not, its limits, who it guards now, and the last day.

    **With the feed, not beside it.** A screen that lists events and says nothing
    about the guard passes a thinned feed off as the whole one, so the one read
    every event list already makes carries it.

    `recent` is the guard's own events from the last day, newest first, read from
    the store so a restart does not forget that it kicked in. Only those NERVIS
    stored as itself: a producer posting an event of the same name is not the guard.
    """
    cutoff = datetime.fromtimestamp(time.time() - GUARD_RECENT_SECONDS, timezone.utc)
    stamp = cutoff.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    recent = [
        event for event in hub.query(event_type=GUARD_EVENT, service="nervis",
                                     latest=True, limit=20)
        if str(event.get("_received_at") or "") >= stamp
    ]
    return {**hub.guard_report(), "recent": recent[::-1]}


@router.get("/quarantine")
async def read_quarantine(request: Request) -> dict[str, Any]:
    """What was refused, and why.

    §11.1 asks for quarantine *with safe diagnostics*. A hub that silently
    dropped malformed events would make "nobody is sending anything" and "one
    producer is sending rubbish" look identical, and only one of those is
    somebody's bug.
    """
    return {"items": request.app.state.hub.quarantined(_int(request.query_params.get("limit"), 50))}


def event_frames(request: Request) -> AsyncIterator[bytes]:
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
    start = _resume_point(hub, resume, fresh)


    async def frames() -> AsyncIterator[bytes]:
        queue = hub.subscribe()
        try:
            # Runbook §4.1: "Reconnect advertises `retry: 3000`." Sent first and
            # once — a client that loses the connection reconnects on its own
            # schedule otherwise, and the default differs by browser.
            yield b"retry: 3000\n\n"
            for missed in hub.query(after=start, limit=RESUME_LIMIT if not fresh else FRESH_TAIL):
                yield sse_frame(missed)
            # Tells a client the backlog is done and everything after this is
            # live. Without it, a burst of replay and a burst of new events are
            # indistinguishable.
            yield b"event: ecosystem.stream.live\ndata: {}\n\n"
            stopping = getattr(request.app.state, "stopping", None)
            while True:
                # **End when the service is stopping.** This generator never
                # returns on its own, and uvicorn's graceful shutdown waits for
                # open connections — so one dashboard tab with the feed open
                # held NERVIS in "Waiting for connections to close" forever,
                # port released, process alive. A client reconnects on its own
                # (it is told `retry: 3000`), so closing here costs nothing and
                # is the difference between a service that stops and one that
                # has to be killed.
                if stopping is not None and stopping.is_set():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield heartbeat()
                    continue
                yield sse_frame(event)
                # A gap frame is terminal: the hub dropped this subscriber
                # before writing it, so nothing else will ever arrive on this
                # queue. Closing is what makes the client reconnect.
                if ends_stream(event):
                    return
        finally:
            # Runs on client disconnect, which is the ordinary way this ends. A
            # subscriber left registered is a queue filling until it is dropped
            # for falling behind — a slow leak that presents as gap warnings
            # about a tab somebody closed an hour ago.
            hub.unsubscribe(queue)

    return frames()


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    """NERVIS's own event stream, and the body of the canonical one.

    **Split from `event_frames` for §16 item 9.** `/ecosystem/events` in the
    shared package was a heartbeat on every service while §4.1 described it as
    the canonical stream — replay, cursor expiry, gap frames, the lot. All of
    that already existed here, on a private path, so the canonical route now
    takes this generator rather than growing a second implementation of
    semantics that are expensive to get subtly different.

    **The cursor check has to happen before any byte is written**, which is why
    `event_frames` is a plain function that validates and *then* returns the
    generator. An async generator would not run its body until the first frame
    was pulled — by which time the response has started and a `409` can no
    longer be sent, so an expired cursor would arrive as a broken stream instead
    of the refusal §4.1 names.
    """
    return StreamingResponse(
        event_frames(request),
        media_type="text/event-stream",
        headers={"cache-control": "no-store", "x-accel-buffering": "no"},
    )


def _resume_point(hub: Any, resume: str, fresh: bool) -> int:
    """Where this subscriber resumes from, or the refusal that says why not.

    Lifted out of `stream` when adding the shutdown check took it past ruff's
    complexity 8. It is the right seam anyway: everything here is about the
    *cursor*, and nothing about it is about streaming.
    """
    # **A cursor that is not a number is not a cursor.** The comment above
    # records this exact bug being fixed for the *no-cursor* case and it
    # survived untouched for the bad-cursor case: `_int("abc", 0)` is 0, `fresh`
    # is False because the header was present, and the stream then replayed the
    # oldest retained events to a client that asked for something unreadable.
    # Refusing is the only answer that cannot be mistaken for data.
    start = max(0, hub.latest_sequence() - FRESH_TAIL) if fresh else _int(resume, -1)
    if start < 0:
        raise HTTPException(
            status_code=400,
            detail={"code": "EVENT_CURSOR_INVALID",
                    "message": f"{resume!r} is not a sequence number"},
        )
    # Runbook §4.1: "A cursor outside retention returns 409
    # `EVENT_CURSOR_EXPIRED`." Retention deletes by age and by count, so a
    # client that was away long enough is asking for events that no longer
    # exist. Resuming it from whatever survived would hand it a stream with a
    # hole in it and no way to know — the same silence the gap frame exists to
    # break, arriving through the front door.
    oldest = hub.oldest_sequence()
    if not fresh and oldest and start < oldest - 1:
        raise HTTPException(
            status_code=409,
            detail={"code": "EVENT_CURSOR_EXPIRED",
                    "message": f"cursor {start} is older than the retention floor {oldest}",
                    "oldest_sequence": oldest,
                    "latest_sequence": hub.latest_sequence()},
        )
    return start


def _int(value: Any, fallback: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return fallback
