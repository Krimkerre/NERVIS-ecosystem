"""Translating between the normalized shapes and Gemini's own (§6, Path B).

**Why this exists when Google publishes an OpenAI-compatible endpoint.** It
does, at `/v1beta/openai`, and RAVIS used it — the transparent path, no
translation, exactly what §6 prefers: *"do not normalize an already-compatible
stream for architectural purity"*. The trouble is that it is not compatible on
the surface §6 singles out as high-risk. Measured against the same request, the
same day, through the same code path:

    google (compat)    finish_reason=stop        tool index absent
    openrouter         finish_reason=tool_calls  tool index 0

Both of those break a client that switches on `finish_reason` to notice a tool
call, or keys fragments by index to assemble one — which is precisely what
Clarvis's agent role does, and what its conformance suite checks. So Gemini goes
on Path B, which is what §6 said in the first place; the compat endpoint was the
shortcut worth trying and the measurement is the reason it was abandoned.

**The four mappings that are decisions rather than transcription.**

*Tool calls have no id.* Gemini's `functionCall` carries a name and arguments
and nothing else, while every OpenAI client needs an id to correlate the result
it sends back. One is synthesised, derived from the call's position so the same
stream always produces the same ids — a random one would make a replayed
conversation stop matching itself.

*Arguments are an object, not a string.* Gemini sends `args` as parsed JSON;
OpenAI sends a string the client parses. Serialised with sorted keys, so the
same call renders identically twice and a fixture can be compared.

*`finishReason` is STOP even when the model called a tool.* Read literally that
is the compat endpoint's bug reproduced in our own code, so the reason is
derived from the content: a candidate carrying a `functionCall` finished
`tool_calls`, whatever the field says.

*Thoughts are content.* Gemini returns reasoning as ordinary parts flagged
`thought: true`, in the same list as the answer. Filed as REASONING rather than
TEXT, because a client that renders them together shows the model's working as
if it were the reply.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Iterator

from ravis.core.requests import NormalizedRequest
from ravis.core.responses import (
    FinishReason,
    NormalizedResponse,
    NormalizedStreamEvent,
    StreamEventType,
    ToolCall,
    Usage,
)

# Gemini's own words for why generation ended, in ours. `OTHER` and anything
# unlisted stay UNKNOWN rather than becoming STOP: a stop that did not happen is
# worse than an admission that nobody knows, and §9.4's rule about not
# presenting a guess as a measurement applies to enums too.
FINISH_REASONS = {
    "STOP": FinishReason.STOP,
    "MAX_TOKENS": FinishReason.LENGTH,
    "SAFETY": FinishReason.CONTENT_FILTER,
    "RECITATION": FinishReason.CONTENT_FILTER,
    "PROHIBITED_CONTENT": FinishReason.CONTENT_FILTER,
    "SPII": FinishReason.CONTENT_FILTER,
    "BLOCKLIST": FinishReason.CONTENT_FILTER,
}

# What `reasoning_effort` becomes. Gemini takes a token budget rather than a
# label, and -1 means "decide for yourself" — which is the honest rendering of
# a request that named no effort at all.
THINKING_BUDGETS = {"minimal": 0, "low": 1024, "medium": 8192, "high": 24576}


def render_request(
    request: NormalizedRequest, *, max_output_tokens: int | None = None
) -> dict[str, Any]:
    """The Gemini request body for one normalized request."""
    body: dict[str, Any] = {"contents": _contents(request.messages)}
    if request.system:
        body["systemInstruction"] = {"parts": [{"text": request.system}]}
    _apply_tools(body, request)
    _apply_generation(body, request, max_output_tokens)
    return body


def _contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI messages as Gemini turns.

    Tool results are not a role in Gemini: they are `functionResponse` parts on
    a *user* turn. Consecutive results merge into one turn, because Gemini
    rejects two user turns in a row and a client that called three tools sends
    three separate result messages.
    """
    turns: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        if role == "system":
            # Already lifted into systemInstruction by the caller; a stray one
            # mid-conversation is rendered as a user turn rather than dropped.
            turns.append({"role": "user", "parts": [{"text": _text_of(message.get("content"))}]})
        elif role == "tool":
            _append_result(turns, message)
        elif role == "assistant":
            turns.append({"role": "model", "parts": _assistant_parts(message)})
        else:
            turns.append({"role": "user", "parts": _user_parts(message.get("content"))})
    return turns


