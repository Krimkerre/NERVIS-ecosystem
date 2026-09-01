"""Notes NERVIS was told (NERVIS.md M23).

Four clauses, and three of them are about what must *not* happen:

  1. A learned note is retrieved exactly like a shipped one — same index, same
     weighting, same quoting. Structural: one directory, one glob.
  2. It never overwrites a hand-written note, and where the two disagree the
     hand-written one wins **and the conflict is shown**.
  3. It is a file a person can read, edit and delete — including by hand, which
     means the parser has to survive somebody tidying it.
  4. `tools/knowledge_check.py` gates it exactly as it gates the others.

The fourth is true because the gate globs the directory, so it is asserted here
rather than reimplemented.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nervis import commands, knowledge, learned


@pytest.fixture()
def notes(tmp_path: Path, monkeypatch: Any) -> Path:
    """A knowledge directory this test owns, with one shipped note in it."""
    (tmp_path / "ravis.md").write_text(
        "# RAVIS\n\n## How a request is routed\n\n"
        "The pool decides, then the cheapest candidate that satisfies it.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(knowledge, "KNOWLEDGE", tmp_path)
    monkeypatch.setattr(learned, "KNOWLEDGE", tmp_path)
    knowledge.forget_cached()
    yield tmp_path
    knowledge.forget_cached()


# ── Retrieved the same way ──────────────────────────────────────────────────


def test_a_learned_note_is_indexed_like_any_other(notes: Path) -> None:
    """No second retrieval path. One directory, one glob, one index."""
    learned.remember(
        "The GPU box", "An RX 6800 on the desk serves models over the LAN.",
        "remember that the gpu box has an rx 6800", root=notes,
    )
    found = knowledge.search("what is in the gpu box")
    assert [s.subject for s in found][:1] == ["learned"]
    assert found[0].learned is True


def test_a_note_is_knowable_the_moment_it_is_written(notes: Path) -> None:
    """The cache was correct until NERVIS could write one of these files.

    Warmed first, the way a running service is. Without invalidation NERVIS
    agrees to remember something, writes it, and does not know it until the next
    restart — which looks exactly like the feature not working and which no test
    against a fresh process would ever show.
    """
    knowledge.search("routing")  # warm
    assert not knowledge.search("is ollama installed")
    learned.remember(
        "Ollama", "Not installed on this machine.", "note that", root=notes
    )
    # Asserted as "findable now", not as a count. The file's own title is a
    # section too, so the first note adds two — and a test that counted would be
    # measuring the preamble rather than the invalidation.
    found = knowledge.search("is ollama installed")
    assert found and found[0].heading == "Ollama"


# ── The shipped note wins, and the conflict is shown ────────────────────────


def test_a_shipped_note_wins_and_the_learned_one_is_still_shown(notes: Path) -> None:
    """Both kept. Exactly one of them is wrong and only a person can say which.

    Silently dropping the learned note hides a disagreement; silently preferring
    it lets a passing remark overwrite the documentation.
    """
    learned.remember(
        "How a request is routed", "Everything goes to the biggest model, always.",
        "remember that everything goes to the biggest model", root=notes,
    )
    found = knowledge.search("how is a request routed")
    assert len(found) == 2
    first, second = found
    assert first.subject == "ravis" and not first.learned
    assert second.learned and second.heading.endswith("(overruled)")
    assert "disagreement is visible" in second.body
    # The file is untouched — the rule is about ranking, not about editing what
    # somebody told NERVIS.
    assert learned.notes(notes)[0].heading == "How a request is routed"


def test_a_learned_note_on_its_own_subject_is_not_overruled(notes: Path) -> None:
    """The rule fires on a clash, not on the note being learned."""
    learned.remember("The GPU box", "An RX 6800 on the desk.", "remember", root=notes)
    found = knowledge.search("what is in the gpu box")
    assert found and not found[0].heading.endswith("(overruled)")


def test_the_clash_is_found_across_capitalisation(notes: Path) -> None:
    """Comparing raw strings would miss, and two notes about one subject would
    both be quoted as though they agreed."""
    learned.remember(
        "how a REQUEST is Routed", "Round robin.", "remember", root=notes
    )
    found = knowledge.search("how is a request routed")
    assert any(s.heading.endswith("(overruled)") for s in found)


# ── A file a person owns ────────────────────────────────────────────────────


def test_appending_never_overwrites(notes: Path) -> None:
    """Two notes under one heading are two notes. Deciding that a later one
    supersedes an earlier one is a judgement, and making it at write time throws
    the earlier one away before anybody could disagree."""
    learned.remember("Ollama", "Not installed.", "remember", root=notes)
    learned.remember("Ollama", "Installed after all.", "remember", root=notes)
    kept = [n.body for n in learned.notes(notes)]
    assert kept == ["Not installed.", "Installed after all."]


def test_a_note_carries_its_date_and_what_prompted_it(notes: Path) -> None:
    learned.remember(
        "The GPU box", "An RX 6800.", "remember that the gpu box has an rx 6800",
        root=notes, today="2026-09-01",
    )
    note = learned.notes(notes)[0]
    assert note.learned_on == "2026-09-01"
    assert note.prompted_by == "remember that the gpu box has an rx 6800"
    assert "_Learned 2026-09-01 from:" in learned.path(notes).read_text(encoding="utf-8")


def test_a_hand_tidied_note_is_still_readable(notes: Path) -> None:
    """Somebody who edits the file may drop the provenance line.

    A parser that refused it would make the file unreadable the first time
    anybody tidied it, which is the opposite of "a person can edit this".
    """
    learned.remember("The GPU box", "An RX 6800.", "remember", root=notes)
    path = learned.path(notes)
    path.write_text(
        path.read_text(encoding="utf-8").split("_Learned")[0], encoding="utf-8"
    )
    knowledge.forget_cached()
    note = learned.notes(notes)[0]
    assert note.heading == "The GPU box"
    assert note.body == "An RX 6800."
    assert note.learned_on == ""


def test_a_heading_cannot_split_a_note_in_two(notes: Path) -> None:
    """`#` is stripped rather than escaped: the parser reading this file is the
    same one that indexes every other knowledge file."""
    learned.remember("Odd ## heading", "Body.", "remember", root=notes)
    assert len(learned.notes(notes)) == 1
    assert "#" not in learned.notes(notes)[0].heading


def test_forgetting_is_by_heading_not_by_position(notes: Path) -> None:
    """A person may have edited the file between reading it and pressing the
    button, and acting on a stale index deletes the wrong note."""
    for heading in ("A", "B", "C"):
        learned.remember(heading, f"about {heading}", "remember", root=notes)
    assert learned.forget("B", notes) is True
    assert [n.heading for n in learned.notes(notes)] == ["A", "C"]
    assert learned.forget("B", notes) is False


def test_forgetting_everything_restores_the_shipped_corpus(notes: Path) -> None:
    before = len(knowledge.sections())
    learned.remember("The GPU box", "An RX 6800.", "remember", root=notes)
    assert len(knowledge.sections()) > before
    assert learned.forget_all(notes) == 1
    assert len(knowledge.sections()) == before
    assert not learned.path(notes).exists()


def test_an_empty_note_is_refused(notes: Path) -> None:
    with pytest.raises(ValueError, match="heading and something to say"):
        learned.remember("", "", "remember", root=notes)


# ── The offer that produces one ─────────────────────────────────────────────
#
# No fixture: `propose` reads the person's words and nothing else. That is the
# same fact `test_composing_a_proposal_never_reads_the_record` asserts for M22,
# and it is why clearing either store restores the unlearned offer exactly.


def test_remember_that_becomes_an_offer_carrying_the_sentence() -> None:
    offer = commands.propose("remember that the gpu box has an RX 6800", [])
    assert offer is not None
    assert offer.operation == "nervis.knowledge.learn"
    # The person's own words, unabridged — the heading is only how it is filed.
    assert offer.target == "the gpu box has an RX 6800"
    assert offer.ready


def test_a_question_about_remembering_is_not_a_note() -> None:
    """`ASKING_FOR` treats "can you…" as a request wearing a question mark,
    which is right everywhere else and wrong here: this operation takes the rest
    of the sentence as its content, so a question mark makes the content a
    question and there is nothing to store."""
    for asked in ("can you remember what I said?", "do you remember the gpu box?",
                  "remember: check the fans?"):
        assert commands.propose(asked, []) is None


def test_remember_with_nothing_after_it_offers_nothing() -> None:
    assert commands.propose("remember", []) is None
    assert commands.propose("remember that", []) is None


def test_learning_is_matched_before_benchmarking() -> None:
    """"Remember that qwen3-4b is the fast one" names a model, and read the
    other way round it becomes an offer to measure one."""
    offer = commands.propose(
        "remember that qwen3-4b is the fast one",
        [{"model_id": "qwen3-4b", "local": True}],
    )
    assert offer is not None
    assert offer.operation == "nervis.knowledge.learn"
