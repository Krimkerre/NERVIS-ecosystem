"""What chat is told about how the ecosystem works.

The gap these files fill was named by the model itself. Asked what RAVIS does,
it described the live readings accurately and then said: *"how it actually
decides which model to send a request to, what the fallback chain looks like,
whether it does load balancing or cost optimization — I don't have readings on
any of that. I'm watching the service, not its logic."* Every one of those
answers was written down in this repository and nothing ever handed it over.

**A second gap, found later: "are you aware of the latest updates and
bugfixes" found nothing either**, because term overlap cannot match a
paraphrase sharing no vocabulary with the corpus at all — see
`knowledge.EMBED_MIN_SCORE`'s own docstring for the measurement that added an
embedding half to `search()` for exactly this case.

**Most tests here run against a small, purpose-built corpus, not the real
`nervis/knowledge/*.md` files** — the same choice `test_fallback.py` makes
with `TWO_CODERS` rather than RAVIS's real provider list. `search()` now
calls RAVIS's `/v1/embeddings`, and §14.5 forbids a live model or network in
this suite, so the vectors for this small corpus and every question below
were computed once, for real, against RAVIS's own route and Ollama's
`nomic-embed-text`, and are replayed from `fixtures/knowledge_vectors.json` —
a recorded fixture, the same pattern the conformance suites already use for
the same reason. `test_the_files_are_indexed_by_section` is the exception:
it tests the real files' own parsing and does not touch search or reading.
"""
from __future__ import annotations

import asyncio
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import pytest

from nervis import knowledge

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge_vectors.json"
VECTORS: dict[str, list[float]] = json.loads(FIXTURES.read_text())

# The corpus every test in this file searches, unless it asks for the real
# one. Headings match real ones in `nervis/knowledge/ravis.md` where a test
# already cited them by name — this is a smaller stand-in, not a rewrite of
# what those sections mean.
SMALL_CORPUS = (
    knowledge.Section("ravis", "How a request is routed",
        "RAVIS decides which model answers a request by scoring pools on "
        "cost, latency and capability, then walking a fallback chain of "
        "candidates in order until one of them answers."),
    knowledge.Section("ravis", "What it optimises for",
        "Cost first for a pool that asked to be cheap, latency first for "
        "one that asked to be fast. Cost optimization prefers the "
        "cheapest model that still satisfies the request."),
    knowledge.Section("ravis", "Pools",
        "ravis/chat, ravis/cheap, ravis/local and ravis/api each state a "
        "different intent, so a caller names what it wants rather than "
        "picking a model by hand."),
    knowledge.Section("nervis", "How it knows anything",
        "NERVIS reads RAVIS and SIRVIS over HTTP and reports what each one "
        "says about its own health and capabilities, never a stale number "
        "presented as current."),
    knowledge.Section("sirvis", "What it measures",
        "SIRVIS benchmarks a model on a local runtime and records "
        "throughput, latency and whether it can call tools reliably."),
)