def _append_result(turns: list[dict[str, Any]], message: dict[str, Any]) -> None:
    part = {
        "functionResponse": {
            "name": str(message.get("name") or message.get("tool_call_id") or "tool"),
            "response": {"content": _text_of(message.get("content"))},
        }
    }
    if turns and turns[-1]["role"] == "user" and _is_results(turns[-1]):
        turns[-1]["parts"].append(part)
        return
    turns.append({"role": "user", "parts": [part]})


def _is_results(turn: dict[str, Any]) -> bool:
    return all("functionResponse" in part for part in turn.get("parts", []))


def _assistant_parts(message: dict[str, Any]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    text = _text_of(message.get("content"))
    if text:
        parts.append({"text": text})
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        parts.append({
            "functionCall": {
                "name": str(function.get("name") or ""),
                "args": _arguments(function.get("arguments")),
            }
        })
    # Gemini refuses an empty parts list. An assistant turn with neither text
    # nor a call is a stream that produced nothing, and an empty string is the
    # faithful rendering of it.
    return parts or [{"text": ""}]


def _user_parts(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"text": content}]
    parts: list[dict[str, Any]] = []
    for piece in content or []:
        if not isinstance(piece, dict):
            continue
        if piece.get("type") == "text":
            parts.append({"text": str(piece.get("text") or "")})
        elif piece.get("type") == "image_url":
            image = _image(piece.get("image_url") or {})
            if image:
                parts.append(image)
    return parts or [{"text": ""}]


def _image(image_url: dict[str, Any]) -> dict[str, Any] | None:
    """A data URI as inline bytes. A remote URL is dropped.

    Gemini takes base64 inline or a Files API reference, and has no equivalent
    of "fetch this URL for me". Uploading on the client's behalf would mean
    RAVIS retrieving an arbitrary address a request named, which §4.4 forbids
    for the same reason the SSRF guard exists.
    """
    url = str(image_url.get("url") or "")
    if not url.startswith("data:"):
        return None
    header, _, payload = url.partition(",")
    mime = header[5:].split(";")[0] or "image/png"
    return {"inlineData": {"mimeType": mime, "data": payload}}


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not content:
        return ""
    return "".join(
        str(piece.get("text") or "")
        for piece in content
        if isinstance(piece, dict) and piece.get("type") == "text"
    )


def _arguments(raw: Any) -> dict[str, Any]:
    """A tool call's arguments as an object, whichever way they arrived.

    Malformed JSON becomes an empty object rather than raising: the request is
    the client's own previous turn being replayed, and refusing to send it back
    would strand a conversation on one bad frame from a provider.
    """
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _apply_tools(body: dict[str, Any], request: NormalizedRequest) -> None:
    if not request.tools:
        return
    declarations = []
    for tool in request.tools:
        function = tool.get("function") or {}
        declaration: dict[str, Any] = {"name": str(function.get("name") or "")}
        if function.get("description"):
            declaration["description"] = str(function["description"])
        parameters = function.get("parameters")
        if isinstance(parameters, dict) and parameters.get("properties"):
            # Gemini rejects a parameterless schema with an empty `properties`,
            # so a tool that takes nothing is declared with no parameters field.
            declaration["parameters"] = parameters
        declarations.append(declaration)
    body["tools"] = [{"functionDeclarations": declarations}]
    config = _tool_config(request.tool_choice)
    if config:
        body["toolConfig"] = config


def _tool_config(choice: Any) -> dict[str, Any] | None:
    """`tool_choice`, in Gemini's vocabulary. None when it said nothing."""
    if choice in (None, "auto"):
        return None
    if choice == "none":
        return {"functionCallingConfig": {"mode": "NONE"}}
    if choice == "required":
        return {"functionCallingConfig": {"mode": "ANY"}}
    if isinstance(choice, dict):
        named = (choice.get("function") or {}).get("name")
        if named:
            return {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [str(named)]}}
    return None


def _apply_generation(
    body: dict[str, Any], request: NormalizedRequest, max_output_tokens: int | None
) -> None:
    config: dict[str, Any] = {}
    if request.temperature is not None:
        config["temperature"] = request.temperature
    limit = request.max_output_tokens or max_output_tokens
    if limit:
        config["maxOutputTokens"] = limit
    if request.response_schema:
        config["responseMimeType"] = "application/json"
        config["responseSchema"] = request.response_schema
    if request.reasoning_effort in THINKING_BUDGETS:
        config["thinkingConfig"] = {"thinkingBudget": THINKING_BUDGETS[request.reasoning_effort]}
    if config:
        body["generationConfig"] = config


