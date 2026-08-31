"""What chat is told about how the ecosystem works.

The gap these files fill was named by the model itself. Asked what RAVIS does,
it described the live readings accurately and then said: *"how it actually
decides which model to send a request to, what the fallback chain looks like,
whether it does load balancing or cost optimization — I don't have readings on
any of that. I'm watching the service, not its logic."* Every one of those
answers was written down in this repository and nothing ever handed it over.
"""
from __future__ import annotations

from nervis import knowledge


def test_the_files_are_indexed_by_section() -> None:
    found = knowledge.sections()

    assert found, "no knowledge files were indexed at all"
    assert {section.subject for section in found} >= {"ravis", "nervis", "sirvis", "clarvis"}
    assert all(section.heading for section in found), "a section with no heading is unsearchable"


def test_the_question_that_failed_now_finds_its_answer() -> None:
    """The exact sentence from the log, and the three things it could not
    answer."""
    for question in (
        "what can you tell me about ravis? about what it does and how it does it",
        "how does it decide which model to send a request to",
        "what does the fallback chain look like",
        "does it do cost optimization",
    ):
        hits = knowledge.search(question)
        assert hits, f"nothing found for {question!r}"
        assert any(hit.subject == "ravis" for hit in hits), question


def test_a_question_squarely_about_state_gets_no_background() -> None:
    """**The falsifier, and it is a partial one, which is the honest shape.**

    "How much have I spent today" wants the ledger, not an essay on cost
    ranking, and the ledger is already in the prompt. These are the questions
    with nothing of the design in them, and they must stay clear.

    The threshold cannot exclude every state question, and the measurement in
    `MIN_SCORE` says why: *"how does RAVIS decide"* and *"anything noteworthy
    with RAVIS lately"* are made of the same words and differ in tense. The bar
    is placed so no design question is lost, which means some state questions
    carry background they did not need.
    """
    for question in (
        "how much have i spent today",
        "you doing okay too?",
        "restart the stack",
        "system status?",
    ):
        assert knowledge.search(question) == [], question


def test_the_reading_says_it_is_the_design_and_not_the_running_system() -> None:
    """A specification and a service are different things. A model handed one
    without being told which will report intentions as behaviour — which is the
    whole reason the canonical specifications were not indexed instead."""
    reading = knowledge.reading("how does ravis decide which model to use")

    assert "not a reading of the running system" in reading
    assert "the one to trust" in reading


def test_the_reading_is_bounded() -> None:
    """It sits beside the live figures in one prompt, and those are what the
    answer is about. This is background."""
    reading = knowledge.reading("ravis routing pools cost latency privacy models providers")

    assert len(reading) <= knowledge.MAX_CHARACTERS + 400


def test_nothing_is_returned_for_a_question_with_no_words_of_its_own() -> None:
    for empty in ("", "   ", "the a an it is"):
        assert knowledge.search(empty) == []


def test_spelling_a_word_the_other_way_still_finds_it() -> None:
    """"Optimization" against a section headed *What it optimises for* scored
    one and was rejected by the threshold — a miss produced by the search rather
    than survived by it."""
    assert knowledge._stem("optimization") == knowledge._stem("optimises")
    assert knowledge._stem("routing") == knowledge._stem("routed")

    assert any(hit.subject == "ravis" for hit in knowledge.search("does it do cost optimization"))


def test_the_order_is_total_so_one_question_gives_one_answer() -> None:
    """§9.7's determinism applies to anything shaping an answer: two sections
    scoring equally must not swap places between calls."""
    question = "how does routing work in this ecosystem"

    assert [s.heading for s in knowledge.search(question)] == \
           [s.heading for s in knowledge.search(question)]
