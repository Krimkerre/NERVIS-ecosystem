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
from ravis.routing.explain import ExcludedCandidate, RouteDecision


class RoutingEngine:
    """Resolves what a client addressed into a model to call.

    Holds no state and performs no I/O: capabilities are passed in. That keeps it
    testable without a provider and keeps the decision reproducible — §9.7's
    determinism gate requires that fixed inputs produce the same decision *and*
    the same explanation.
    """

    def select(self, requested: str, candidates: dict[str, ModelCapabilities]) -> RouteDecision:
        """Resolve a requested model, pool or direct address to a decision.

        Three shapes arrive here, and they are handled differently on purpose:
        a plain model name is passed through untouched, a direct address names
        its target explicitly, and a pool is resolved by invariant.
        """
        if is_pool_id(requested):
            return self._select_from_pool(POOLS_BY_ID[requested], candidates)

        target = direct_target(requested)
        if target is not None:
            return self._direct(requested, target, candidates)

        # A plain model name. RAVIS does not second-guess it: §5.3 puts an
        # explicit request above any inference, and the transparent path exists
        # precisely so a client can address an upstream model directly.
        return RouteDecision(
            requested=requested,
            selected=requested,
            reason="named directly by the client; no pool resolution applied",
        )

    def _direct(
        self, requested: str, target: str, candidates: dict[str, ModelCapabilities]
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
        )

    def _select_from_pool(
        self, pool: VirtualModelPool, candidates: dict[str, ModelCapabilities]
    ) -> RouteDecision:
        """Resolve a pool to one model, or explain why it cannot be resolved."""
        decision = RouteDecision(
            requested=pool.pool_id,
            pool_id=pool.pool_id,
            considered=sorted(candidates),
            requirements=_describe_requirements(pool),
        )
        decision.excluded = _exclusions(pool, candidates)
        eligible = pool.eligible(candidates)

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
        decision.reason = _selection_reason(pool, eligible)
        return decision


def _describe_requirements(pool: VirtualModelPool) -> list[str]:
    """The pool's invariants, in the words a route explanation will show."""
    described = [
        f"{capability.value} REQUIRED"
        for capability in sorted(pool.requirements.required, key=lambda item: item.value)
    ]
    if pool.requirements.minimum_context:
        described.append(f"minimum context {pool.requirements.minimum_context}")
    return described or ["none"]


def _exclusions(
    pool: VirtualModelPool, candidates: dict[str, ModelCapabilities]
) -> list[ExcludedCandidate]:
    """Every candidate that failed, with all of its reasons.

    All reasons rather than the first: a model failing on both tools and context
    needs a different fix from one failing on context alone, and an explanation
    that stops at the first failure hides that.
    """
    excluded = []
    for model in sorted(candidates):
        reasons = pool.requirements.unmet_by(candidates[model])
        if reasons:
            excluded.append(ExcludedCandidate(model=model, reasons=reasons))
    return excluded


def _selection_reason(pool: VirtualModelPool, eligible: list[str]) -> str:
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
    others = len(eligible) - 1
    tail = f"; {others} other eligible candidate(s) not preferred" if others else ""
    return (
        f"{basis}. No benchmark evidence, cost data or health history is available yet, "
        f"so nothing ranked it above the others on quality{tail}"
    )


def requirements_of(pool_id: str) -> frozenset[Capability]:
    """The capabilities a pool requires, for callers that need them directly."""
    pool = POOLS_BY_ID.get(pool_id)
    return pool.requirements.required if pool else frozenset()
