"""Keeping a long conversation inside what a model can read.

*"Do we need some form of compaction for longer sessions in NERVIS, like we have here?"*
(22 September 2026). Measured before answering: every prior turn was sent with every message,
so a 138-turn conversation re-sent all 138 — cost and latency growing with the conversation,
and a wall the model hits mid-answer.

The promises these tests hold to:

- **what is stored is never trimmed** — compaction changes the request, not the conversation;
- **the newest turn is always sent**, however long it is;
- **the summary is rolled**, so a long evening costs a short call now and then rather than a
  re-reading of everything each time;
- **off means off** — the whole conversation goes, exactly as it did before.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis import chat as store
from nervis import compaction
from nervis.app import create_app
from nervis.chat import Message
from nervis.config import Settings
from nervis.storage import prepare_database


@pytest.fixture()
def database() -> Any:
    return prepare_database(":memory:")


@pytest.fixture()
def client(tmp_path: Any) -> Any:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"), workspace_path=str(tmp_path), _env_file=None,
    )
    with TestClient(create_app(settings)) as made:
        yield made


def turns(*sizes: int) -> list[dict[str, Any]]:
    return [{"role": "user" if number % 2 == 0 else "assistant",
             "content": f"{number}" + "x" * size}
            for number, size in enumerate(sizes)]


def a_long_conversation(database: Any, count: int = 6, size: int = 5_000) -> str:
    conversation_id = store.start_conversation(database, profile="ravis/chat", title="Long one")
    for number in range(count):
        store.append(database, conversation_id,
                     Message(message_id=f"m{number}",
                             role="user" if number % 2 == 0 else "assistant",
                             content=f"turn {number} " + "x" * size))
    return conversation_id


def test_a_short_conversation_is_sent_exactly_as_it_is(database: Any) -> None:
    """Most conversations are short, and must not pay for a feature they do not need."""
    conversation_id = a_long_conversation(database, count=3, size=50)

    sent = compaction.what_to_send(database, conversation_id)

    assert len(sent) == 3
    assert all(turn["role"] in ("user", "assistant") for turn in sent), "no note was added"


def test_the_newest_turn_is_kept_however_long_it_is() -> None:
    """A budget that could drop the question being asked breaks the conversation to save it."""
    older, recent = compaction.split(turns(100, 100, 50_000))

    assert len(recent) == 1 and len(older) == 2
    assert recent[0]["content"].startswith("2")


def test_the_line_is_drawn_in_characters_not_turns() -> None:
    """Ten turns of one word and ten of an essay are not the same thing to a model."""
    older, recent = compaction.split(turns(*([100] * 40)), budget=1_000)

    kept = sum(len(one["content"]) for one in recent)
    assert kept <= 1_000, "the budget is a budget"
    assert kept + len(older[-1]["content"]) > 1_000, "and it is filled — one more would not fit"
    assert len(older) + len(recent) == 40, "every turn is on one side of the line or the other"


def test_with_a_summary_the_older_turns_travel_as_one_note() -> None:
    sent, stood_for = compaction.folded(turns(5_000, 5_000, 5_000, 500), "they discussed Avahi")

    assert stood_for == 1
    assert sent[0]["role"] == "system" and "they discussed Avahi" in sent[0]["content"]
    assert compaction.SUMMARY_PREFACE.split("\n")[0] in sent[0]["content"], (
        "the note says what it is, or the model answers as though it had been said in those words"
    )
    assert [one["role"] for one in sent[1:]] == ["assistant", "user", "assistant"]


def test_before_the_first_summary_exists_the_recent_turns_go_alone(database: Any) -> None:
    """One turn's worth of missing history, rather than a reply held up by a second call."""
    conversation_id = a_long_conversation(database)

    sent = compaction.what_to_send(database, conversation_id)

    assert len(sent) < 6 and all(one["role"] != "system" for one in sent)
    assert len(store.history(database, conversation_id)) == 6, "and nothing was removed"


def test_a_stored_summary_is_used_and_the_conversation_is_untouched(database: Any) -> None:
    conversation_id = a_long_conversation(database)
    older, _ = compaction.split(store.history(database, conversation_id))
    compaction.remember_summary(database, conversation_id, "earlier: they set up the link", "",
                                len(older))

    sent = compaction.what_to_send(database, conversation_id)

    assert sent[0]["role"] == "system" and "set up the link" in sent[0]["content"]
    assert len(store.messages(database, conversation_id)) == 6
    assert len(store.history(database, conversation_id)) == 6


