"""The block of earlier conversations that rides in every prompt when memory is set to "all".

Chat has two memories over other conversations and they are not the same thing. Recall
(`recall.py`) searches every conversation for words matching the question, quotes what it finds
behind a fence, and shows under the reply which conversations it used. This one — the persona
digest — sends a tail of the newest few conversations **unconditionally**, on every turn, with
no matching and nothing shown. The inventory of 23 September 2026 calls that overlap out; these
tests are about the part of it that misfired in front of the owner.

    [Introducing You to Benny]
      assistant: …which is exactly what you want to show Benny.
                 What would you like him to see next?

Three days later, opening an unrelated conversation with *"testing your new memory…"*, chat
replied *"Hello, Matty — good to see you back with Benny. What would you like to show him
next?"* — copying its own closing line, which was the last text in the block and therefore the
text sitting nearest the question. Told Benny was not there, it said *"what would you like to
show him next time?"*: the same line again, because the same block was still in the same place.

The preamble already forbade this in as many words, at length. It did not hold, so the fix is
structural rather than a sterner sentence — **the digest carries the person's turns and not
NERVIS's**. That also puts it on the right side of the rule the rest of the system follows: a
stored reply is model output, and `recall.py` fences every passage it quotes for exactly that
reason. This path fenced nothing, so it now carries only the half that never needed a fence.
"""

from __future__ import annotations

from typing import Any

import pytest

from nervis import chat as store
from nervis.api.chat import _house_system, _nudge_system
from nervis.api.chat_personas import (
    RECALLED_CONVERSATIONS,
    RECALLED_TURNS_EACH,
    _recall,
)
from nervis.chat import Message
from nervis.storage import Database, prepare_database


@pytest.fixture()
def database() -> Database:
    made = prepare_database(":memory:")
    with made.connection as connection:
        connection.execute("INSERT INTO setting (key, value) VALUES ('chat.memory', '\"all\"')")
    return made


def conversation(database: Database, title: str, *turns: tuple[str, str]) -> str:
    """One stored conversation. Each turn is (role, what was said)."""
    conversation_id = store.start_conversation(database, profile="ravis/chat", title=title)
    for number, (role, said) in enumerate(turns):
        store.append(database, conversation_id,
                     Message(message_id=f"{conversation_id}-{number}", role=role, content=said))
    return conversation_id


def test_what_nervis_said_is_not_sent_back_to_it(database: Database) -> None:
    """The Benny failure, in the shape it actually happened.

    The guest's name appears only in NERVIS's own reply. Nobody typed it, so nothing about
    him should reach a later conversation — and the line that was copied was a closing
    question, which is the most copyable thing a reply can end with.
    """
    conversation(database, "A demonstration",
                 ("user", "show me the system status"),
                 ("assistant", "All green — which is exactly what you want to show Benny. "
                               "What would you like him to see next?"))
    now = conversation(database, "Something else", ("user", "testing your new memory"))

    block = _recall(database, now)

    assert "show me the system status" in block, "their side is what the block is for"
    assert "Benny" not in block
    assert "What would you like him to see next?" not in block


def test_a_conversation_nobody_spoke_in_contributes_nothing(database: Database) -> None:
    """A greeting NERVIS opened with and the person never answered is not a memory of theirs."""
    conversation(database, "Opened and abandoned", ("assistant", "Good evening. What can I do?"))
    now = conversation(database, "Now", ("user", "hello"))

    assert "Opened and abandoned" not in _recall(database, now)


def test_the_tail_is_their_last_turns_not_the_last_turns(database: Database) -> None:
    """The cap counts what they said. Before, a conversation whose end was six NERVIS replies
    contributed six replies and none of their questions — the worst possible slice of it."""
    turns: list[tuple[str, str]] = []
    for number in range(RECALLED_TURNS_EACH + 2):
        turns.append(("user", f"question {number}"))
        turns.append(("assistant", f"answer {number}"))
    conversation(database, "A long one", *turns)
    now = conversation(database, "Now", ("user", "hello"))

    block = _recall(database, now)
    # The tails only. The preamble above them is prose and contains the word "answer".
    tails = block[block.index("[A long one"):]

    assert "answer" not in tails
    kept = [line for line in tails.splitlines() if line.strip().startswith("question ")]
    assert len(kept) == RECALLED_TURNS_EACH
    assert "question 7" in block and "question 0" not in block, "the newest of their turns"


