"""The read-only management surface (RAVIS.md §15.1, M18a).

Every endpoint here answers a question a person is asking while something is
going wrong: which models can this pool actually use, why was that one excluded,
what did RAVIS decide for that request, is the provider healthy. That audience
shapes three rules the code follows throughout.

**Reads, and one write.** `PUT /pools/{pool_key}/members` narrows a pool and
persists the narrowing to `pools.json`. It arrived with the Pools screen and
this paragraph went on saying "no endpoint here mutates" -- which is how it also
shipped without the authorization the sentence promised, so on a non-loopback
bind an unauthenticated caller could change which models every client routes to.
It now takes the same guard the credential writes take.

The rest of §15.1's mutation machinery -- `Idempotency-Key` and an audit event
-- is still M18b, and is still not here. That is a real gap and is stated as one
rather than described as read-only.

**Redacted by construction.** §15.1 requires provider and model results to be
redacted, so nothing here reads a credential in the first place — an endpoint
that never holds a secret cannot leak one. Provider records report *whether* a
credential is configured, never any part of its value.

**Unknown stays unknown.** Where a subsystem does not exist yet — §9.8's routing
timings, event publication at M18b — the endpoint returns an empty collection and
says why, rather than inventing a plausible number. (Cost, policies and sessions
were the examples here until M15, M16 and M11 shipped them; the rule is what
generalises, not the list.) §14 forbids presenting an
estimate as an invoice, and a dashboard showing a confident zero is worse than
one showing nothing.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ravis.api.management import audit
from ravis.api.management.credentials import _may_write, _refused
from ravis.api.management.decisions import DecisionLog
from ravis.core.capabilities import Capability
from ravis.core.pools import DEFAULT_POOLS, POOL_PREFIX, POOLS_BY_ID, size_tier
from ravis.cost import PriceBook, UsageLedger, band_for
from ravis.credentials import CredentialStore
from ravis.errors import NotFoundError
from ravis.evidence import EvidenceStore
from ravis.evidence.sirvis import candidates_with_evidence
from ravis.observations import MINIMUM_SAMPLES
from ravis.policy import ApplicationPolicies
from ravis.provider_state import ProviderState
from ravis.providers.base import describe
from ravis.reliability import HealthRegistry, HealthScope
from ravis.sessions import SessionStore
from ravis.transparent import (
    merged_candidates,
    remote_models,
    translated_candidates,
)
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
        candidates = await merged_candidates(transparents, evidence)
        translated, _ = await translated_candidates(
            getattr(request.app.state, "translating", {}), evidence
        )
        for model, known in translated.items():
            candidates.setdefault(model, known)
        return candidates
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


def _role_evidence(request: Request, pool: Any, models: list[str]) -> dict[str, str]:
    """What this pool's role has measured about each candidate.

    Read here for the reason `read_pools` already gives about counts: the number
    on a dashboard has to be the number the router uses. Membership is derived
    from evidence where evidence exists (§5.1.1), so a listing computed without
    it describes a different pool than the one that answers.
    """
    store = getattr(request.app.state, "evidence", None)
    if store is None or not pool.evidence_role or not hasattr(store, "roles_measured"):
        return {}
    fit = {model: store.roles_measured(model).get(pool.evidence_role, "") for model in models}
    return {model: state for model, state in fit.items() if state}


def _members(
    pool: Any, eligible: list[str], membership: Any, prices: dict[str, Any] | None = None,
    evidence: dict[str, str] | None = None,
) -> list[str]:
    """What this pool is choosing among right now.

    **Intersected with `eligible`, which this returned the stored tuple without
    doing.** An operator's narrowing is applied *after* the invariants and never
    instead of them -- `engine.py` ranks on `model in eligible and (not chosen
    or model in chosen)` -- so a stored list naming models the pool no longer
    admits described a pool that does not exist. On this machine that showed as
    `ravis/cheap` reporting 62 members against 49 eligible, a count a narrowing
    cannot produce, while the router refused the pool outright.
    """
    stored = tuple(membership.for_pool(pool.pool_id)) if membership is not None else ()
    if stored:
        admitted = set(eligible)
        return [model for model in stored if model in admitted]
    return list(pool.default_membership(eligible, prices, evidence))


@router.get("/pools")
async def read_pools(request: Request) -> dict[str, Any]:
    """Every pool, its declared requirements, and its *derived* membership.

    Membership is computed here rather than stored (§5.2). That is the whole
    behaviour worth seeing on a dashboard: install a model that meets the
    requirements and it joins with nobody editing a list, and a pool nothing
    satisfies shows as unavailable rather than silently routing somewhere close.
    """
    # **The same candidate set and the same remote set the router uses.** This
    # computed `remote_models(transparents)` alone, which by its own definition
    # walks only the transparent upstreams -- so every model served by a
    # *translated* provider (Anthropic, Google) counted as local. `ravis/local`
    # and `ravis/private` then listed 49 hosted models and reported available,
    # while `ravis/api` reported empty: exactly inverted, on the two pools whose
    # whole promise is where a request goes. `_pool_candidates` has always had
    # it right (`remote_models(transparents) | frozenset(owners)`), and
    # `/api/v1/pools/{key}/members` answered correctly from it while this
    # endpoint contradicted it on the identical catalogue.
    candidates, remote = await _pool_candidates(request)
    if not candidates:
        # Pre-M8 single-adapter deployment: no declared addresses to derive a
        # remote set from. `remote_models({})` was already empty here, so this
        # preserves that path exactly.
        candidates, remote = await _candidates(request), frozenset()
    membership = getattr(request.app.state, "pool_membership", None)
    prices = {model: known.price_per_million for model, known in candidates.items()}
    residency = request.app.state.model_registry.residency
    items = []
    for pool in DEFAULT_POOLS:
        eligible = pool.eligible(candidates, remote)
        # Computed once. It was called twice for `members` and `member_count`,
        # which is how the two could in principle disagree, and it is the number
        # `available` has to be derived from.
        members = _members(
            pool, eligible, membership, prices, _role_evidence(request, pool, eligible)
        )
        items.append(
            {
                "pool_id": pool.pool_id,
                "label": pool.label,
                "description": pool.description,
                # §5.4, and what moves `ravis.virtual_profiles@1` off DEGRADED:
                # a consumer may pin the revision and be told when the pool's
                # behaviour changes under it. Derived from the definition, so it
                # cannot claim a stability the pool does not have.
                "version": pool.version,
                # Including the operator's narrowing, which is the change that
                # most often alters which model comes back and the one thing the
                # definition hash could not see.
                "revision": pool.revision_with(
                    membership.for_pool(pool.pool_id) if membership is not None else ()
                ),
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
                "members": members,
                "member_count": len(members),
                "eligible_count": len(eligible),
                "default_tier": pool.default_tier,
                "listed": pool.listed,
                # A pool nothing satisfies is unavailable, not empty-and-fine --
                # and "nothing satisfies it" has to mean *after* the operator's
                # narrowing, because that is what the router chooses from. This
                # read `bool(eligible)`, so a pool whose narrowing admitted none
                # of the eligible models reported available while every request
                # to it was refused.
                "available": bool(members),
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
    # Both the candidate set and *which of them leave the machine*. NERVIS's
    # spoken output needs the second (NERVIS.md §18.2: a reply a privacy
    # constraint kept on-device must not then be read aloud by a cloud service),
    # and it is the same `is_local_address` question `ravis/local` is enforced
    # with — so a screen, the router and the voice cannot disagree about it.
    candidates, placed = await _pool_candidates(request)
    remote: frozenset[str] | None = placed
    if not candidates:
        # A pre-M8 single-adapter deployment declares no addresses to compute a
        # remote set from. Reported as unknown rather than guessed, and a reader
        # that must fail closed — the voice gate — treats unknown as local.
        candidates, remote = await _candidates(request), None
    residency = request.app.state.model_registry.residency
    items = []
    for model, known in sorted(candidates.items()):
        items.append(
            {
                "model_id": model,
                "residency": residency.state_of(model).value,
                # None where it could not be determined. Three states, because
                # "not known to be remote" and "known to be local" are the same
                # answer only to a reader that is already failing closed.
                "local": None if remote is None else model not in remote,
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
    health: HealthRegistry = request.app.state.health
    disabled = state.disabled()

    entries = _provider_entries(request)
    # Held on the app so it lives exactly as long as the process does. A
    # module-level dict would outlive a test and be shared by the next one.
    cache = request.app.state.__dict__.setdefault("provider_health_cache", ProviderHealthCache())
    now = time.monotonic()
    probes = await asyncio.gather(
        *(
            _probe(adapter, cache, now) if name not in disabled else _skipped()
            for name, adapter, _, _built in entries
        )
    )
    rows = []
    for (name, adapter, base_url, built), probe in zip(entries, probes, strict=True):
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
            # §10's circuit and the failure rate behind it.
            #
            # Read without creating a record: a provider nobody has called must
            # not acquire a CLOSED breaker and a clean history purely by being
            # listed, because that reads as "healthy" and is really "unknown".
            # Both are `None` in that case, and a screen showing them can say
            # "not probed" instead of implying a verdict.
            **_reliability(health, name),
            "credential_configured": status.configured,
            "credential_source": status.source.value,
            # Why this provider's catalogue is the size it is. `last_error` has
            # been recorded on every failed refresh since M1, under a docstring
            # calling it "a distinction a diagnostic needs" — and no diagnostic
            # read it. A provider reachable now whose catalogue is empty because
            # the refresh before this one failed looked identical to one that
            # genuinely has no models.
            **_catalogue_of(built),
            **probe,
        })
    return _listing(rows)


def _catalogue_of(built: Any) -> dict[str, Any]:
    """What this provider's last catalogue refresh produced, and why.

    Empty for a translating provider, which has no catalogue to refresh — the
    keys are omitted rather than reported as zero, because a count of nothing
    and no count at all are different claims.

    Takes the upstream rather than its name. Resolving a name here read the
    wrong table when both a transparent and a translated provider answered to
    one, and reported the wrong provider's model count as this one's.
    """
    if built is None:
        return {}
    snapshot = built.registry.snapshot
    return {
        "catalogue_size": len(snapshot.models),
        "catalogue_refreshed": snapshot.has_been_refreshed,
        "catalogue_error": snapshot.last_error,
    }


def _provider_entries(request: Request) -> list[tuple[str, Any, str, Any]]:
    """(name, adapter, base_url, upstream) for every provider.

    Transparent first because that is declaration order and the order a
    collision is resolved in — a screen listing them the other way round would
    invite the wrong conclusion about which one serves a shared model id.

    **The upstream travels with the row rather than being looked up by name
    afterwards.** A name identifies a provider only within one of the two
    tables: declaring a transparent upstream called `google` alongside the
    translated Gemini provider gave two rows one name, and the by-name lookup
    then handed the transparent upstream's catalogue to the translated row. The
    fourth element is `None` for a translated provider, which has no catalogue.
    """
    entries: list[tuple[str, Any, str, Any]] = []
    transparents: dict[str, Any] = getattr(request.app.state, "transparents", {})
    for name, built in transparents.items():
        entries.append((name, built.adapter, built.upstream.base_url, built))
    if not transparents:
        entries.append(
            ("default", request.app.state.adapter, request.app.state.upstream.base_url, None)
        )
    for name, adapter in getattr(request.app.state, "translating", {}).items():
        entries.append((name, adapter, getattr(adapter, "base_url", ""), None))
    return entries


def _local_share(request: Request, recent: list[Any]) -> tuple[int, int]:
    """How many executed decisions ran on this machine, and how many ran at all.

    Read from the provider each decision actually used, against the same
    `is_local_address` test `ravis/local` is enforced with — so the figure on a
    dashboard and the promise in a pool cannot disagree about what "local"
    means.
    """
    transparents = getattr(request.app.state, "transparents", {})
    local_names = {
        name for name, built in transparents.items()
        if is_local_address(built.spec.base_url)
    }
    local = executed = 0
    for entry in recent:
        provider = (entry.attempts or {}).get("provider")
        if not provider:
            continue
        executed += 1
        local += provider in local_names
    return local, executed


def _reliability(health: HealthRegistry, name: str) -> dict[str, Any]:
    """One provider's circuit state and error rate, or nulls if never called."""
    known = health.known(HealthScope.PROVIDER, name)
    if known is None:
        return {"breaker": None, "error_rate": None, "requests": 0,
                "consecutive_failures": 0}
    return {
        "breaker": known.state.value,
        "error_rate": known.error_rate,
        "requests": known.requests,
        "consecutive_failures": known.consecutive_failures,
    }


