"""The read-only management surface (RAVIS.md §15.1, M18a).

Every endpoint here answers a question a person is asking while something is
going wrong: which models can this pool actually use, why was that one excluded,
what did RAVIS decide for that request, is the provider healthy. That audience
shapes three rules the code follows throughout.

**Reads only.** No endpoint here mutates. Mutations are M18b and carry
`Idempotency-Key`, separate authorization and an audit event (§15.1); shipping
them alongside reads would mean shipping that machinery now or shipping a
mutation without it.

**Redacted by construction.** §15.1 requires provider and model results to be
redacted, so nothing here reads a credential in the first place — an endpoint
that never holds a secret cannot leak one. Provider records report *whether* a
credential is configured, never any part of its value.

**Unknown stays unknown.** Where a subsystem does not exist yet — cost at M15,
policies at M16, sessions at M11 — the endpoint returns an empty collection and
says why, rather than inventing a plausible number. §14 forbids presenting an
estimate as an invoice, and a dashboard showing a confident zero is worse than
one showing nothing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from ravis.api.management.decisions import DecisionLog
from ravis.core.capabilities import Capability
from ravis.core.pools import DEFAULT_POOLS
from ravis.errors import NotFoundError
from ravis.providers.base import describe
from ravis.reliability import HealthRegistry

router = APIRouter(prefix="/api/v1", tags=["management"])

# The revision every list response carries. It moves when the underlying set
# changes, so a consumer can tell a real change from a re-read (§15.1).
SNAPSHOT_REVISION = 1


def _listing(items: list[dict[str, Any]]) -> dict[str, Any]:
    """The `{items, next_cursor, snapshot_revision}` envelope §15.1 requires.

    `next_cursor` is always null at M18a: every collection here is bounded by
    the number of installed models or declared pools, and paginating a list of
    thirteen would be ceremony. The field is present because its *absence* would
    be a contract change later.
    """
    return {"items": items, "next_cursor": None, "snapshot_revision": SNAPSHOT_REVISION}


async def _candidates(request: Request) -> dict[str, Any]:
    """Capabilities for every model the upstream currently offers."""
    adapter = request.app.state.adapter
    registry = request.app.state.model_registry
    return {model: await adapter.capabilities(model) for model in registry.model_ids()}


@router.get("/health")
async def read_health(request: Request) -> dict[str, Any]:
    """Management-side health, distinct from `/ecosystem/health`.

    The MEP endpoint answers "can peers negotiate with this service". This one
    answers "is the thing it depends on working", which is a different question
    with a different answer during an upstream outage.

    Two kinds of health are reported, and conflating them would hide the
    interesting one. `upstream_reachable` is a **probe**: can it be reached
    right now. `targets` is **observed** history — §10's counters and circuit
    states, accumulated from real traffic. A provider that answers a probe
    instantly while failing every completion is healthy by the first measure
    and open-circuited by the second, and that combination is exactly the
    situation somebody is trying to diagnose.
    """
    adapter = request.app.state.adapter
    registry: HealthRegistry = request.app.state.health
    health = await adapter.health()
    return {
        "status": "healthy" if health.reachable else "degraded",
        "upstream_reachable": health.reachable,
        "upstream_detail": health.detail,
        "upstream_latency_ms": health.latency_ms,
        "models_known": len(request.app.state.model_registry.model_ids()),
        # Empty until traffic has flowed. That is an honest empty rather than a
        # missing key: nothing has been observed yet, which is different from
        # nothing being wrong (runbook §14.4).
        "targets": registry.snapshot(),
    }


@router.get("/pools")
async def read_pools(request: Request) -> dict[str, Any]:
    """Every pool, its declared requirements, and its *derived* membership.

    Membership is computed here rather than stored (§5.2). That is the whole
    behaviour worth seeing on a dashboard: install a model that meets the
    requirements and it joins with nobody editing a list, and a pool nothing
    satisfies shows as unavailable rather than silently routing somewhere close.
    """
    candidates = await _candidates(request)
    residency = request.app.state.model_registry.residency
    items = []
    for pool in DEFAULT_POOLS:
        eligible = pool.eligible(candidates)
        items.append(
            {
                "pool_id": pool.pool_id,
                "label": pool.label,
                "description": pool.description,
                "requirements": {
                    "required": sorted(
                        capability.value for capability in pool.requirements.required
                    ),
                    "minimum_context": pool.requirements.minimum_context,
                },
                "members": eligible,
                "member_count": len(eligible),
                # A pool nothing satisfies is unavailable, not empty-and-fine.
                "available": bool(eligible),
                "loaded_members": [
                    model for model in eligible if model in residency.loaded
                ],
            }
        )
    return _listing(items)


@router.get("/models")
async def read_models(request: Request) -> dict[str, Any]:
    """Every model, with capability *evidence* rather than bare booleans.

    §15.1 asks for capability-evidenced results, and the provenance is the point:
    a capability believed because it was measured is a different claim from one
    believed because a catalogue said so, and a dashboard that flattens them
    hides the disagreement that matters.
    """
    candidates = await _candidates(request)
    residency = request.app.state.model_registry.residency
    items = []
    for model, known in sorted(candidates.items()):
        items.append(
            {
                "model_id": model,
                "residency": residency.state_of(model).value,
                "context_window": known.context_window,
                "capabilities": {
                    capability.value: {
                        "state": known.state_of(capability).value,
                        "provenance": (
                            known.claims[capability].provenance.value
                            if capability in known.claims
                            else None
                        ),
                        "detail": (
                            known.claims[capability].detail
                            if capability in known.claims
                            else ""
                        ),
                    }
                    for capability in Capability
                },
            }
        )
    return _listing(items)


@router.get("/providers")
async def read_providers(request: Request) -> dict[str, Any]:
    """Configured providers, their health, and how they are reached.

    Carries `credential_configured` as a boolean and never the value — §15.1's
    "never expose credential values", enforced by not reading one here at all.
    """
    adapter = request.app.state.adapter
    upstream = request.app.state.upstream
    health = await adapter.health()
    return _listing(
        [
            {
                **describe(adapter),
                "base_url": upstream.base_url,
                "credential_configured": bool(upstream.api_key),
                "reachable": health.reachable,
                "detail": health.detail,
                "latency_ms": health.latency_ms,
                "models": len(request.app.state.model_registry.model_ids()),
            }
        ]
    )


@router.get("/profiles")
async def read_profiles(request: Request) -> dict[str, Any]:
    """Routing profiles.

    §9.3 settles what these are: profile display names map one-to-one onto the
    §5 pool IDs, so a profile is the human-facing view of a pool rather than a
    second set of objects. Returning them from the same source is what keeps
    that true — two lists would drift.
    """
    del request
    return _listing(
        [
            {
                "profile_id": pool.pool_id,
                "label": pool.label,
                "description": pool.description,
                # Revisioning arrives with §5.4's versioned profiles; saying 1
                # is honest only because nothing can change it yet.
                "revision": 1,
            }
            for pool in DEFAULT_POOLS
        ]
    )


@router.get("/policies")
async def read_policies() -> dict[str, Any]:
    """Routing rules, in the `IF … THEN …` form of §9.6.

    Empty until the policy engine lands at M16, and empty is the honest answer:
    no policy is currently in force, so a dashboard should show none rather than
    an invented default that would misrepresent what RAVIS is doing.
    """
    return _listing([])


@router.get("/route-decisions")
async def read_route_decisions(
    request: Request, limit: int = Query(default=50, ge=1, le=200)
) -> dict[str, Any]:
    """Recent routing decisions, newest first.

    The endpoint this milestone exists for. A route decision on screen — pool,
    selected model, requirements, and every excluded candidate with its reason —
    is how an integration gets debugged, and it has to be *recorded* rather than
    recomputed: re-running the router would use today's catalogue and residency
    and could reach a different answer than the one being asked about.
    """
    log: DecisionLog = request.app.state.decision_log
    return _listing([entry.as_dict() for entry in log.recent(limit)])


@router.get("/route-decisions/{decision_id}")
async def read_route_decision(request: Request, decision_id: str) -> dict[str, Any]:
    """One decision by ID, or a 404 that says it aged out."""
    log: DecisionLog = request.app.state.decision_log
    entry = log.find(decision_id)
    if entry is None:
        raise NotFoundError(
            f"decision {decision_id} is not in the recent-decision log; "
            "decisions are kept in memory and age out",
            decision_id=decision_id,
        )
    return entry.as_dict()


@router.get("/usage")
async def read_usage(request: Request) -> dict[str, Any]:
    """What RAVIS has actually done, and what it cannot yet say.

    Request counts are real. Cost is `None` rather than `0.0`, because the cost
    engine is M15 and §14 forbids presenting an estimate as an invoice — a
    dashboard reading "€0.00" would be a confident lie, while a blank is a true
    statement about what is known.
    """
    log: DecisionLog = request.app.state.decision_log
    recent = log.recent(log.capacity)
    return {
        "decisions_recorded": len(recent),
        "routed": sum(1 for entry in recent if entry.decision.routed),
        "no_route": sum(1 for entry in recent if not entry.decision.routed),
        "spend_today": None,
        "spend_currency": None,
        "cost_available": False,
        "cost_detail": "the cost engine lands at M15; no pricing is configured",
    }
