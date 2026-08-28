"""M4's translation, tested where it has no network to hide behind.

`anthropic_wire` is pure, so these tests hand it a dict and read a dict back.
That is deliberate: the failures this file exists to catch — a tool index off by
one, parallel results split across turns, a refusal reported as a completion —
all produce a *plausible* response, so an end-to-end test that only checks for
200 would pass while the client received nonsense.
"""

from __future__ import annotations

import json

import pytest

from ravis.core.requests import NormalizedRequest
from ravis.core.responses import FinishReason, StreamEventType
from ravis.providers.anthropic_wire import (
    StreamReader,
    dropped_parameters,
    read_response,
    render_request,
    sse_payloads,
)
from ravis.providers.base import TranslationError


def rendered(**fields: object) -> dict[str, object]:
    """One request rendered, with the boring arguments filled in."""
    request = NormalizedRequest(**fields)  # type: ignore[arg-type]
    return render_request(request, model="claude-opus-5", max_output_tokens=16000)


# ── The request ──────────────────────────────────────────────────────────────


def test_system_messages_become_the_top_level_system_field() -> None:
    """Anthropic takes the system prompt beside the conversation, not inside it."""
    body = rendered(
        messages=[
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "Hi"},
        ]
    )

    assert body["system"] == "Be terse."
    assert body["messages"] == [{"role": "user", "content": "Hi"}]


def test_every_system_message_is_lifted_not_only_the_first() -> None:
    """A later system message must not be delivered as if the user said it."""
    body = rendered(
        messages=[
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "Hi"},
            {"role": "system", "content": "Answer in French."},
        ]
    )

    assert body["system"] == "Be terse.\n\nAnswer in French."
    assert [message["role"] for message in body["messages"]] == ["user"]


def test_max_tokens_is_supplied_when_the_client_named_none() -> None:
    """Anthropic requires it; OpenAI does not. The gap is filled, not refused."""
    body = rendered(messages=[{"role": "user", "content": "Hi"}])

    assert body["max_tokens"] == 16000


def test_max_tokens_the_client_named_is_not_overridden() -> None:
    """The default fills an absence; it is not a cap on a stated limit."""
    body = rendered(messages=[{"role": "user", "content": "Hi"}], max_output_tokens=64)

    assert body["max_tokens"] == 64


def test_parallel_tool_results_arrive_in_one_user_message() -> None:
    """Splitting them is valid JSON and trains the model out of parallel calls."""
    body = rendered(
        messages=[
            {"role": "user", "content": "Read both files"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_a", "function": {"name": "read", "arguments": '{"p":"a"}'}},
                    {"id": "call_b", "function": {"name": "read", "arguments": '{"p":"b"}'}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_a", "content": "alpha"},
            {"role": "tool", "tool_call_id": "call_b", "content": "beta"},
            {"role": "user", "content": "thanks"},
        ]
    )

    results = [message for message in body["messages"] if _has_block(message, "tool_result")]
    assert len(results) == 1, "two parallel results must be one turn, not two"
    assert [block["tool_use_id"] for block in results[0]["content"]] == ["call_a", "call_b"]


def test_assistant_tool_calls_become_tool_use_blocks() -> None:
    """OpenAI carries arguments as a JSON string; Anthropic takes an object."""
    body = rendered(
        messages=[
            {"role": "user", "content": "Edit it"},
            {
                "role": "assistant",
                "content": "On it.",
                "tool_calls": [
                    {"id": "call_a", "function": {"name": "edit", "arguments": '{"path":"a.ts"}'}}
                ],
            },
            {"role": "tool", "tool_call_id": "call_a", "content": "done"},
        ]
    )

    blocks = body["messages"][1]["content"]
    assert blocks[0] == {"type": "text", "text": "On it."}
    assert blocks[1] == {
        "type": "tool_use",
        "id": "call_a",
        "name": "edit",
        "input": {"path": "a.ts"},
    }


def test_unparseable_tool_arguments_are_refused_not_guessed() -> None:
    """An empty input would have the model call the tool with nothing."""
    with pytest.raises(TranslationError, match="not JSON"):
        rendered(
            messages=[
                {"role": "user", "content": "Edit it"},
                {
                    "role": "assistant",
                    "tool_calls": [{"id": "c", "function": {"name": "e", "arguments": "{oops"}}],
                },
                {"role": "tool", "tool_call_id": "c", "content": "done"},
            ]
        )


def test_a_trailing_assistant_message_is_refused_as_a_prefill() -> None:
    """Anthropic returns 400 for this; the refusal here says why.

    The value is the message. A provider 400 tells the client its request was
    invalid, two layers from the thing that made it so.
    """
    with pytest.raises(TranslationError, match="prefill"):
        rendered(
            messages=[
                {"role": "user", "content": "Write a haiku"},
                {"role": "assistant", "content": "Silent"},
            ]
        )


def test_an_untranslatable_content_part_is_refused_rather_than_dropped() -> None:
    """§7: an unsupported feature must never silently disappear."""
    with pytest.raises(TranslationError, match="input_audio"):
        rendered(
            messages=[
                {"role": "user", "content": [{"type": "input_audio", "input_audio": {}}]}
            ]
        )


def test_an_inline_image_becomes_a_base64_source() -> None:
    """The `data:` URI form an OpenAI client sends for a local file."""
    body = rendered(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
                ],
            }
        ]
    )

    assert body["messages"][0]["content"][0] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
    }


