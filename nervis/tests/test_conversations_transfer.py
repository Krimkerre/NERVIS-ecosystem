"""Carrying conversations from the owner's other computer to this one.

*"What I'm also missing is conversation history — that was the main thing I wanted to sync"*
(22 September 2026).

The rules that make a history safe to move, each pinned here because a later "improvement"
could undo any of them without a test noticing:

- **adds only** — nothing here is edited, and nothing is ever deleted by a pull;
- **identifiers and times survive** — a conversation from Sunday arrives dated Sunday, not
  dated "when it was imported", and bringing it twice adds nothing;
- **only what was ticked crosses**, and an empty choice brings nothing rather than everything,
  which is the opposite of the settings pull and deliberate: a history is long and personal.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis import chat
from nervis.api import conversations_transfer as api
from nervis.app import create_app
from nervis.chat import Message
from nervis.config import Settings
from nervis.conversations_transfer import differences, listing, one, take
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


def a_conversation(database: Any, title: str = "About the ThinkPad") -> str:
    conversation_id = chat.start_conversation(database, profile="ravis/chat", title=title)
    chat.append(database, conversation_id,
                Message(message_id=f"{conversation_id}-a", role="user",
                        content="does the link work?", profile="ravis/chat"))
    chat.append(database, conversation_id,
                Message(message_id=f"{conversation_id}-b", role="assistant",
                        content="it does now", profile="ravis/chat"))
    return conversation_id


def theirs(conversation_id: str, *, title: str = "About the ThinkPad", turns: int = 2,
           when: str = "2026-09-18T10:00:00") -> dict[str, Any]:
    """What the other computer's `/transfer/{id}` answers with."""
    return {
        "conversation": {"conversation_id": conversation_id, "title": title,
                         "profile": "ravis/chat", "created_at": when, "updated_at": when},
        "messages": [{"message_id": f"{conversation_id}-{number}",
                      "role": "user" if number % 2 == 0 else "assistant",
                      "content": f"turn {number}", "model": "ravis/chat", "profile": "ravis/chat",
                      "request_id": "", "route_decision_id": "", "interrupted": False,
                      "created_at": when}
                     for number in range(turns)],
    }


def test_the_list_carries_counts_and_no_message_bodies(database: Any) -> None:
    """A history of hundreds would be megabytes if each conversation arrived just to be counted."""
    a_conversation(database)

    listed = listing(database)

    assert [row["title"] for row in listed] == ["About the ThinkPad"]
    assert listed[0]["messages"] == 2
    assert "messages" not in str(listed[0].get("content", "")), "no bodies in a listing"
    assert set(listed[0]) == {"conversation_id", "title", "profile", "created_at",
                              "updated_at", "messages"}


def test_one_conversation_comes_with_its_turns_in_order(database: Any) -> None:
    conversation_id = a_conversation(database)

    found = one(database, conversation_id)

    assert found is not None
    assert [message["content"] for message in found["messages"]] == [
        "does the link work?", "it does now"]
    assert one(database, "no-such-conversation") is None


def test_a_conversation_arrives_with_its_own_identifiers_and_times(database: Any) -> None:
    """**Not stamped "now".** `chat.append` stamps the moment a turn is spoken, which is right
    for speaking and wrong for carrying: Sunday's conversation must not arrive looking like it
    happened during the import."""
    stored = take(database, theirs("c-from-thinkpad", when="2026-09-18T10:00:00"))

    assert stored == {"ok": True, "conversation_id": "c-from-thinkpad", "added": 2,
                      "title": "About the ThinkPad"}
    listed = listing(database)
    assert listed[0]["conversation_id"] == "c-from-thinkpad"
    assert listed[0]["created_at"].startswith("2026-09-18")
    assert [message["created_at"] for message in one(database, "c-from-thinkpad")["messages"]] == [
        "2026-09-18T10:00:00", "2026-09-18T10:00:00"]


def test_bringing_the_same_conversation_twice_adds_nothing(database: Any) -> None:
    take(database, theirs("c-1"))

    again = take(database, theirs("c-1"))

    assert again["added"] == 0
    assert listing(database)[0]["messages"] == 2


def test_a_conversation_that_grew_gains_only_its_new_turns(database: Any) -> None:
    take(database, theirs("c-1", turns=2))

    grown = take(database, theirs("c-1", turns=5))

    assert grown["added"] == 3, "the three turns this computer had never seen"
    assert listing(database)[0]["messages"] == 5


