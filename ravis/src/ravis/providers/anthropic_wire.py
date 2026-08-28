"""OpenAI chat-completions ↔ Anthropic Messages, as pure functions (§6, Path B).

Everything here is I/O-free and synchronous. That is the point: translation is
the part of M4 that can be wrong in ways no integration test catches, so it is
written where a test can hand it a dict and read a dict back — no client, no
event loop, no fixture server. The adapter next door does the HTTP and calls
into this.

**The four things that make this hard**, each of which has a comment where it
is handled rather than only here:

1. *Parallel tool results must arrive together.* OpenAI sends one `role: "tool"`
   message per result; Anthropic takes them as `tool_result` blocks inside a
   single user message. Emitting one user message per result is valid JSON and
   trains the model to stop calling tools in parallel.

2. *A stream index is not a tool index.* Anthropic indexes **content blocks** —
   text counts — while OpenAI indexes **tool calls**. A response whose first
   block is text and whose second is a tool call has Anthropic index 1 and
   OpenAI index 0, and getting this wrong misassembles every fragment.

3. *A fragment is not a call.* `input_json_delta` carries a slice of JSON that
   frequently does not parse alone, and it is passed through as a slice. The
   serializer's rule — id and name on the opening frame only — depends on that.

4. *`stop_reason: "refusal"` has no OpenAI equivalent.* It is mapped
   deliberately below rather than defaulted.

**What this module refuses to do is as load-bearing as what it does.** §7 says
an unsupported feature must never silently disappear, so a content part or a
tool shape this translation does not understand raises `TranslationError`
instead of being dropped. The request is then refused with a message naming the
cause — which is the whole difference between a compatibility gap someone can
fix and a response that is quietly missing an image.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Iterator

from ravis.core.requests import NormalizedRequest
from ravis.core.responses import (
    FinishReason,
    NormalizedResponse,
    NormalizedStreamEvent,
    StreamEventType,
    ToolCall,
    Usage,
)
from ravis.providers.base import TranslationError

# The only value this header has ever taken. A constant rather than a setting:
# it names the request/response *shape* this module renders and parses, so a
# deployment that changed it would be running a translation written for a
# different wire — configurable would make that a runtime surprise instead of a
# code change.
ANTHROPIC_VERSION = "2023-06-01"

# Anthropic's stop reasons in OpenAI's vocabulary.
#
# `refusal` is the deliberate one. It is a real terminal state — HTTP 200, no
# content, a `stop_details.category` saying which classifier declined — and
# OpenAI has no name for it. `content_filter` is the closest true statement the
# wire can carry: it is the only OpenAI finish reason meaning *the provider
# declined to answer*, which is exactly what happened. The category itself has
# no equivalent at all, so it is logged and put on the event's `raw` rather than
# invented onto the wire as something a client would misread.
#
# `pause_turn` is deliberately absent, and so is any unknown reason: both become
# UNKNOWN, which the serializer publishes as `null`. Mapping them to "stop"
# would assert the model finished when nobody established that.
STOP_REASONS = {
    "end_turn": FinishReason.STOP,
    "stop_sequence": FinishReason.STOP,
    "max_tokens": FinishReason.LENGTH,
    "tool_use": FinishReason.TOOL_CALLS,
    "refusal": FinishReason.CONTENT_FILTER,
}

# OpenAI's reasoning efforts against Anthropic's. `minimal` has no Anthropic
# level below `low`, so it maps there rather than being dropped; `xhigh` and
# `max` are Anthropic's own and pass through for a client that already knows
# them. Anything else is left off entirely — guessing an effort spends the
# user's money at a depth they did not ask for.
EFFORTS = {
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max",
}


# ── Request: OpenAI in, Anthropic out ────────────────────────────────────────


def render_request(
    request: NormalizedRequest, *, model: str, max_output_tokens: int
) -> dict[str, Any]:
    """One normalized request as an Anthropic Messages body.

    `max_output_tokens` is the configured fallback, not a cap: Anthropic
    *requires* `max_tokens` and OpenAI does not, so a request that named no
    limit still needs one. Defaulting it here rather than refusing keeps every
    ordinary OpenAI client working; the operator sets the number.

    `stream` is deliberately not set here. The adapter method that is running —
    `complete` or `stream` — is the only thing that knows which it is, and a
    second opinion in this function is a field that can disagree with the code
    path consuming the response.
    """
    system, conversation = _split_system(request.messages)
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": request.max_output_tokens or max_output_tokens,
        "messages": _render_messages(conversation),
    }
    if system:
        body["system"] = system
    _apply_tools(body, request)
    _apply_output_config(body, request)
    return body


def _split_system(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Lift every system message out, in order, leaving the conversation.

    Deliberately a second pass over the messages rather than a use of
    `NormalizedRequest.system`, which lifts only a *leading* system message with
    string content. That view is right for its purpose and too narrow for this
    one: a system message left in place would be rendered as a `user` turn here,
    silently promoting an instruction to something the model treats as the
    user's words.
    """
    system: list[str] = []
    conversation: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "system":
            conversation.append(message)
            continue
        system.append(_system_text(message.get("content")))
    return "\n\n".join(part for part in system if part), conversation


