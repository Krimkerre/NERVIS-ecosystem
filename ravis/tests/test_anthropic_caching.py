"""Asking Anthropic to cache, which is the only provider that must be asked.

DeepSeek and OpenAI cache a repeated prefix by themselves. Anthropic caches
only what a request marks with `cache_control`, and RAVIS marked nothing — so
every Claude conversation re-read its whole history at full price, however long
it ran. A cache hit bills at a tenth of ordinary input.

The interesting decision is *where* the mark goes, and it is not the obvious
place. The cached prefix is everything up to and including the marked block, so
marking the last turn would cache a prefix ending in the question just asked —
a prefix nothing will ever repeat. One turn earlier ends on the last completed
exchange, which is precisely what the next request repeats verbatim.
"""

from __future__ import annotations

from typing import Any

from ravis.core.requests import NormalizedRequest
from ravis.providers.anthropic_wire import CACHEABLE_FROM_TURNS, render_request


def _body(*messages: dict[str, Any]) -> dict[str, Any]:
    return render_request(
        NormalizedRequest(messages=list(messages)),
        model="claude-opus-5",
        max_output_tokens=1024,
    )


def _marked(body: dict[str, Any]) -> list[int]:
    """Which turns carry a breakpoint, by index."""
    found = []
    for index, turn in enumerate(body["messages"]):
        blocks = turn.get("content")
        if isinstance(blocks, list) and any(
            isinstance(b, dict) and "cache_control" in b for b in blocks
        ):
            found.append(index)
    return found


def test_a_continuing_conversation_is_marked_for_caching() -> None:
    body = _body(
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "second"},
    )

    assert _marked(body) == [1], "nothing asked Anthropic to cache anything"
    assert body["messages"][1]["content"][-1]["cache_control"] == {"type": "ephemeral"}


def test_the_mark_never_lands_on_the_question_being_asked() -> None:
    """**The whole design in one assertion.** A prefix ending at the newest turn
    is a prefix that has never existed before and will never exist again —
    NERVIS attaches this turn's live readings to it. Caching it writes an entry
    nothing can ever hit, at 1.25x."""
    body = _body(
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "second"},
    )

    assert len(body["messages"]) - 1 not in _marked(body)


def test_a_one_shot_request_is_not_marked() -> None:
    """A cache write costs 25% more than an ordinary read. Marking the shape
    most API clients send — one question, no history — is a pure surcharge on a
    prefix nothing will reuse."""
    assert _marked(_body({"role": "user", "content": "just the one question"})) == []


def test_the_threshold_is_the_first_turn_that_could_be_reused() -> None:
    """Pins the number to its reason rather than to itself: below one completed
    exchange there is nothing a later request could repeat."""
    assert CACHEABLE_FROM_TURNS == 3


def test_a_string_turn_becomes_a_block_so_it_can_carry_the_mark() -> None:
    """`cache_control` is a property of a content block, and most turns arrive
    as bare strings. The text has to survive the conversion intact."""
    body = _body(
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "the exact words"},
        {"role": "user", "content": "second"},
    )

    block = body["messages"][1]["content"][-1]
    assert block["type"] == "text"
    assert block["text"] == "the exact words"


def test_an_empty_turn_is_left_alone_rather_than_marked() -> None:
    """Anthropic rejects an empty text block, and a 400 is a far worse outcome
    than a missed cache write."""
    body = _body(
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "second"},
    )

    assert _marked(body) == []


def test_only_one_breakpoint_is_spent() -> None:
    """Anthropic allows four per request and errors when a fifth is needed. A
    long conversation must not accumulate one per turn."""
    turns: list[dict[str, Any]] = []
    for index in range(8):
        turns.append({"role": "user", "content": f"q{index}"})
        turns.append({"role": "assistant", "content": f"a{index}"})
    turns.append({"role": "user", "content": "latest"})

    assert len(_marked(_body(*turns))) == 1