def test_a_linked_image_becomes_a_url_source() -> None:
    """The other form, which Anthropic fetches itself."""
    body = rendered(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}
                ],
            }
        ]
    )

    assert body["messages"][0]["content"][0]["source"] == {
        "type": "url",
        "url": "https://example.test/a.png",
    }


def test_tools_are_rendered_with_input_schema() -> None:
    """`function.parameters` is `input_schema` on the other side."""
    body = rendered(
        messages=[{"role": "user", "content": "Hi"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"p": {"type": "string"}}},
                },
            }
        ],
    )

    assert body["tools"] == [
        {
            "name": "read",
            "input_schema": {"type": "object", "properties": {"p": {"type": "string"}}},
            "description": "Read a file",
        }
    ]


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        ("required", {"type": "any"}),
        ("auto", {"type": "auto"}),
        ({"type": "function", "function": {"name": "read"}}, {"type": "tool", "name": "read"}),
    ],
)
def test_tool_choice_translations(choice: object, expected: dict[str, str]) -> None:
    body = rendered(
        messages=[{"role": "user", "content": "Hi"}],
        tools=[{"type": "function", "function": {"name": "read"}}],
        tool_choice=choice,
    )

    assert body["tool_choice"] == expected


def test_tool_choice_none_removes_the_tools_entirely() -> None:
    """The one rendering whose meaning is certain: no tools, no tool call."""
    body = rendered(
        messages=[{"role": "user", "content": "Hi"}],
        tools=[{"type": "function", "function": {"name": "read"}}],
        tool_choice="none",
    )

    assert "tools" not in body
    assert "tool_choice" not in body


def test_a_json_schema_response_format_becomes_output_config_format() -> None:
    """Structured output moved under `output_config`; the old key is gone."""
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    body = rendered(
        messages=[{"role": "user", "content": "Hi"}],
        response_schema={"name": "answer", "schema": schema, "strict": True},
    )

    assert body["output_config"] == {"format": {"type": "json_schema", "schema": schema}}
    assert "output_format" not in body


def test_reasoning_effort_becomes_output_config_effort() -> None:
    """`budget_tokens` is rejected on current models; effort is the control."""
    body = rendered(messages=[{"role": "user", "content": "Hi"}], reasoning_effort="medium")

    assert body["output_config"] == {"effort": "medium"}
    assert "thinking" not in body


def test_an_unrecognised_effort_is_left_off_rather_than_guessed() -> None:
    body = rendered(messages=[{"role": "user", "content": "Hi"}], reasoning_effort="turbo")

    assert "output_config" not in body


def test_temperature_is_not_sent_and_is_reported_as_dropped() -> None:
    """Sampling parameters are rejected outright by every current model.

    Dropping it silently would break §7; refusing the request would break every
    OpenAI client that sets a temperature by default. It is dropped and named.
    """
    request = NormalizedRequest(messages=[{"role": "user", "content": "Hi"}], temperature=0.7)
    body = render_request(request, model="claude-opus-5", max_output_tokens=16000)

    assert "temperature" not in body
    assert dropped_parameters(request) == ["temperature"]


