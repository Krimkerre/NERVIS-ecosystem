"""Choosing a model for a request (RAVIS.md §9).

M5 implements the eligibility half of §9.1's pipeline and the smallest honest
version of the ranking half. That asymmetry is deliberate, and worth
understanding before adding to it.

Eligibility is a hard filter: pool invariants, then capability checks that fail
closed. §9.1 is explicit that no preference outweighs a failed hard constraint,
so this runs first and separately, and a candidate removed here is never
reconsidered by scoring.

Ranking, at M5, is declared preference then alphabetical order — and nothing
more. There is no benchmark evidence yet (M13), no cost data (M15) and no health
history (M12), so any richer score would be arithmetic over numbers nobody
measured. §9.4 rules out exactly that: routing must be explainable rather than an
opaque oracle, and an explanation that cites an invented weighting is worse than
one that admits the choice was made on stable ordering.
"""

from __future__ import annotations

from ravis.core.capabilities import Capability, ModelCapabilities
from ravis.core.pools import POOLS_BY_ID, VirtualModelPool, direct_target, is_pool_id
from ravis.core.requests import NormalizedRequest
from ravis.routing.explain import ExcludedCandidate, RouteDecision
from ravis.routing.requirements import RequestRequirements, analyse, unmet_by, unverified_notes
from ravis.runtime.residency import Residency, ResidencySnapshot, residency_rank
from ravis.runtime.resources import MemoryReading


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
        """
        requirements = analyse(request) if request else RequestRequirements()
        if is_pool_id(requested):
            return self._select_from_pool(
                POOLS_BY_ID[requested],
                candidates,
                residency or ResidencySnapshot(),
                memory or MemoryReading(),
                requirements,
            )

        target = direct_target(requested)
        if target is not None:
            return self._direct(requested, target, candidates, requirements)

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
    ) -> RouteDecision:
        """Handle `ravis/<provider>/<model>`.

        Bypasses selection but not existence: addressing a model the upstream
        does not offer is a no-route rather than a request forwarded to fail
        confusingly at the provider.
        """
        if candidates and target not in candidates:
            return RouteDecision(
                requested=requested,
                selected=None,
                reason=f"{target} is not offered by the configured upstream",
                considered=sorted(candidates),
            )
        return RouteDecision(
            requested=requested,
            selected=target,
            reason="direct address; selection bypassed, policy and tracking still apply",
            requirements=requirements.describe(),
        )

    def _select_from_pool(
        self,
        pool: VirtualModelPool,
        candidates: dict[str, ModelCapabilities],
        residency: ResidencySnapshot,
        memory: MemoryReading,
        requirements: RequestRequirements,
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
        decision.excluded = _exclusions(pool, candidates, requirements)
        eligible = _rank(pool, candidates, residency, memory, requirements)

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
        decision.reason = _selection_reason(pool, eligible, residency, memory)
        return decision


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
) -> list[ExcludedCandidate]:
    """Every candidate that failed, with all of its reasons.

    Pool invariants and request requirements are reported together and
    undifferentiated, because the person reading this wants to know why a model
    was not used — not which of two rule sources rejected it.
    """
    excluded = []
    for model in sorted(candidates):
        reasons = pool.requirements.unmet_by(candidates[model])
        reasons += unmet_by(requirements, candidates[model])
        if reasons:
            excluded.append(ExcludedCandidate(model=model, reasons=reasons))
    return excluded


def _rank(
    pool: VirtualModelPool,
    candidates: dict[str, ModelCapabilities],
    residency: ResidencySnapshot,
    memory: MemoryReading,
    requirements: RequestRequirements,
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
        if not pool.requirements.unmet_by(known) and not unmet_by(requirements, known)
    ]
    pressured = memory.under_pressure

    def key(model: str) -> tuple[int, int, str]:
        preference = pool.preference_rank(model)
        warmth = residency_rank(residency.state_of(model))
        return (warmth, preference, model) if pressured else (preference, warmth, model)

    return sorted(members, key=key)


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

    others = len(eligible) - 1
    tail = f"; {others} other eligible candidate(s) ranked lower" if others else ""
    return (
        f"{'. '.join(parts)}. No benchmark evidence, cost data or health history is "
        f"available yet, so nothing ranked it above the others on quality{tail}"
    )


def requirements_of(pool_id: str) -> frozenset[Capability]:
    """The capabilities a pool requires, for callers that need them directly."""
    pool = POOLS_BY_ID.get(pool_id)
    return pool.requirements.required if pool else frozenset()