def _system_text(content: Any) -> str:
    """A system message's text, whether it arrived as a string or as parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _render_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation, with tool results gathered the way Anthropic needs.

    The gathering is the whole reason this is not a `map`. OpenAI emits one
    `role: "tool"` message per result and Anthropic takes them as `tool_result`
    blocks in **one** user message — so consecutive results are buffered and
    flushed together, and a run of three parallel results becomes one turn
    rather than three.
    """
    rendered: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "tool":
            pending.append(_tool_result(message))
            continue
        _flush_results(pending, rendered)
        if message.get("role") == "assistant":
            rendered.append(_assistant_message(message))
        else:
            rendered.append({"role": "user", "content": _user_content(message.get("content"))})
    _flush_results(pending, rendered)
    _refuse_prefill(rendered)
    return rendered


def _flush_results(pending: list[dict[str, Any]], rendered: list[dict[str, Any]]) -> None:
    """Emit the buffered tool results as one user turn, then clear the buffer."""
    if pending:
        rendered.append({"role": "user", "content": list(pending)})
        pending.clear()


def _tool_result(message: dict[str, Any]) -> dict[str, Any]:
    """One OpenAI tool message as an Anthropic `tool_result` block."""
    call_id = message.get("tool_call_id")
    if not call_id:
        raise TranslationError("a tool message arrived without a tool_call_id")
    content = message.get("content")
    return {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": content if isinstance(content, str) else json.dumps(content),
    }


def _refuse_prefill(rendered: list[dict[str, Any]]) -> None:
    """Refuse a trailing assistant turn, because that is a prefill.

    An OpenAI client ending its messages with an assistant turn is asking the
    model to continue that text. Anthropic accepted this for years and **now
    returns 400** on every current model, so passing it through produces a
    provider error whose cause is two layers away from the client that caused
    it. Refusing here says the actual thing.

    The cost is real and worth stating: prefill still works on Anthropic's older
    models, and this refuses it there too rather than keeping a model-version
    table that would be wrong the week it was written. A client that wants the
    old behaviour should ask for the output shape instead — `response_format`
    translates to a real structured-output constraint below.
    """
    if rendered and rendered[-1].get("role") == "assistant":
        raise TranslationError(
            "the request ends with an assistant message, which Anthropic reads as a prefill "
            "and rejects on current models; use response_format for structured output instead"
        )


def _assistant_message(message: dict[str, Any]) -> dict[str, Any]:
    """An assistant turn, with any tool calls rendered as `tool_use` blocks."""
    calls = message.get("tool_calls") or []
    if not calls:
        return {"role": "assistant", "content": _user_content(message.get("content"))}
    blocks: list[dict[str, Any]] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        blocks.append({"type": "text", "text": text})
    blocks.extend(_tool_use(call) for call in calls)
    return {"role": "assistant", "content": blocks}


