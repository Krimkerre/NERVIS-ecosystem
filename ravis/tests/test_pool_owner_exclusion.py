"""A model the owner rules out of a pool stays out: against evidence, a pick and the fallback.

13 September 2026: the owner ruled `openai/gpt-4.1-mini` out of coding work after it skipped
ticking plan steps in a Clarvis build whose tests passed. It stays in the chat pools, and an
explicit direct address still reaches it.
"""

from __future__ import annotations

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
)
from ravis.core.pools import POOLS_BY_ID
from ravis.routing.engine import RoutingEngine

MINI = "openai/gpt-4.1-mini"
OTHER = "anthropic/claude-sonnet-5"
CODING_POOLS = ("ravis/clarvis-agent", "ravis/coding", "ravis/agent")


def capable(model_id: str) -> ModelCapabilities:
    known = ModelCapabilities(model_id)
    for capability in Capability:
        known.record(CapabilityClaim(capability, CapabilityState.SUPPORTED, Provenance.MEASURED))
    known.context_window = 200_000
    return known


def test_the_coding_pools_never_pick_it_and_say_why() -> None:
    candidates = {name: capable(name) for name in (MINI, OTHER)}
    for pool in CODING_POOLS:
        decision = RoutingEngine().select(pool, candidates, remote_models=frozenset(candidates))
        assert decision.selected == OTHER, (pool, decision.selected)
        reasons = {entry.model: entry.reasons for entry in decision.excluded}
        assert reasons.get(MINI) == ["ruled out of this pool by the owner"], (pool, reasons)


def test_a_passing_measurement_does_not_bring_it_back() -> None:
    """A measured build normally joins whatever the families say; the owner's word outranks it."""
    candidates = {MINI: capable(MINI)}
    evidence = {MINI: {
        "clarvis-agent": CapabilityState.SUPPORTED.value, "agent": CapabilityState.SUPPORTED.value,
    }}
    for pool in CODING_POOLS:
        decision = RoutingEngine().select(
            pool, candidates, remote_models=frozenset(candidates), role_evidence=evidence
        )
        assert decision.selected is None, (pool, decision.selected)


def test_an_operator_pick_does_not_bring_it_back() -> None:
    candidates = {name: capable(name) for name in (MINI, OTHER)}
    decision = RoutingEngine().select(
        "ravis/clarvis-agent", candidates, remote_models=frozenset(candidates), chosen=(MINI,)
    )
    assert decision.selected != MINI


def test_the_chat_pools_still_reach_it() -> None:
    candidates = {MINI: capable(MINI)}
    for pool in ("ravis/clarvis-chat", "ravis/chat"):
        decision = RoutingEngine().select(pool, candidates, remote_models=frozenset(candidates))
        assert decision.selected == MINI, (pool, decision.selected)


def test_only_the_coding_pools_carry_the_exclusion() -> None:
    carrying = {pool_id for pool_id, pool in POOLS_BY_ID.items() if pool.owner_excludes(MINI)}
    assert carrying == set(CODING_POOLS)
