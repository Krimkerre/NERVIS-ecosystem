"""The cacheable prefix, which is the whole point of moving the readings.

Every provider that caches a prompt does it by *prefix*: it hashes the request
from its first byte up to some point and reuses the work only if the next
request opens with exactly the same bytes. NERVIS put the clock, the recalled
conversations and about eleven hundred tokens of live readings at the front of
every request, and all three change every turn — so the hash never matched, the
entire conversation was re-read from scratch each time, and nothing was ever
cached on any provider. On DeepSeek a cache hit costs about 3% of a miss.

`test_m4_chat.py` asserts the model still *receives* all of that. This file
asserts the other half, which nothing else can: that what comes **before** the
question stays byte-identical from one turn to the next. Both halves are needed
and neither implies the other — a version that cached perfectly by dropping the
readings would pass this file and fail that one, and the version that shipped
for months passed that one while costing real money here.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from tests.test_m4_chat import an_api, frames, turn

READING_MARKERS = ("services (", "clarvis editor windows registered")


def _captured(client: Any, sent: list[dict[str, Any]]) -> None:
    """Point the client's upstream at `sent`, so every request is recorded."""
    def capture(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # Chat completions only. NERVIS also asks RAVIS for a generated title
        # and reads its catalogue, and those share this transport -- letting
        # them into the list makes every assertion below read the wrong request.
        if "messages" in body:
            sent.append(body)
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("noted"))))

    client.app.state.probe_client = httpx.AsyncClient(transport=httpx.MockTransport(capture))


def _two_turns() -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []
    client = an_api(frames("first"))
    opened = turn(client, "first question", system="Be someone.")
    conversation = opened.headers["x-conversation-id"]
    _captured(client, sent)
    turn(client, "second question", system="Be someone.", conversation_id=conversation)
    turn(client, "third question", system="Be someone.", conversation_id=conversation)
    return sent


def test_the_system_message_is_identical_on_every_turn() -> None:
    """**The prefix.** Anything here that changes per turn invalidates the cache
    for the entire conversation behind it, because the hash is cumulative from
    the very first byte."""
    sent = _two_turns()

    systems = {json.dumps(call["messages"][0]) for call in sent}

    assert len(systems) == 1, (
        "the system message changed between turns, so no provider can reuse "
        "anything after it"
    )


def test_nothing_per_turn_is_in_the_system_message() -> None:
    """Named directly rather than left to the equality test above, because the
    equality could hold by luck — two turns a second apart can print the same
    clock, and a machine whose services did not change prints the same readings.
    This fails on the arrangement itself."""
    sent = _two_turns()
    system = json.dumps(sent[0]["messages"][0])

    for marker in READING_MARKERS:
        assert marker not in system, f"the live readings are back in the cached half ({marker})"
    assert "the time is" not in system.lower(), "the clock is back in the cached half"


def test_the_replayed_history_carries_no_readings() -> None:
    """**The other end of the same mistake, and the subtler one.**

    The readings ride on the question. If they were *stored* with it, every
    later turn would replay a different copy of them inside the history — the
    prefix would change from behind, and the fix would undo itself while every
    other test still passed.
    """
    sent = _two_turns()
    replayed = sent[-1]["messages"][:-1]

    for message in replayed:
        text = json.dumps(message)
        for marker in READING_MARKERS:
            assert marker not in text, (
                f"a stored turn carries the readings ({marker}) — the history is "
                "no longer identical from turn to turn"
            )


def test_everything_before_the_question_is_reused_verbatim() -> None:
    """The property stated whole: turn three opens with exactly what turn two
    opened with, plus the completed exchange between them. That is what a
    provider needs to reuse the work, and it is what none of the pieces above
    guarantee on their own."""
    sent = _two_turns()
    earlier, later = sent[-2]["messages"], sent[-1]["messages"]

    # Everything from the earlier request except its own final question, which
    # is the one part deliberately allowed to differ.
    shared = len(earlier) - 1

    assert later[:shared] == earlier[:shared], (
        "turn three did not open with turn two's prefix, so the conversation is "
        "re-read from scratch every time"
    )


def test_the_readings_still_arrive_on_the_question() -> None:
    """The guard on the guards. Every assertion above is satisfied by simply
    never sending the readings at all, which would be a far worse bug than the
    one being fixed."""
    sent = _two_turns()
    asked = json.dumps(sent[-1]["messages"][-1])

    assert any(marker in asked for marker in READING_MARKERS), (
        "the readings reached neither the system prompt nor the question"
    )
