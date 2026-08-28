"""M7's Google half — the Gemini native adapter (§6, Path B).

The four mappings that are judgements rather than transcription, and the
measurement that put Gemini on this path at all: its OpenAI-compatible endpoint
reports `finish_reason: stop` on a streamed tool call and omits the tool-call
index, both of which Clarvis's agent role reads.
"""

from __future__ import annotations

import json

from ravis.core.requests import NormalizedRequest
from ravis.core.responses import FinishReason, StreamEventType
from ravis.providers.google_wire import (
    StreamReader,
    dropped_parameters,
    read_response,
    read_usage,
    render_request,
)

CALL = {"functionCall": {"name": "read_file", "args": {"path": "/etc/hosts"}}}


def a_response(parts: list[dict], finish: str = "STOP", usage: dict | None = None) -> dict:
    body = {"candidates": [{"content": {"parts": parts, "role": "model"}, "finishReason": finish}]}
    if usage is not None:
        body["usageMetadata"] = usage
    return body


# ── The reason this adapter exists ───────────────────────────────────────────


def test_a_tool_call_finishes_as_a_tool_call_whatever_gemini_says() -> None:
    """Gemini reports `STOP` for a candidate that called a tool, and its
    OpenAI-compatible endpoint passes that straight through — which is the
    defect that moved Gemini onto the translated path.

    Reproducing it here would have made the whole exercise pointless, so the
    reason is derived from the content rather than read off the field.
    """
    answer = read_response(a_response([CALL]), provider="google", model="gemini")

    assert answer.finish_reason is FinishReason.TOOL_CALLS
    assert [call.name for call in answer.tool_calls] == ["read_file"]


def test_a_streamed_tool_call_carries_an_index_and_an_id() -> None:
    """The other half of the same defect. A client assembling fragments keys
    them by index, and correlates the result it sends back by id; the compat
    endpoint supplies neither."""
    events = list(StreamReader().events(a_response([CALL], finish="STOP")))
    fragments = [e for e in events if e.type is StreamEventType.TOOL_CALL_FRAGMENT]

    assert [f.tool_index for f in fragments] == [0]
    assert fragments[0].tool_id
    assert fragments[0].tool_name == "read_file"
    assert json.loads(fragments[0].arguments) == {"path": "/etc/hosts"}


def test_two_calls_in_separate_frames_keep_counting() -> None:
    """Gemini numbers nothing, so the index is this reader's own running count —
    and a second call arriving in a later frame has to continue the sequence
    rather than restart it."""
    reader = StreamReader()
    first = list(reader.events(a_response([CALL], finish=None)))  # type: ignore[arg-type]
    second = list(reader.events(a_response([CALL], finish="STOP")))

    indexes = [
        e.tool_index for e in first + second if e.type is StreamEventType.TOOL_CALL_FRAGMENT
    ]
    assert indexes == [0, 1]


def test_a_tool_call_id_is_stable_across_replays() -> None:
    """Derived from position rather than random: a conversation stored with one
    set of ids and replayed with another stops matching its own tool results."""
    once = read_response(a_response([CALL]), provider="google", model="g").tool_calls[0].id
    twice = read_response(a_response([CALL]), provider="google", model="g").tool_calls[0].id

    assert once == twice


def test_arguments_are_a_string_because_that_is_what_clients_parse() -> None:
    """Gemini sends `args` as parsed JSON; OpenAI sends a string. Sorted keys,
    so the same call renders identically twice."""
    answer = read_response(
        a_response([{"functionCall": {"name": "f", "args": {"b": 2, "a": 1}}}]),
        provider="google",
        model="g",
    )

    assert answer.tool_calls[0].arguments == '{"a": 1, "b": 2}'


# ── Content ──────────────────────────────────────────────────────────────────


def test_thoughts_are_reasoning_rather_than_the_answer() -> None:
    """Gemini returns its working in the same parts list as the reply, flagged
    `thought`. A client that rendered them together shows the model thinking as
    if it were the answer."""
    answer = read_response(
        a_response([{"text": "thinking", "thought": True}, {"text": "the answer"}]),
        provider="google",
        model="g",
    )

    assert answer.reasoning == "thinking"
    assert answer.text == "the answer"


def test_an_unrecognised_finish_reason_stays_unknown() -> None:
    """`OTHER` does not become `stop`. A stop that did not happen is worse than
    an admission that nobody knows."""
    answer = read_response(a_response([{"text": "hi"}], finish="OTHER"), provider="g", model="m")

    assert answer.finish_reason is FinishReason.UNKNOWN


