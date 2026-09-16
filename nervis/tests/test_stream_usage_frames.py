"""The frames a stream carries once RAVIS asks for usage, read as no text at all.

Since RAVIS 0.28.3 (16 September 2026) RAVIS asks every streamed upstream that
was not told either way for its token counts (`stream_options.include_usage`),
so the stream it relays to NERVIS can end with one more frame: `choices` empty,
`usage` filled in. OpenAI also writes `"usage": null` into every frame before
it. OpenRouter already sent that last frame unasked, and NERVIS read it
correctly — this pins that it still does, now that every provider may send it.
"""

from __future__ import annotations

import json
from typing import Any

from nervis.api.chat import _delta, _drawn, _refusal_of


def _line(frame: dict[str, Any]) -> str:
    return "data: " + json.dumps(frame)


USAGE_ONLY = _line({
    "id": "c1", "object": "chat.completion.chunk", "model": "gpt-4.1-2025-04-14",
    "choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 1, "total_tokens": 13},
})
WITH_NULL_USAGE = _line({
    "id": "c1", "object": "chat.completion.chunk", "model": "gpt-4.1-2025-04-14",
    "choices": [{"index": 0, "delta": {"content": "pong"}, "finish_reason": None}],
    "usage": None,
})


def test_the_usage_frame_adds_no_text_and_does_not_end_the_stream() -> None:
    assert _delta(USAGE_ONLY) == ("", False)


def test_a_frame_with_null_usage_still_carries_its_text() -> None:
    assert _delta(WITH_NULL_USAGE) == ("pong", False)


def test_the_usage_frame_is_neither_a_picture_nor_a_refusal() -> None:
    assert _drawn(USAGE_ONLY) == []
    assert _refusal_of(USAGE_ONLY) == ""
    assert _refusal_of(WITH_NULL_USAGE) == ""
