"""Choosing a model for a request (RAVIS.md §9).

M5 implements the eligibility half of §9.1's pipeline and the smallest honest
version of the ranking half. That asymmetry is deliberate, and worth
understanding before adding to it.

Eligibility is a hard filter: pool invariants, then capability checks that fail
closed. §9.1 is explicit that no preference outweighs a failed hard constraint,
so this runs first and separately, and a candidate removed here is never
reconsidered by scoring.

Ranking, at M5, is declared preference then alphabetical order — and nothing
more. There is no benchmark evidence yet (M13) and no cost data (M15), so any
richer score would be arithmetic over numbers nobody measured. Health (M12) is
deliberately not a score either: §10 says do not keep routing to a failing
provider, which is an exclusion, and turning "it failed twice" into a ranking
weight would be inventing a quality signal out of an availability one.

§9.4 rules out exactly that: routing must be explainable rather than an
opaque oracle, and an explanation that cites an invented weighting is worse than
one that admits the choice was made on stable ordering.
"""

from __future__ import annotations

import math
from difflib import get_close_matches
from typing import Mapping

from ravis.core.capabilities import ModelCapabilities
from ravis.core.pools import (
    POOL_PREFIX,
    POOLS_BY_ID,
    VirtualModelPool,
    direct_provider,
    direct_target,
    is_pool_id,
    parameter_scale,
    size_rank,
)
from ravis.core.requests import NormalizedRequest
from ravis.routing.explain import ExcludedCandidate, RouteDecision
from ravis.routing.requirements import RequestRequirements, analyse, unmet_by, unverified_notes
from ravis.runtime.residency import Residency, ResidencySnapshot, residency_rank
from ravis.runtime.resources import MemoryReading

# How many alternatives a decision publishes. §10 describes the chain as
# Primary → Fallback 1 → Fallback 2, so two is the specified depth rather than
# an arbitrary cap. Deeper is not obviously better: by the third alternative the
# request has already waited through two failures, and a client that has been
# waiting that long is usually better served by an error it can act on.
MAX_FALLBACKS = 2