def test_switched_off_the_whole_conversation_is_sent(client: Any) -> None:
    conversation_id = a_long_conversation(client.app.state.database)
    client.put("/api/v1/settings/chat.compaction", json={"value": False})

    sent = compaction.what_to_send(client.app.state.database, conversation_id)

    assert len(sent) == 6, "exactly what happened before compaction existed"


def test_a_fold_is_only_worth_a_call_when_there_is_enough_new_to_say() -> None:
    """Below the threshold the older turns are a turn or two from being inside the budget."""
    long_enough = turns(5_000, 5_000, 5_000, 100)
    held = {"summary": "something already said", "covered": 1}

    assert compaction.needs_folding(long_enough, held) == []
    assert compaction.needs_folding(turns(*([3_000] * 8)), held), "eight turns is plenty new"


def test_the_first_fold_happens_even_for_a_small_overflow() -> None:
    """With no summary at all, the older turns are not otherwise represented anywhere."""
    assert compaction.needs_folding(turns(5_000, 5_000, 5_000, 100),
                                    {"summary": "", "covered": 0})


def test_the_fold_is_rolled_rather_than_rewritten() -> None:
    """The model is given what the summary says and only what has happened since."""
    prompt = compaction.fold_prompt("they set up the link", turns(20)[:1])

    assert "The summary so far:\nthey set up the link" in prompt
    assert "user: 0" in prompt
    assert "Reply with the summary alone" in prompt


def test_the_conversation_read_says_what_the_model_is_being_sent(client: Any) -> None:
    """Every turn is still in `items`; `compaction` is how many travel as a summary instead."""
    conversation_id = a_long_conversation(client.app.state.database)

    answer = client.get(f"/api/v1/chat/conversations/{conversation_id}").json()

    assert len(answer["items"]) == 6, "the conversation itself is whole"
    assert answer["compaction"]["on"] is True
    assert answer["compaction"]["summarised"] >= 1


def test_the_note_forbids_filling_the_gaps_it_creates() -> None:
    """**The failure this was written against, on a real conversation** (22 September 2026).

    The note used to say "treat it as your own memory of what was said". Asked about something
    that had never been mentioned in that conversation, the model connected it confidently to
    the nearest thing it could see and produced a sentence that meant nothing. A summary is
    lossy by construction; a model told it remembers will answer as though it does.
    """
    note = compaction.SUMMARY_PREFACE.lower()

    assert "memory of what was said" not in note
    assert "compressed" in note and "missing" in note
    assert "say so" in note and "instead of guessing" in note


def test_the_note_still_says_what_it_is_before_the_summary_itself() -> None:
    sent, _ = compaction.folded(turns(5_000, 5_000, 5_000, 500), "they discussed Avahi")

    said = sent[0]["content"]
    assert said.index("summarised") < said.index("they discussed Avahi"), (
        "the summary must be introduced as one, or it reads as part of the conversation"
    )


# ── Quoting the turns a question is actually about ───────────────────────────────────────
#
# A summary is lossy and a question is specific. Until these, a model asked about something
# that only the summarised turns knew could either say so or invent — and it invented.

def test_the_older_turns_that_mention_the_question_travel_with_it() -> None:
    older = [
        {"role": "user", "content": "we decided the tray icon stays grey when nothing is loaded"},
        {"role": "assistant", "content": "noted — grey when idle"},
        {"role": "user", "content": "anyway, the weather has been miserable all week"},
    ]

    quoted = compaction.relevant_older(older, "what did we decide about the tray icon?")

    assert [one["content"][:20] for one in quoted] == ["we decided the tray "]


def test_quoted_turns_keep_the_order_they_were_said_in() -> None:
    older = [{"role": "user", "content": f"turn {number} about the tray icon"}
             for number in range(4)]

    quoted = compaction.relevant_older(older, "tray icon")

    assert [one["content"][5] for one in quoted] == ["0", "1", "2", "3"]


def test_a_question_with_nothing_in_common_quotes_nothing() -> None:
    older = [{"role": "user", "content": "the tray icon stays grey"}]

    assert compaction.relevant_older(older, "and what about lunch") == []
    assert compaction.relevant_older(older, "") == []


def test_quoting_is_bounded_so_it_cannot_undo_the_compaction() -> None:
    older = [{"role": "user", "content": "tray icon " + "x" * 2_000} for _ in range(10)]

    quoted = compaction.relevant_older(older, "tray icon")

    assert len(quoted) <= compaction.QUOTE_TURNS
    assert sum(len(one["content"]) for one in quoted) <= compaction.QUOTE_BUDGET + 1