def test_thinking_tokens_are_counted_in_output_and_kept_separate() -> None:
    """`candidatesTokenCount` excludes thinking, which arrives as
    `thoughtsTokenCount`. Billing counts both, and a client comparing output
    against a limit is asking what was generated — while the split stays
    visible."""
    usage = read_usage({"promptTokenCount": 10, "candidatesTokenCount": 5, "thoughtsTokenCount": 7})

    assert usage.input_tokens == 10
    assert usage.output_tokens == 12
    assert usage.reasoning_tokens == 7


def test_unreported_usage_stays_none_rather_than_zero() -> None:
    """§14: a zero would be a lie with a currency attached."""
    usage = read_usage(None)

    assert usage.input_tokens is None
    assert usage.is_reported is False


# ── Requests ─────────────────────────────────────────────────────────────────


def test_tool_results_become_function_responses_on_a_user_turn() -> None:
    """Gemini has no `tool` role. Consecutive results merge into one turn,
    because it rejects two user turns in a row and a client that called three
    tools sends three separate result messages."""
    body = render_request(NormalizedRequest(messages=[
        {"role": "user", "content": "go"},
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "a", "arguments": '{"x":1}'}},
            {"function": {"name": "b", "arguments": "{}"}},
        ]},
        {"role": "tool", "name": "a", "content": "one"},
        {"role": "tool", "name": "b", "content": "two"},
    ]))

    roles = [turn["role"] for turn in body["contents"]]
    assert roles == ["user", "model", "user"]
    assert len(body["contents"][2]["parts"]) == 2
    assert body["contents"][2]["parts"][0]["functionResponse"]["name"] == "a"


def test_the_system_prompt_becomes_a_system_instruction() -> None:
    body = render_request(NormalizedRequest(system="Be terse.", messages=[
        {"role": "user", "content": "hi"}]))

    assert body["systemInstruction"]["parts"][0]["text"] == "Be terse."


def test_a_required_tool_choice_becomes_any_mode() -> None:
    body = render_request(NormalizedRequest(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"function": {"name": "f", "parameters": {"type": "object", "properties": {}}}}],
        tool_choice="required",
    ))

    assert body["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"


def test_a_parameterless_tool_declares_no_parameters() -> None:
    """Gemini rejects a schema whose `properties` is empty, so a tool that takes
    nothing is declared without the field rather than with an empty one."""
    body = render_request(NormalizedRequest(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"function": {"name": "now", "parameters": {"type": "object", "properties": {}}}}],
    ))

    assert "parameters" not in body["tools"][0]["functionDeclarations"][0]


def test_a_remote_image_is_dropped_and_said_so() -> None:
    """Gemini takes inline bytes or a Files reference and has no "fetch this URL
    for me". Retrieving an address a request named is what §4.4's SSRF guard
    exists to prevent — so it is dropped, and reported rather than silently
    ignored."""
    request = NormalizedRequest(messages=[{"role": "user", "content": [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}},
    ]}])

    body = render_request(request)
    parts = body["contents"][0]["parts"]

    assert [p.get("text") for p in parts] == ["look"]
    assert any("image_url" in dropped for dropped in dropped_parameters(request))


def test_an_inline_image_survives() -> None:
    body = render_request(NormalizedRequest(messages=[{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]}]))

    inline = body["contents"][0]["parts"][0]["inlineData"]
    assert inline == {"mimeType": "image/png", "data": "AAAA"}


def test_a_call_in_one_frame_still_finishes_the_stream_as_a_tool_call() -> None:
    """Gemini sends the call and the finish in separate frames.

    This asked the *finishing frame* whether it carried a call, so the answer
    was always no and the stream ended `stop` with a tool call in it — the exact
    defect that moved Gemini off the compatible endpoint, reproduced inside the
    adapter written to avoid it. Whether a tool was called is a fact about the
    stream, not about one frame.
    """
    reader = StreamReader()
    list(reader.events(a_response([CALL], finish=None)))  # type: ignore[arg-type]
    ending = list(reader.events(a_response([], finish="STOP")))

    finishes = [e for e in ending if e.type is StreamEventType.FINISH]
    assert [f.finish_reason for f in finishes] == [FinishReason.TOOL_CALLS]