class RoutingEngine:
    """Resolves what a client addressed into a model to call.

    Holds no state and performs no I/O: capabilities are passed in. That keeps it
    testable without a provider and keeps the decision reproducible — §9.7's
    determinism gate requires that fixed inputs produce the same decision *and*
    the same explanation.
    """

    def select(
        self,
        requested: str,
        candidates: dict[str, ModelCapabilities],
        residency: ResidencySnapshot | None = None,
        memory: MemoryReading | None = None,
        request: NormalizedRequest | None = None,
        unavailable: Mapping[str, str] | None = None,
        foreign_providers: frozenset[str] = frozenset(),
        remote_models: frozenset[str] = frozenset(),
        chosen: tuple[str, ...] = (),
        observed_ttft_ms: Mapping[str, float] | None = None,
    ) -> RouteDecision:
        """Resolve a requested model, pool or direct address to a decision.

        Three shapes arrive here, and they are handled differently on purpose:
        a plain model name is passed through untouched, a direct address names
        its target explicitly, and a pool is resolved by invariant.

        Residency and memory are optional and default to unknown, so a caller
        with no runtime visibility gets exactly the behaviour it had before —
        preference then alphabetical — rather than a router quietly acting on
        assumptions about a runtime it cannot see. `request` is optional for the
        same reason: without it only the pool's own invariants apply.

        `unavailable` carries the models whose circuit breaker is currently open
        (§10), each with the reason it opened. It is passed in rather than read
        from anywhere, because this class performs no I/O and holds no state —
        health lives in the reliability layer, and keeping the engine a pure
        function of its arguments is what makes §9.7's determinism gate
        testable.
        """
        requirements = analyse(request) if request else RequestRequirements()
        if is_pool_id(requested):
            return self._select_from_pool(
                POOLS_BY_ID[requested],
                candidates,
                residency or ResidencySnapshot(),
                memory or MemoryReading(),
                requirements,
                unavailable or {},
                remote_models,
                chosen,
                observed_ttft_ms or {},
            )

        target = direct_target(requested)
        if target is not None:
            return self._direct(requested, target, candidates, requirements,
                                direct_provider(requested) in foreign_providers)

        if requested.startswith(POOL_PREFIX):
            return _unknown_address(requested, candidates)

        # A plain model name. RAVIS does not second-guess it: §5.3 puts an
        # explicit request above any inference, and the transparent path exists
        # precisely so a client can address an upstream model directly. The
        # request's own requirements are reported but not enforced — refusing a
        # model the client named by name would be RAVIS overruling an explicit
        # instruction on the strength of capability data it may not have.
        return RouteDecision(
            requested=requested,
            selected=requested,
            reason="named directly by the client; no pool resolution applied",
            requirements=requirements.describe(),
        )

    def _direct(
        self,
        requested: str,
        target: str,
        candidates: dict[str, ModelCapabilities],
        requirements: RequestRequirements,
        foreign: bool = False,
    ) -> RouteDecision:
        """Handle `ravis/<provider>/<model>`.

        Bypasses selection but not existence: addressing a model the upstream
        does not offer is a no-route rather than a request forwarded to fail
        confusingly at the provider.

        **Unless the provider is not the one whose catalogue this is.** A
        translated provider (§6, Path B) publishes its own model list, and an
        Anthropic model will never appear in a local runtime's `/v1/models`.
        Checking it against the wrong catalogue would refuse every Path B
        request the moment the local upstream had any models at all — existence
        is real, but only the owning provider can answer it.
        """
        if not foreign and candidates and target not in candidates:
            return RouteDecision(
                requested=requested,
                selected=None,
                reason=f"{target} is not offered by the configured upstream",
                considered=sorted(candidates),
            )
        return RouteDecision(
            requested=requested,
            selected=target,
            reason="direct address to another provider; its own catalogue is authoritative"
            if foreign
            else "direct address; selection bypassed, policy and tracking still apply",
            requirements=requirements.describe(),
        )

    def _select_from_pool(
        self,
        pool: VirtualModelPool,
        candidates: dict[str, ModelCapabilities],
        residency: ResidencySnapshot,
        memory: MemoryReading,
        requirements: RequestRequirements,
        unavailable: Mapping[str, str],
        remote: frozenset[str] = frozenset(),
        chosen: tuple[str, ...] = (),
        observed: Mapping[str, float] | None = None,
    ) -> RouteDecision:
        """Resolve a pool to one model, or explain why it cannot be resolved.

        Two independent sets of hard constraints apply, and both are checked
        before anything is ranked (§9.1): the pool's own invariants, which are
        configuration, and the request's requirements, which are derived from
        what the client sent. A candidate failing either is gone before scoring.
        """
        decision = RouteDecision(
            requested=pool.pool_id,
            pool_id=pool.pool_id,
            considered=sorted(candidates),
            requirements=_all_requirements(pool, requirements),
            unverified=unverified_notes(requirements, candidates),
        )
        # An operator's selection wins outright; the pool's own default applies
        # only when they have made none. Resolved here rather than by the caller
        # because the default is expressed over *this* catalogue — "the small
        # ones" means nothing until you know what is present.
        effective = chosen or pool.default_membership(
            sorted(candidates),
            {model: known.price_per_million for model, known in candidates.items()},
        )
        by_default = not chosen
        decision.excluded = _exclusions(
            pool, candidates, requirements, unavailable, remote, effective, by_default
        )
        eligible = _rank(
            pool, candidates, residency, memory, requirements, unavailable, remote,
            effective, observed or {},
        )

        if not eligible:
            # §5.2: a pool with no satisfying candidate is *unavailable*. Never
            # relaxed to "closest match" — that is how an agent ends up on a
            # model that cannot call tools.
            decision.reason = (
                f"no candidate satisfies {pool.pool_id}; the pool is unavailable"
                if candidates
                else "no models are available from the configured upstream"
            )
            return decision

        decision.selected = eligible[0]
        # §10 requires every fallback candidate to satisfy the original hard
        # constraints and the pool invariants. Taking them from the ranked
        # eligible list makes that structural: a candidate that failed either
        # check is not in this list to be chosen from.
        decision.fallbacks = eligible[1 : 1 + MAX_FALLBACKS]
        decision.reason = _selection_reason(pool, eligible, residency, memory)
        return decision