def test_each_conversation_is_dated(database: Database) -> None:
    """The inventory's third finding: of the paths that carry remembered text into a prompt,
    only recall said when anything was said. A block headed with a name reads as a standing
    fact; one headed with a date three days old reads as three days old."""
    conversation(database, "Older", ("user", "something said then"))
    now = conversation(database, "Now", ("user", "hello"))

    block = _recall(database, now)

    assert "[Older, last spoken in on " in block
    heading = next(line for line in block.splitlines() if line.startswith("[Older"))
    assert len(heading.rsplit("on ", 1)[1].rstrip("]")) == 10, "the day, not the hour"


def test_a_conversation_with_no_date_is_still_sent(database: Database) -> None:
    """Empty rather than a guess, and never the word None in front of a model."""
    conversation(database, "Undated", ("user", "something"))
    with database.connection as connection:
        connection.execute("UPDATE chat_conversation SET updated_at = '' WHERE title = 'Undated'")
    now = conversation(database, "Now", ("user", "hello"))

    block = _recall(database, now)

    assert "[Undated]" in block and "None" not in block


def test_the_block_says_its_own_replies_are_missing(database: Database) -> None:
    """Otherwise the absence is an invitation: a model that sees only questions will happily
    invent the answers it must have given."""
    conversation(database, "Older", ("user", "something said then"))
    now = conversation(database, "Now", ("user", "hello"))

    block = _recall(database, now)

    assert "your own replies are deliberately not here" in block
    assert "do not reconstruct" in block


def test_it_is_still_bounded_to_the_newest_few(database: Database) -> None:
    for number in range(RECALLED_CONVERSATIONS + 3):
        conversation(database, f"Chat {number}", ("user", f"said {number}"))
    now = conversation(database, "Now", ("user", "hello"))

    block = _recall(database, now)

    assert sum(1 for line in block.splitlines() if line.startswith("[")) == RECALLED_CONVERSATIONS


def test_nothing_at_all_when_memory_is_this_conversation_only(database: Database) -> None:
    with database.connection as connection:
        connection.execute("UPDATE setting SET value = '\"session\"' WHERE key = 'chat.memory'")
    conversation(database, "Older", ("user", "something"))
    now = conversation(database, "Now", ("user", "hello"))

    assert _house_system({"system": "Be someone."}, database, False, now).count("[Older") == 0


# --- sent once, not twice ---------------------------------------------------

def nudge_prompt(database: Database, conversation_id: str, body: dict[str, Any]) -> str:
    """The whole system prompt for an unprompted remark, through the chat path's own function
    rather than a copy of it — a test that reimplements what it checks proves nothing."""
    return _nudge_system(database, conversation_id, 2, "",
                         _house_system(body, database, False, conversation_id))


@pytest.mark.parametrize("body", [{"system": "Be someone."}, {}])
def test_an_unprompted_remark_carries_the_digest_exactly_once(
    database: Database, body: dict[str, Any],
) -> None:
    """`_house_system` owns this block and appends it whenever a persona is set and the scope is
    "all" — the ordinary case. The nudge path joined its own copy on top of that, so the same
    text went twice. Measured on the owner's store, 23 September 2026: 12,019 characters of
    system prompt, of which the recalled conversations were both halves.

    Both cases matter and they take different routes. With a persona, `_house_system` supplies
    it and the nudge must not; with none, `_house_system` supplies nothing — a request with no
    persona receives none of NERVIS's extras — and the nudge must.
    """
    conversation(database, "Older", ("user", "something said then"))
    now = conversation(database, "Now", ("user", "hello"))

    whole = nudge_prompt(database, now, body)

    assert whole.count("earlier conversations on this machine") == 1
    assert whole.count("[Older") == 1


def test_the_second_nudge_still_has_something_to_recall(database: Database) -> None:
    """The deduplication must not empty the flavour that needs it: with nothing to recall the
    nudge asks a question instead, and that fallback has to stay reachable rather than being
    triggered by a prompt that in fact holds the block."""
    conversation(database, "Older", ("user", "something said then"))
    now = conversation(database, "Now", ("user", "hello"))

    whole = nudge_prompt(database, now, {"system": "Be someone."})

    assert "something said then" in whole
