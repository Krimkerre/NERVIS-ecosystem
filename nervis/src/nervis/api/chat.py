"""`/api/v1/chat` — a normal RAVIS client, with the reply kept (§7).

Three things happen at once on a streamed turn, and the order matters:

1. **Bytes reach the browser unchanged.** RAVIS speaks OpenAI's SSE and so does
   this. Reshaping the frames would make NERVIS a second protocol nobody
   documented, and would break the moment RAVIS added a field.
2. **The text is accumulated** so the turn can be stored. §7.2 wants messages
   kept; a proxy that only forwarded would lose every reply the moment the page
   reloaded, which is the state the dashboard was already in with
   `localStorage`.
3. **A disconnect cancels the upstream.** A browser tab closing must stop RAVIS
   generating, or a cancelled reply keeps burning a local GPU with nobody
   reading it.

**The partial reply is stored, marked interrupted.** Discarding it would delete
text the user watched arrive, and storing it unmarked would let the next turn
present a half-sentence as a finished thought.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator

import httpx
from ecosystem_protocol import new_traceparent
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from nervis import chat as store
from nervis.errors import InvalidConfigurationError, NotFoundError
from nervis.negotiation import Operation, may_attempt, negotiate
from nervis.registry import RegistryEntry

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

# The capability a completion needs. §5.2's gate applies here exactly as it does
# to a read: a RAVIS that has withdrawn chat should produce a stated refusal
# rather than a request nobody negotiated.
CHAT_CAPABILITY = "ravis.openai_compatible.chat_completions"

# Long, because a cold model is a real wait. RAVIS's own upstream timeout is
# 300 s and a client timing out first would abandon a request RAVIS is still
# faithfully serving — leaving a model loading for a reply nobody will read.
CHAT_TIMEOUT_SECONDS = 300.0


@router.get("/conversations")
async def list_conversations(request: Request) -> dict[str, Any]:
    return {"items": store.conversations(request.app.state.database)}


@router.get("/conversations/{conversation_id}")
async def read_conversation(conversation_id: str, request: Request) -> dict[str, Any]:
    database = request.app.state.database
    if not store.exists(database, conversation_id):
        raise NotFoundError(f"no conversation {conversation_id!r}")
    return {
        "conversation_id": conversation_id,
        "items": [message.as_dict() for message in store.messages(database, conversation_id)],
    }


@router.put("/conversations/{conversation_id}/title")
async def set_title(conversation_id: str, request: Request) -> dict[str, Any]:
    """Rename a conversation.

    A person renames it, or the first message truncates into it. **Nothing
    generates one**: §7 wants titles produced as a RAVIS background call
    carrying §9.6.1's marker, RAVIS does not honour that marker yet, and a title
    routed as ordinary work through `ravis/auto` can select a paid model for a
    string nobody reads.
    """
    database = request.app.state.database
    if not store.exists(database, conversation_id):
        raise NotFoundError(f"no conversation {conversation_id!r}")
    body = await _json_body(request)
    title = str(body.get("title") or "").strip()
    if not title:
        raise InvalidConfigurationError("title must be a non-empty string")
    store.rename(database, conversation_id, title)
    return {"conversation_id": conversation_id, "title": title}


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, request: Request) -> dict[str, Any]:
    """§7.2: allow deletion. Messages go with it, by cascade."""
    if not store.delete(request.app.state.database, conversation_id):
        raise NotFoundError(f"no conversation {conversation_id!r}")
    return {"conversation_id": conversation_id, "deleted": True}


@router.post("")
async def send(request: Request) -> Any:
    """One turn: store the question, ask RAVIS, stream the answer, store it.

    `stream` defaults to true because §7's MVP names streaming, and because a
    non-streamed local completion is a blank screen for however long the model
    takes.
    """
    database = request.app.state.database
    body = await _json_body(request)
    content = str(body.get("content") or "").strip()
    if not content:
        raise InvalidConfigurationError("content must be a non-empty string")
    profile = str(body.get("profile") or "ravis/auto")

    entry: RegistryEntry | None = request.app.state.registry.get("ravis")
    verdict = negotiate(Operation("chat", "ravis", CHAT_CAPABILITY, "Chat"), entry)
    if not may_attempt(verdict, entry) or entry is None:
        # Same shape as M3's reads, and the same rule: refused when the
        # *capability* is missing, attempted when only the registry's liveness
        # reading says otherwise. §5.2's "never calls a guessed endpoint" does
        # not stop applying because this one writes — and neither does the
        # reason it must not become a veto on a twenty-second-old observation.
        raise InvalidConfigurationError(
            verdict.reason or "RAVIS cannot take a completion right now",
            availability=verdict.availability.value,
        )

    conversation_id = str(body.get("conversation_id") or "")
    if conversation_id and not store.exists(database, conversation_id):
        raise NotFoundError(f"no conversation {conversation_id!r}")
    if not conversation_id:
        conversation_id = store.start_conversation(database, profile=profile)

    # Prior turns are read *before* the new question is stored, so the question
    # is not sent twice.
    prior = store.history(database, conversation_id)
    store.append(
        database,
        conversation_id,
        store.Message(message_id=store.new_id(), role="user", content=content, profile=profile),
    )

    request_id = getattr(request.state, "request_id", "") or uuid.uuid4().hex
    payload: dict[str, Any] = {
        "model": profile,
        "messages": [*prior, {"role": "user", "content": content}],
        "stream": True,
    }
    for name in ("temperature", "max_tokens", "top_p"):
        if name in body:
            payload[name] = body[name]
    if body.get("system"):
        payload["messages"].insert(0, {"role": "system", "content": str(body["system"])})

    trace_id = getattr(request.state, "trace_id", "")
    _note_turn(request, conversation_id, profile, request_id, trace_id)
    return StreamingResponse(
        _relay(
            request, entry, payload, conversation_id, profile, request_id, trace_id,
        ),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-store",
            "x-conversation-id": conversation_id,
            "x-request-id": request_id,
        },
    )


async def _relay(
    request: Request,
    entry: RegistryEntry,
    payload: dict[str, Any],
    conversation_id: str,
    profile: str,
    request_id: str,
    trace_id: str = "",
) -> AsyncIterator[bytes]:
    """Forward RAVIS's frames unchanged while keeping what they said."""
    database = request.app.state.database
    client: httpx.AsyncClient = request.app.state.probe_client
    collected: list[str] = []
    model = ""
    interrupted = True
    # Whether RAVIS answered at all. Distinguishes "the model produced no text"
    # from "the request never got off the ground", which must not be stored as
    # an empty reply.
    started = False

    # The conversation id reaches the browser as an SSE comment as well as a
    # header. A `fetch` reading a stream can see headers, but an `EventSource`
    # cannot — and a client that cannot learn which conversation it just started
    # has to guess, which is how a reply lands in the wrong one.
    yield f": conversation {conversation_id}\n\n".encode()

    try:
        async with client.stream(
            "POST",
            entry.declaration.base_url + "/v1/chat/completions",
            json=payload,
            headers=_forwarded(request_id, trace_id),
            timeout=CHAT_TIMEOUT_SECONDS,
        ) as response:
            if response.status_code >= 400:
                yield _error_frame(await _refusal(response))
                return
            started = True
            async for line in response.aiter_lines():
                text, done = _delta(line)
                if text:
                    collected.append(text)
                model = model or _model_of(line)
                yield f"{line}\n\n".encode() if line else b"\n"
                if done:
                    interrupted = False
    except httpx.HTTPError as failure:
        yield _error_frame(f"RAVIS stopped answering: {type(failure).__name__}")
    finally:
        # Runs on a client disconnect too, which is what makes a cancelled reply
        # survive as the partial text the user actually saw.
        #
        # **Stored even when empty**, which was a bug the first time. A
        # reasoning model can spend an entire `max_tokens` budget on
        # `reasoning_content` and emit no `content` at all — observed on the
        # second turn of the first real conversation — and skipping the append
        # left the question sitting there with no answer beside it and no
        # indication that anything had happened. An empty assistant turn that
        # finished says "it answered with nothing", which is true and is what a
        # screen needs in order to explain it.
        if started:
            store.append(
                database,
                conversation_id,
                store.Message(
                    message_id=store.new_id(),
                    role="assistant",
                    content="".join(collected),
                    model=model,
                    profile=profile,
                    request_id=request_id,
                    interrupted=interrupted,
                ),
            )