def _unknown_address(requested: str, candidates: dict[str, ModelCapabilities]) -> RouteDecision:
    """A `ravis/…` name that is neither a pool nor a direct address.

    §5.3 says an explicit request outranks any inference RAVIS could make, and
    that is why a plain model name is forwarded untouched. It does not apply
    here, because `ravis/` is **RAVIS's own namespace**: no upstream serves a
    model called `ravis/chat`, so a name in that namespace that RAVIS does not
    recognise is a typo rather than an instruction.

    Forwarding it anyway is what this exists to stop, and the failure was worse
    than an error. `ravis/chat` reached LM Studio, which answered with whatever
    happened to be loaded — so the request succeeded with no pool, no capability
    filtering, no tool invariant and no fallback, and looked entirely fine. It
    also invented a health record for a model nobody has. A 422 that names the
    near-miss turns twenty minutes of confusion into one line.
    """
    suggestion = _nearest_pool(requested)
    advice = (
        f"did you mean {suggestion}?"
        if suggestion
        else f"known pools are: {', '.join(sorted(POOLS_BY_ID))}"
    )
    return RouteDecision(
        requested=requested,
        selected=None,
        reason=(
            f"{requested} is not a pool, and is not a direct address of the form "
            f"{POOL_PREFIX}<provider>/<model> — {advice}"
        ),
        considered=sorted(candidates),
    )


def _nearest_pool(requested: str) -> str:
    """The pool someone probably meant, or empty when nothing is close enough.

    Containment is tried before edit distance, because the mistake this actually
    sees is *dropping the qualifier* — `ravis/chat` for `ravis/clarvis-chat`,
    `ravis/agent` for `ravis/clarvis-agent`. Edit distance is hopeless at that:
    the shared `ravis/` prefix dominates the ratio, and it confidently proposed
    `ravis/cheap` for `chat` and `ravis/fast` for `agent`.

    Edit distance still earns its place for the other shape, a genuine
    misspelling — `clarvis-cat` — where containment finds nothing. The cutoff is
    raised above the default because a wrong suggestion is worse than none: it
    sends the reader to fix something that was never the problem.
    """
    remainder = requested[len(POOL_PREFIX):]
    if not remainder:
        return ""
    contained = sorted(
        (pool for pool in POOLS_BY_ID if remainder in pool[len(POOL_PREFIX):]),
        key=len,
    )
    if contained:
        return contained[0]
    near = get_close_matches(remainder, [p[len(POOL_PREFIX):] for p in POOLS_BY_ID], n=1,
                             cutoff=0.7)
    return f"{POOL_PREFIX}{near[0]}" if near else ""


def _all_requirements(pool: VirtualModelPool, requirements: RequestRequirements) -> list[str]:
    """Every hard constraint in force, from the pool and from the request.

    "none" appears only when *both* sources are empty. Emitting it per source
    produced explanations reading `['none', 'vision REQUIRED …']`, which says the
    opposite of what it means in the place a reader looks first.
    """
    described = _describe_requirements(pool) + requirements.describe()
    return described or ["none"]


def _describe_requirements(pool: VirtualModelPool) -> list[str]:
    """The pool's own invariants, in the words a route explanation will show."""
    described = [
        f"{capability.value} REQUIRED"
        for capability in sorted(pool.requirements.required, key=lambda item: item.value)
    ]
    if pool.requirements.minimum_context:
        described.append(f"minimum context {pool.requirements.minimum_context}")
    return described


