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

import asyncio
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ravis.api.management.decisions import DecisionLog
from ravis.core.capabilities import Capability
from ravis.core.pools import DEFAULT_POOLS, POOL_PREFIX, POOLS_BY_ID, size_tier
from ravis.credentials import CredentialStore
from ravis.errors import NotFoundError
from ravis.evidence import EvidenceStore
from ravis.evidence.sirvis import candidates_with_evidence
from ravis.provider_state import ProviderState
from ravis.providers.base import describe
from ravis.reliability import HealthRegistry
from ravis.transparent import merged_candidates, remote_models
from ravis.upstreams import is_local_address

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
    """Capabilities for every model **every** upstream currently offers.

    This read one upstream — `state.adapter`, the first declared — so the Pools
    screen counted twenty members while the pool itself was choosing among six
    hundred. Two numbers on one screen disagreeing about the same pool is worse
    than either being wrong: it makes the reader distrust both, and the one that
    was right looked broken.

    Falls back to the single adapter when nothing is declared plurally, which is
    what every deployment written before M8 is.
    """
    transparents = getattr(request.app.state, "transparents", {})
    evidence = getattr(request.app.state, "evidence", None)
    if transparents:
        return await merged_candidates(transparents, evidence)
    adapter = request.app.state.adapter
    registry = request.app.state.model_registry
    return await candidates_with_evidence(adapter, registry.model_ids(), evidence)


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


def _members(pool: Any, eligible: list[str], membership: Any) -> list[str]:
    """What this pool is choosing among right now."""
    stored = tuple(membership.for_pool(pool.pool_id)) if membership is not None else ()
    return list(stored or pool.default_membership(eligible))


@router.get("/pools")
async def read_pools(request: Request) -> dict[str, Any]:
    """Every pool, its declared requirements, and its *derived* membership.

    Membership is computed here rather than stored (§5.2). That is the whole
    behaviour worth seeing on a dashboard: install a model that meets the
    requirements and it joins with nobody editing a list, and a pool nothing
    satisfies shows as unavailable rather than silently routing somewhere close.
    """
    candidates = await _candidates(request)
    remote = remote_models(getattr(request.app.state, "transparents", {}))
    membership = getattr(request.app.state, "pool_membership", None)
    residency = request.app.state.model_registry.residency
    items = []
    for pool in DEFAULT_POOLS:
        eligible = pool.eligible(candidates, remote)
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
                # What the pool would actually choose among: its invariants,
                # then an operator's selection or its own default tier. The
                # count on a dashboard has to be the count the router uses, or
                # it is describing a different pool than the one that answers.
                "members": _members(pool, eligible, membership),
                "member_count": len(_members(pool, eligible, membership)),
                "eligible_count": len(eligible),
                "default_tier": pool.default_tier,
                "listed": pool.listed,
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
    """Every configured provider, its credential status, health and whether it
    is switched on.

    Lists them *all* — each transparent upstream and each translated provider.
    Until M8 there was one upstream and this returned one row; a screen built on
    that would have shown a single provider on a deployment reaching three.

    Carries `credential_configured` as a boolean and never the value — §15.1's
    "never expose credential values", enforced by not reading one here at all.

    Health is probed concurrently and **skipped for a disabled provider**: an
    operator who switched something off should not have RAVIS keep calling it,
    and a timeout against a provider nobody wants would slow the one screen that
    explains why nothing is routing.
    """
    state: ProviderState = request.app.state.provider_state
    credentials: CredentialStore = request.app.state.credentials
    disabled = state.disabled()

    entries = _provider_entries(request)
    probes = await asyncio.gather(
        *(
            _probe(adapter) if name not in disabled else _skipped()
            for name, adapter, _ in entries
        )
    )
    rows = []
    for (name, adapter, base_url), probe in zip(entries, probes, strict=True):
        status = credentials.status(name)
        rows.append({
            **describe(adapter),
            "name": name,
            "base_url": base_url,
            # Whether requests to this provider stay on the machine, from its
            # address — the same one question `ravis/local` is enforced on, so a
            # screen and the router cannot disagree about it. A translated
            # provider carries no base_url here and reads as remote, which is
            # what Anthropic is.
            "local": is_local_address(base_url),
            "enabled": name not in disabled,
            "credential_configured": status.configured,
            "credential_source": status.source.value,
            # Why this provider's catalogue is the size it is. `last_error` has
            # been recorded on every failed refresh since M1, under a docstring
            # calling it "a distinction a diagnostic needs" — and no diagnostic
            # read it. A provider reachable now whose catalogue is empty because
            # the refresh before this one failed looked identical to one that
            # genuinely has no models.
            **_catalogue_of(request, name),
            **probe,
        })
    return _listing(rows)


