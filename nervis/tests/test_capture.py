"""Noticing a fact nobody asked NERVIS to keep.

M23 built the whole road for a learned note: somebody says *remember that…*, a button appears,
they press it, and their own sentence is filed with its date. Three weeks later, on the owner's
own machine, `learned.md` did not exist. Nothing had ever been filed — not because the
machinery was broken but because nobody says *remember that* to a chat window.

    *"I think I remember asking to let NERVIS remember various facts on its own, without me
    needing to explicitly prompt it."* (23 September 2026)

So the trigger widens and nothing else does. The confirm step stays exactly where it was — the
owner was asked and chose *offer it, I click to keep* over filing anything automatically — and
what gets written is still the person's own words, never a model's. What changes is that NERVIS
now recognises the **shape** of a standing statement and asks about it.

The line this file defends is the one M23 drew and this feature walks right up to: NERVIS still
does not decide what is *worth* remembering. It recognises a grammar and puts the question to
the person. Every test below is either that line holding, or the cost of widening the trigger —
the transient, the guess and the judgement that must never become a dated note.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from nervis import chat as store
from nervis import commands, proposals
from nervis.storage import Database, prepare_database


@pytest.fixture()
def database() -> Database:
    return prepare_database(":memory:")


def noticed(question: str) -> commands.Proposal | None:
    """What NERVIS would offer on this sentence, with noticing switched on."""
    return commands.propose(question, [], notice=True)


# --- what it picks up -------------------------------------------------------

@pytest.mark.parametrize("said", [
    "the GPU box has an RX 6800",
    "the ThinkPad runs CachyOS",
    "my sister is called Anna",
    "the NAS lives in the cupboard",
    "the box has 32GB",
    "I prefer short replies",
    "I always run the tests before pushing",
])
def test_a_standing_fact_is_offered_without_being_asked_for(said: str) -> None:
    offer = noticed(said)

    assert offer is not None, f"nothing offered for {said!r}"
    assert offer.operation == "nervis.knowledge.learn"
    assert offer.unprompted is True
    assert offer.target == said, "the note is what they said, word for word"


def test_the_note_is_the_sentence_and_not_the_message() -> None:
    """A note is read on its own months later. The paragraph it arrived in is not there then."""
    offer = noticed("Right, where were we. The GPU box has an RX 6800. Let's carry on.")

    assert offer is not None
    assert offer.target == "The GPU box has an RX 6800"


def test_nothing_is_written_by_noticing_alone(tmp_path: Any) -> None:
    """The whole of the owner's answer to *what should it do*: offer it, do not file it.

    `propose` is a pure function of somebody's words — no database, no filesystem, no model.
    This asserts the shape of that rather than trusting the docstring: an offer is a value.
    """
    from nervis import learned

    offer = noticed("the GPU box has an RX 6800")

    assert offer is not None and offer.ready
    assert not learned.path(tmp_path).exists(), "an offer is a value, not a write"


# --- what it must not pick up ----------------------------------------------

@pytest.mark.parametrize("said", [
    "the tunnel is down",
    "the build is failing",
    "the ThinkPad is offline",
    "the stack is running now",
    "the tests are still red",
])
def test_a_reading_off_a_dial_is_not_a_standing_fact(said: str) -> None:
    """Filed, dated and quoted back in March, *"the tunnel is down"* is simply a lie.

    This is the failure that makes a file of notes not worth reading, and it is worse than
    missing a fact: a note nobody wrote costs a click, a note that is wrong costs the file.
    """
    assert noticed(said) is None


@pytest.mark.parametrize("said", [
    "I think the box has 32GB",
    "the NAS is probably in the cupboard",
    "the ThinkPad might be the faster one",
    "it should be an RX 6800",
])
def test_a_guess_is_not_filed_as_a_fact(said: str) -> None:
    assert noticed(said) is None


@pytest.mark.parametrize("said", [
    "that is not what I meant",
    "this is the one",
    "it is the fast one",
])
def test_a_sentence_that_needs_its_conversation_is_not_a_note(said: str) -> None:
    """*"That is 8 GB"* is unreadable the moment it leaves the conversation it was said in,
    and a note exists precisely to be read somewhere else."""
    assert noticed(said) is None


@pytest.mark.parametrize("said", ["the box is big", "the reply is fine", "the ThinkPad is slow"])
def test_a_judgement_is_not_a_fact(said: str) -> None:
    """Nothing in it can be checked later, so nothing in it can be found wrong."""
    assert noticed(said) is None


def test_one_word_is_enough_when_the_word_is_a_name_or_a_number() -> None:
    """The other side of the judgement rule: brevity is not the test, specificity is."""
    assert noticed("the ThinkPad runs CachyOS") is not None
    assert noticed("the box has 32GB") is not None
    assert noticed("the box is heavy") is None


def test_a_paragraph_is_not_a_note() -> None:
    long_one = "the GPU box " + "which we bought for the benchmarks " * 6 + "has an RX 6800"

    assert noticed(long_one) is None, "filed under its first eight words, nobody could find it"


def test_a_question_is_never_noticed() -> None:
    assert noticed("does the GPU box have an RX 6800?") is None
    assert noticed("is the ThinkPad running CachyOS") is None


def test_a_fact_wrapped_around_a_plan_is_passed_over() -> None:
    """A comma does not end a sentence here, and that costs this one.

    Splitting on commas would turn *"the box has 32 GB, 8 cores and an RX 6800"* into a note
    saying it has 32 GB — true as a sentence, false as a note. Losing the offer costs a click;
    keeping two thirds of a fact costs trust in every other note in the file.
    """
    assert noticed("the GPU box has an RX 6800, I'll set it up tomorrow") is None


# --- how it sits beside everything else ------------------------------------

def test_something_asked_for_always_wins() -> None:
    """Noticing is tried last, and that placement is the design: every other offer answers a
    request, and a request must never be displaced by a remark."""
    offer = commands.propose(
        "benchmark qwen3-4b", [{"model_id": "qwen3-4b", "local": True}], notice=True)

    assert offer is not None and offer.operation == "sirvis.benchmark.submit"


def test_remember_that_is_still_a_request_not_a_remark() -> None:
    """The M23 road is untouched, and the difference matters downstream: an asked-for offer is
    never suppressed, which is how somebody who deleted a note by hand gets it back."""
    offer = commands.propose("remember that the GPU box has an RX 6800", [], notice=True)

    assert offer is not None
    assert offer.target == "the GPU box has an RX 6800"
    assert offer.unprompted is False


def test_noticing_is_off_for_a_caller_that_does_not_ask_for_it() -> None:
    """`propose` is called where there is nobody to decline, so the default is silence."""
    assert commands.propose("the GPU box has an RX 6800", []) is None


def test_the_model_is_not_told_they_asked() -> None:
    """The ordinary sentence opens *"The person asked about…"*, which is false here — and a
    model handed it writes "you asked me to remember that", a small untruth about somebody's
    own words."""
    offer = noticed("the GPU box has an RX 6800")
    assert offer is not None

    said = commands.told(offer)

    assert "did not ask" in said
    assert "The person asked about" not in said
    assert "nothing will be unless they press it" in said


def test_the_offer_carries_its_own_flag_to_the_page() -> None:
    """The page draws an uninvited offer as a quiet chip rather than a primary button, and it
    needs to be told which one this is."""
    offer = noticed("the GPU box has an RX 6800")
    assert offer is not None

    assert offer.as_dict()["unprompted"] is True
    assert commands.propose("remember that the port is 8721", [],
                            notice=True).as_dict()["unprompted"] is False  # type: ignore[union-attr]


# --- the switch -------------------------------------------------------------

def test_noticing_is_on_when_nobody_has_said_otherwise(database: Database) -> None:
    """Unlike recall, which reads every conversation on the machine and waits to be asked for.

    This adds a button and nothing else — nothing read, nothing written, nothing sent, until
    somebody presses it. Off-by-default is what made capture a feature that existed only in
    the tests for three weeks.
    """
    assert store.noticing(database) is True


def test_turning_it_off_is_honoured(database: Database) -> None:
    with database.connection as connection:
        connection.execute("INSERT INTO setting (key, value) VALUES (?, ?)",
                           (store.CAPTURE_SETTING, json.dumps(False)))

    assert store.noticing(database) is False


def test_an_unreadable_switch_counts_as_on(database: Database) -> None:
    """The same direction as `barred`: the failure a person can see beats the one that looks
    exactly like the feature quietly not working."""
    with database.connection as connection:
        connection.execute("INSERT INTO setting (key, value) VALUES (?, ?)",
                           (store.CAPTURE_SETTING, "not json at all"))

    assert store.noticing(database) is True


def test_the_switch_travels_to_another_computer() -> None:
    """It is a preference about how chat behaves, like the three beside it in the panel."""
    from nervis.settings_transfer import EXPORTABLE

    assert store.CAPTURE_SETTING in EXPORTABLE
    assert "chat.compaction" in EXPORTABLE


# --- asked once ------------------------------------------------------------

def test_an_uninvited_offer_is_not_made_twice(database: Database) -> None:
    """Nobody asked for it, so a second appearance is not a reminder — it is nagging."""
    from nervis.api.chat import _remembered

    offer = noticed("the GPU box has an RX 6800")
    assert offer is not None
    assert _remembered(database, offer) is not None, "the first time it is offered"

    proposals.record(database, proposal_id="pr_one", operation=offer.operation,
                     target=offer.target, outcome=proposals.DECLINED)

    assert _remembered(database, offer) is None


def test_accepting_it_also_settles_it(database: Database) -> None:
    """Accepted means the note is filed; offering to file it again is offering a duplicate."""
    from nervis.api.chat import _remembered

    offer = noticed("the ThinkPad runs CachyOS")
    assert offer is not None
    proposals.record(database, proposal_id="pr_two", operation=offer.operation,
                     target=offer.target, outcome=proposals.ACCEPTED)

    assert _remembered(database, offer) is None


def test_asking_for_it_outright_is_never_suppressed(database: Database) -> None:
    """The way back for somebody who deleted the note out of the file by hand."""
    from nervis.api.chat import _remembered

    asked = commands.propose("remember that the ThinkPad runs CachyOS", [], notice=True)
    assert asked is not None
    proposals.record(database, proposal_id="pr_three", operation=asked.operation,
                     target=asked.target, outcome=proposals.DECLINED)

    again = _remembered(database, asked)

    assert again is not None
    assert again.history is not None, "and it says so on the offer rather than acting on it"


def test_a_different_fact_is_still_offered(database: Database) -> None:
    """Keyed on the sentence, so declining one note says nothing about the next."""
    from nervis.api.chat import _remembered

    proposals.record(database, proposal_id="pr_four", operation="nervis.knowledge.learn",
                     target="the GPU box has an RX 6800", outcome=proposals.DECLINED)

    other = noticed("the ThinkPad runs CachyOS")
    assert other is not None

    assert _remembered(database, other) is not None
