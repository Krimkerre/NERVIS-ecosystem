"""`/api/v1/traces` — what one request did, across services (§11.2).

Assembled from the event hub and nothing else. §11.1 makes the hub operational
telemetry rather than the system of record, so a trace here is *what was
recorded*, not what happened — and where those differ, the difference is
reported rather than filled in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from nervis import logs
from nervis.errors import NotFoundError
from nervis.peers import ravis as ravis_peer
from nervis.peers import sirvis as sirvis_peer
from nervis.peers.reader import peer_credential
from nervis.peers.reader import read as peer_read
from nervis.traces import assemble, summarise
from nervis.unified import models_in, unify

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


@router.get("/{trace_id}/unified")
async def read_unified(trace_id: str, request: Request) -> dict[str, Any]:
    """M17 — the trace, plus what else NERVIS knows about that moment.

    The spans answer *what happened*; this answers *what was going on*, which is
    the question somebody actually arrives with. Three additions, each carrying
    how it was linked rather than being presented as equally certain: the health
    each service was in **then**, the log lines belonging to this trace, and what
    is known about the models it used.

    **Every section fails alone.** M17's exit calls a broken link a partial
    trace, and that is the ordinary case here — the thing being diagnosed is
    usually the thing that is broken, so a view that failed whole when one input
    was missing would be useless exactly when it is needed.
    """
    events = request.app.state.hub.query(trace_id=trace_id, limit=1000)
    if not events:
        raise NotFoundError(f"nothing is recorded under trace {trace_id!r}")
    trace = assemble(trace_id, events)
    await _note_silent_peers(request, trace)

    hub = request.app.state.hub
    assembled = trace.as_dict()
    return unify(
        assembled,
        [entry.as_dict() for entry in request.app.state.registry.all()],
        hub.query(event_type="nervis.service.state_changed", limit=500, latest=True),
        _log_lines(request, trace_id),
        evidence=await _evidence_for(request, models_in(assembled)),
    )


async def _evidence_for(
    request: Request, models: list[str]
) -> dict[str, Any] | None:
    """SIRVIS's measurements of the models this trace used, where it has any.

    Matched on `target_key`, which is SIRVIS's own identity for a build, and
    **only exactly**. §13 keeps that identity whole precisely so a consumer
    cannot decide two builds are the same thing; a fuzzy match here would
    attribute one model's throughput to another and call it evidence.

    Most traces name a hosted model, for which SIRVIS has nothing and correctly
    says so — M17's *where available* is a statement about how often this is
    empty, not an excuse for it.
    """
    if not models:
        return None
    entry = request.app.state.registry.get("sirvis")
    if entry is None or not entry.is_usable:
        return None
    result = await peer_read(
        request.app.state.probe_client, entry, sirvis_peer.BY_KEY["evidence"],
        service="sirvis", params={"limit": 100},
        credential=peer_credential(request, "sirvis"),
    )
    if not result.available or not isinstance(result.data, dict):
        return None
    wanted = set(models)
    found = {
        str(one.get("target_key")): one
        for one in (result.data.get("items") or [])
        if isinstance(one, dict) and str(one.get("target_key")) in wanted
    }
    return found or None


def _log_lines(request: Request, trace_id: str) -> list[dict[str, Any]]:
    """Recent lines from every adapter, for the correlator to pick over.

    Read here rather than in `unified` so that module stays free of I/O and can
    be tested as a fold. An unconfigured run directory yields nothing, which the
    correlator reports as an absence like any other.
    """
    configured = str(getattr(request.app.state.settings, "run_directory", "") or "")
    if not configured:
        return []
    run = Path(configured)
    found: list[dict[str, Any]] = []
    for service in logs.FILES:
        # By trace id first: an exact hit is worth more than the whole window,
        # and `read` already searches further back when filtering.
        matched = logs.read(run, service, limit=20, text=trace_id)
        for line in matched.get("items", []):
            found.append({**line, "service": service})
    if found:
        return found
    for service in logs.FILES:
        for line in logs.read(run, service, limit=8).get("items", []):
            found.append({**line, "service": service})
    return found


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
        credential=peer_credential(request, ravis_peer.SERVICE),
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