def _catalogue_of(request: Request, name: str) -> dict[str, Any]:
    """What this provider's last catalogue refresh produced, and why.

    Empty for a translating provider, which has no catalogue to refresh — the
    keys are omitted rather than reported as zero, because a count of nothing
    and no count at all are different claims.
    """
    transparents: dict[str, Any] = getattr(request.app.state, "transparents", {})
    built = transparents.get(name)
    if built is None:
        return {}
    snapshot = built.registry.snapshot
    return {
        "catalogue_size": len(snapshot.models),
        "catalogue_refreshed": snapshot.has_been_refreshed,
        "catalogue_error": snapshot.last_error,
    }


def _provider_entries(request: Request) -> list[tuple[str, Any, str]]:
    """(name, adapter, base_url) for every provider, transparent then translated.

    Transparent first because that is declaration order and the order a
    collision is resolved in — a screen listing them the other way round would
    invite the wrong conclusion about which one serves a shared model id.
    """
    entries: list[tuple[str, Any, str]] = []
    transparents: dict[str, Any] = getattr(request.app.state, "transparents", {})
    for name, built in transparents.items():
        entries.append((name, built.adapter, built.upstream.base_url))
    if not transparents:
        entries.append(("default", request.app.state.adapter, request.app.state.upstream.base_url))
    for name, adapter in getattr(request.app.state, "translating", {}).items():
        entries.append((name, adapter, getattr(adapter, "base_url", "")))
    return entries


async def _probe(adapter: Any) -> dict[str, Any]:
    """One provider's health, as the listing reports it.

    A failing probe is a *result*, not an error: "this provider is not
    answering" is exactly what the screen exists to show, so an exception here
    becomes a row saying so rather than a 500 that hides every other provider.
    """
    try:
        health = await adapter.health()
    except Exception as failure:  # noqa: BLE001 - any adapter fault is a health result
        return {"reachable": False, "detail": str(failure), "latency_ms": None}
    return {
        "reachable": health.reachable,
        "detail": health.detail,
        "latency_ms": health.latency_ms,
    }


async def _skipped() -> dict[str, Any]:
    return {"reachable": None, "detail": "disabled — not probed", "latency_ms": None}


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
                # Whether a picker should offer it. Reported rather than
                # filtered out here: this endpoint is the list of pools, and a
                # client addressing `ravis/private` directly must still find it
                # described. Hiding it from the *payload* would make the ID look
                # retired when it is merely not worth choosing between.
                "listed": pool.listed,
            }
            for pool in DEFAULT_POOLS
        ]
    )


class PoolMembersInput(BaseModel):
    """The models an operator chose for one pool."""

    models: list[str] = []


@router.get("/pools/{pool_key}/members")
async def read_pool_members(pool_key: str, request: Request) -> Any:
    """Every model that *could* be in this pool, and which are chosen.

    `eligible` is the pool's own answer — what satisfies its invariants — and it
    is reported separately from `chosen` because the two answer different
    questions. A model that is not eligible cannot be ticked into the pool at
    all: `ravis/local` promises the request never leaves this machine, and a
    promise an operator can tick away in a picker is not a promise.

    An empty `chosen` means "whatever qualifies", which is what a pool has
    always meant and what it goes back to meaning when everything is ticked.
    """
    pool_id = f"{POOL_PREFIX}{pool_key}" if not pool_key.startswith(POOL_PREFIX) else pool_key
    pool = POOLS_BY_ID.get(pool_id)
    if pool is None:
        return JSONResponse(
            {"error": {"message": f"unknown pool {pool_id}", "type": "not_found"}},
            status_code=404,
        )
    candidates, remote = await _pool_candidates(request)
    eligible = pool.eligible(candidates, remote)
    stored = tuple(request.app.state.pool_membership.for_pool(pool_id))
    # What the pool would use right now: the operator's selection if they made
    # one, otherwise its own default tier over what is actually present.
    chosen = set(stored or pool.default_membership(eligible))
    return {
        "pool_id": pool_id,
        "label": pool.label,
        "locality": pool.requirements.locality,
        "items": [
            {"id": model, "selected": model in chosen, "remote": model in remote,
             "tier": size_tier(model)}
            for model in eligible
        ],
        "total": len(eligible),
        "chosen_total": len(chosen),
        # Three states, not two. A pool can be using an operator's selection, or
        # its own default tier, or everything — and "narrowed" alone could not
        # tell the middle case from the last, which is the one a picker most
        # needs to explain.
        "narrowed": bool(stored),
        "default_tier": pool.default_tier,
        "catalogue_total": len(candidates),
    }