def _exclusions(
    pool: VirtualModelPool,
    candidates: dict[str, ModelCapabilities],
    requirements: RequestRequirements,
    unavailable: Mapping[str, str],
    remote: frozenset[str] = frozenset(),
    chosen: tuple[str, ...] = (),
    by_default: bool = False,
) -> list[ExcludedCandidate]:
    """Every candidate that failed, with all of its reasons.

    Pool invariants, request requirements and open circuits are reported
    together and undifferentiated, because the person reading this wants to know
    why a model was not used — not which of three rule sources rejected it.

    An open circuit is listed here, among the hard exclusions, rather than
    treated as a preference. §10 is unambiguous that a failing provider is to be
    routed *away from*, and a soft penalty would keep sending it traffic.
    """
    excluded = []
    for model in sorted(candidates):
        reasons = pool.requirements.unmet_by(candidates[model], remote=model in remote)
        if chosen and model not in chosen:
            # Worded differently for the two cases on purpose: one is fixed by
            # ticking a box and the other by understanding what the pool is for.
            reasons.append(
                _default_exclusion(pool) if by_default
                else "not among the models chosen for this pool"
            )
        reasons += unmet_by(requirements, candidates[model])
        refused = model in unavailable
        if refused:
            reasons.append(unavailable[model])
        if reasons:
            excluded.append(
                ExcludedCandidate(model=model, reasons=reasons, circuit_open=refused)
            )
    return excluded


def _rank(
    pool: VirtualModelPool,
    candidates: dict[str, ModelCapabilities],
    residency: ResidencySnapshot,
    memory: MemoryReading,
    requirements: RequestRequirements,
    unavailable: Mapping[str, str],
    remote: frozenset[str] = frozenset(),
    chosen: tuple[str, ...] = (),
    observed: Mapping[str, float] | None = None,
) -> list[str]:
    """Order the eligible candidates, cheapest-to-reach among equals.

    Residency is a *preference*, never a constraint — §9.2 lists "prefer already
    loaded" as soft, so a cold model is never excluded, only ranked below a warm
    one that is otherwise equal.

    Which preference leads depends on memory. Normally the pool's declared intent
    wins and residency breaks its ties: a coding pool should reach for a coding
    model even if that means a load. **Under memory pressure the order inverts**,
    because loading anything new is the thing to avoid — this is M14's acceptance
    criterion, that pressure produces a safe route change, and it changes the
    route without ever changing what the pool is allowed to select.
    """
    members = [
        model for model, known in candidates.items()
        if not pool.requirements.unmet_by(known, remote=model in remote)
        and not unmet_by(requirements, known)
        and model not in unavailable
        # An operator's selection narrows what the invariants already allowed.
        # Applied *after* them, never instead: `ravis/local` promises the
        # request never leaves this machine, and a promise somebody can tick
        # away in a picker is not a promise.
        and (not chosen or model in chosen)
    ]
    pressured = memory.under_pressure

    def key(model: str) -> tuple[float | str, ...]:
        preference = pool.preference_rank(model)
        # Reach, not warmth: a hosted model is not a cold one. See `_reach_rank`.
        warmth = _reach_rank(model, residency, remote)
        # Built in order rather than by prepending, because the order *is* the
        # policy and prepending hid it. `prefer_local` was appended after
        # warmth, so a local model whose residency was unknown lost to a remote
        # one on reach before placement was ever consulted — and residency is
        # unknown for every local model until a runtime reports it.
        #
        # Cost first, then placement, then the pool's own preference and reach:
        # a pool that asked to be cheap means money before anything else, and
        # between two free models the one that costs no network is the cheaper.
        #
        # Heterogeneous because the last component is the model name — the
        # total, reproducible order §9.7's determinism gate requires.
        terms: list[float | str] = []
        if pool.prefer_fast:
            terms.append(_speed_rank(model, observed or {}, pool.speed_bucket_ms))
        if pool.prefer_cheap:
            terms.append(_price_rank(candidates.get(model)))
        if pool.prefer_local:
            terms.append(0.0 if model not in remote else 1.0)
        terms.extend((warmth, preference) if pressured else (preference, warmth))
        lead: tuple[float | str, ...] = tuple(terms)
        # Size is the *last* thing consulted, and only for a pool that declared
        # a preference at all.
        #
        # The restriction is not fussiness. Its justification — "among
        # candidates a pool already considers identical, the smaller is both
        # faster and cheaper" — assumes the pool has *expressed* something for
        # them to be equal on. A pool with an empty `prefer` considers
        # everything equal, so size stops being a tiebreak and silently becomes
        # the entire ranking. It did: eight of the thirteen pools resolved to
        # the smallest installed model, which on this machine is a 1.7B nobody
        # has ever benchmarked, and `ravis/balanced` selecting the tiniest thing
        # available is self-evidently not balanced.
        #
        # Pools that say nothing fall back to alphabetical order, which is
        # meaningless — and meaningless is the honest state until M13, because
        # it is at least not systematically biased towards whatever is smallest.
        if not pool.prefer:
            return (*lead, 1, 0.0, model)
        return (*lead, *size_rank(model))

    return sorted(members, key=key)


