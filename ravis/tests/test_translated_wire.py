"""M3b — normalized events rendered as an OpenAI-compatible stream (§6).

The transparent path forwards bytes and is tested by not changing them. This is
the other path, where every frame is constructed — and §6 names exactly what
breaks when it is constructed wrongly: tool-call indexes, tool-call IDs,
fragmented JSON arguments, reasoning fields, finish reasons, usage chunks and
`[DONE]`.

So these assert against the *recorded* fixtures rather than against a reading of
anyone's documentation. `fixtures.py` is what a real OpenAI-compatible server
emits, awkward parts included, and the point of the suite is to notice when
RAVIS smooths something away.
"""

from __future__ import annotations

import json

from ravis.api.openai.serialize import completion, stream_frames
from ravis.core.responses import (
    FinishReason,
    NormalizedResponse,
    NormalizedStreamEvent,
    StreamEventType,
    ToolCall,
    Usage,
)


def _deltas(frames: list[bytes]) -> list[dict]:
    """Every frame's delta, in order, ignoring the terminator."""
    out = []
    for frame in frames:
        payload = frame[len(b"data: "):].strip()
        if payload == b"[DONE]":
            continue
        body = json.loads(payload)
        if body["choices"]:
            out.append(body["choices"][0]["delta"])
    return out


def _render(*events: NormalizedStreamEvent) -> list[bytes]:
    return list(stream_frames(events, model="m", completion_id="c"))


def test_a_stream_opens_with_a_role_and_ends_with_done() -> None:
    """Every recorded fixture starts with a bare role delta: it is what lets a
    client open its message before any content exists."""
    frames = _render(NormalizedStreamEvent(type=StreamEventType.TEXT, text="Hello"))

    assert _deltas(frames)[0] == {"role": "assistant"}
    assert frames[-1] == b"data: [DONE]\n\n"


def test_text_and_reasoning_never_share_a_field() -> None:
    """§8.5. Merging them puts a model's private deliberation in front of the
    user as though it were the answer."""
    frames = _render(
        NormalizedStreamEvent(type=StreamEventType.REASONING, text="The user wants "),
        NormalizedStreamEvent(type=StreamEventType.TEXT, text="Hi."),
    )

    assert _deltas(frames)[1:] == [{"reasoning_content": "The user wants "}, {"content": "Hi."}]


def test_a_tool_call_opens_once_and_then_carries_only_arguments() -> None:
    """The rule the whole module is built around: a fragment is not a call.
    Repeating the id and name on every frame is valid JSON and breaks reassembly
    for any client that concatenates what it is given."""
    frames = _render(
        NormalizedStreamEvent(type=StreamEventType.TOOL_CALL_FRAGMENT, tool_index=0,
                              tool_id="call_123", tool_name="edit_file", arguments='{"pa'),
        NormalizedStreamEvent(type=StreamEventType.TOOL_CALL_FRAGMENT, tool_index=0,
                              arguments='th":"src/'),
        NormalizedStreamEvent(type=StreamEventType.TOOL_CALL_FRAGMENT, tool_index=0,
                              arguments='auth.ts"}'),
    )

    calls = [d["tool_calls"][0] for d in _deltas(frames)[1:]]
    assert calls[0] == {"index": 0, "id": "call_123", "type": "function",
                        "function": {"name": "edit_file", "arguments": '{"pa'}}
    assert calls[1] == {"index": 0, "function": {"arguments": 'th":"src/'}}
    assert calls[2] == {"index": 0, "function": {"arguments": 'auth.ts"}'}}
    # §8.3's point: reassembly is the only way to recover the path, and no
    # individual frame parses as JSON on its own.
    assert json.loads("".join(c["function"]["arguments"] for c in calls)) == {
        "path": "src/auth.ts"
    }


