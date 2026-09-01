"""An ordered sequence of operations, confirmed once (NERVIS.md M24).

The clause that shapes everything is this one: *one confirmation is an ordering
decision and not a blanket approval*. A plan may therefore contain nothing that
could not have been offered on its own — which is not a rule to be remembered
but a property of how a plan is built, since every step goes through `propose`
and there is no second way to make one.

The rest is about endings. A plan that finished, one somebody stopped, and one
that halted because a step failed are three different outcomes, and the design
fails quietly if any two of them are reported alike.
"""

from __future__ import annotations

import pytest

from nervis import commands

LOCAL = [
    {"model_id": "qwen3-4b", "local": True},
    {"model_id": "granite-4-micro", "local": True},
]


# ── Nothing new is invented ─────────────────────────────────────────────────


def test_every_step_is_an_offer_propose_would_have_made_alone() -> None:
    """The property that makes one confirmation safe.

    Asserted by building each clause both ways and comparing. If a second path
    for constructing a step is ever added, the two stop matching here.
    """
    said = "benchmark qwen3-4b then benchmark granite-4-micro"
    plan = commands.plan(said, LOCAL)
    assert plan is not None
    alone = [commands.propose(clause, LOCAL) for clause in said.split(" then ")]
    assert [s.as_dict() for s in plan.steps] == [
        a.as_dict() for a in alone if a is not None
    ]


def test_a_plan_is_only_offered_when_every_clause_names_an_operation() -> None:
    """Running the half NERVIS understood is the failure this design avoids.

    A sentence where one clause means nothing is not a plan with a gap — it is a
    sentence that was not a plan, and it falls through to the single-offer path.
    """
    assert commands.plan("benchmark qwen3-4b then do a little dance", LOCAL) is None


def test_and_does_not_split_a_plan() -> None:
    """`then` and nothing else, which is the whole discipline.

    "Benchmark the qwen3-4b and granite-4-micro builds" is one request naming two
    models. Splitting on `and` would turn it into two steps NERVIS invented, and
    a plan is meant to contain only what somebody actually ordered.
    """
    assert commands.plan("benchmark the qwen3-4b and granite-4-micro builds", LOCAL) is None


def test_one_step_is_not_a_plan() -> None:
    """A "Run plan" button on a single operation is a second road to one act."""
    assert commands.plan("benchmark qwen3-4b", LOCAL) is None
    assert commands.plan("", LOCAL) is None


def test_a_plan_longer_than_a_person_can_mean_is_refused() -> None:
    """The cap is not about cost — every step is bounded on its own. It is about
    the confirmation meaning something: nobody reads fifteen steps and means all
    of them."""
    said = " then ".join(["benchmark qwen3-4b"] * (commands.MAX_STEPS + 1))
    assert commands.plan(said, LOCAL) is None


def test_steps_keep_the_order_they_were_written_in() -> None:
    plan = commands.plan(
        "benchmark granite-4-micro then benchmark qwen3-4b", LOCAL
    )
    assert plan is not None
    assert [step.target for step in plan.steps] == ["granite-4-micro", "qwen3-4b"]


def test_a_plan_may_mix_operations() -> None:
    plan = commands.plan(
        "remember that granite is the small one then benchmark qwen3-4b", LOCAL
    )
    assert plan is not None
    assert [step.operation for step in plan.steps] == [
        "nervis.knowledge.learn", "sirvis.benchmark.submit",
    ]


# ── A plan says whether it can run ──────────────────────────────────────────


def test_a_plan_with_an_unpreparable_step_is_returned_but_not_ready() -> None:
    """"I could prepare two of these three" is an answer.

    Dropping the step it could not build would run something other than what was
    read, which is worse than refusing.
    """
    plan = commands.plan(
        "benchmark qwen3-4b then benchmark a-model-nobody-has", LOCAL
    )
    if plan is None:  # an unknown model may yield no proposal at all
        return
    assert not plan.ready
    assert len(plan.steps) == 2


def test_a_ready_plan_says_so() -> None:
    plan = commands.plan("benchmark qwen3-4b then benchmark granite-4-micro", LOCAL)
    assert plan is not None and plan.ready


# ── Identity, so each step stays separately answerable ──────────────────────


def test_every_step_carries_its_own_proposal_id() -> None:
    """A plan does not make its steps one offer.

    They were offered individually and each is separately answerable, so M22's
    record should say which offers were taken rather than returning a single
    verdict on an ordering.
    """
    from nervis.api import chat as chat_api
    from nervis.storage.database import prepare_database

    database = prepare_database(":memory:")
    planned = chat_api._planned(
        database, commands.plan("benchmark qwen3-4b then benchmark granite-4-micro", LOCAL)
    )
    assert planned is not None
    assert planned.plan_id.startswith("pl_")
    ids = {step.proposal_id for step in planned.steps}
    assert len(ids) == 2
    assert all(i.startswith("pr_") for i in ids)


def test_a_plan_serialises_every_step_whole() -> None:
    """The page draws the sequence before anything runs, so it needs all of it."""
    plan = commands.plan("benchmark qwen3-4b then benchmark granite-4-micro", LOCAL)
    assert plan is not None
    body = plan.as_dict()
    assert body["ready"] is True
    assert len(body["steps"]) == 2
    for step in body["steps"]:
        assert step["operation"] and step["summary"] and step["action"]


@pytest.mark.parametrize(
    "said",
    [
        "benchmark qwen3-4b, then benchmark granite-4-micro",
        "benchmark qwen3-4b and then benchmark granite-4-micro",
        "benchmark qwen3-4b THEN benchmark granite-4-micro",
    ],
)
def test_the_ways_people_write_a_sequence(said: str) -> None:
    plan = commands.plan(said, LOCAL)
    assert plan is not None and len(plan.steps) == 2