#: How long a reachability reading stays good enough to reuse.
#:
#: Reported as "RAVIS often has a delay when checking pools or providers".
#: Measured: every open of the screen probed every provider live, concurrently,
#: so the wait was the slowest one — OpenAI's `/models` at ~830 ms. Nothing was
#: malfunctioning; the answer comes from another continent.
#:
#: Short on purpose. Somebody who has just started LM Studio is watching this
#: screen to see it appear, and a minute of stale "not answering" would read as
#: the fix not having worked. Ten seconds removes the cost of flipping back to a
#: screen you were just on, which is where the delay is actually felt.
HEALTH_CACHE_SECONDS = 10.0


async def _probe(
    adapter: Any,
    cache: "ProviderHealthCache | None" = None,
    now: float | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """One provider's health, as the listing reports it.

    A failing probe is a *result*, not an error: "this provider is not
    answering" is exactly what the screen exists to show, so an exception here
    becomes a row saying so rather than a 500 that hides every other provider.

    **Failures are cached too, and that is the point rather than an oversight.**
    An unreachable provider is the probe that costs the most — it runs to
    `HEALTH_TIMEOUT_SECONDS` rather than answering — and it is the one somebody
    reloads most often, because they are waiting for it to come back.

    `now` is a parameter so the window can be tested without sleeping through
    it; `cache` is passed in rather than held here so it lives on the app and
    dies with it, and so two tests never share one.
    """
    if cache is not None and now is not None and not force:
        cached = cache.readings.get(adapter.name)
        if cached is not None:
            if now - cached[0] < HEALTH_CACHE_SECONDS:
                return cached[1]
            # **Stale is served, and refreshed behind the answer.** A ten-second
            # window only helps somebody who returns to the screen inside ten
            # seconds, which is not how the screen is used — you look at
            # Providers, go elsewhere, come back a minute later, and pay the
            # full probe again. That was still "RAVIS often has a delay".
            #
            # So an expired reading answers immediately and a fresh probe runs
            # behind it. The reading on screen is then at most one visit old
            # rather than at most ten seconds old, which is the right trade for
            # a reachability badge: it is already a statement about a moment
            # that has passed by the time it is painted.
            _refresh_later(adapter, cache)
            return cached[1]
    try:
        health = await adapter.health()
        result = {
            "reachable": health.reachable,
            "detail": health.detail,
            "latency_ms": health.latency_ms,
        }
    except Exception as failure:  # noqa: BLE001 - any adapter fault is a health result
        result = {"reachable": False, "detail": str(failure), "latency_ms": None}
    if cache is not None and now is not None:
        cache.readings[adapter.name] = (now, result)
    return result


@dataclass
class ProviderHealthCache:
    """Readings, and the refreshes running behind them.

    A plain dict held both for a while, under a reserved `_inflight` key whose
    value was a different shape from every other entry — a type that lies is
    worse than a class nobody needed.
    """

    readings: dict[str, tuple[float, dict[str, Any]]] = field(default_factory=dict)
    inflight: dict[str, Any] = field(default_factory=dict)


def _refresh_later(adapter: Any, cache: "ProviderHealthCache") -> None:
    """Re-probe one provider without anybody waiting for it.

    **One in flight per provider.** Without the guard, a screen opened three
    times in a second launches three probes at a provider that is slow —
    which is exactly the provider you least want to send three requests to.
    The sentinel is the task itself, cleared when it finishes.
    """
    if adapter.name in cache.inflight:
        return

    async def run() -> None:
        try:
            await _probe(adapter, cache, time.monotonic(), force=True)
        finally:
            cache.inflight.pop(adapter.name, None)

    try:
        cache.inflight[adapter.name] = asyncio.get_running_loop().create_task(run())
    except RuntimeError:
        # No running loop, which is a synchronous caller in a test rather than
        # anything to recover from. The stale reading has already been returned.
        cache.inflight.pop(adapter.name, None)


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
    chosen = set(stored or pool.default_membership(
        eligible, {m: k.price_per_million for m, k in candidates.items()},
        _role_evidence(request, pool, list(eligible)),
    ))
    return {
        "pool_id": pool_id,
        "label": pool.label,
        "locality": pool.requirements.locality,
        "items": [
            {"id": model, "selected": model in chosen, "remote": model in remote,
             "tier": size_tier(model),
             "price_per_million": candidates[model].price_per_million}
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


def _if_match_refusal(request: Request, current: str) -> JSONResponse | None:
    """§15.1's `If-Match`, or None when the caller did not ask for one.

    Optional by design. A dashboard that has not been taught to send one keeps
    working exactly as before — making it mandatory would break every existing
    client to protect against a race most of them never run. A caller that
    *does* send one gets the guarantee, which is the useful half.

    `*` matches anything, per the HTTP definition: it means "I know this exists
    and I do not care which version", which is a different and weaker claim than
    naming one.
    """
    wanted = request.headers.get("if-match", "").strip()
    if not wanted or wanted == "*":
        return None
    if wanted.strip('"') == current:
        return None
    return JSONResponse(
        {"error": {
            "message": "the pool changed since you read it; re-read and try again",
            "type": "conflict",
            "expected": wanted.strip('"'),
            "current": current,
        }},
        status_code=412,
    )


def _curation_detail(pool: Any, dropped: int) -> str:
    """What this pool's curated default currently leaves out, in its own terms."""
    if not dropped:
        return "nothing dropped — every eligible model belongs here"
    if pool.curated:
        return f"{dropped} not one of the families this pool is for"
    if pool.excluded:
        return f"{dropped} specialists and models that cannot hold a request"
    return f"{dropped} cannot answer a chat completion at all"


@router.post("/pools/curate")
async def curate_pools(request: Request) -> Any:
    """Hand every pool back to its own curated default.

    **This deliberately stores nothing.** The first version wrote each pool's
    computed membership in as an explicit selection, which is the one thing that
    would break it: a stored list is a snapshot of the catalogue at the moment
    it was written, and the catalogue is the part that moves. A provider adding
    `claude-haiku-5` next week would publish a model that matches this pool's
    declared families and still never be routed to, because a list written today
    cannot contain it.

    That is not hypothetical. The machine this was built on carried a stored
    selection of 549 models, saved when 549 was the whole catalogue and stale by
    42 models within a fortnight — while the pools screen reported it as the
    membership and the router used it.

    So what this does is *remove* the narrowings. Membership is then derived on
    every read and every route from `default_membership`: the pool's
    requirements, its declared families, and whatever the providers publish
    right now. New models join the moment they appear and withdrawn ones leave
    with no action from anybody, which is what §5.2 means by membership being
    computed rather than stored.

    Per-pool hand selections stay available in the picker for exactly the case
    that needs one — pinning a model, or excluding one an operator disagrees
    with — and this is how they are undone.
    """
    refusal = _may_write(request)
    if refusal is not None:
        return _refused(request, refusal)
    candidates, remote = await _pool_candidates(request)
    prices = {model: known.price_per_million for model, known in candidates.items()}
    applied = []
    for pool in POOLS_BY_ID.values():
        eligible = pool.eligible(candidates, remote)
        had = bool(request.app.state.pool_membership.for_pool(pool.pool_id))
        request.app.state.pool_membership.set_for(pool.pool_id, ())
        # What the pool selects *right now*. Reported rather than stored, so the
        # number is a description of the current catalogue and not a promise
        # about the next one.
        chosen = pool.default_membership(
            eligible, prices, _role_evidence(request, pool, list(eligible))
        )
        applied.append({
            "pool_id": pool.pool_id,
            "curated": bool(pool.curated),
            "selected": len(chosen),
            "eligible": len(eligible),
            "was_pinned": had,
            "detail": _curation_detail(pool, len(eligible) - len(chosen)),
        })
    audit.record(request, audit.ACTION_POOL_MEMBERS, pool="*",
                 chosen=len(applied), refused=0, narrowed=False)
    return {"pools": applied, "stored": False,
            "note": "membership is derived per request; nothing was pinned"}


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
    # The same boundary the credential writes use: a loopback bind is the
    # deployment this endpoint is for, and a published one must not let an
    # anonymous caller re-point every client's routing.
    refusal = _may_write(request)
    if refusal is not None:
        return _refused(request, refusal)
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
    # §15.1's `If-Match`, and the one endpoint where the revision needed no
    # inventing: `revision_with` already hashes the pool's behavioural
    # definition folded with the operator's narrowing, and it is already
    # published on the read. It deliberately excludes the catalogue, so
    # installing a model does not invalidate somebody's open editor.
    #
    # **The lost update this prevents is real, not theoretical.** Every one of
    # these stores is a read-modify-write over a whole JSON file, so two
    # overlapping editors silently discard one of the two edits — no error,
    # no trace, and the second reader sees a selection nobody chose.
    held = request.app.state.pool_membership.for_pool(pool_id)
    conflict = _if_match_refusal(request, pool.revision_with(held))
    if conflict is not None:
        return conflict
    request.app.state.pool_membership.set_for(pool_id, stored)
    audit.record(request, audit.ACTION_POOL_MEMBERS, pool=pool_id,
                 chosen=len(stored), refused=len(refused), narrowed=bool(stored))
    return {
        "pool_id": pool_id,
        "chosen": list(stored),
        "narrowed": bool(stored),
        # The post-state's revision, so the next edit can send it back. §15.1
        # asks a mutation to return "the actual post-state plus revision".
        "revision": pool.revision_with(stored),
        # Named rather than silently dropped, because a picker that reported
        # success on a selection it did not keep would teach an operator that
        # the invariants are advisory.
        "refused": refused,
    }


async def _pool_candidates(request: Request) -> tuple[dict[str, Any], frozenset[str]]:
    """Every model the router could consider, and which of them are remote.

    **Translated providers count even with no transparent upstream declared.**
    This returned `({}, frozenset())` the moment `transparents` was empty, so a
    deployment serving only hosted providers -- Anthropic and Google with no
    local runtime, which is this machine whenever LM Studio is not running --
    reported no candidates for any pool, and every screen built on it went
    blank rather than saying the hosted models were there.
    """
    transparents = getattr(request.app.state, "transparents", {})
    translating = getattr(request.app.state, "translating", {})
    if not transparents and not translating:
        return {}, frozenset()
    evidence = getattr(request.app.state, "evidence", None)
    candidates = await merged_candidates(transparents, evidence) if transparents else {}
    # Translated providers too, or this screen describes a different candidate
    # set than the router uses — and the router is the one that answers.
    translated, owners = await translated_candidates(translating, evidence)
    for model, known in translated.items():
        candidates.setdefault(model, known)
    return candidates, remote_models(transparents) | frozenset(owners)


@router.get("/observations")
async def read_observations(request: Request) -> dict[str, Any]:
    """What RAVIS has actually timed, per model, with the sample count.

    **Never a figure without the count that earned it.** A median of two network
    calls is two numbers, and a screen that shows it beside a median of sixty
    without saying which is which invites a decision the data cannot support.
    `confident` is what routing asks; `samples` is what a reader judges by.

    §13.3 calls this evidence kind `OBSERVED_BY_RAVIS` — measured, but not under
    controlled conditions. SIRVIS's benchmarks are the other kind: fixed prompt,
    warm runtime, recorded method. This is a rolling window over whatever real
    traffic looked like, so a model that answered three long prompts has a
    median reflecting the prompts as much as the model.
    """
    store = getattr(request.app.state, "observations", None)
    if store is None:
        return {"items": [], "minimum_samples": MINIMUM_SAMPLES}
    return {
        "items": [
            {"model_id": model, **observed.as_dict()}
            for model, observed in sorted(store.all().items())
        ],
        "minimum_samples": MINIMUM_SAMPLES,
        # How many are measured well enough to be ranked on, which is the number
        # that says whether this is doing anything yet.
        "confident_total": sum(1 for o in store.all().values() if o.confident),
    }


@router.get("/sessions/{session_id}")
async def read_session(session_id: str, request: Request) -> dict[str, Any]:
    """One routing session (§15.1, §12.1).

    **Scoped to the caller's own application**, which is the isolation §12.1
    asks for rather than a nicety: two applications may use the identical
    session ID, and serving one to the other would merge exactly what the
    specification says must never merge. A session belonging to somebody else
    is reported as absent, not as forbidden — telling a caller that an ID it
    cannot read *exists* is itself a leak.

    Carries no prompt or response content, because a session never held any.
    """
    store: SessionStore | None = getattr(request.app.state, "sessions", None)
    identity = getattr(request.state, "identity", None)
    application = identity.application_id if identity else "anonymous"
    session = store.get(application, session_id) if store else None
    if session is None:
        raise NotFoundError(f"no session {session_id!r}")
    return session.as_dict()


@router.get("/sessions")
async def read_sessions(request: Request) -> dict[str, Any]:
    """This application's recent sessions, newest first.

    Never every application's. A single list across all of them would undo the
    isolation the composite key exists to provide, on the one screen most likely
    to be read as authoritative.
    """
    store: SessionStore | None = getattr(request.app.state, "sessions", None)
    identity = getattr(request.state, "identity", None)
    application = identity.application_id if identity else "anonymous"
    sessions = store.for_application(application) if store else []
    return _listing([session.as_dict() for session in sessions])


@router.get("/usage/records")
async def read_usage_records(request: Request) -> dict[str, Any]:
    """Recent per-call usage, newest first (§14, §17's `UsageRecord`).

    The aggregate on `/usage` answers "what has this cost"; this answers "which
    calls, and how sure is each figure". They are different questions and a
    total cannot be audited without the second — `cost_state` on every row is
    what stops a reader assuming the whole sum was arrived at the same way.

    Carries no prompt and no completion: a usage record has nowhere to put one.
    """
    ledger: UsageLedger | None = getattr(request.app.state, "usage_ledger", None)
    records = ledger.recent(100) if ledger else []
    return _listing([record.as_dict() for record in records])


@router.get("/policies")
async def read_policies(request: Request) -> dict[str, Any]:
    """What policy is in force, per application (§9.6).

    Empty still means *no policy is configured*, which stays the honest answer
    for a deployment that has set none — but it now means that because the file
    says so, rather than because the engine did not exist. A dashboard reading
    an empty list here can say "unrestricted" and be right.

    The `default` row is listed alongside the named ones, and only when it
    actually constrains something. An application with no row of its own is
    governed by it, so omitting it would leave a reader unable to answer "what
    applies to Clarvis" from this response.

    Nothing here can carry a credential: a policy names providers and patterns,
    and the credential that identifies an application is never part of it.
    """
    policies: ApplicationPolicies = getattr(
        request.app.state, "policies", ApplicationPolicies()
    )
    rows = [
        {"application_id": name, "constraints": policy.describe()}
        for name, policy in sorted(policies.by_application.items())
    ]
    if policies.default.describe():
        rows.insert(
            0,
            {"application_id": "*", "constraints": policies.default.describe()},
        )
    return _listing(rows)


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
    local, executed = _local_share(request, recent)
    return {
        "decisions_recorded": len(recent),
        "routed": sum(1 for entry in recent if entry.decision.routed),
        "no_route": sum(1 for entry in recent if not entry.decision.routed),
        # How much of the traffic stayed on this machine.
        #
        # Counted from the decisions that actually *ran*, not from those merely
        # recorded: a decision mid-stream has no provider yet, and folding it in
        # as "not local" would report a dip every time somebody was reading a
        # long answer. `None` when nothing has executed, because a share of zero
        # requests is not zero percent.
        "local_share": None if not executed else local / executed,
        "executed": executed,
        **_spend(request),
    }


def _spend(request: Request) -> dict[str, Any]:
    """What RAVIS estimates it has spent, and how much of that it can stand behind.

    **Three numbers, not one.** A total on its own invites being read as a bill,
    which §14 forbids in as many words. `priced` and `unpriced` say how much of
    the traffic the figure actually covers: twelve dollars across forty calls of
    which nine had no published price is a different statement from twelve
    dollars across forty calls, and only the second is what a lone total looks
    like.

    `cost_available` stays false when nothing has been priced yet, so a consumer
    can tell "nothing has cost anything" from "nothing could be costed".
    """
    ledger: UsageLedger | None = getattr(request.app.state, "usage_ledger", None)
    prices: PriceBook | None = getattr(request.app.state, "prices", None)
    if ledger is None:
        return {
            "spend_estimated": None, "spend_currency": None,
            "cost_available": False, "cost_detail": "no usage ledger is configured",
        }
    day = ledger.since(time.time() - 24 * 3600)
    total, priced, unpriced = ledger.spend(day)
    # **One currency, or no total.** `spend` adds costs without consulting
    # `record.currency`, and this endpoint labelled the result "USD" whatever
    # the prices were stated in -- so a mixed ledger produced euros and dollars
    # summed into one figure, rendered with a dollar sign by the dashboard and
    # compared against a budget limit in a third currency. Where they disagree
    # the total is withheld and the reason is stated, because a conversion rate
    # nobody supplied is not something RAVIS may invent.
    stated = ledger.currencies(day)
    mixed = len(stated) > 1
    currency = next(iter(stated)) if len(stated) == 1 else None
    budget = getattr(request.app.state, "budget", None)
    band, spent, band_unpriced = band_for(budget, ledger, time.time())
    return {
        # Named `spend_estimated` rather than `spend`: the field name itself has
        # to carry the claim, because a dashboard reads the key and not this
        # docstring, and §14's rule is that an estimate never reads as an invoice.
        # Nine places, not six. A single small call costs 6.45e-06 dollars and
        # `round(x, 6)` renders that as 6e-06 — the rounding silently destroying
        # the precision the engine exists to produce. Found by comparing RAVIS's
        # figure against OpenRouter's own for the same call.
        "spend_estimated": round(total, 9) if priced and not mixed else None,
        "spend_currency": currency,
        "spend_window": "24h",
        "calls_priced": priced,
        "calls_unpriced": unpriced,
        "prices_known": prices.known() if prices else 0,
        "cost_available": priced > 0 and not mixed,
        "cost_detail": (
            f"prices are stated in {len(stated)} currencies ({', '.join(sorted(stated))}); "
            f"RAVIS does not convert between them, so no single total is offered"
            if mixed else
            f"estimated from published prices for {priced} of {priced + unpriced} call(s) "
            f"in the last 24h; never an invoice (§14)"
            if priced
            else "no call has been priced yet — either none has run, or no provider "
                 "published a price for the models used"
        ),
        "budget": None if budget is None else {
            "limit": budget.limit,
            "currency": budget.currency,
            "period": budget.period,
            "hard": budget.hard,
            "spent_estimated": round(spent, 9),
            "band": band.value,
            # §14 requires a budget to fail predictably when a price is
            # unavailable. It cannot do that silently: a band computed while
            # nine calls went unpriced is a band standing on partial evidence,
            # and whoever is about to be throttled by it is owed the number.
            "calls_unpriced_in_window": band_unpriced,
        },
    }