@pytest.fixture
def small_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swap `sections()` for the small corpus above, for the life of one test.

    Wrapped in its own `lru_cache` so `search()`'s internal calls to
    `sections()` behave exactly as they do against the real one — cached,
    with an identity `_section_vectors` can key on. `_weight` and the vector
    cache are cleared going in and coming out, so no state leaks between a
    test using this fixture and one reading the real files.
    """
    def reset() -> None:
        knowledge._weight.cache_clear()
        knowledge._vectors_for = None
        knowledge._vectors = None

    monkeypatch.setattr(knowledge, "sections", lru_cache(maxsize=1)(lambda: SMALL_CORPUS))
    reset()
    yield
    reset()


def _client_with_embeddings() -> httpx.AsyncClient:
    """A fake RAVIS that answers `/v1/embeddings` from the recorded fixture.

    Real vectors, no live call: `handle` never leaves the process. A text not
    in the fixture is a test asking a question nobody recorded a vector for —
    refused loudly (500) rather than answered with something invented, so a
    new question added to a test without regenerating the fixture fails
    obviously instead of scoring against noise.
    """
    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        inputs = payload["input"] if isinstance(payload["input"], list) else [payload["input"]]
        missing = [text for text in inputs if text not in VECTORS]
        if missing:
            return httpx.Response(500, json={"error": f"no recorded vector for {missing!r}"})
        rows = [
            {"object": "embedding", "index": i, "embedding": VECTORS[text]}
            for i, text in enumerate(inputs)
        ]
        return httpx.Response(
            200, json={"object": "list", "data": rows, "model": "nomic-embed-text"}
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def _client_without_embeddings() -> httpx.AsyncClient:
    """A fake RAVIS with no embedding model configured — the real, expected
    degraded state `ravis.embeddings@1` names, not a network failure."""
    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(503, json={
            "error": {"message": "no embedding model", "type": "embedding_not_configured"}
        })

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def _search(question: str, **kwargs: Any) -> list[knowledge.Section]:
    async def run() -> list[knowledge.Section]:
        async with _client_with_embeddings() as client:
            return await knowledge.search(question, client, "http://ravis.invalid", **kwargs)
    return asyncio.run(run())


def _reading(question: str) -> str:
    async def run() -> str:
        async with _client_with_embeddings() as client:
            return await knowledge.reading(question, client, "http://ravis.invalid")
    return asyncio.run(run())


def test_the_files_are_indexed_by_section() -> None:
    """The real files, not the small corpus above — this is what verifies
    `nervis/knowledge/*.md` itself still parses."""
    found = knowledge.sections()

    assert found, "no knowledge files were indexed at all"
    assert {section.subject for section in found} >= {"ravis", "nervis", "sirvis", "clarvis"}
    assert all(section.heading for section in found), "a section with no heading is unsearchable"


@pytest.mark.usefixtures("small_corpus")
def test_the_question_that_failed_now_finds_its_answer() -> None:
    """The exact sentence from the log, and the three things it could not
    answer. The first of these four scores nothing by term overlap alone
    against this smaller corpus — "ravis" itself is too common a word in a
    five-section corpus to carry weight — and is found only because the
    embedding half now runs alongside it, which is the point being tested."""
    for question in (
        "what can you tell me about ravis? about what it does and how it does it",
        "how does it decide which model to send a request to",
        "what does the fallback chain look like",
        "does it do cost optimization",
    ):
        hits = _search(question)
        assert hits, f"nothing found for {question!r}"
        assert any(hit.subject == "ravis" for hit in hits), question


@pytest.mark.usefixtures("small_corpus")
def test_a_question_squarely_about_state_gets_no_background() -> None:
    """**The falsifier, and it is a partial one, which is the honest shape.**

    "How much have I spent today" wants the ledger, not an essay on cost
    ranking, and the ledger is already in the prompt. These are the questions
    with nothing of the design in them, and they must stay clear.

    Three of the four, not all four — the same shape of imperfection
    `MIN_SCORE`'s own measurement already documented for term overlap, now
    measured again for `EMBED_MIN_SCORE` and found again: "system status?"
    scores above the embedding floor against a real section about exactly
    that (see `EMBED_MIN_SCORE`'s docstring), which is real background
    arriving where a term-overlap search would have stayed silent, not a
    false alarm.
    """
    for question in ("how much have i spent today", "you doing okay too?", "restart the stack"):
        assert _search(question) == [], question
    assert _search("system status?") != [], (
        "\"system status?\" was expected to find real background via embeddings"
    )


@pytest.mark.usefixtures("small_corpus")
def test_a_paraphrase_with_no_shared_vocabulary_now_finds_something() -> None:
    """The question that started this: term overlap scores it zero no matter
    how the corpus is weighted, because none of "aware", "latest", "update"
    or "bugfix" appear anywhere in it. Only the embedding half can catch a
    paraphrase like this, so this test is the one this whole change is for."""
    hits = _search("are you aware of the latest updates and bugfixes")

    assert hits, "the motivating question still finds nothing"


@pytest.mark.usefixtures("small_corpus")
def test_embeddings_unavailable_falls_back_to_term_overlap_alone() -> None:
    """`ravis.embeddings@1` is `DEGRADED`, never assumed `AVAILABLE` — a
    machine with no local embedding model configured must still get the
    term-overlap answer it always got, not silence."""
    async def run() -> tuple[list[knowledge.Section], list[knowledge.Section]]:
        async with _client_without_embeddings() as client:
            works = await knowledge.search(
                "how does it decide which model to send a request to", client, "http://ravis.invalid"
            )
            paraphrase = await knowledge.search(
                "are you aware of the latest updates and bugfixes", client, "http://ravis.invalid"
            )
        return works, paraphrase

    works, paraphrase = asyncio.run(run())
    assert any(hit.subject == "ravis" for hit in works), "term overlap alone should still find this"
    assert paraphrase == [], (
        "a paraphrase with no shared vocabulary should find nothing without embeddings"
    )


@pytest.mark.usefixtures("small_corpus")
def test_a_live_figure_outranks_the_notes() -> None:
    """A specification and a service are different things. A model handed one
    without being told which will report intentions as behaviour — which is the
    whole reason the canonical specifications were not indexed instead."""
    reading = _reading("how does ravis decide which model to use")

    assert "A live reading beats these notes" in reading
    assert "the one to trust" in reading


@pytest.mark.usefixtures("small_corpus")
def test_the_notes_outrank_the_feed_on_what_was_built() -> None:
    """**The other half, and it was missing — measured live on 8 September
    2026.** The framing said the notes describe *the design* and that a live
    reading is the one to trust, full stop. Asked "what did you fix most
    recently about attachments" with the answer sitting in the reading, chat
    replied *"I haven't fixed anything about attachments — that's not in my
    recent activity"* and listed the event feed instead.

    Both halves are needed and they divide cleanly: a live reading wins on
    what is happening now, and the notes win on what exists and what changed,
    because a few minutes of events is not a changelog and absence from it is
    not evidence of anything.
    """
    reading = _reading("how does ravis decide which model to use")

    assert "these notes are the record and the live feed is not" in reading
    assert "not a changelog" in reading


@pytest.mark.usefixtures("small_corpus")
def test_the_reading_is_bounded() -> None:
    """It sits beside the live figures in one prompt, and those are what the
    answer is about. This is background."""
    reading = _reading("ravis routing pools cost latency privacy models providers")

    assert len(reading) <= knowledge.MAX_CHARACTERS + 400


@pytest.mark.usefixtures("small_corpus")
def test_the_reading_is_not_silently_cut_to_one_field() -> None:
    """The regression guard for a real bug: `fenced()` clipped every string to
    one diagnostic field's length (400 characters) regardless of what called
    it, so a reading that assembled several sections and thousands of
    characters still arrived at the model as one clipped paragraph — and
    still measured over a thousand characters once the fence's own wrapper
    text is counted, which is why a bare length check does not catch this.
    `search()` for this question returns "How a request is routed" and
    "Pools" both — so the second heading appearing at all proves the first
    section's body did not consume the whole budget by itself.
    """
    reading = _reading("ravis routing pools cost latency privacy models providers")

    assert "Pools" in reading, (
        "only the first matched section survived — the multi-section budget was cut short"
    )


@pytest.mark.usefixtures("small_corpus")
def test_nothing_is_returned_for_a_question_with_no_words_of_its_own() -> None:
    for empty in ("", "   ", "the a an it is"):
        assert _search(empty) == []


@pytest.mark.usefixtures("small_corpus")
def test_spelling_a_word_the_other_way_still_finds_it() -> None:
    """"Optimization" against a section headed *What it optimises for* scored
    one and was rejected by the threshold — a miss produced by the search rather
    than survived by it."""
    assert knowledge._stem("optimization") == knowledge._stem("optimises")
    assert knowledge._stem("routing") == knowledge._stem("routed")

    assert any(hit.subject == "ravis" for hit in _search("does it do cost optimization"))


@pytest.mark.usefixtures("small_corpus")
def test_the_order_is_total_so_one_question_gives_one_answer() -> None:
    """§9.7's determinism applies to anything shaping an answer: two sections
    scoring equally must not swap places between calls."""
    question = "how does routing work in this ecosystem"

    assert [s.heading for s in _search(question)] == [s.heading for s in _search(question)]


def test_a_verb_and_its_inflections_are_one_word() -> None:
    """**Found while explaining the scoring, not by a test.** `decide` was worth
    0.00 — it appeared in none of thirty sections, against notes that say
    "decides" throughout — because every verb ending in `e` split from its own
    inflections. Eight of nine pairs failed, and `route`, `decide`, `choose` and
    `serve` are exactly this corpus's vocabulary.
    """
    for one, other in (
        ("decide", "decides"), ("route", "routes"), ("route", "routing"),
        ("route", "routed"), ("choose", "chooses"), ("serve", "serves"),
        ("optimise", "optimises"), ("optimise", "optimisation"),
        ("optimization", "optimises"), ("pool", "pools"), ("use", "uses"),
    ):
        assert knowledge._stem(one) == knowledge._stem(other), f"{one} vs {other}"


def test_a_short_word_is_not_stemmed_to_nothing() -> None:
    """The other direction. Trimming aggressively enough to unify "use" and
    "uses" must not reduce a word to a stub that collides with everything."""
    for word in ("api", "log", "run", "key", "cpu"):
        assert len(knowledge._stem(word)) >= 3, word


@pytest.mark.usefixtures("small_corpus")
def test_a_question_about_you_is_a_question_about_nervis() -> None:
    """**NERVIS is the assistant, and these notes are written in the third
    person.** Asked in turn about RAVIS, SIRVIS and Clarvis, the next question
    is "and you?" — which names nothing, matches no vector well either, and
    got the persona reciting its own character instead of anything about the
    service.
    """
    for question in ("and you?", "what about you", "tell me about yourself",
                     "how do you work", "what are you"):
        hits = _search(question, subject="nervis") if knowledge._about_itself(question) else []
        assert hits, question
        assert hits[0].subject == "nervis", question
        assert "nervis" in _reading(question).lower()


def test_you_inside_a_question_about_a_peer_is_not_about_nervis() -> None:
    """The falsifier. "Can you tell me about RAVIS" is a question about RAVIS
    that happens to contain the word — and "you" is in most questions anybody
    types, so firing on it would attach NERVIS to nearly every turn."""
    for question in ("can you tell me about ravis", "what can you say about sirvis",
                     "would you hook clarvis up to ravis"):
        assert not knowledge._about_itself(question), question