def _default_exclusion(pool: VirtualModelPool) -> str:
    """Why a pool's own default left this candidate out.

    Named rather than generic, because the two defaults are fixed differently. A
    tier exclusion is corrected by ticking the model; a price exclusion usually
    means the model is not free and the pool is the free one, which is a
    different conversation.

    The first version interpolated `default_tier` unconditionally and produced
    "outside this pool's default  tier" — two spaces and no tier — for every
    price-filtered pool, because `ravis/cheap` has no tier at all.
    """
    if pool.default_tier and pool.max_price_per_million is not None:
        return (
            f"outside this pool's default {pool.default_tier} tier, or above its "
            f"${pool.max_price_per_million:g} per-million ceiling — tick it to include it"
        )
    if pool.default_tier:
        return f"outside this pool's default {pool.default_tier} tier — tick it to include it"
    if pool.max_price_per_million is not None:
        ceiling = pool.max_price_per_million
        return (
            "costs more than nothing per token, and this pool is the free one"
            if ceiling == 0
            else f"above this pool's ${ceiling:g} per-million ceiling"
        ) + " — tick it to include it"
    return "not among this pool's default members — tick it to include it"


def _reach_rank(
    model: str, residency: ResidencySnapshot, remote: frozenset[str]
) -> float:
    """How much work stands between the router and a first token.

    **A remote model is not a cold one, and treating them alike was the
    switchboard's central error.** `Residency.UNKNOWN` ranked 2, tied with
    `COLD`, and every cloud model is UNKNOWN because a cloud model has no
    residency to report. So RAVIS believed that calling an API and loading a
    seventy-billion-parameter model off disk cost about the same. One is a
    network round trip; the other is tens of seconds and gigabytes of RAM.

    The ordering that follows is fact rather than estimate. A resident local
    model needs neither a load nor a network hop. A remote model needs no load —
    that is what "hosted" means — and one round trip. A cold local model needs
    the load, which is the largest of the three by orders of magnitude.

    What this deliberately does *not* claim is how long any of it takes. RAVIS
    measures time-to-first-token per model in `HealthRegistry`, but only for
    models it has actually called: a handful out of six hundred. Ranking a
    catalogue on four samples would be the invented measurement §9.4 forbids,
    so this ranks the *steps required*, which is knowable for every model.
    """
    if model in remote:
        return _REMOTE_REACH
    return residency_rank(residency.state_of(model))


# Strictly between WARM (1) and COLD (2), which is the whole point and which the
# first attempt got wrong: it was set to 2, tying with COLD, and reproduced the
# exact conflation it was written to remove. A float rather than renumbering
# `_RESIDENCY_RANK`, because those values mean something to the residency layer
# and nothing here should redefine them.
_REMOTE_REACH = 1.5

# Where an unpriced model sorts in a cheap pool: last.
#
# **Not free.** Only OpenRouter and the local runtimes publish a price — OpenAI,
# Google and Anthropic ship catalogues with no pricing at all — so treating
# absence as zero would hand every cheap route to the providers that happen to
# say least about themselves. Sorting unknown last is the fail-closed direction:
# a model whose cost nobody stated is not chosen *for* its cost.
_UNPRICED = float("inf")


# Where an unmeasured model sorts in a speed-ranked pool: with the middle, not
# at the back.
#
# Every model starts unmeasured, and RAVIS only measures a model by routing to
# it. Sorting unmeasured last would close the loop: never chosen, never
# measured, never chosen. Neutral lets an untimed candidate still win on the
# terms below — the pool's declared preference, its size tier, reach — and get
# its first samples, after which it ranks on what it actually did.
_UNMEASURED_MS = 1_000.0


