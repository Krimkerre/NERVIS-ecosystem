"""Occasionally answering with something other than the best model, on purpose.

Asked for after a longer conversation about a trap the operator spotted
themselves: *"is there something in place that never used models get picked
too? no way to measure things, if the model never gets selected, right?"*

Exactly right, and it had teeth once `ravis/chat` started ranking on speed.
A model nothing has timed sorts as merely average, which is enough to keep it
out of first place forever -- never chosen, never measured, never chosen. And
SIRVIS cannot close the gap for hosted models: it drives local runtimes, so an
API model can only ever be measured by being used.

So: two switches, both off unless asked for, both in chat's Model settings.
The first spends the occasional request on a model that is not the best pick.
The second aims those requests at models nothing has measured at all.

**Why the dice are not thrown in here.** The engine is a pure function of its
arguments and §9.7 gates that. `Exploration` therefore carries the *result* of a
throw, made in the API layer, which is why every test below is deterministic
while the real thing is not.
"""

from __future__ import annotations

from ravis.core.capabilities import ModelCapabilities
from ravis.routing.engine import DEFAULT_EXPLORATION_RATE, Exploration, RoutingEngine

# **The ordinary winner, checked against the engine rather than assumed.**
# `ministral-14b-2512` is one of the families `ravis/chat` is written around
# (`preference_rank` 7), so it wins outright and no timing moves it -- which is
# the speed tiebreak behaving correctly, and was worth finding out the hard way:
# the first draft of this file assumed the *fastest* model won and two tests
# failed immediately.
USUAL = "mistralai/ministral-14b-2512"
# Two the pool admits but ranks well below it, and identically to each other:
# same 24B size, same `preference_rank` of 24. Somewhere for exploration to go.
QUICK = "mistralai/mistral-small-3.1-24b-instruct"
SLOW = "mistralai/mistral-small-24b-instruct-2501"

# Everything timed, so any answer other than `USUAL` is exploration and nothing
# else. Note the usual winner is not the quickest -- the pool's judgement still
# outranks speed, which is the point of the tiebreak being a tiebreak.
TIMED = {QUICK: 200.0, SLOW: 3_000.0, USUAL: 2_500.0}


def _catalogue(*models: str) -> dict[str, ModelCapabilities]:
    return {
        model: ModelCapabilities(model_id=model, context_window=32_000)
        for model in models
    }


def _decide(explore: Exploration | None = None,
            observed: dict[str, float] | None = None):  # noqa: ANN202
    candidates = _catalogue(QUICK, SLOW, USUAL)
    return RoutingEngine().select(
        "ravis/chat", candidates, remote_models=frozenset(candidates),
        observed_ttft_ms=TIMED if observed is None else observed,
        explore=explore,
    )


def test_nothing_changes_when_nobody_asked_for_it() -> None:
    """**The default, and the one that matters most.** Exploration costs the
    person asking a worse answer some of the time. Nothing may switch it on by
    inference."""
    assert _decide().selected == USUAL


def test_a_roll_above_the_rate_is_an_ordinary_turn() -> None:
    """Switched on is not the same as firing. Most turns of an exploring
    conversation are still ordinary ones -- otherwise it would be a lottery
    rather than a conversation."""
    assert _decide(Exploration(rate=0.08, roll=0.5)).selected == USUAL


def test_a_roll_under_the_rate_tries_something_else() -> None:
    """The switch doing its job."""
    chosen = _decide(Exploration(rate=0.5, roll=0.01)).selected

    assert chosen != USUAL, "exploration was on and fired, and still took the usual pick"
    assert chosen in {QUICK, SLOW}


def test_it_aims_at_the_model_nothing_has_timed() -> None:
    """**The second switch, and the whole reason for the first.** With `SLOW`
    the only untimed candidate, every exploring request should spend itself on
    it -- it is the one the ranking can never reach on merit, being both an
    also-ran family and unmeasured."""
    partial = {QUICK: 200.0, USUAL: 2_500.0}

    for roll in (0.001, 0.02, 0.04, 0.079):
        decision = _decide(Exploration(rate=0.08, roll=roll), observed=partial)
        assert decision.selected == SLOW, f"roll {roll} did not reach the unmeasured model"


