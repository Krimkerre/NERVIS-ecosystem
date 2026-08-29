"""`/api/v1/traces` — what one request did, across services (§11.2).

Assembled from the event hub and nothing else. §11.1 makes the hub operational
telemetry rather than the system of record, so a trace here is *what was
recorded*, not what happened — and where those differ, the difference is
reported rather than filled in.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from nervis.errors import NotFoundError
from nervis.peers import ravis as ravis_peer
from nervis.peers.reader import read as peer_read
from nervis.traces import assemble, summarise

router = APIRouter(prefix="/api/v1/traces", tags=["traces"])


@router.get("")
async def list_traces(request: Request) -> dict[str, Any]:
    """Every trace the hub holds, newest first.

    A listing rather than a fan-out of assemblies: the index answers *which
    traces exist*, and assembling each to answer that would read every event
    twice.
    """
    limit = request.query_params.get("limit")
    # **The limit is on traces, not on the event scan.** This passed `limit`
    # straight to `query`, which bounds events: oldest-first it summarised the
    # oldest events the hub had ever kept and called the result newest-first,
    # and newest-first it found nothing whenever the recent window happened to
    # carry no `trace_id` -- the ordinary state after a restart. Either way the
    # Overview reported "no trace has been recorded yet" on a hub holding
    # traces, which is exactly the false absence that card exists to avoid.
    events = request.app.state.hub.events_of_recent_traces(_int(limit, 25))
    return {"items": summarise(events)}


@router.get("/{trace_id}")
async def read_trace(trace_id: str, request: Request) -> dict[str, Any]:
    """One trace: its spans, its events, and what is missing from it.

    A trace with no events is a 404 rather than an empty timeline. An empty
    waterfall reads as "this request did nothing", which is a much stronger
    claim than "nothing was recorded under this id".
    """
    events = request.app.state.hub.query(trace_id=trace_id, limit=1000)
    if not events:
        raise NotFoundError(f"nothing is recorded under trace {trace_id!r}")
    trace = assemble(trace_id, events)
    await _note_silent_peers(request, trace)
    return trace.as_dict()


async def _note_silent_peers(request: Request, trace: Any) -> None:
    """Say when a peer took part in this trace and published nothing.

    RAVIS records `trace_id` on every route decision, so *"RAVIS handled this
    request"* is a **recorded fact** NERVIS can read — which makes the absence
    of a RAVIS lane explainable rather than merely empty.

    Still a warning and never a span. §11.2 forbids synthesizing a span as fact,
    and a decision record is evidence that something happened, not evidence of
    *when it started and stopped*. Drawing a bar from it would be inventing the
    two timestamps the rule exists to protect.
    """
    if any(span.service == "ravis" for span in trace.spans):
        return
    decisions = await peer_read(
        request.app.state.probe_client,
        request.app.state.registry.get(ravis_peer.SERVICE),
        ravis_peer.BY_KEY["routes"],
        service=ravis_peer.SERVICE,
        params={"limit": 100},
    )
    if not decisions.available or not isinstance(decisions.data, dict):
        return
    matched = [
        d for d in decisions.data.get("items") or []
        if isinstance(d, dict) and d.get("trace_id") == trace.trace_id
    ]
    # **Only when RAVIS genuinely has no lane.** This warning was
    # unconditional, which was right while RAVIS published nothing at all and
    # became a false claim the moment M18b shipped: it told a reader that RAVIS
    # "published no events" while its two events sat in the span above the
    # sentence. A note that contradicts the picture beside it is worse than no
    # note, because one of them has to be wrong and the reader cannot tell
    # which.
    if matched and not any(span.service == "ravis" for span in trace.spans):
        trace.warnings.append(
            f"RAVIS routed this request — decision {matched[0].get('decision_id')}, "
            f"selected {matched[0].get('selected') or 'nothing'} — and published no "
            "events for it, so it has no lane. Either it is not configured with a "
            "hub to publish to, or the events have not arrived yet."
        )


def _int(value: Any, fallback: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return fallback