def _speed_rank(
    model: str, observed: Mapping[str, float], bucket_ms: float = 0.0
) -> float:
    """Median time-to-first-token, for models with enough samples to mean it.

    `Observations.ttft_for_ranking` has already dropped anything below the
    sample floor, so a model missing here is one RAVIS has not timed enough —
    not one that was slow.

    `bucket_ms` widens the comparison so models within a window count as equally
    fast and a later term decides between them. It is how a pool uses speed
    *and* cost without weighting one against the other: 380 ms and 420 ms are
    not a difference anybody would trade money for, and treating that ordering
    as meaningful lets a rounding error outrank a published price.
    """
    measured = observed.get(model, _UNMEASURED_MS)
    if bucket_ms <= 0:
        return measured
    return math.ceil(measured / bucket_ms) * bucket_ms


def _price_rank(known: ModelCapabilities | None) -> float:
    """What a million tokens costs, or infinity when nobody published it."""
    if known is None or known.price_per_million is None:
        return _UNPRICED
    return known.price_per_million


def _selection_reason(
    pool: VirtualModelPool,
    eligible: list[str],
    residency: ResidencySnapshot,
    memory: MemoryReading,
) -> str:
    """Say honestly why the winner won.

    Which, at M5, is usually "it sorted first". Saying so is the point: §9.7
    requires an explanation to separate facts from unknowns, and claiming a
    quality judgement RAVIS has no evidence for would be the opaque magic §9.4
    forbids. This sentence changes when SIRVIS evidence lands at M13.
    """
    selected = eligible[0]
    matched = next((fragment for fragment in pool.prefer if fragment in selected), "")
    basis = (
        f"matches the pool's declared preference '{matched}'"
        if matched
        else "first eligible candidate in stable order"
    )
    parts = [basis]

    state = residency.state_of(selected)
    if state in (Residency.HOT, Residency.WARM):
        parts.append(f"already loaded ({state.value}), so no load is required")
    elif residency.known:
        parts.append(f"not currently loaded ({state.value}); using it will cost a load")

    if memory.under_pressure and memory.free_fraction is not None:
        parts.append(
            f"memory is tight ({memory.free_fraction:.0%} free), so already-loaded "
            "models were preferred over the pool's usual ordering"
        )

    parts.append(_size_note(pool, eligible, residency, memory))
    others = len(eligible) - 1
    tail = f"; {others} other eligible candidate(s) ranked lower" if others else ""
    # **Eligibility and order are different questions**, and this sentence used
    # to answer only one of them — it said "no benchmark evidence is available
    # yet" unconditionally, which stopped being true at M13 and then appeared
    # inside decisions that were only possible *because* evidence existed. What
    # is still true is narrower: evidence admits and excludes, and nothing here
    # ranks one admitted build above another on quality. Cost genuinely is
    # absent until M15.
    return (
        f"{'. '.join(part for part in parts if part)}. "
        f"Evidence decides eligibility rather than order — a build is admitted or "
        f"excluded on it, and nothing ranks one admitted build above another on "
        f"quality. No cost data is available yet, and health is used to exclude "
        f"rather than to rank{tail}"
    )


def _size_note(
    pool: VirtualModelPool,
    eligible: list[str],
    residency: ResidencySnapshot,
    memory: MemoryReading,
) -> str:
    """Say so when size, rather than anything meaningful, broke the tie.

    §9.7 requires an explanation to separate facts from unknowns, and "it is
    smaller" is a weak reason that must not be allowed to read as a strong one.
    Naming it is also what stops the tiebreak becoming invisible policy: a
    reader who disagrees with it can see it happening.

    Returns an empty string when nothing was tied, which is the common case.
    """
    del memory  # Pressure changes the ordering, not whether size was the tiebreak.
    selected = eligible[0]
    peers = [
        model
        for model in eligible[1:]
        if pool.preference_rank(model) == pool.preference_rank(selected)
        and residency_rank(residency.state_of(model))
        == residency_rank(residency.state_of(selected))
    ]
    if not peers:
        return ""
    scale = parameter_scale(selected)
    measure = f"smallest at {scale:g}B" if scale is not None else "first in stable order"
    return (
        f"{len(peers)} other candidate(s) matched this pool exactly as well, and it was "
        f"chosen as the {measure} — a tiebreak on cost to run, not on quality"
    )