def test_what_is_here_is_never_replaced(database: Any) -> None:
    """A title typed here, a turn held here: a pull adds, and takes nothing away."""
    conversation_id = a_conversation(database, title="My own name for it")

    take(database, {"conversation": {"conversation_id": conversation_id, "title": "Theirs",
                                     "profile": "ravis/chat"},
                    "messages": [{"message_id": "new-turn", "role": "user",
                                  "content": "one more", "created_at": "2026-09-19T09:00:00"}]})

    listed = listing(database)
    assert listed[0]["title"] == "My own name for it"
    assert listed[0]["messages"] == 3, "their turn was added beside the two that were here"


@pytest.mark.parametrize("rubbish", [
    "not a conversation", 42, {"conversation": {}, "messages": "not a list"},
    {"conversation": {"title": "no identifier"}, "messages": []},
])
def test_anything_that_is_not_a_conversation_is_refused(database: Any, rubbish: Any) -> None:
    answer = take(database, rubbish)

    assert answer["ok"] is False
    assert listing(database) == []


def test_a_turn_with_no_identifier_or_a_strange_role_is_left_out(database: Any) -> None:
    """The far end is another whole stack; what it sends is read, not trusted."""
    take(database, {"conversation": {"conversation_id": "c-1", "title": "T"},
                    "messages": [
                        {"message_id": "", "role": "user", "content": "no id"},
                        {"message_id": "m-2", "role": "root", "content": "strange role"},
                        {"message_id": "m-3", "role": "user", "content": "kept"},
                    ]})

    kept = one(database, "c-1")
    assert kept is not None
    assert [message["content"] for message in kept["messages"]] == ["kept"]


def test_the_difference_is_what_a_pull_would_add() -> None:
    ours = [{"conversation_id": "shared", "messages": 4},
            {"conversation_id": "only-here", "messages": 9}]
    others = [{"conversation_id": "shared", "title": "Shared", "messages": 7},
              {"conversation_id": "only-there", "title": "Theirs", "messages": 2},
              {"conversation_id": "same", "messages": 0}]

    changes = differences(others + [{"conversation_id": "only-here", "messages": 1}], ours)

    assert [(row["conversation_id"], row["state"], row["adds"]) for row in changes] == [
        ("shared", "more", 3), ("only-there", "new", 2), ("same", "new", 0)]


def test_nothing_ticked_brings_nothing(client: Any) -> None:
    """The opposite of the settings pull, and deliberate: a history is long and personal."""
    answer = client.post("/api/v1/chat/peer", json={"ids": []}).json()

    assert answer["ok"] is False and answer["brought"] == []
    assert "nothing was ticked" in answer["detail"]


def test_only_the_ticked_conversations_are_fetched(client: Any, monkeypatch: Any) -> None:
    asked: list[str] = []

    def _fetch(conversation_id: str) -> tuple[dict[str, Any], str]:
        asked.append(conversation_id)
        return theirs(conversation_id), ""

    monkeypatch.setattr(api, "peer_conversation", _fetch)

    answer = client.post("/api/v1/chat/peer", json={"ids": ["c-1", "c-2"]}).json()

    assert asked == ["c-1", "c-2"]
    assert answer["ok"] is True and answer["messages"] == 4
    assert [one_row["conversation_id"] for one_row in answer["brought"]] == ["c-1", "c-2"]


def test_the_preview_says_what_would_be_added(client: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(api, "peer_conversations", lambda: (
        [{"conversation_id": "c-1", "title": "Theirs", "messages": 3}], ""))
    monkeypatch.setattr(api, "linked_peer", lambda _: {"enabled": True, "address": "me@thinkpad",
                                                       "inbound": True})

    answer = client.get("/api/v1/chat/peer").json()

    assert answer["reachable"] is True
    assert [(row["title"], row["state"], row["adds"]) for row in answer["changes"]] == [
        ("Theirs", "new", 3)]


def test_a_sleeping_computer_is_an_answer_not_an_error(client: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(api, "peer_conversations", lambda: (None, "it did not answer"))

    response = client.get("/api/v1/chat/peer")

    assert response.status_code == 200
    assert response.json()["reachable"] is False
