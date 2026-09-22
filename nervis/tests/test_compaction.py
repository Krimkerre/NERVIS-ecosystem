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