def dropped_parameters(request: NormalizedRequest) -> list[str]:
    """What this provider cannot be told, so a diagnostic can say so.

    Reported rather than silently ignored, per §9.4: a parameter that vanished
    without a word is indistinguishable from one that was honoured.
    """
    dropped = []
    if request.reasoning_effort and request.reasoning_effort not in THINKING_BUDGETS:
        dropped.append(f"reasoning_effort={request.reasoning_effort}")
    for message in request.messages:
        for piece in message.get("content") or []:
            if isinstance(piece, dict) and piece.get("type") == "image_url":
                url = str((piece.get("image_url") or {}).get("url") or "")
                if not url.startswith("data:"):
                    dropped.append("image_url (remote URLs are not fetched)")
    return dropped


# ── Reading ──────────────────────────────────────────────────────────────────


def read_response(payload: dict[str, Any], *, provider: str, model: str) -> NormalizedResponse:
    """One non-streamed Gemini response as a normalized one."""
    candidates = payload.get("candidates") or []
    candidate = candidates[0] if candidates else {}
    text, reasoning, calls, images = _collect(candidate.get("content") or {})
    return NormalizedResponse(
        text=text,
        reasoning=reasoning,
        images=images,
        tool_calls=calls,
        finish_reason=finish_reason(candidate.get("finishReason"), bool(calls)),
        usage=read_usage(payload.get("usageMetadata")),
        provider=provider,
        model=str(payload.get("modelVersion") or model),
        provider_request_id=str(payload.get("responseId") or ""),
    )


def _collect(content: dict[str, Any]) -> tuple[str, str, list[ToolCall], list[str]]:
    text, reasoning = "", ""
    calls: list[ToolCall] = []
    images: list[str] = []
    for part in content.get("parts") or []:
        if not isinstance(part, dict):
            continue
        if url := _emitted_image(part):
            images.append(url)
        elif "functionCall" in part:
            call = part["functionCall"] or {}
            calls.append(ToolCall(
                index=len(calls),
                id=tool_call_id(len(calls), str(call.get("name") or "")),
                name=str(call.get("name") or ""),
                arguments=json.dumps(call.get("args") or {}, sort_keys=True),
            ))
        elif part.get("thought"):
            reasoning += str(part.get("text") or "")
        elif "text" in part:
            text += str(part.get("text") or "")
    return text, reasoning, calls, images


def _emitted_image(part: dict[str, Any]) -> str:
    """An image part as a `data:` URL, or "" for a part that is not one.

    Gemini answers an image request with an `inlineData` part beside the text
    rather than with a separate field, and it spells the key `inlineData` on the
    REST wire while accepting `inline_data` on the way in — both are read here
    so a response recorded through either spelling survives.
    """
    blob = part.get("inlineData") or part.get("inline_data")
    if not isinstance(blob, dict):
        return ""
    payload = str(blob.get("data") or "")
    mime = str(blob.get("mimeType") or blob.get("mime_type") or "image/png")
    if not payload or not mime.startswith("image/"):
        return ""
    return f"data:{mime};base64,{payload}"


def tool_call_id(index: int, name: str) -> str:
    """A stable id for a call Gemini gave none.

    Derived from position and name rather than random, so replaying the same
    stream produces the same ids — a conversation stored with one set and
    replayed with another stops matching its own tool results.
    """
    return f"call_{index}_{name}" if name else f"call_{index}"


def finish_reason(raw: Any, has_tool_call: bool) -> FinishReason:
    """Why generation ended, corrected for the case Gemini gets wrong.

    **A candidate carrying a `functionCall` finished `tool_calls`**, whatever
    the field says — and Gemini says `STOP`. Taking the field literally is
    exactly the defect that put this adapter on Path B, so reproducing it here
    would have made the whole exercise pointless.
    """
    if has_tool_call:
        return FinishReason.TOOL_CALLS
    if raw is None:
        return FinishReason.UNKNOWN
    return FINISH_REASONS.get(str(raw).upper(), FinishReason.UNKNOWN)


