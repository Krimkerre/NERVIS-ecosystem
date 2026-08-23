"""Normalized events back out as an OpenAI-compatible stream (§6, Path B).

The transparent path forwards bytes and touches nothing. This is the other
half: a native provider's stream, already normalized by its adapter, rendered
into the wire shape Clarvis's client parses. §6 is blunt about the risk —
"every stream transformation can introduce bugs, and the high-risk surfaces are
exactly the ones Clarvis depends on: tool-call indexes, tool-call IDs,
fragmented JSON arguments, reasoning fields, finish reasons, usage chunks and
`[DONE]`" — so this module is written against the recorded fixtures rather than
against anyone's reading of the OpenAI documentation. Those fixtures are what a
real server emits, awkward parts included.

**The rule that shapes everything here: a fragment is not a call.** A tool call
arrives as a first frame carrying its index, id, type and function name, then
further frames carrying only more `arguments`. Repeating the id and name on
every frame would be valid JSON and would break reassembly for any client that
concatenates what it is given — and §8.3's worked example splits the arguments
mid-string precisely so that no frame parses alone.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Iterator

from ravis.core.responses import (
    FinishReason,
    NormalizedResponse,
    NormalizedStreamEvent,
    StreamEventType,
    Usage,
)

# Finish reasons the OpenAI wire defines. `CANCELLED` and `UNKNOWN` are RAVIS's
# own vocabulary and have no wire spelling: a cancelled stream never reaches a
# finish frame (§8.6 — the client is gone), and an unknown reason is published
# as `null` rather than guessed at, because "stop" would assert the model
# finished when nobody established that.
_WIRE_FINISH = {
    FinishReason.STOP: "stop",
    FinishReason.LENGTH: "length",
    FinishReason.TOOL_CALLS: "tool_calls",
    FinishReason.CONTENT_FILTER: "content_filter",
}


DONE = b"data: [DONE]\n\n"


def opening_frame(*, model: str, completion_id: str) -> bytes:
    """The bare role delta every recorded fixture starts with.

    It is what lets a client open its message before any content exists, and it
    is emitted once whether the stream goes on to carry text, a tool call or
    nothing at all.
    """
    return _frame(completion_id, model, {"role": "assistant"})


def frame_for(event: NormalizedStreamEvent, started: set[int], *,
              model: str, completion_id: str) -> bytes | None:
    """One event as one frame, or None for events the wire does not carry.

    `started` is the caller's — the set of tool-call indexes already opened —
    because "is this the first fragment of this call" is a fact about the
    stream, not about the event, and the event cannot know it.
    """
    rendered = _delta_for(event, started)
    return None if rendered is None else rendered(completion_id, model)


def stream_frames(
    events: Iterable[NormalizedStreamEvent], *, model: str, completion_id: str
) -> Iterator[bytes]:
    """Every frame for a finished sequence of events, `[DONE]` included.

    A thin wrapper over the two functions above rather than a second
    implementation: the synchronous form is what the tests drive and the async
    relay drives the same pieces, so the two cannot drift into disagreeing about
    a tool-call boundary.
    """
    yield opening_frame(model=model, completion_id=completion_id)
    started: set[int] = set()
    for event in events:
        frame = frame_for(event, started, model=model, completion_id=completion_id)
        if frame is not None:
            yield frame
    yield DONE


def _delta_for(event: NormalizedStreamEvent, started: set[int]) -> Any:
    """One event as a frame-maker, or None for events the wire does not carry."""
    if event.type is StreamEventType.TEXT:
        return lambda i, m: _frame(i, m, {"content": event.text})
    if event.type is StreamEventType.REASONING:
        # §8.5: thinking stays out of `content`. Merging them would put a
        # model's private deliberation in front of the user as the answer.
        return lambda i, m: _frame(i, m, {"reasoning_content": event.text})
    if event.type is StreamEventType.TOOL_CALL_FRAGMENT:
        return lambda i, m: _frame(i, m, {"tool_calls": [_tool_fragment(event, started)]})
    if event.type is StreamEventType.FINISH:
        reason = event.finish_reason
        return lambda i, m: _frame(
            i, m, {}, finish=_WIRE_FINISH.get(reason) if reason else None
        )
    if event.type is StreamEventType.USAGE:
        return lambda i, m: _usage_frame(i, m, event.usage)
    # ERROR is deliberately not rendered as a delta. An error mid-stream is the
    # relay's business — it decides between a fallback and a terminated stream —
    # and a chunk that looked like content would hide that decision.
    return None


def _tool_fragment(event: NormalizedStreamEvent, started: set[int]) -> dict[str, Any]:
    """A tool-call fragment, opening the call only on its first frame.

    The index is the identity, not the position in this list: §8.3's parallel
    case interleaves fragments for index 0 and index 1, and only the index says
    which belongs to which.
    """
    index = event.tool_index or 0
    if index in started:
        return {"index": index, "function": {"arguments": event.arguments}}
    started.add(index)
    return {
        "index": index,
        "id": event.tool_id,
        "type": "function",
        "function": {"name": event.tool_name, "arguments": event.arguments},
    }


def _frame(completion_id: str, model: str, delta: dict[str, Any],
           finish: str | None = None) -> bytes:
    body: dict[str, Any] = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return b"data: " + json.dumps(body).encode() + b"\n\n"


def _usage_frame(completion_id: str, model: str, usage: Usage | None) -> bytes:
    """The usage chunk, which carries no choices.

    OpenAI's `stream_options.include_usage` shape: an empty `choices` array and
    a populated `usage`. A client that iterates choices sees nothing to add,
    which is why it is safe to send after the finish frame.
    """
    body = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [],
        "usage": _usage_body(usage),
    }
    return b"data: " + json.dumps(body).encode() + b"\n\n"


def _usage_body(usage: Usage | None) -> dict[str, Any]:
    if usage is None:
        return {}
    return {
        "prompt_tokens": usage.input_tokens,
        "completion_tokens": usage.output_tokens,
        "total_tokens": (usage.input_tokens or 0) + (usage.output_tokens or 0),
    }


def completion(response: NormalizedResponse, *, model: str, completion_id: str) -> dict[str, Any]:
    """A non-streamed response in the OpenAI completion shape.

    Tool calls arrive whole here rather than fragmented, because a non-streamed
    response is whole by definition — the fragmentation in the streaming path is
    a property of the wire, not of the data.
    """
    message: dict[str, Any] = {"role": "assistant", "content": response.text or None}
    if response.reasoning:
        message["reasoning_content"] = response.reasoning
    if response.tool_calls:
        message["tool_calls"] = [
            {"index": call.index, "id": call.id, "type": "function",
             "function": {"name": call.name, "arguments": call.arguments}}
            for call in response.tool_calls
        ]
    return {
        "id": completion_id,
        "object": "chat.completion",
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": _WIRE_FINISH.get(response.finish_reason),
        }],
        "usage": _usage_body(response.usage),
    }