def _delta(line: str) -> tuple[str, bool]:
    """The text in one SSE line, and whether the stream just ended."""
    if not line.startswith("data:"):
        return "", False
    body = line[5:].strip()
    if body == "[DONE]":
        return "", True
    try:
        frame = json.loads(body)
    except ValueError:
        return "", False
    choices = frame.get("choices") or []
    if not choices:
        return "", False
    return str((choices[0].get("delta") or {}).get("content") or ""), False


def _model_of(line: str) -> str:
    """Which build RAVIS actually selected, from the first frame that says."""
    if not line.startswith("data:"):
        return ""
    try:
        return str(json.loads(line[5:].strip()).get("model") or "")
    except ValueError:
        return ""


def _error_frame(message: str) -> bytes:
    """A refusal, in the stream, because the response already began.

    Once a `200` and a content type have gone out there is no status code left
    to change. An SSE frame the client can render beats a truncated stream it
    has to guess about.
    """
    return f"event: error\ndata: {json.dumps({'message': message})}\n\n".encode()


async def _refusal(response: httpx.Response) -> str:
    """RAVIS's own words, which §4.3's envelope exists to make readable."""
    try:
        body = json.loads(await response.aread())
    except ValueError:
        return f"RAVIS answered HTTP {response.status_code}"
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return f"RAVIS answered HTTP {response.status_code}"


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError as failure:
        raise InvalidConfigurationError(f"body is not valid JSON: {failure}") from failure
    if not isinstance(body, dict):
        raise InvalidConfigurationError("body must be a JSON object")
    return body


def _forwarded(request_id: str, trace_id: str) -> dict[str, str]:
    """The context headers one turn carries to RAVIS (§4.3).

    `traceparent` is a **new span in the same trace**, not the incoming header
    forwarded: forwarding would make RAVIS's parent NERVIS's parent, and §11.2's
    waterfall would draw two siblings where there is a call.
    """
    headers = {"content-type": "application/json", "x-request-id": request_id}
    if trace_id:
        headers["traceparent"] = new_traceparent(trace_id)
    return headers


def _note_turn(
    request: Request, conversation_id: str, profile: str, request_id: str, trace_id: str
) -> None:
    """NERVIS's own span in this trace.

    Without it a chat turn draws one lane — RAVIS's — and §11.2's waterfall
    exists to show the *call*, not the callee alone.

    **Only when there is a trace.** An untraced turn does not manufacture one: a
    trace containing a single service is a fact about nothing, and filling the
    index with them would make the real ones harder to find.

    Extracted because adding it inline pushed `send` past the complexity gate —
    which is the gate working. It was measured sitting exactly on 8 two days
    ago, with the note that M11 or M16 would be what tipped it. This got there
    first.
    """
    hub = getattr(request.app.state, "hub", None)
    if hub is None or not trace_id:
        return
    hub.emit(
        "nervis.chat.turn_started",
        subject={"type": "conversation", "id": conversation_id},
        data={"profile": profile, "conversation_id": conversation_id},
        trace_id=trace_id,
        request_id=request_id,
    )
