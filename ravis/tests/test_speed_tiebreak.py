"""Measured speed as a tiebreak in `ravis/chat` — after the pool's judgement.

Asked for in one sentence: *"make it so speed is also taken into consideration
for chat... not the main criterium, but definitely something to take into the
routing decision.. nobody likes a slow chatbot."*

Both halves of that are load-bearing and neither is provable without the other,
so both are tested here. `prefer_fast` — which `ravis/fast` declares — ranks
speed *ahead* of the pool's declared families, and a conversational pool doing
that would hand every turn to whichever small build answered quickest. So chat
gets `speed_tiebreak_ms`, consulted after the families, with a bucket wide
enough that only a difference a person would feel reorders anything.
"""

from __future__ import annotations

from ravis.core.capabilities import ModelCapabilities
from ravis.routing.engine import RoutingEngine

# **Two models the pool ranks identically**, checked rather than assumed:
# `preference_rank` returns 24 for both, and both are 24B, so the terms above
# speed cannot separate them and the tiebreak is what decides. The first version
# of this file used two models the families already ordered — the tests passed
# with the tiebreak removed, which is the only way to find out that they were
# testing nothing.
# **The quick one is the one that loses without the tiebreak.** Equal rank and
# equal size leave stable order to decide, which puts `…-2501` first — so the
# fast model has to be the other one, or the test passes for the wrong reason.
# It did, at first: with the tiebreak removed these still went green.
QUICK = "mistralai/mistral-small-3.1-24b-instruct"
SLOW = "mistralai/mistral-small-24b-instruct-2501"
# Ranked 62 — the score a model the pool never declared receives — and timed
# absurdly fast, to prove speed cannot buy its way past the pool's judgement.
FAST_STRANGER = "some-vendor/unlisted-4b"
# Ranked 7: squarely one of the families the pool is written around.
DECLARED = "mistralai/ministral-14b-2512"

def _catalogue(*models: str) -> dict[str, ModelCapabilities]:
    return {
        model: ModelCapabilities(model_id=model, context_window=32_000)
        for model in models
    }


def _chosen(observed: dict[str, float], *models: str) -> str | None:
    candidates = _catalogue(*(models or (QUICK, SLOW)))
    return RoutingEngine().select(
        "ravis/chat", candidates, remote_models=frozenset(candidates),
        observed_ttft_ms=observed,
    ).selected


def test_with_nothing_measured_the_pool_decides_alone() -> None:
    """The control: whatever wins here wins on the families, not on timings."""
    assert _chosen({}) in {QUICK, SLOW}


def test_a_clearly_faster_equal_wins_the_conversation() -> None:
    """**The half that was missing.** Among models the pool already considers
    right for the job, the one measured seconds quicker gets the turn."""
    assert _chosen({QUICK: 300.0, SLOW: 4_000.0}) == QUICK


def test_a_difference_nobody_would_feel_reorders_nothing() -> None:
    """The bucket. 300ms against 380ms is not a reason to change model, and a
    ranking that acted on it would let a rounding error outrank everything
    below it."""
    close = _chosen({QUICK: 300.0, SLOW: 380.0})
    same_order = _chosen({QUICK: 380.0, SLOW: 300.0})

    assert close == same_order, (
        "an 80ms difference reordered the pool; the tiebreak's bucket is meant "
        "to make differences that small invisible"
    )


def test_a_fast_stranger_does_not_take_the_conversation() -> None:
    """A model the pool never declared, timed sixty times faster than one it was
    written around, still does not win.

    **What this does not prove, checked rather than assumed.** Setting the pool
    to `prefer_fast` instead — speed ahead of preference — leaves this outcome
    unchanged, so the property holds through some earlier term and not through
    the choice made here. Left in because it is worth holding, and worded to
    stop claiming credit it has not earned. The claim about *where* speed sits
    is asserted structurally below, which is the level it is actually decidable
    at.
    """
    chosen = _chosen({FAST_STRANGER: 50.0, DECLARED: 3_000.0},
                     FAST_STRANGER, DECLARED)

    assert chosen == DECLARED


def test_chat_ranks_speed_as_a_tiebreak_and_not_as_its_purpose() -> None:
    """**The declaration itself, because the behaviour cannot separate them.**

    `prefer_fast` puts measured speed ahead of the pool's declared families;
    `speed_tiebreak_ms` puts it after. On the catalogue above the two produce
    the same winner, so a behavioural test cannot tell a conversational pool
    from a latency pool — and the difference is exactly what was asked for:
    speed considered, not speed deciding.

    `ravis/fast` is asserted alongside it, so this fails if the two pools' roles
    are ever swapped by an edit that looks locally reasonable in either file.
    """
    from ravis.core.pools import POOLS_BY_ID

    chat = POOLS_BY_ID["ravis/chat"]
    fast = POOLS_BY_ID["ravis/fast"]

    assert chat.speed_tiebreak_ms > 0, "chat ignores measured speed entirely"
    assert not chat.prefer_fast, (
        "chat ranks on speed ahead of what the pool is for, which hands every "
        "turn to whichever small build answers quickest"
    )
    assert chat.speed_tiebreak_ms >= 500, (
        "the bucket is narrow enough that a difference nobody would feel "
        "reorders the pool"
    )
    assert fast.prefer_fast, "the pool whose whole purpose is latency stopped ranking on it"