@router.put("/pools/{pool_key}/members")
async def set_pool_members(
    pool_key: str, body: PoolMembersInput, request: Request
) -> Any:
    """Narrow one pool to the models chosen, or clear the narrowing.

    Only ever a narrowing. Anything that fails the pool's invariants is dropped
    from the selection here rather than stored and quietly ignored later — a
    stored member that can never be routed to is a lie the file tells the next
    person to read it.
    """
    pool_id = f"{POOL_PREFIX}{pool_key}" if not pool_key.startswith(POOL_PREFIX) else pool_key
    pool = POOLS_BY_ID.get(pool_id)
    if pool is None:
        return JSONResponse(
            {"error": {"message": f"unknown pool {pool_id}", "type": "not_found"}},
            status_code=404,
        )
    candidates, remote = await _pool_candidates(request)
    eligible = set(pool.eligible(candidates, remote))
    asked = [model for model in body.models if model in eligible]
    refused = [model for model in body.models if model not in eligible]
    # Everything eligible ticked is stored as no narrowing at all: the two
    # select the same models today and diverge the moment the catalogue grows,
    # and "I ticked everything" plainly means the pool should keep qualifying
    # models on its own.
    stored = () if len(asked) == len(eligible) else tuple(asked)
    request.app.state.pool_membership.set_for(pool_id, stored)
    return {
        "pool_id": pool_id,
        "chosen": list(stored),
        "narrowed": bool(stored),
        # Named rather than silently dropped, because a picker that reported
        # success on a selection it did not keep would teach an operator that
        # the invariants are advisory.
        "refused": refused,
    }


async def _pool_candidates(request: Request) -> tuple[dict[str, Any], frozenset[str]]:
    """Every model the router could consider, and which of them are remote."""
    transparents = getattr(request.app.state, "transparents", {})
    if not transparents:
        return {}, frozenset()
    candidates = await merged_candidates(
        transparents, getattr(request.app.state, "evidence", None)
    )
    return candidates, remote_models(transparents)


@router.get("/policies")
async def read_policies() -> dict[str, Any]:
    """Routing rules, in the `IF … THEN …` form of §9.6.

    Empty until the policy engine lands at M16, and empty is the honest answer:
    no policy is currently in force, so a dashboard should show none rather than
    an invented default that would misrepresent what RAVIS is doing.
    """
    return _listing([])


@router.get("/evidence")
async def read_evidence(request: Request) -> dict[str, Any]:
    """What SIRVIS told RAVIS, and what RAVIS concluded from it (§13).

    This endpoint exists because the capability surface cannot carry it.
    `/api/v1/models` publishes the *winning* claim per capability, and an
    UNKNOWN verdict deliberately records no claim at all — so the most useful
    sentence in the system, *why* a build was not admitted to a pool, had
    nowhere to live. "No reliable tool calls" and "measured once where the
    threshold needs three repetitions" send a reader to entirely different
    places, and §9.7 requires a route to be able to say which.

    The threshold travels with the verdicts. It is SIRVIS's to set (§13.2) and
    RAVIS's to apply, and a consumer reading a refusal should not have to find
    the specification to learn what bar was missed.
    """
    store: EvidenceStore = request.app.state.evidence
    registry = request.app.state.model_registry
    return _listing([
        {"model_id": model, **store.explain(model)} for model in registry.model_ids()
    ]) | {"source": store.snapshot()}


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
