"""Finding a conversation by something said in it.

*"I want options for history browsing… can you devise of a searchbar, so I can put in a search
term if I don't know which chat holds the conversation I need?"* (22 September 2026).

**The search asks NERVIS, not the browser.** A browser remembers the last fifty conversations;
NERVIS holds every one, including those brought over from another computer. Searching the list
on screen would search the wrong thing — and the whole case for a search box is not knowing
where the answer is.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis import chat
from nervis.app import create_app
from nervis.chat import Message, search
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


def conversation(database: Any, title: str, *turns: str) -> str:
    conversation_id = chat.start_conversation(database, profile="ravis/chat", title=title)
    for number, said in enumerate(turns):
        chat.append(database, conversation_id,
                    Message(message_id=f"{conversation_id}-{number}",
                            role="user" if number % 2 == 0 else "assistant", content=said))
    return conversation_id


def test_a_word_from_the_middle_of_a_conversation_finds_it(database: Any) -> None:
    conversation(database, "Casual chat", "how is the weather", "the ThinkPad is booting")
    conversation(database, "Something else", "nothing to do with it")

    found = search(database, "thinkpad")

    assert [row["title"] for row in found] == ["Casual chat"]
    assert found[0]["hits"] == 1 and found[0]["messages"] == 2


def test_the_title_counts_too(database: Any) -> None:
    conversation(database, "About the ThinkPad", "no mention in here")

    assert [row["title"] for row in search(database, "thinkpad")] == ["About the ThinkPad"]


def test_the_snippet_shows_the_word_in_what_was_said(database: Any) -> None:
    """A list of titles is not an answer when the word appears in turn forty of a long one."""
    conversation(database, "Long one",
                 "x " * 200 + "the avahi daemon was not running " + "y " * 200)

    (found,) = search(database, "avahi")

    assert "avahi daemon was not running" in found["snippet"]
    assert found["snippet"].startswith("…") and found["snippet"].endswith("…")
    assert len(found["snippet"]) < 200, "a snippet is a snippet, not the turn"


def test_a_per_cent_sign_is_searched_for_rather_than_matching_everything(database: Any) -> None:
    """`%` and `_` are `LIKE`'s wildcards; somebody typing "100%" is typing a number."""
    conversation(database, "Battery", "it was at 100% when I left")
    conversation(database, "Other", "nothing numeric here")

    assert [row["title"] for row in search(database, "100%")] == ["Battery"]
    assert search(database, "_") == [], "an underscore matches an underscore, not any character"


def test_an_empty_search_finds_nothing_rather_than_everything(database: Any) -> None:
    conversation(database, "Something", "anything")

    assert search(database, "") == [] and search(database, "   ") == []


def test_the_newest_conversation_comes_first_and_the_list_is_bounded(database: Any) -> None:
    for number in range(5):
        conversation(database, f"Chat {number}", "the same word every time")

    found = search(database, "same word", limit=3)

    assert len(found) == 3
    assert [row["updated_at"] for row in found] == sorted(
        (row["updated_at"] for row in found), reverse=True)


def test_the_route_answers_with_what_it_searched_for(client: Any) -> None:
    conversation(client.app.state.database, "Linking", "the tunnel is up again")

    answer = client.get("/api/v1/chat/search", params={"q": "tunnel"}).json()

    assert answer["term"] == "tunnel"
    assert [row["title"] for row in answer["items"]] == ["Linking"]
    assert client.get("/api/v1/chat/search").json()["items"] == []