def test_nothing_is_reported_dropped_when_the_client_asked_for_nothing() -> None:
    request = NormalizedRequest(messages=[{"role": "user", "content": "Hi"}])

    assert dropped_parameters(request) == []


# ── The response ─────────────────────────────────────────────────────────────


def test_content_blocks_are_sorted_into_text_reasoning_and_calls() -> None:
    """A tool call's index counts tool calls, not content blocks."""
    answer = read_response(
        {
            "id": "msg_1",
            "model": "claude-opus-5",
            "content": [
                {"type": "thinking", "thinking": "considering"},
                {"type": "text", "text": "Editing."},
                {"type": "tool_use", "id": "toolu_9", "name": "edit", "input": {"p": "a.ts"}},
            ],
            "stop_reason": "tool_use",
        },
        provider="anthropic",
        model="claude-opus-5",
    )

    assert answer.text == "Editing."
    assert answer.reasoning == "considering"
    assert answer.finish_reason is FinishReason.TOOL_CALLS
    assert answer.tool_calls[0].index == 0, "content block 2, but tool call 0"
    assert json.loads(answer.tool_calls[0].arguments) == {"p": "a.ts"}
    assert answer.provider_request_id == "msg_1"


@pytest.mark.parametrize(
    ("stop_reason", "expected"),
    [
        ("end_turn", FinishReason.STOP),
        ("stop_sequence", FinishReason.STOP),
        ("max_tokens", FinishReason.LENGTH),
        ("tool_use", FinishReason.TOOL_CALLS),
        # The deliberate one: a safety decline is the only thing OpenAI's
        # vocabulary can call a provider declining to answer.
        ("refusal", FinishReason.CONTENT_FILTER),
        # Not "stop": nobody established that the model finished.
        ("pause_turn", FinishReason.UNKNOWN),
        (None, FinishReason.UNKNOWN),
    ],
)
def test_stop_reason_translations(stop_reason: str | None, expected: FinishReason) -> None:
    answer = read_response(
        {"content": [], "stop_reason": stop_reason}, provider="anthropic", model="m"
    )

    assert answer.finish_reason is expected


def test_usage_carries_cache_reads_and_leaves_the_rest_unknown() -> None:
    """A write to the cache is not a read from it, and costs differently."""
    answer = read_response(
        {
            "content": [],
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 80,
                "cache_creation_input_tokens": 15,
            },
        },
        provider="anthropic",
        model="m",
    )

    # 180, not 100. Anthropic reports `input_tokens` and
    # `cache_read_input_tokens` as *disjoint* counts; OpenAI and Google report a
    # total with the cached figure as a subset, and the normalized shape follows
    # the latter because `cost.estimate` prices the halves apart by subtracting
    # one from the other. Passing 100 through meant the engine computed
    # `max(100 - 80, 0) = 20` new tokens against a call that actually read 100
    # fresh ones -- understating it, which is the direction §14 names because a
    # budget reads an understatement as room left.
    assert answer.usage.input_tokens == 180, "fresh plus served-from-cache"
    assert answer.usage.cached_input_tokens == 80
    assert answer.usage.reasoning_tokens is None


def test_an_unrecognised_content_block_does_not_fail_a_paid_response() -> None:
    """The request side refuses; the response side absorbs. Money is the reason."""
    answer = read_response(
        {"content": [{"type": "something_new"}, {"type": "text", "text": "Hi"}]},
        provider="anthropic",
        model="m",
    )

    assert answer.text == "Hi"


# ── The stream ───────────────────────────────────────────────────────────────


def events_from(frames: list[bytes]) -> list[object]:
    """Every normalized event a fixture stream produces, in order."""
    reader = StreamReader()
    lines = "".join(frame.decode() for frame in frames).splitlines()
    return [event for payload in sse_payloads(lines) for event in reader.events(payload)]


