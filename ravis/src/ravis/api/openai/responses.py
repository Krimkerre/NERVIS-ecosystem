"""`POST /v1/responses` — OpenAI's newer surface, translated into the one RAVIS routes (M23).

**A translation, not a second gateway.** Everything that decides anything —
admission, policy, routing, §10's fallback chain, the ledger, the decision
record — happens once, in `chat.complete`. This module only changes shape on the
way in and on the way out. A second implementation of any of that would be a
second set of rules for the same questions, and two surfaces of one gateway
disagreeing about what an application may do is worse than one surface.

**Thin on purpose, and it says which parts are thin.** The owner chose this on
18 September 2026 over native support, for a surface nothing in this house asks
for yet: Clarvis and NERVIS both speak Chat Completions. So what is translated
is what a client actually needs — messages in and out, instructions, tools and
their calls, structured output, reasoning effort, token caps and usage — and
what is not translated is **refused by name** rather than accepted and ignored.

That last rule is the whole design. A shim that silently drops `store` answers a
request the caller did not make: they asked for the answer to be kept, RAVIS
kept nothing, and they find out when they try to read it back. Every refusal
below names the field and says what would have to exist for it to work.

Advertised `DEGRADED` for the same reason `ravis.embeddings@1` is: real and
working, scoped, and honest about the scope (§4.1).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ravis.api.openai.chat import complete

router = APIRouter(prefix="/v1")

# What this translation does not do, and what each would need. Named in the
# refusal so a caller learns the boundary from the error rather than from a
# specification they have not read.
NOT_TRANSLATED: dict[str, str] = {
    "stream": "streamed responses are not translated yet; RAVIS streams Chat "
              "Completions frames, and the Responses surface streams its own typed "
              "events, which is a separate piece of work — send stream=false, or use "
              "/v1/chat/completions, which streams",
    "previous_response_id": "RAVIS keeps no conversation on the server, so there is no "
                            "earlier response to continue from; send the messages you "
                            "want considered in `input`",
    "store": "RAVIS stores no answers — §9.7 and the runbook keep message content out "
             "of everything it records — so an answer it claimed to have stored could "
             "never be read back",
}

# Tool types that are the provider's own machinery rather than the caller's
# function. RAVIS forwards a request; it does not run a web search or hold a
# file store, and a provider that does would need its own integration.
BUILT_IN_TOOLS = frozenset(
    {"web_search", "web_search_preview", "file_search", "computer_use_preview",
     "code_interpreter", "image_generation", "local_shell", "mcp"}
)


@router.post("/responses")
async def create_response(request: Request) -> Response:
    """Answer one Responses request by routing it as a Chat Completions one."""
    try:
        payload = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError:
        return _error("The request body is not valid JSON.", "invalid_request_error")
    if not isinstance(payload, dict):
        return _error("The request body must be a JSON object.", "invalid_request_error")
    refusal = _refuse_untranslated(payload)
    if refusal is not None:
        return refusal
    answered = await complete(request, json.dumps(as_chat(payload)).encode())
    return _as_response(answered, payload)


def _refuse_untranslated(payload: dict[str, Any]) -> JSONResponse | None:
    """Refuse, by name, anything this translation would otherwise swallow."""
    for field, reason in NOT_TRANSLATED.items():
        # `stream: false` is the ordinary case and not a request for anything.
        if payload.get(field):
            return _error(reason, "unsupported_parameter", param=field)
    for tool in payload.get("tools") or []:
        kind = tool.get("type", "") if isinstance(tool, dict) else ""
        if kind in BUILT_IN_TOOLS:
            return _error(
                f"The {kind!r} tool is run by the provider, and RAVIS forwards requests "
                "rather than running provider machinery. Functions your own client "
                "executes are translated.",
                "unsupported_parameter",
                param="tools",
            )
    return None


# ── Into a Chat Completions request ──────────────────────────────────────────


def as_chat(payload: dict[str, Any]) -> dict[str, Any]:
    """The same request, in the shape RAVIS routes.

    `metadata` travels untouched, and deliberately: §9.6.1's background marker
    and §14's declared privacy arrive there, so a Responses caller gets the same
    policy it would get from Chat Completions rather than a quietly more
    permissive one.
    """
    body: dict[str, Any] = {
        "model": payload.get("model", ""),
        "messages": _messages(payload),
    }
    for source, target in (
        ("max_output_tokens", "max_tokens"),
        ("temperature", "temperature"),
        ("top_p", "top_p"),
        ("metadata", "metadata"),
        ("parallel_tool_calls", "parallel_tool_calls"),
        ("user", "user"),
    ):
        if payload.get(source) is not None:
            body[target] = payload[source]
    if payload.get("tools"):
        body["tools"] = [_tool(tool) for tool in payload["tools"]]
    if payload.get("tool_choice") is not None:
        body["tool_choice"] = _tool_choice(payload["tool_choice"])
    effort = (payload.get("reasoning") or {}).get("effort")
    if effort:
        body["reasoning_effort"] = effort
    shape = _response_format(payload.get("text") or {})
    if shape is not None:
        body["response_format"] = shape
    return body


def _messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """`instructions` and `input`, as a conversation.

    `instructions` becomes the system message, which is what it is: a standing
    instruction for the turn. It goes first, because a system message after the
    conversation is a different request.
    """
    messages: list[dict[str, Any]] = []
    if payload.get("instructions"):
        messages.append({"role": "system", "content": payload["instructions"]})
    given = payload.get("input")
    if isinstance(given, str):
        messages.append({"role": "user", "content": given})
        return messages
    for item in given or []:
        translated = _input_item(item)
        if translated is not None:
            messages.append(translated)
    return messages


def _input_item(item: Any) -> dict[str, Any] | None:
    """One entry of `input`: a message, or a function call's result.

    A bare string in the list is a user message, which is what the SDK produces
    for the shorthand form. Anything whose type this translation does not know
    is left out rather than guessed at — a reasoning trace replayed as a user
    message would change the conversation.
    """
    if isinstance(item, str):
        return {"role": "user", "content": item}
    if not isinstance(item, dict):
        return None
    kind = item.get("type", "message")
    if kind == "function_call_output":
        return {
            "role": "tool",
            "tool_call_id": item.get("call_id", ""),
            "content": item.get("output", ""),
        }
    if kind == "function_call":
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": item.get("call_id", ""),
                "type": "function",
                "function": {"name": item.get("name", ""),
                             "arguments": item.get("arguments", "")},
            }],
        }
    if kind != "message":
        return None
    content = item.get("content")
    return {"role": item.get("role", "user"),
            "content": content if isinstance(content, str) else _parts(content or [])}


def _parts(content: list[Any]) -> list[dict[str, Any]]:
    """Content parts, in Chat Completions' vocabulary for the same things."""
    parts: list[dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            parts.append({"type": "text", "text": part})
            continue
        if not isinstance(part, dict):
            continue
        kind = part.get("type", "")
        if kind in ("input_text", "output_text", "text"):
            parts.append({"type": "text", "text": part.get("text", "")})
        elif kind in ("input_image", "image_url"):
            url = part.get("image_url") or part.get("file_url") or ""
            parts.append({
                "type": "image_url",
                "image_url": url if isinstance(url, dict) else {"url": url},
            })
    return parts


def _tool(tool: Any) -> dict[str, Any]:
    """A Responses function tool as a Chat Completions one.

    The two differ only in nesting — Responses puts `name` and `parameters` on
    the tool, Chat Completions puts them under `function` — so anything already
    in the nested form is passed through untouched, which is what an SDK that
    supports both sends.
    """
    if not isinstance(tool, dict) or "function" in tool:
        return tool if isinstance(tool, dict) else {}
    function = {key: tool[key] for key in ("name", "description", "parameters", "strict")
                if key in tool}
    return {"type": "function", "function": function}


def _tool_choice(choice: Any) -> Any:
    """`auto`, `none`, `required`, or one named function."""
    if isinstance(choice, dict) and choice.get("type") == "function" and "name" in choice:
        return {"type": "function", "function": {"name": choice["name"]}}
    return choice


def _response_format(text: dict[str, Any]) -> dict[str, Any] | None:
    """`text.format` as `response_format`, for the shapes that mean the same thing."""
    shape = text.get("format")
    if not isinstance(shape, dict):
        return None
    kind = shape.get("type")
    if kind == "json_schema":
        schema = {key: shape[key] for key in ("name", "schema", "strict", "description")
                  if key in shape}
        return {"type": "json_schema", "json_schema": schema}
    if kind in ("json_object", "text"):
        return {"type": kind}
    return None


# ── Back out again ───────────────────────────────────────────────────────────


def _as_response(answered: Response, asked: dict[str, Any]) -> Response:
    """The completion, in the Responses shape — or the error, untouched.

    An error travels as it is. RAVIS's errors are already `{"error": {...}}` with
    a type and a message, which is the shape both surfaces use, and rewriting
    one would mean inventing a Responses-flavoured version of a sentence the
    router wrote for a reason.
    """
    if answered.status_code != 200:
        return answered
    try:
        completion = json.loads(bytes(answered.body))
    except (json.JSONDecodeError, ValueError):  # pragma: no cover - upstream nonsense
        return answered
    choices = completion.get("choices") or [{}]
    message = choices[0].get("message") or {}
    finished = choices[0].get("finish_reason", "")
    body: dict[str, Any] = {
        "id": f"resp_{uuid.uuid4().hex[:24]}",
        "object": "response",
        "created_at": completion.get("created", int(time.time())),
        "model": completion.get("model", asked.get("model", "")),
        "status": "incomplete" if finished == "length" else "completed",
        "output": _output(message),
        # The SDK's convenience field: the assistant's text, already joined.
        # Clients read it far more often than they walk `output`.
        "output_text": message.get("content") or "",
        "usage": _usage(completion.get("usage") or {}),
        "metadata": asked.get("metadata") or {},
        "parallel_tool_calls": asked.get("parallel_tool_calls", True),
        "instructions": asked.get("instructions"),
        "tools": asked.get("tools") or [],
        "temperature": asked.get("temperature"),
        "top_p": asked.get("top_p"),
        "max_output_tokens": asked.get("max_output_tokens"),
        "error": None,
    }
    if finished == "length":
        body["incomplete_details"] = {"reason": "max_output_tokens"}
    return JSONResponse(body, status_code=200, headers=_carried(answered))


def _output(message: dict[str, Any]) -> list[dict[str, Any]]:
    """The assistant's turn as output items: its text, then any calls it made."""
    items: list[dict[str, Any]] = []
    if message.get("content"):
        items.append({
            "type": "message",
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": message["content"],
                         "annotations": []}],
        })
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        items.append({
            "type": "function_call",
            "id": f"fc_{uuid.uuid4().hex[:24]}",
            # The id the caller must quote when it sends the result back, which
            # `_input_item` reads out of `function_call_output`.
            "call_id": call.get("id", ""),
            "name": function.get("name", ""),
            "arguments": function.get("arguments", ""),
            "status": "completed",
        })
    return items


def _usage(usage: dict[str, Any]) -> dict[str, Any]:
    """Token counts under the names this surface gives them.

    Absent counts stay absent as zero rather than being invented: an upstream
    that reported nothing reported nothing, and §14's distinction between a
    figure and a guess is the same distinction here.
    """
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    counted: dict[str, Any] = {
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }
    if cached is not None:
        counted["input_tokens_details"] = {"cached_tokens": cached}
    if reasoning is not None:
        counted["output_tokens_details"] = {"reasoning_tokens": reasoning}
    return counted


def _carried(answered: Response) -> dict[str, str]:
    """RAVIS's own headers, kept across the reshaping.

    The trace and request ids in particular: a request that crossed this surface
    must still be findable in the same timeline as one that did not.
    """
    return {
        name: value
        for name, value in answered.headers.items()
        if name.lower().startswith(("x-ravis", "x-request-id", "traceparent"))
    }


def _error(message: str, kind: str, param: str | None = None) -> JSONResponse:
    """An error in the shape both surfaces share."""
    body: dict[str, Any] = {"error": {"message": message, "type": kind, "code": None}}
    if param is not None:
        body["error"]["param"] = param
    return JSONResponse(body, status_code=400)