def test_interleaved_calls_are_told_apart_by_index_alone() -> None:
    """The case that breaks a proxy assuming one call at a time. Only the index
    says which fragment belongs to which call."""
    frames = _render(
        NormalizedStreamEvent(type=StreamEventType.TOOL_CALL_FRAGMENT, tool_index=0,
                              tool_id="call_a", tool_name="read_file", arguments='{"p'),
        NormalizedStreamEvent(type=StreamEventType.TOOL_CALL_FRAGMENT, tool_index=1,
                              tool_id="call_b", tool_name="list_dir", arguments='{"d'),
        NormalizedStreamEvent(type=StreamEventType.TOOL_CALL_FRAGMENT, tool_index=0,
                              arguments='ath":"a.ts"}'),
        NormalizedStreamEvent(type=StreamEventType.TOOL_CALL_FRAGMENT, tool_index=1,
                              arguments='ir":"src"}'),
    )

    calls = [d["tool_calls"][0] for d in _deltas(frames)[1:]]
    assert [c["index"] for c in calls] == [0, 1, 0, 1]
    # Each call opened exactly once, on its own first fragment.
    assert [c.get("id") for c in calls] == ["call_a", "call_b", None, None]


def test_a_finish_reason_rides_on_an_empty_delta() -> None:
    frames = _render(NormalizedStreamEvent(type=StreamEventType.FINISH,
                                           finish_reason=FinishReason.TOOL_CALLS))
    body = json.loads(frames[-2][len(b"data: "):])

    assert body["choices"][0] == {"index": 0, "delta": {}, "finish_reason": "tool_calls"}


def test_a_reason_with_no_wire_spelling_is_null_rather_than_guessed() -> None:
    """`stop` would assert the model finished when nobody established that.
    `CANCELLED` never reaches a finish frame at all — §8.6, the client is gone."""
    frames = _render(NormalizedStreamEvent(type=StreamEventType.FINISH,
                                           finish_reason=FinishReason.UNKNOWN))

    assert json.loads(frames[-2][len(b"data: "):])["choices"][0]["finish_reason"] is None


def test_usage_arrives_with_no_choices() -> None:
    """OpenAI's `include_usage` shape. A client iterating choices finds nothing
    to append, which is what makes it safe after the finish frame."""
    frames = _render(NormalizedStreamEvent(
        type=StreamEventType.USAGE, usage=Usage(input_tokens=11, output_tokens=4)))
    body = json.loads(frames[-2][len(b"data: "):])

    assert body["choices"] == []
    assert body["usage"] == {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15}


def test_an_error_event_is_not_rendered_as_content() -> None:
    """An error mid-stream is the relay's decision — fall back, or terminate —
    and a chunk that looked like content would hide that it happened."""
    frames = _render(NormalizedStreamEvent(type=StreamEventType.ERROR, error="upstream died"))

    assert _deltas(frames) == [{"role": "assistant"}]


def test_a_non_streamed_response_carries_whole_tool_calls() -> None:
    """Fragmentation is a property of the wire, not of the data."""
    body = completion(
        NormalizedResponse(text="done", finish_reason=FinishReason.TOOL_CALLS,
                           tool_calls=[ToolCall(index=0, id="call_a", name="edit_file",
                                                arguments='{"path":"a.ts"}')],
                           usage=Usage(input_tokens=9, output_tokens=2)),
        model="m", completion_id="c")

    assert body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == (
        '{"path":"a.ts"}'
    )
    assert body["choices"][0]["finish_reason"] == "tool_calls"
    assert body["usage"]["total_tokens"] == 11


def test_an_emitted_image_takes_the_shape_the_transparent_path_already_sends() -> None:
    """Measured against OpenRouter through RAVIS's own transparent path on
    7 September 2026: `google/gemini-2.5-flash-image` and `openai/gpt-5-image-mini`
    both arrived as one delta carrying
    `images: [{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}]`.

    A translated provider that invented a second spelling would make the same
    picture reach a caller differently depending on which route RAVIS chose,
    which is the one thing a gateway exists to prevent.
    """
    url = "data:image/png;base64,AAAA"

    streamed = _deltas(_render(
        NormalizedStreamEvent(type=StreamEventType.IMAGE, image_url=url),
    ))
    whole = completion(NormalizedResponse(text="Here you go: ", images=[url]),
                       model="m", completion_id="c")

    part = {"type": "image_url", "image_url": {"url": url}}
    assert streamed[1] == {"images": [part]}
    assert whole["choices"][0]["message"]["images"] == [part]
    assert whole["choices"][0]["message"]["content"] == "Here you go: "


def test_a_reply_with_no_image_says_nothing_about_images() -> None:
    """An always-present empty array would tell every caller that every model
    can draw, which is exactly the claim the capability is meant to carry."""
    body = completion(NormalizedResponse(text="hi"), model="m", completion_id="c")

    assert "images" not in body["choices"][0]["message"]