def test_a_matching_turn_too_long_for_the_budget_is_trimmed_not_dropped() -> None:
    """It is long because it said a lot about what was asked; dropping it loses the point."""
    older = [{"role": "user", "content": "the tray icon " + "x" * 9_000}]

    (quoted,) = compaction.relevant_older(older, "tray icon")

    assert quoted["content"].startswith("the tray icon ")
    assert quoted["content"].endswith("…")
    assert len(quoted["content"]) <= compaction.QUOTE_BUDGET + 1


def test_the_quoted_turns_ride_with_the_question_not_in_front_of_the_history(
    database: Any
) -> None:
    """**Where they sit decides what a long conversation costs.**

    Providers reuse a prompt by matching its opening bytes. Quotes are chosen per question, so
    they change every turn — in front of the history they would break that match on every
    message and the whole conversation would be re-read each time, which is the expensive
    mistake `nervis.md` records from 9 September 2026. They go at the end, with the readings.
    """
    conversation_id = store.start_conversation(database, profile="ravis/chat", title="Long one")
    for number in range(8):
        store.append(database, conversation_id,
                     Message(message_id=f"m{number}",
                             role="user" if number % 2 == 0 else "assistant",
                             content=(f"turn {number} about the tray icon"
                                      if number < 3 else f"turn {number} " + "x" * 4_000)))
    older, _ = compaction.split(store.history(database, conversation_id))
    compaction.remember_summary(database, conversation_id, "they talked about things", "",
                                len(older))

    prior = compaction.what_to_send(database, conversation_id)
    quotes = compaction.quoted_for(database, conversation_id, "what about the tray icon?")

    assert len([one for one in prior if one["role"] == "system"]) == 1, (
        "only the summary is in front of the question, and it changes rarely"
    )
    assert "tray icon" not in prior[0]["content"], "the quotes are not in the history"
    assert quotes.startswith(compaction.QUOTE_PREFACE[:40]) and "tray icon" in quotes


def test_a_question_that_drags_nothing_back_adds_nothing(database: Any) -> None:
    conversation_id = a_long_conversation(database)
    older, _ = compaction.split(store.history(database, conversation_id))
    compaction.remember_summary(database, conversation_id, "a summary", "", len(older))

    assert compaction.quoted_for(database, conversation_id, "about lunch tomorrow") == ""
    assert compaction.quoted_for(database, conversation_id, "") == ""


def test_what_is_sent_carries_the_summary_then_the_words_themselves(database: Any) -> None:
    conversation_id = store.start_conversation(database, profile="ravis/chat", title="Long one")
    for number in range(8):
        store.append(database, conversation_id,
                     Message(message_id=f"m{number}",
                             role="user" if number % 2 == 0 else "assistant",
                             content=(f"turn {number} about the tray icon"
                                      if number < 3 else f"turn {number} " + "x" * 4_000)))
    older, _ = compaction.split(store.history(database, conversation_id))
    compaction.remember_summary(database, conversation_id, "they talked about several things",
                                "", len(older))

    sent = compaction.what_to_send(database, conversation_id)
    quotes = compaction.quoted_for(database, conversation_id, "what about the tray icon?")

    assert sent[0]["content"].startswith(compaction.SUMMARY_PREFACE[:40])
    assert quotes.startswith(compaction.QUOTE_PREFACE[:40])
    assert "tray icon" in quotes, "in the words they were said in"


def test_a_reopened_conversation_asks_nothing_and_quotes_nothing(database: Any) -> None:
    """There is no question yet, so there is nothing to look for in the older turns."""
    conversation_id = a_long_conversation(database)
    older, _ = compaction.split(store.history(database, conversation_id))
    compaction.remember_summary(database, conversation_id, "a summary", "", len(older))

    sent = compaction.what_to_send(database, conversation_id)

    assert len([one for one in sent if one["role"] == "system"]) == 1


def test_the_fold_is_asked_for_sections_rather_than_prose() -> None:
    """Asked for "a summary", a model drops the file name and the thing decided against."""
    prompt = compaction.fold_prompt("", turns(20)[:1])

    for section in compaction.SUMMARY_SECTIONS:
        assert f"## {section}" in prompt
    assert "exact phrase" in prompt and "anything you are guessing at" in prompt
