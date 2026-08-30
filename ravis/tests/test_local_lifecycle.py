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

# The resident, weaker candidate. Named as a coding build because the pool is
# `ravis/coding` and its membership is now curated: it was `"chatty"`, which the
# pool correctly no longer admits, and a general chat model inside a coding pool
# was never what these tests were about. What they are about — whether paying a
# load is worth it for the session ahead — is unchanged, and needs two members
# of the pool under test rather than one member and one outsider.
SMALL_CODER = "codegemma-2b"

# The better, cold candidate. A named family rather than the bare `qwen-coder`
# it was, so the pool's declared order puts it above the small resident one —
# which is the premise every test below rests on and used to get from the
# fixture's names alone.
STRONG_CODER = "qwen3-coder-30b"


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
        _candidates(STRONG_CODER, SMALL_CODER),
        residency=_loaded(SMALL_CODER),
        memory=_memory(0.5),
    )

    assert decision.selected == STRONG_CODER


def test_memory_pressure_inverts_that_order() -> None:
    """M14's acceptance criterion: pressure produces a safe route change.

    Same pool, same candidates, same residency — only the memory reading differs,
    and now the loaded model wins because loading anything new is the thing to
    avoid.
    """
    decision = RoutingEngine().select(
        AGENT_POOL_PREFERENCE,
        _candidates(STRONG_CODER, SMALL_CODER),
        residency=_loaded(SMALL_CODER),
        memory=_memory(0.05),
    )

    assert decision.selected == SMALL_CODER


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
        _candidates(STRONG_CODER, SMALL_CODER),
        residency=_loaded(SMALL_CODER),
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


# ── M14's remaining half: the load-versus-don't tradeoff (§12.2) ─────────────


def test_an_unmeasured_application_routes_exactly_as_before() -> None:
    """The default, and the reason the tradeoff is tri-state.

    `None` means RAVIS has not watched this application long enough to know how
    long its sessions run. Guessing "short" there would silently invert a
    documented behaviour — that a coding pool reaches for a coding model even at
    the cost of a load — for every client that had never been observed. Not
    knowing routes as it always did.
    """
    decision = RoutingEngine().select(
        AGENT_POOL_PREFERENCE,
        _candidates(STRONG_CODER, SMALL_CODER),
        residency=_loaded(SMALL_CODER),
        memory=_memory(0.5),
        expected_session_requests=None,
    )

    assert decision.selected == STRONG_CODER


def test_a_measured_short_session_declines_to_pay_a_load() -> None:
    """§12.2, in its own words: *for one simple question B is the worst choice
    despite being the stronger model.*

    Same pool, same candidates, same memory as the test above — the only
    difference is that RAVIS has measured this application making one-shot
    calls, so the fourteen-second load buys nothing.
    """
    decision = RoutingEngine().select(
        AGENT_POOL_PREFERENCE,
        _candidates(STRONG_CODER, SMALL_CODER),
        residency=_loaded(SMALL_CODER),
        memory=_memory(0.5),
        expected_session_requests=1,
    )

    assert decision.selected == SMALL_CODER
    assert "amortise" in decision.reason, decision.reason


def test_a_measured_long_session_pays_the_load_for_the_better_model() -> None:
    """The other half: *for a session expected to make 100 requests, loading B
    is worth it.*

    This needed no rule of its own — preference already outranks warmth, so the
    stronger model wins once the brake is off. Asserted anyway, because "the
    default happens to be right" is a claim that should fail loudly if the
    default changes.
    """
    decision = RoutingEngine().select(
        AGENT_POOL_PREFERENCE,
        _candidates(STRONG_CODER, SMALL_CODER),
        residency=_loaded(SMALL_CODER),
        memory=_memory(0.5),
        expected_session_requests=100,
    )

    assert decision.selected == STRONG_CODER


def test_memory_pressure_still_refuses_a_load_for_a_long_session() -> None:
    """Where M14's two halves meet, and the direction that must not invert.

    A long session is not a reason to load into a machine that has no room. The
    observation half exists precisely to stop that, and a busy conversation must
    not be able to talk it round.
    """
    decision = RoutingEngine().select(
        AGENT_POOL_PREFERENCE,
        _candidates(STRONG_CODER, SMALL_CODER),
        residency=_loaded(SMALL_CODER),
        memory=_memory(0.05),
        expected_session_requests=100,
    )

    assert decision.selected == SMALL_CODER


def test_a_hosted_model_is_never_penalised_as_a_load() -> None:
    """A remote model needs a round trip and no load, whatever the session length.

    `_reach_rank` already draws this line and the tradeoff has to respect it, or
    a short-session client would be pushed onto whatever happens to be resident
    even when the alternative costs nothing to reach.
    """
    decision = RoutingEngine().select(
        AGENT_POOL_PREFERENCE,
        _candidates(STRONG_CODER, SMALL_CODER),
        residency=_loaded(SMALL_CODER),
        memory=_memory(0.5),
        remote_models=frozenset({STRONG_CODER}),
        expected_session_requests=1,
    )

    assert decision.selected == STRONG_CODER