def _tool_use(call: dict[str, Any]) -> dict[str, Any]:
    """One assistant tool call as a `tool_use` block.

    The arguments are parsed here and nowhere else. OpenAI carries them as a
    JSON *string* and Anthropic takes a JSON *object*, so a round trip is
    unavoidable — and a string that does not parse is a request that cannot be
    translated at all, rather than one to send with an empty input and let the
    model guess at.
    """
    function = call.get("function") or {}
    arguments = function.get("arguments") or "{}"
    try:
        parsed = json.loads(arguments)
    except ValueError as failure:
        raise TranslationError(
            f"tool call {call.get('id', '?')} carried arguments that are not JSON: {failure}"
        ) from failure
    return {
        "type": "tool_use",
        "id": call.get("id", ""),
        "name": function.get("name", ""),
        "input": parsed if isinstance(parsed, dict) else {},
    }


def _user_content(content: Any) -> Any:
    """User content, as a string when it was one and as blocks when it was not."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return [_content_part(part) for part in content]
    raise TranslationError(f"a message carried content of an unsupported type: {type(content)}")


def _content_part(part: Any) -> dict[str, Any]:
    """One multimodal content part.

    Raises on anything not recognised — `input_audio`, `file`, a provider's own
    extension — because §7 forbids an unsupported feature disappearing quietly.
    A refused request is a bug report; a dropped image is a mystery.
    """
    if not isinstance(part, dict):
        raise TranslationError(f"a content part was not an object: {part!r}")
    kind = part.get("type")
    if kind == "text":
        return {"type": "text", "text": part.get("text", "")}
    if kind == "image_url":
        return _image(part.get("image_url") or {})
    raise TranslationError(f"content parts of type {kind!r} have no Anthropic translation")


def _image(image_url: dict[str, Any]) -> dict[str, Any]:
    """An OpenAI image part as an Anthropic image block.

    Both forms an OpenAI client sends are handled: an inline `data:` URI, which
    Anthropic takes as a base64 source, and an ordinary URL, which it fetches
    itself. The `detail` hint has no Anthropic equivalent and is dropped — the
    one deliberate exception to the rule above, because it is an advisory about
    tokenisation rather than content, and refusing every request from a client
    that sets it would break more than it protects.
    """
    url = image_url.get("url", "")
    if not url:
        raise TranslationError("an image part carried no url")
    if not url.startswith("data:"):
        return {"type": "image", "source": {"type": "url", "url": url}}
    header, _, data = url.partition(",")
    media_type = header[len("data:") :].split(";")[0]
    if not data or not media_type:
        raise TranslationError("an inline image was not a base64 data URI")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def _apply_tools(body: dict[str, Any], request: NormalizedRequest) -> None:
    """Tools and tool choice, or neither.

    `tool_choice: "none"` drops the tools entirely rather than being translated.
    That is not a shortcut — it is the one rendering whose meaning is certain:
    a request with no tools cannot produce a tool call, which is exactly what
    the client asked for, and it does not depend on a `none` spelling this
    translation has not verified.
    """
    if not request.tools or request.tool_choice == "none":
        return
    body["tools"] = [_tool(tool) for tool in request.tools]
    choice = _tool_choice(request.tool_choice)
    if choice is not None:
        body["tool_choice"] = choice


def _tool(tool: dict[str, Any]) -> dict[str, Any]:
    """One OpenAI function tool as an Anthropic tool."""
    function = tool.get("function") or {}
    name = function.get("name")
    if not name:
        raise TranslationError("a tool was declared without a function name")
    rendered: dict[str, Any] = {
        "name": name,
        # An absent schema becomes an empty object schema rather than being
        # omitted: Anthropic requires `input_schema`, and a no-argument tool is
        # a legitimate thing for a client to declare.
        "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
    }
    if function.get("description"):
        rendered["description"] = function["description"]
    return rendered


def _tool_choice(choice: Any) -> dict[str, Any] | None:
    """OpenAI's tool choice as Anthropic's, or None to leave it default."""
    if choice == "required":
        return {"type": "any"}
    if choice == "auto":
        return {"type": "auto"}
    if isinstance(choice, dict):
        named = (choice.get("function") or {}).get("name")
        if named:
            return {"type": "tool", "name": named}
    return None


def _apply_output_config(body: dict[str, Any], request: NormalizedRequest) -> None:
    """Structured output and reasoning effort, both under `output_config`.

    Two things a training prior gets wrong, so both are stated: structured
    output lives at `output_config.format` rather than the older top-level
    `output_format`, and reasoning depth is `output_config.effort` rather than a
    thinking token budget — `budget_tokens` is rejected outright on current
    models.
    """
    output_config: dict[str, Any] = {}
    schema = (request.response_schema or {}).get("schema")
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    effort = EFFORTS.get(request.reasoning_effort or "")
    if effort:
        output_config["effort"] = effort
    if output_config:
        body["output_config"] = output_config


def dropped_parameters(request: NormalizedRequest) -> list[str]:
    """Request parameters this translation cannot carry, named rather than dropped.

    Today that is `temperature`, and the reason is a removal rather than an
    oversight: sampling parameters are **rejected with a 400** on every current
    Anthropic model. Forwarding one turns an ordinary OpenAI client — most of
    which set a temperature by default — into a hard failure, so it is left off
    the request.

    §7 says an unsupported feature must never silently disappear, and this is
    how the promise is kept without refusing the request: the caller logs what
    it lost. Refusing instead was considered and rejected, because it would make
    RAVIS unusable with the default settings of the clients it exists to serve.
    """
    return ["temperature"] if request.temperature is not None else []


# ── Response: Anthropic in, normalized out ───────────────────────────────────


def read_response(payload: dict[str, Any], *, provider: str, model: str) -> NormalizedResponse:
    """A whole Anthropic message as a normalized response."""
    texts: list[str] = []
    reasoning: list[str] = []
    calls: list[ToolCall] = []
    for block in payload.get("content") or []:
        _collect(block, texts, reasoning, calls)
    return NormalizedResponse(
        text="".join(texts),
        reasoning="".join(reasoning),
        tool_calls=calls,
        finish_reason=finish_reason(payload.get("stop_reason")),
        usage=usage_from(payload.get("usage") or {}),
        provider=provider,
        model=payload.get("model") or model,
        provider_request_id=payload.get("id") or "",
    )


def _collect(
    block: Any, texts: list[str], reasoning: list[str], calls: list[ToolCall]
) -> None:
    """Sort one content block into text, reasoning or a tool call.

    Unrecognised blocks are skipped rather than raising — the opposite of the
    request side, and deliberately so. A block type this code has not seen is
    the provider adding something, and failing a response that already cost the
    user money would turn an additive change into an outage. The request side
    refuses because nothing has been spent yet and the client can be told.
    """
    if not isinstance(block, dict):
        return
    kind = block.get("type")
    if kind == "text":
        texts.append(block.get("text", ""))
    elif kind == "thinking":
        # §8.5: reasoning stays out of `content`, all the way through.
        reasoning.append(block.get("thinking", ""))
    elif kind == "tool_use":
        # The index is this call's position among *tool calls*, which is what
        # OpenAI numbers — not its position among content blocks.
        calls.append(
            ToolCall(
                index=len(calls),
                id=block.get("id", ""),
                name=block.get("name", ""),
                arguments=json.dumps(block.get("input") or {}),
            )
        )


def finish_reason(stop_reason: Any) -> FinishReason:
    """Anthropic's stop reason in OpenAI's vocabulary, or UNKNOWN."""
    return STOP_REASONS.get(stop_reason or "", FinishReason.UNKNOWN)


def usage_from(usage: dict[str, Any]) -> Usage:
    """Token counts, leaving anything unreported as None.

    `cache_creation_input_tokens` is read and deliberately not carried: the
    normalized shape has one cache field and it means *tokens served from the
    cache*, which is a different and cheaper thing than tokens written to it.
    Adding the write count to it would overstate the saving (§14).

    **Cache reads are added into `input_tokens`, because Anthropic reports the
    two as disjoint and the normalized shape defines input as the total.**
    OpenAI and Google both report a total with the cached figure as a subset,
    and `cost.estimate` prices the halves apart by subtracting one from the
    other -- so passing Anthropic's counts through unchanged subtracted tokens
    that had never been added. A call answered largely from cache had its new
    input driven to zero: 20 new tokens against 5,000 cached priced as if the
    20 were free. §14 calls out the understating direction specifically, because
    a budget reads an understatement as room left.
    """
    served_from_cache = usage.get("cache_read_input_tokens")
    fresh = usage.get("input_tokens")
    return Usage(
        input_tokens=(
            fresh + served_from_cache
            if isinstance(fresh, int) and isinstance(served_from_cache, int)
            else fresh
        ),
        output_tokens=usage.get("output_tokens"),
        cached_input_tokens=served_from_cache,
    )


# ── Stream: Anthropic SSE in, normalized events out ──────────────────────────


class StreamReader:
    """Anthropic's stream events as normalized ones, in order.

    Stateful because two facts span events and neither is carried in the frame
    that needs them: which OpenAI tool index a content-block index belongs to,
    and the usage totals, which arrive split between the first event and the
    last. Everything else is a straight rendering.
    """

    def __init__(self) -> None:
        # Anthropic content-block index → OpenAI tool-call index. Populated at
        # `content_block_start` and read by every `input_json_delta` after it,
        # because the delta carries only the block index.
        self._tool_index: dict[int, int] = {}
        self._tools_seen = 0
        self._usage = Usage()
        self._handlers: dict[str, Callable[[dict[str, Any]], Iterable[NormalizedStreamEvent]]] = {
            "message_start": self._message_start,
            "content_block_start": self._block_start,
            "content_block_delta": self._block_delta,
            "message_delta": self._message_delta,
            "message_stop": self._message_stop,
            "error": self._error,
        }

    def events(self, payload: dict[str, Any]) -> Iterable[NormalizedStreamEvent]:
        """Whatever one Anthropic stream event becomes — often nothing.

        `ping`, `content_block_stop` and anything unrecognised produce no
        events. Silence is the correct rendering: OpenAI's wire has no frame for
        "a block ended", and inventing one would put an empty delta in front of
        a client for every block in every response.
        """
        handler = self._handlers.get(payload.get("type", ""))
        return handler(payload) if handler else ()

    def _message_start(self, payload: dict[str, Any]) -> Iterable[NormalizedStreamEvent]:
        """Hold the input-token counts until there is somewhere to put them."""
        message = payload.get("message") or {}
        self._usage = usage_from(message.get("usage") or {})
        return ()

    def _block_start(self, payload: dict[str, Any]) -> Iterable[NormalizedStreamEvent]:
        """Open a tool call, or pass through text a block started with."""
        block = payload.get("content_block") or {}
        if block.get("type") == "tool_use":
            index = self._assign_tool_index(payload.get("index", 0))
            # The opening fragment: id and name, no arguments yet. Exactly what
            # OpenAI's first tool-call frame carries, and the frame the
            # serializer will not repeat.
            return (
                NormalizedStreamEvent(
                    type=StreamEventType.TOOL_CALL_FRAGMENT,
                    tool_index=index,
                    tool_id=block.get("id", ""),
                    tool_name=block.get("name", ""),
                    arguments="",
                ),
            )
        if block.get("type") == "text" and block.get("text"):
            return (NormalizedStreamEvent(type=StreamEventType.TEXT, text=block["text"]),)
        return ()

    def _assign_tool_index(self, block_index: int) -> int:
        """The OpenAI tool index for an Anthropic content-block index.

        The translation §8.3 depends on. Anthropic numbers content blocks and
        text blocks take numbers too, so the first tool call in a response that
        opened with a sentence is block 1 and tool 0.
        """
        index = self._tools_seen
        self._tool_index[block_index] = index
        self._tools_seen += 1
        return index

    def _block_delta(self, payload: dict[str, Any]) -> Iterable[NormalizedStreamEvent]:
        """One incremental update — text, reasoning, or a slice of tool JSON."""
        delta = payload.get("delta") or {}
        kind = delta.get("type")
        if kind == "text_delta":
            return (NormalizedStreamEvent(type=StreamEventType.TEXT, text=delta.get("text", "")),)
        if kind == "thinking_delta":
            return (
                NormalizedStreamEvent(
                    type=StreamEventType.REASONING, text=delta.get("thinking", "")
                ),
            )
        if kind == "input_json_delta":
            # Passed through as the slice it is. It routinely does not parse on
            # its own, and assembling it here would reframe what a transparent
            # path forwards untouched.
            return (
                NormalizedStreamEvent(
                    type=StreamEventType.TOOL_CALL_FRAGMENT,
                    tool_index=self._tool_index.get(payload.get("index", 0), 0),
                    arguments=delta.get("partial_json", ""),
                ),
            )
        # `signature_delta` lands here: it authenticates a thinking block for
        # replay to Anthropic and means nothing on an OpenAI wire.
        return ()

    def _message_delta(self, payload: dict[str, Any]) -> Iterable[NormalizedStreamEvent]:
        """The finish reason, and the output tokens that arrive with it."""
        self._accumulate(payload.get("usage") or {})
        delta = payload.get("delta") or {}
        if "stop_reason" not in delta:
            return ()
        return (
            NormalizedStreamEvent(
                type=StreamEventType.FINISH,
                finish_reason=finish_reason(delta.get("stop_reason")),
                # A refusal's category rides here rather than on the wire: it
                # has no OpenAI equivalent, and a diagnostic that can name which
                # classifier declined is worth more than a field a client would
                # misread.
                raw={"stop_details": delta.get("stop_details")}
                if delta.get("stop_details")
                else {},
            ),
        )

    def _accumulate(self, usage: dict[str, Any]) -> None:
        """Fold a late usage report into what `message_start` reported.

        Field by field rather than by replacement: `message_delta` carries the
        output tokens and not the input ones, so assigning would discard the
        half already known.
        """
        counted = usage_from(usage)
        if counted.input_tokens is not None:
            self._usage.input_tokens = counted.input_tokens
        if counted.output_tokens is not None:
            self._usage.output_tokens = counted.output_tokens
        if counted.cached_input_tokens is not None:
            self._usage.cached_input_tokens = counted.cached_input_tokens

    def _message_stop(self, payload: dict[str, Any]) -> Iterable[NormalizedStreamEvent]:
        """The usage chunk, last — where an OpenAI client expects to find it."""
        del payload  # The event carries nothing; its arrival is the signal.
        if not self._usage.is_reported:
            return ()
        return (NormalizedStreamEvent(type=StreamEventType.USAGE, usage=self._usage),)

    def _error(self, payload: dict[str, Any]) -> Iterable[NormalizedStreamEvent]:
        """A mid-stream error, handed to the relay rather than rendered as text.

        Anthropic can end a stream with an `error` event after content has
        already been sent — an overload during generation, most often. The relay
        decides what that means for the client; a delta here would disguise it
        as more of the answer.
        """
        error = payload.get("error") or {}
        return (
            NormalizedStreamEvent(
                type=StreamEventType.ERROR,
                error=error.get("message") or "the provider ended the stream with an error",
                raw=payload,
            ),
        )


def sse_payloads(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """The JSON objects carried by an SSE byte stream's `data:` lines.

    The `event:` lines are deliberately ignored: every Anthropic payload repeats
    its type in the body, so parsing both would mean two sources of truth about
    what an event is, and they can disagree when a frame is split.

    A `data:` line that is not JSON is skipped rather than raising. Ending a
    stream that is already delivering content because one frame was malformed
    costs the client everything that came after it; skipping costs one frame.
    """
    for line in lines:
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if not data:
            continue
        try:
            payload = json.loads(data)
        except ValueError:
            continue
        if isinstance(payload, dict):
            yield payload
