"""Routing that switches between local and cloud, rather than merely allowing both.

§9.2's soft column reads *"prefer local · prefer fast · prefer cheap · prefer
already loaded"*. Only the last was implemented. `ravis/cheap` — "Least monetary
cost, preferring local models" — declared no requirements, no preference and no
tier, so with an installed local model and a paid cloud model both eligible it
selected whichever sorted first alphabetically. The description was prose with
nothing behind it, the same failure the locality work fixed for `ravis/local`.
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
from ravis.runtime.residency import Residency, ResidencySnapshot


def a_model(model_id: str, price: float | None = None) -> ModelCapabilities:
    known = ModelCapabilities(model_id)
    for capability in Capability:
        known.record(
            CapabilityClaim(capability, CapabilityState.SUPPORTED, Provenance.MEASURED)
        )
    known.context_window = 200_000
    known.price_per_million = price
    return known


LOCAL = "qwen3-8b"
PAID = "anthropic/claude-opus"
FREE_CLOUD = "z-ai/glm:free"
UNPRICED = "openai/mystery"

CANDIDATES = {
    LOCAL: a_model(LOCAL, 0.0),
    PAID: a_model(PAID, 90.0),
    FREE_CLOUD: a_model(FREE_CLOUD, 0.0),
    UNPRICED: a_model(UNPRICED, None),
}
REMOTE = frozenset({PAID, FREE_CLOUD, UNPRICED})


def selected(pool: str, **kwargs: object) -> str | None:
    return RoutingEngine().select(
        pool, CANDIDATES, remote_models=REMOTE, **kwargs  # type: ignore[arg-type]
    ).selected


# ── prefer cheap ───────────────────────────────────────────────────────────


def test_the_cheap_pool_picks_the_cheapest() -> None:
    assert selected("ravis/cheap") == LOCAL


def test_an_unpriced_model_is_not_treated_as_free() -> None:
    """Only OpenRouter and the local runtimes publish a price.

    OpenAI, Google and Anthropic ship catalogues with no pricing at all, so
    reading absence as zero would hand every cheap route to whichever provider
    says least about itself. Unknown sorts last.
    """
    decision = RoutingEngine().select(
        "ravis/cheap", CANDIDATES, remote_models=REMOTE
    )

    # Below even the $90 model, and below the fallback cut — asserted on the
    # ranking rather than on `fallbacks`, which `MAX_FALLBACKS` truncates.
    assert decision.selected != UNPRICED
    assert UNPRICED not in decision.fallbacks
    assert PAID in decision.fallbacks


def test_free_beats_paid_wherever_it_runs() -> None:
    """A free cloud model outranks a paid one — placement is the tiebreak
    *after* price, not before it."""
    only_cloud = {PAID: CANDIDATES[PAID], FREE_CLOUD: CANDIDATES[FREE_CLOUD]}
    decision = RoutingEngine().select(
        "ravis/cheap", only_cloud, remote_models=REMOTE
    )

    assert decision.selected == FREE_CLOUD


def test_local_breaks_the_tie_between_two_free_models() -> None:
    """Both price at zero, so `prefer_local` decides. OpenRouter has hundreds of
    free models, which makes this the ordinary case rather than a corner."""
    free_only = {LOCAL: CANDIDATES[LOCAL], FREE_CLOUD: CANDIDATES[FREE_CLOUD]}
    decision = RoutingEngine().select("ravis/cheap", free_only, remote_models=REMOTE)

    assert decision.selected == LOCAL


# ── the preferences are opt-in ─────────────────────────────────────────────


def test_a_pool_that_declares_nothing_is_unchanged() -> None:
    """An unconditional price or placement term would become the entire
    ordering for every pool that declares no preference — which is most of
    them, and the exact trap the size tiebreak is already guarded against."""
    assert POOLS_BY_ID["ravis/auto"].prefer_cheap is False
    assert POOLS_BY_ID["ravis/auto"].prefer_local is False


# ── reach: a hosted model is not a cold one ────────────────────────────────


def test_a_remote_model_outranks_a_cold_local_one() -> None:
    """The switchboard's central error.

    `Residency.UNKNOWN` ranked 2, tied with `COLD`, and every cloud model is
    UNKNOWN because a cloud model has no residency to report. So RAVIS believed
    calling an API and loading a 70B model off disk cost about the same.
    """
    cold = ResidencySnapshot(states={LOCAL: Residency.COLD}, known=True)
    decision = RoutingEngine().select(
        "ravis/auto", CANDIDATES, residency=cold, remote_models=REMOTE
    )

    assert decision.selected != LOCAL


def test_a_resident_local_model_outranks_a_remote_one() -> None:
    """Already loaded needs neither a load nor a network hop."""
    hot = ResidencySnapshot(states={LOCAL: Residency.HOT}, known=True)
    decision = RoutingEngine().select(
        "ravis/auto", CANDIDATES, residency=hot, remote_models=REMOTE
    )

    assert decision.selected == LOCAL


def test_reach_ranks_steps_not_seconds() -> None:
    """What this deliberately does not claim is how long anything takes.

    TTFT is measured per model in `HealthRegistry`, but only for models actually
    called — a handful out of six hundred. Ranking a catalogue on that would be
    the invented measurement §9.4 forbids, so reach ranks the *steps required*,
    which is knowable for every model.
    """
    from ravis.routing.engine import _reach_rank

    hot = ResidencySnapshot(states={LOCAL: Residency.HOT}, known=True)
    cold = ResidencySnapshot(states={LOCAL: Residency.COLD}, known=True)

    assert _reach_rank(LOCAL, hot, REMOTE) < _reach_rank(PAID, hot, REMOTE)
    assert _reach_rank(PAID, cold, REMOTE) < _reach_rank(LOCAL, cold, REMOTE)