def test_a_text_stream_yields_text_then_finish_then_usage() -> None:
    """Usage last, where an OpenAI client looks for it."""
    events = events_from(
        [
            b'data: {"type":"message_start","message":{"usage":{"input_tokens":9}}}\n\n',
            b'data: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"text_delta","text":"Hi"}}\n\n',
            b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
            b'"usage":{"output_tokens":2}}\n\n',
            b'data: {"type":"message_stop"}\n\n',
        ]
    )

    assert [event.type for event in events] == [  # type: ignore[attr-defined]
        StreamEventType.TEXT,
        StreamEventType.FINISH,
        StreamEventType.USAGE,
    ]
    # Both halves survived: the input count came from the first event and the
    # output count from the second-to-last.
    assert events[2].usage.input_tokens == 9  # type: ignore[attr-defined]
    assert events[2].usage.output_tokens == 2  # type: ignore[attr-defined]


def test_a_tool_call_after_text_is_openai_tool_index_zero() -> None:
    """Anthropic content block 1, OpenAI tool index 0.

    The single most consequential line in the translation: get it wrong and
    every fragment is filed under a call that does not exist.
    """
    from tests.conftest_upstream import ANTHROPIC_TOOL_FRAMES

    events = events_from(ANTHROPIC_TOOL_FRAMES)
    fragments = [
        event
        for event in events
        if event.type is StreamEventType.TOOL_CALL_FRAGMENT  # type: ignore[attr-defined]
    ]

    assert {fragment.tool_index for fragment in fragments} == {0}  # type: ignore[attr-defined]
    assert fragments[0].tool_id == "toolu_9"  # type: ignore[attr-defined]
    assert fragments[0].tool_name == "edit_file"  # type: ignore[attr-defined]


def test_tool_arguments_are_passed_through_unassembled() -> None:
    """A fragment is not a call: the slices arrive as the provider split them."""
    from tests.conftest_upstream import ANTHROPIC_TOOL_FRAMES

    events = events_from(ANTHROPIC_TOOL_FRAMES)
    slices = [
        event.arguments  # type: ignore[attr-defined]
        for event in events
        if event.type is StreamEventType.TOOL_CALL_FRAGMENT  # type: ignore[attr-defined]
    ]

    assert slices == ["", '{"pa', 'th":"foo.ts"}']
    assert json.loads("".join(slices)) == {"path": "foo.ts"}


def test_thinking_deltas_stay_out_of_the_content_stream() -> None:
    """§8.5: private deliberation must not reach the user as the answer."""
    events = events_from(
        [
            b'data: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"thinking_delta","thinking":"hmm"}}\n\n',
            b'data: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"signature_delta","signature":"abc"}}\n\n',
        ]
    )

    assert [event.type for event in events] == [StreamEventType.REASONING]  # type: ignore[attr-defined]


def test_a_mid_stream_error_becomes_an_error_event() -> None:
    """Not a delta: an error disguised as content hides the relay's decision."""
    events = events_from(
        [b'data: {"type":"error","error":{"type":"overloaded_error","message":"busy"}}\n\n']
    )

    assert events[0].type is StreamEventType.ERROR  # type: ignore[attr-defined]
    assert events[0].error == "busy"  # type: ignore[attr-defined]


def test_a_refusal_carries_its_category_off_the_wire() -> None:
    """`content_filter` on the wire; the category where a diagnostic can read it."""
    events = events_from(
        [
            b'data: {"type":"message_delta","delta":{"stop_reason":"refusal",'
            b'"stop_details":{"type":"refusal","category":"cyber"}}}\n\n'
        ]
    )

    assert events[0].finish_reason is FinishReason.CONTENT_FILTER  # type: ignore[attr-defined]
    assert events[0].raw["stop_details"]["category"] == "cyber"  # type: ignore[attr-defined]


def test_a_malformed_frame_costs_one_frame_not_the_stream() -> None:
    """A stream already delivering content must not end over one bad line."""
    events = events_from(
        [
            b"data: {not json\n\n",
            b'data: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"text_delta","text":"Hi"}}\n\n',
        ]
    )

    assert [event.type for event in events] == [StreamEventType.TEXT]  # type: ignore[attr-defined]


def _has_block(message: dict[str, object], kind: str) -> bool:
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == kind for block in content
    )
