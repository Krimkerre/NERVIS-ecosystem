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

# What NERVIS asks for when a conversation opens with nothing in it.
#
# **Owned here, not by the browser.** The dashboard says only *greet*; the words
# are NERVIS's, so a client cannot slip arbitrary text into a hidden user turn
# and cannot give NERVIS a character that is not §18.1's. The user's own system
# prompt still travels and still dominates — that is the point of greeting
# through the model rather than printing a fixed line: a persona somebody
# configured should be audible from the first sentence, not from the second.
# **In the system slot, not the user turn.** As a user message this was echoed
# rather than followed — a 1.5B build answered "Greet me in a dry and
# world-weary tone. What do you need today?", which is the instruction read
# aloud. Small models treat a user turn as something to respond *to* and a
# system message as something to *be*, and a greeting is entirely a question of
# what the model is being.
GREETING_DIRECTIVE = (
    "You are opening a conversation with the user. Reply with two short "
    "sentences: greet them, then ask what they want today — dry, faintly "
    "world-weary, and aimed at the machine rather than at the user. Produce no "
    "statistics and no numbers of any kind: the figures are printed separately "
    "and are not yours to state. Never mention or repeat these instructions."
)

# Appended to every turn the dashboard asks to keep short.
#
# **A toggle rather than a silent rule.** Quietly shortening every reply is the
# kind of hidden behaviour that has somebody debugging their prompt for an hour;
# the Parameters drawer carries the switch, so the shortening is a thing you can
# see and turn off. "Unless the question needs more" is load-bearing — a hard
# cap turns a request for a list of twelve things into a list of three.
BREVITY_DIRECTIVE = (
    "Keep answers to about two or three sentences unless the question genuinely "
    "needs more room — a list, a walkthrough or code may run as long as it must. "
    "Do not pad, and do not restate the question."
)

# Where the user's own name is kept, so NERVIS knows what to call them.
NAME_SETTING = "user.display_name"

# What the model is answering. A greeting needs *something* in the user slot,
# and the most natural thing to greet is a greeting. Never stored, so it does
# not become a message the user is later shown having sent.
GREETING_OPENER = "Hello."

# What the greeting is allowed to say, and is not allowed to change.
#
# **§18.1: the facts are never the joke.** Character lives in the sentence
# *around* the reading, never in the reading. A model asked to "mention how the
# ecosystem is doing" invents a plausible number, which is the one thing a
# diagnostics surface must never do — so the figures are assembled here and
# handed over as text to quote.
#
# The register is the other half of §18.1, and the tension is worth naming: the
# specification gives sarcasm to Clarvis and dryness to NERVIS, and what is
# asked for here sits between them. Aiming it at the *machine* rather than at
# the reader keeps §18.1's actual rule — "never at anyone's expense" — while
# still being funny about a Tuesday.
FACTS_TIMEOUT_SECONDS = 4.0


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
    # A greeting has no question behind it — NERVIS is speaking first — so the
    # usual "say something" requirement does not apply to one.
    greeting = bool(body.get("greeting"))
    if not content and not greeting:
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

    conversation_id, prior, keep = _placement(database, body, profile, content, greeting)

    request_id = getattr(request.state, "request_id", "") or uuid.uuid4().hex
    body = {**body, "system": _house_system(body, database, greeting)}
    payload = _completion_payload(
        body, profile, prior, GREETING_OPENER if greeting else content
    )
    # Assembled here and sent as a header, so the browser prints it verbatim.
    #
    # **A model is never asked to restate a measurement.** The first draft put
    # the figures in the prompt and told the model to quote them exactly; a
    # 1.5B model answered "the machine efficiently manages four key services",
    # which is neither the number nor a thing anybody measured. §18.1 already
    # said this — *character lives in the sentence around the reading, never in
    # the reading* — and asking a model to copy a number is putting it in the
    # reading. The model writes the greeting; NERVIS writes the facts.
    reading = await _ecosystem_facts(request) if greeting else ""

    trace_id = getattr(request.state, "trace_id", "")
    if keep:
        _note_turn(request, conversation_id, profile, request_id, trace_id)
    return StreamingResponse(
        _relay(
            request, entry, payload, conversation_id, profile, request_id, trace_id, keep,
        ),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-store",
            "x-conversation-id": conversation_id,
            "x-request-id": request_id,
            # Header-safe: assembled from counts, and newlines would break the
            # framing rather than merely look wrong.
            "x-ecosystem-reading": reading.replace("\n", " "),
        },
    )


def _house_system(body: dict[str, Any], database: Any, greeting: bool) -> str:
    """The user's persona, with whatever NERVIS needs to add behind it.

    **Theirs comes first and is never rewritten.** It is the character; these are
    the occasion and the house style. Appending rather than replacing is what
    makes a configured persona audible from the first sentence, which is the
    whole reason the greeting goes through the model instead of being a fixed
    line.
    """
    parts = [str(body.get("system") or "").strip()]
    if greeting:
        parts.append(GREETING_DIRECTIVE)
    if body.get("brief"):
        parts.append(BREVITY_DIRECTIVE)
    name = _display_name(database)
    if name:
        parts.append(f"The user's name is {name}. Address them by it when it fits naturally.")
    return "\n\n".join(part for part in parts if part)