def read_usage(metadata: Any) -> Usage:
    """Token counts, where Gemini reported them.

    `candidatesTokenCount` excludes thinking tokens, which arrive separately as
    `thoughtsTokenCount`. Added together for `output_tokens`, because a client
    comparing output against a limit is asking what was generated, and billing
    counts both — while `reasoning_tokens` keeps the split visible.
    """
    if not isinstance(metadata, dict):
        return Usage()
    thoughts = metadata.get("thoughtsTokenCount")
    output = metadata.get("candidatesTokenCount")
    if output is not None and thoughts:
        output = int(output) + int(thoughts)
    return Usage(
        input_tokens=_count(metadata.get("promptTokenCount")),
        output_tokens=_count(output),
        cached_input_tokens=_count(metadata.get("cachedContentTokenCount")),
        reasoning_tokens=_count(thoughts),
    )


def _count(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def sse_payloads(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """The JSON objects on an SSE stream's `data:` lines.

    A `data:` line that is not JSON is skipped rather than raising, for the same
    reason the Anthropic reader skips one: ending a stream that is already
    delivering content because a single frame was malformed costs the client
    everything after it, and skipping costs one frame.
    """
    for line in lines:
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            frame = json.loads(payload)
        except ValueError:
            continue
        if isinstance(frame, dict):
            yield frame


class StreamReader:
    """Gemini's streamed candidates as normalized events, in order.

    Stateful for one reason: **tool-call indexes span frames.** Gemini numbers
    nothing, so the index a client keys its assembly on is this reader's own
    running count, and a second call in a later frame has to continue the
    sequence rather than restart it.

    Gemini sends each part whole rather than fragmenting the JSON, so a call's
    arguments arrive in a single event. That is a legal shape for the contract —
    a fragment may be any slice, including all of it — and it is why no
    reassembly buffer appears here.
    """

    def __init__(self) -> None:
        self._tools_seen = 0
        self._finished = False

    def events(self, frame: dict[str, Any]) -> Iterator[NormalizedStreamEvent]:
        if error := frame.get("error"):
            yield NormalizedStreamEvent(
                type=StreamEventType.ERROR,
                error=str(error.get("message") or error),
                raw=frame,
            )
            return
        candidates = frame.get("candidates") or []
        candidate = candidates[0] if candidates else {}
        for part in (candidate.get("content") or {}).get("parts") or []:
            event = self._part(part, frame)
            if event is not None:
                yield event
        yield from self._ending(candidate, frame)

    def _part(self, part: Any, frame: dict[str, Any]) -> NormalizedStreamEvent | None:
        """One part of a candidate, or None for one this path does not carry."""
        if not isinstance(part, dict):
            return None
        if url := _emitted_image(part):
            return NormalizedStreamEvent(
                type=StreamEventType.IMAGE, image_url=url, raw=frame
            )
        if "functionCall" in part:
            return self._call(part["functionCall"] or {})
        if part.get("thought"):
            return NormalizedStreamEvent(
                type=StreamEventType.REASONING, text=str(part.get("text") or ""), raw=frame
            )
        if "text" in part:
            return NormalizedStreamEvent(
                type=StreamEventType.TEXT, text=str(part.get("text") or ""), raw=frame
            )
        return None

    def _ending(
        self, candidate: dict[str, Any], frame: dict[str, Any]
    ) -> Iterator[NormalizedStreamEvent]:
        """The finish and its usage, once.

        **Whether a tool was called is a fact about the stream, not the frame.**
        This asked the finishing frame whether *it* carried a call, and Gemini
        sends the call and the finish separately — so the answer was always no,
        and the stream ended `stop` with a tool call in it. That is the exact
        defect that moved Gemini off the compatible endpoint, reproduced in the
        adapter written to avoid it, and caught only because the same live probe
        was run again afterwards.

        Usage arrives on most frames and is cumulative, so only the last is
        worth forwarding — emitted beside the finish, where a client reads it.
        """
        if candidate.get("finishReason") is None or self._finished:
            return
        self._finished = True
        yield NormalizedStreamEvent(
            type=StreamEventType.FINISH,
            finish_reason=finish_reason(candidate.get("finishReason"), self._tools_seen > 0),
            raw=frame,
        )
        usage = read_usage(frame.get("usageMetadata"))
        if usage.is_reported:
            yield NormalizedStreamEvent(type=StreamEventType.USAGE, usage=usage, raw=frame)

    def _call(self, call: dict[str, Any]) -> NormalizedStreamEvent:
        index = self._tools_seen
        self._tools_seen += 1
        name = str(call.get("name") or "")
        return NormalizedStreamEvent(
            type=StreamEventType.TOOL_CALL_FRAGMENT,
            tool_index=index,
            tool_id=tool_call_id(index, name),
            tool_name=name,
            arguments=json.dumps(call.get("args") or {}, sort_keys=True),
        )