def test_it_still_explores_once_everything_has_been_measured() -> None:
    """A single old sample from a machine under different load is not a current
    figure, so "explore" still means something when nothing is unmeasured. The
    alternative -- going quiet -- would make the switch stop working precisely
    when it had succeeded."""
    chosen = {
        _decide(Exploration(rate=0.5, roll=roll)).selected
        for roll in (0.01, 0.2, 0.45)
    }

    assert chosen - {USUAL}, "with everything measured, exploration stopped happening"


def test_the_whole_field_is_reachable_not_just_the_front() -> None:
    """The roll is reused as an index rather than drawn a second time, which is
    a purity requirement and an easy thing to get wrong: using it raw would only
    ever land in the first `rate` share of the list -- at 8%, the first model.
    Rescaling is what makes the last candidate reachable."""
    reached = {
        _decide(Exploration(rate=0.08, roll=roll)).selected
        for roll in (0.001, 0.03, 0.05, 0.079)
    }

    assert len(reached) > 1, "every exploring request landed on the same model"


def test_it_says_it_was_deliberate_rather_than_claiming_merit() -> None:
    """**Non-negotiable.** An explanation that let this read as an ordinary win
    would make every other route explanation less trustworthy too -- the same
    class of untruth as the cold-start line removed from the engine a day
    earlier. It also has to say where to switch it off."""
    decision = _decide(Exploration(rate=0.5, roll=0.01), observed={USUAL: 2_500.0})

    assert "on purpose" in decision.reason
    assert "never chosen is never measured" in decision.reason
    assert "chat's settings" in decision.reason


def test_the_model_that_would_have_won_is_still_the_fallback() -> None:
    """An explored model can come from anywhere in the ranking, so the fallback
    list cannot be sliced from index 1 -- doing that lists the explored model as
    its own fallback and drops the model that should catch the failure. Which
    matters more here than usual: an untried model is exactly the one most
    likely to fail."""
    decision = _decide(Exploration(rate=0.5, roll=0.01))

    assert decision.selected not in decision.fallbacks
    assert USUAL in decision.fallbacks, (
        "the model that would have won is not there to catch a failure -- and an "
        "untried model is exactly the one most likely to need it"
    )


def test_the_same_roll_always_routes_the_same_way() -> None:
    """§9.7's determinism gate. The engine stays a pure function of what it is
    given; the randomness lives at the edge, in the API layer."""
    once = _decide(Exploration(rate=0.5, roll=0.31)).selected
    again = _decide(Exploration(rate=0.5, roll=0.31)).selected

    assert once == again


def test_the_default_rate_is_occasional_rather_than_constant() -> None:
    """A number worth pinning: it is the cost of the feature. Frequent enough to
    time a catalogue over an afternoon, rare enough that a conversation does not
    feel like a lottery."""
    assert 0.0 < DEFAULT_EXPLORATION_RATE <= 0.15


def test_exploring_never_lands_back_on_the_usual_pick() -> None:
    """**A no-op dressed as a decision, found by a test that expected change.**

    The first draft drew the explored model from the whole eligible list. Since
    the roll is rescaled into an index, every roll well under the rate landed on
    index 0 -- the model that was going to answer anyway. Roughly one
    exploration in three quietly did nothing, while the reason line claimed a
    deliberate choice had been made.

    Swept across the range rather than sampled at one point, because the
    original defect was concentrated at the low end and a single mid-range roll
    would have missed it entirely.
    """
    for step in range(40):
        roll = step / 40 * 0.5
        chosen = _decide(Exploration(rate=0.5, roll=roll)).selected
        assert chosen != USUAL, f"roll {roll:.4f} explored its way to the usual pick"
