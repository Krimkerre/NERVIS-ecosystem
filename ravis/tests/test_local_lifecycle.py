"""M14 — residency preference and the memory-pressure route change.

The behaviour this exists to prevent is concrete: during M5 testing a loaded
model was evicted and replaced simply because another sorted earlier
alphabetically. Residency is what stops that, and memory pressure is what makes
it decisive.
"""

from __future__ import annotations

from ravis.core.capabilities import ModelCapabilities
from ravis.routing.engine import RoutingEngine
from ravis.runtime.residency import Residency, ResidencySnapshot
from ravis.runtime.resources import MemoryReading

# A pool with no declared preference and no invariant, so residency is the only
# signal in play. Deliberately `ravis/auto` rather than `ravis/clarvis-chat`,
# which gained a preference in M12 — a test whose premise says "no preference"
# must be pointed at a pool that actually has none, or it starts passing for a
# reason its own comment denies.
UNCONSTRAINED = "ravis/auto"
# A pool that prefers coding models, so declared intent and residency compete.
AGENT_POOL_PREFERENCE = "ravis/coding"


def _candidates(*names: str) -> dict[str, ModelCapabilities]:
    return {name: ModelCapabilities(model_id=name) for name in names}


def _loaded(*names: str) -> ResidencySnapshot:
    return ResidencySnapshot(states={name: Residency.HOT for name in names}, known=True)


def _memory(free_fraction: float) -> MemoryReading:
    total = 16 * 2**30
    return MemoryReading(available_bytes=int(total * free_fraction), total_bytes=total)


def test_a_loaded_model_wins_a_tie() -> None:
    """The M5 regression, in one test.

    Both models satisfy the pool and neither is preferred, so alphabetical order
    would pick `alpha`. It is not loaded; `zeta` is. Loading `alpha` would evict
    something for no benefit at all.
    """
    decision = RoutingEngine().select(
        UNCONSTRAINED, _candidates("alpha", "zeta"), residency=_loaded("zeta"), memory=_memory(0.5)
    )

    assert decision.selected == "zeta"


def test_declared_preference_still_beats_residency_normally() -> None:
    """A coding pool should reach for a coding model even if that costs a load.

    Residency is a soft preference (§9.2), not an override: paying a load once
    for the right model is usually correct when memory is not tight.
    """
    decision = RoutingEngine().select(
        AGENT_POOL_PREFERENCE,
        _candidates("qwen-coder", "chatty"),
        residency=_loaded("chatty"),
        memory=_memory(0.5),
    )

    assert decision.selected == "qwen-coder"


def test_memory_pressure_inverts_that_order() -> None:
    """M14's acceptance criterion: pressure produces a safe route change.

    Same pool, same candidates, same residency — only the memory reading differs,
    and now the loaded model wins because loading anything new is the thing to
    avoid.
    """
    decision = RoutingEngine().select(
        AGENT_POOL_PREFERENCE,
        _candidates("qwen-coder", "chatty"),
        residency=_loaded("chatty"),
        memory=_memory(0.05),
    )

    assert decision.selected == "chatty"


def test_pressure_never_changes_which_models_are_eligible() -> None:
    """Residency ranks; it never excludes (§9.2).

    A cold model under pressure is still a legal answer when it is the only one —
    degrading a hard invariant to save memory would be the wrong trade entirely.
    """
    decision = RoutingEngine().select(
        UNCONSTRAINED, _candidates("only-cold"), residency=_loaded(), memory=_memory(0.01)
    )

    assert decision.selected == "only-cold"


def test_unknown_residency_leaves_ordering_untouched() -> None:
    """A router with no runtime visibility behaves exactly as it did before."""
    decision = RoutingEngine().select(
        UNCONSTRAINED,
        _candidates("alpha", "zeta"),
        residency=ResidencySnapshot(),
        memory=_memory(0.5),
    )

    assert decision.selected == "alpha"


def test_the_explanation_says_the_model_was_already_loaded() -> None:
    """§9.7: a route explanation names the factors that decided it."""
    decision = RoutingEngine().select(
        UNCONSTRAINED, _candidates("alpha", "zeta"), residency=_loaded("zeta"), memory=_memory(0.5)
    )

    assert "already loaded" in decision.reason


def test_the_explanation_says_when_pressure_changed_the_route() -> None:
    decision = RoutingEngine().select(
        AGENT_POOL_PREFERENCE,
        _candidates("qwen-coder", "chatty"),
        residency=_loaded("chatty"),
        memory=_memory(0.05),
    )

    assert "memory is tight" in decision.reason


def test_the_explanation_warns_when_a_load_will_be_paid() -> None:
    """So a slow first response is explained rather than mysterious."""
    decision = RoutingEngine().select(
        UNCONSTRAINED,
        _candidates("alpha"),
        residency=_loaded("something-else"),
        memory=_memory(0.5),
    )

    assert "will cost a load" in decision.reason