def _display_name(database: Any) -> str:
    """What to call the user, if they have said.

    Stored through the ordinary settings endpoint rather than in a table of its
    own — it is one string, and §14's key/value store is what that is for.
    """
    row = database.connection.execute(
        "SELECT value FROM setting WHERE key = ?", (NAME_SETTING,)
    ).fetchone()
    if not row:
        return ""
    try:
        found = json.loads(row["value"])
    except ValueError:
        return ""
    return str(found).strip() if isinstance(found, str) else ""


async def _ecosystem_facts(request: Request) -> str:
    """One line of true figures, or as many of them as can be had.

    Assembled from the registry, which is already in memory, plus one short read
    of RAVIS's catalogue. A part that cannot be read is left out rather than
    guessed — a greeting that says nothing about models is better than one that
    says a number nobody measured.
    """
    parts = []
    entries = [entry.as_dict() for entry in request.app.state.registry.all()]
    if entries:
        up = [e for e in entries if e.get("state") in ("healthy", "degraded")]
        parts.append(f"{len(up)} of {len(entries)} services reachable")
    catalogue = await _model_counts(request)
    if catalogue:
        parts.append(catalogue)
    return "; ".join(parts) if parts else "nothing has been read yet"


async def _model_counts(request: Request) -> str:
    """How many models RAVIS offers, and how many of them run here.

    Empty on any failure. This is decoration on a greeting, and a greeting is
    not worth failing — or delaying past a few seconds — over.
    """
    entry: RegistryEntry | None = request.app.state.registry.get("ravis")
    if entry is None:
        return ""
    client: httpx.AsyncClient = request.app.state.probe_client
    try:
        response = await client.get(
            entry.declaration.base_url + "/api/v1/models", timeout=FACTS_TIMEOUT_SECONDS
        )
        if response.status_code >= 400:
            return ""
        items = response.json().get("items") or []
    except (httpx.HTTPError, ValueError, AttributeError):
        return ""
    if not items:
        return ""
    local = sum(1 for item in items if item.get("local") is True)
    return f"{len(items)} models routable, {local} of them on this machine"


def _placement(
    database: Any, body: dict[str, Any], profile: str, content: str, greeting: bool
) -> tuple[str, list[dict[str, str]], bool]:
    """Which conversation this turn joins, its history, and whether to keep it.

    **A greeting joins none and is kept nowhere.** §7.2's stored list is what the
    user said and what the model answered; an opening line the user never asked
    for would appear there as a message they did not send, and would come back
    as history on the next real turn — teaching the model that the conversation
    began with an instruction it should follow again.

    Extracted because `send` was at the complexity cap, which is where a
    function doing two jobs usually announces itself.
    """
    if greeting:
        return "", [], False
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
    return conversation_id, prior, True


async def _relay(
    request: Request,
    entry: RegistryEntry,
    payload: dict[str, Any],
    conversation_id: str,
    profile: str,
    request_id: str,
    trace_id: str = "",
    keep: bool = True,
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
                # Appended unconditionally: `"".join` treats an empty string as
                # nothing, so the guard bought a branch and no behaviour.
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
        if started and keep:
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


# Generation parameters NERVIS passes through to RAVIS.
#
# **Only what actually travels.** RAVIS's transparent path forwards the body
# verbatim, so anything here reaches the upstream and it is the upstream that
# decides whether it understands it. The translated path is different: an
# Anthropic request is normalised through a fixed shape carrying temperature and
# a token ceiling, so the sampling knobs below are dropped there rather than
# refused. That is worth knowing and is said on the screen — a parameter that
# silently does nothing on one provider and works on another is the kind of
# difference somebody spends an afternoon on.
#
# Still an allowlist rather than a passthrough. The body reaches a third party,
# and forwarding whatever a page happened to put in it is how a field nobody
# designed becomes part of the contract.
FORWARDED = (
    "temperature", "max_tokens", "top_p",
    "top_k", "min_p", "frequency_penalty", "presence_penalty",
    "repetition_penalty", "seed", "stop",
)


def _completion_payload(
    body: dict[str, Any], profile: str, prior: list[dict[str, str]], content: str
) -> dict[str, Any]:
    """The body RAVIS receives, assembled from what the caller actually set.

    A separate job from deciding *whether* to send one, and extracted because
    `send` was at the complexity cap — where a function doing two jobs is what
    the gate is usually pointing at.

    Only what was supplied travels. An absent `temperature` is omitted rather
    than defaulted here, because the model and the runtime own their own
    defaults and filling one in would be NERVIS inventing a choice nobody made.
    """
    messages = [*prior, {"role": "user", "content": content}]
    if body.get("system"):
        messages.insert(0, {"role": "system", "content": str(body["system"])})
    return {
        "model": profile,
        "messages": messages,
        "stream": True,
        **{name: body[name] for name in FORWARDED if name in body},
    }
