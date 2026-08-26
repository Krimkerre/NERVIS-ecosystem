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


def test_the_cheap_pool_contains_only_models_that_cost_nothing() -> None:
    """"Cheap" means free, which is a fact somebody published.

    A local runtime bills nothing per token and OpenRouter publishes hundreds of
    `:free` variants at exactly zero. A paid model is not in this pool at all,
    rather than merely ranked below the free ones.
    """
    decision = RoutingEngine().select("ravis/cheap", CANDIDATES, remote_models=REMOTE)

    assert decision.selected == LOCAL
    assert PAID not in decision.fallbacks
    assert set(decision.fallbacks) <= {FREE_CLOUD}


def test_an_unpriced_model_is_not_treated_as_free() -> None:
    """Only OpenRouter and the local runtimes publish a price.

    OpenAI, Google and Anthropic ship catalogues with no pricing at all, so
    reading absence as zero would hand the cheap pool to whichever provider says
    least about itself. Google's free tier is real, but it is a quota on an
    account rather than a property of a model — and whether it applies depends
    on whether billing is attached, which is account state RAVIS cannot see.
    """
    decision = RoutingEngine().select("ravis/cheap", CANDIDATES, remote_models=REMOTE)

    assert decision.selected != UNPRICED
    assert UNPRICED not in decision.fallbacks


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


# ── balanced: two real signals, no invented exchange rate ──────────────────


MID_A = "vendor/sonnet-a"
MID_B = "vendor/sonnet-b"
MID_SLOW = "vendor/sonnet-slow"

MID = {
    MID_A: a_model(MID_A, 15.0),
    MID_B: a_model(MID_B, 3.0),
    MID_SLOW: a_model(MID_SLOW, 0.5),
}
MID_REMOTE = frozenset(MID)
# A and B are 40ms apart; the third is two seconds behind both.
TIMINGS = {MID_A: 380.0, MID_B: 420.0, MID_SLOW: 2_400.0}


def balanced(observed: dict[str, float]) -> str | None:
    return RoutingEngine().select(
        "ravis/balanced", MID, remote_models=MID_REMOTE, observed_ttft_ms=observed
    ).selected


def test_balanced_treats_a_small_speed_gap_as_a_tie_and_takes_the_cheaper() -> None:
    """The whole reason it is balanced rather than fast.

    380ms and 420ms is not a difference anybody would trade money for, and
    treating that ordering as meaningful lets a rounding error outrank a
    published price. Both land in the same quarter-second bucket, so the $3
    model wins over the $15 one.
    """
    assert balanced(TIMINGS) == MID_B


def test_balanced_still_refuses_a_genuinely_slow_model() -> None:
    """Cheapest of the three by a wide margin, and two seconds behind.

    Price only decides *within* a speed bucket. A model in a slower bucket never
    reaches the comparison, which is what stops "balanced" collapsing into
    "cheap".
    """
    assert balanced(TIMINGS) != MID_SLOW


def test_balanced_falls_back_to_price_when_nothing_is_measured() -> None:
    """Every model starts unmeasured and they all bucket identically, so the
    published price is the only fact left — which is the right answer rather
    than a coin toss on alphabetical order."""
    assert balanced({}) == MID_SLOW


def test_the_three_pools_answer_differently_from_the_same_facts() -> None:
    """If they agreed, two of them would be decoration."""
    fast = RoutingEngine().select(
        "ravis/fast", MID, remote_models=MID_REMOTE, observed_ttft_ms=TIMINGS
    ).selected
    cheap = RoutingEngine().select(
        "ravis/cheap", MID, remote_models=MID_REMOTE, observed_ttft_ms=TIMINGS
    ).selected

    assert fast == MID_A          # quickest, exactly ordered
    assert cheap == MID_SLOW      # least money, speed irrelevant
    assert balanced(TIMINGS) == MID_B
    assert len({fast, cheap, balanced(TIMINGS)}) == 3


def test_a_bucket_of_zero_orders_exactly() -> None:
    """`ravis/fast` wants the quickest, not the quickest-ish."""
    from ravis.core.pools import POOLS_BY_ID
    from ravis.routing.engine import _speed_rank

    assert POOLS_BY_ID["ravis/fast"].speed_bucket_ms == 0.0
    assert _speed_rank(MID_A, TIMINGS, 0.0) == 380.0
    assert _speed_rank(MID_A, TIMINGS, 250.0) == 500.0
    assert _speed_rank(MID_B, TIMINGS, 250.0) == 500.0


# ── one candidate's 401 is not evidence about the other sixty-six ──────────


def a_chain(from_pool: bool):
    from ravis.reliability.attempts import AttemptChain
    from ravis.reliability.health import HealthRegistry

    chain = AttemptChain(health=HealthRegistry(), provider="p", from_pool=from_pool)
    chain.load("first", ["second", "third"])
    return chain


def test_a_pool_falls_back_past_an_authentication_failure() -> None:
    """Observed live: `ravis/balanced` selected a free Gemma model on
    OpenRouter, OpenRouter's own call to Google came back 401, and the chain
    stopped with sixty-six untried candidates and a credential error about
    somebody else's key. Nothing in that 401 was evidence about the other
    sixty-six — and a pool asked for something that works.
    """
    from ravis.reliability.failures import FailureClass

    chain = a_chain(from_pool=True)
    assert chain.next_target() == "first"
    chain.failed("first", 0.0, FailureClass.AUTHENTICATION, "401")

    assert chain.next_target() == "second"


def test_a_direct_address_still_surfaces_the_401() -> None:
    """"Use this one" and "pick something that works" are different requests.

    Falling back here would replace a fixable error that names the problem with
    a no-route that does not.
    """
    from ravis.reliability.failures import FailureClass

    chain = a_chain(from_pool=False)
    assert chain.next_target() == "first"
    chain.failed("first", 0.0, FailureClass.AUTHENTICATION, "401")

    assert chain.next_target() is None


def test_every_other_terminal_class_keeps_its_policy_even_from_a_pool() -> None:
    """An invalid request really would fail the same way everywhere, and
    spending a second model's time to prove it is what the flag prevents."""
    from ravis.reliability.failures import FailureClass

    chain = a_chain(from_pool=True)
    assert chain.next_target() == "first"
    chain.failed("first", 0.0, FailureClass.INVALID_REQUEST, "bad body")

    assert chain.next_target() is None
