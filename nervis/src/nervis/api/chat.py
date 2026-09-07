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

import asyncio
import contextlib
import json
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from nervis import chat as store
from nervis import commands, documents, knowledge, proposals, situation, transcript
from nervis import recall as memory
from nervis.api.chat_calls import (
    _forwarded,
    _json_body,
    _named,
    _refusal,
)
from nervis.api.chat_documents import _document
from nervis.api.chat_personas import (
    AUDIENCE_DIRECTIVE,
    BREVITY_DIRECTIVE,
    GREETING_DIRECTIVE,
    GREETING_OPENER,
    NAME_SETTING,
    NUDGE_OPENER,
    _memory_scope,
    _nudge_directive,
    _recall,
)
from nervis.api.chat_reads import (
    CATALOGUE_RETRY_SECONDS,
    CATALOGUE_TTL_SECONDS,
    DEFAULT_CHAT_POOL,
    EVENT_SAMPLE,
    FACTS_TIMEOUT_SECONDS,
    RATE_LIMIT_PAUSE_SECONDS,
    _decisions,
    _editors,
    _evidence,
    _jobs,
    _machine,
    _observations,
    _policies,
    _pools,
    _providers,
    _quarantine,
    _residency,
    _runs,
    _runtime,
    _runtime_sets,
    _spend,
    _spend_records,
    _traces,
    _wants_results,
)
from nervis.api.chat_titles import (
    _attachment_name,
    _attachment_title,
    _reply_title,
    _stored_title,
    _title_later,
    opening_title,
)
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


def _workspace_root(request: Request) -> str:
    return str(getattr(request.app.state.settings, "workspace_path", "") or "").strip()


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
    # How many silences deep this one is, so the flavour can escalate. Zero, and
    # anything unparseable, means this is not a nudge.
    try:
        nudge = int(body.get("nudge") or 0)
    except (TypeError, ValueError):
        nudge = 0
    if not content and not greeting and nudge <= 0:
        raise InvalidConfigurationError("content must be a non-empty string")
    # **`ravis/chat`, not `ravis/auto`.** Auto declares no constraint by design
    # — "let RAVIS decide, with no constraint beyond what the request needs" —
    # and a pool that declares nothing has nothing to order candidates by, so
    # the engine falls back to alphabetical. That is the documented behaviour
    # and it is fine for a pool nobody names on purpose; it is the wrong
    # default for the one surface where a person is talking to the thing. The
    # chat pool exists, says what it is for, and is what a conversation should
    # get when the caller expressed no preference.
    profile = str(body.get("profile") or DEFAULT_CHAT_POOL)

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

    conversation_id, prior, keep = _placement(
        database, body, profile, content, greeting, nudge > 0
    )
    # **Reconciled here, before anything downstream reads either id.** The
    # browser mints its own id and files an attachment under it from the very
    # first turn — before a real `conversation_id` exists at all — and is
    # never told to switch once one does, so `attachment_id` and
    # `conversation_id` are two permanently different keys for the same
    # conversation. Done this early rather than inside `_document` (which
    # only reads by `attachment_id` and would never need the other one
    # itself): the save/export filename offer below also depends on finding
    # the attachment under `conversation_id`, and runs before `_document`
    # does on exactly the turn that just attached it — "save it" in the same
    # breath as "here's the file" is the ordinary order, not an edge case.
    root_for_attachments = str(getattr(request.app.state.settings, "workspace_path", "") or "")
    if root_for_attachments:
        documents.reconcile_attachments(
            Path(root_for_attachments), str(body.get("attachment_id") or ""), conversation_id,
        )

    request_id = getattr(request.state, "request_id", "") or uuid.uuid4().hex
    asked = content
    reading, awareness = await _situation(request, greeting, content)
    # **Made from what the person typed, not from what the model says.** §11.5
    # forbids model output becoming an action, and the way that rule survives a
    # refactor is for the proposal to be built before the model has seen
    # anything at all. `offer` is a value; nothing here can carry it out.
    #
    # Both reads are the cached ones `_situation` just took. They were two more
    # round trips before, and RAVIS rate-limits — the second copy of the
    # catalogue read is what came back 429 and emptied the model counts out of
    # the reading in the same turn that had just read them successfully.
    # **The plan is tried first, and it composes `propose` rather than
    # replacing it** (M24). A sentence with `then` in it is an ordering; one
    # without is a single request. Where a sentence looks like a plan but a
    # clause names no operation, `plan` returns nothing and this falls through
    # to the single-offer path, which reads the first clause — running half of
    # what somebody asked for is the failure the whole design avoids.
    offer = (
        commands.propose(
            content,
            await _catalogue(request),
            await _jobs(request, content),
            await _pools(request, content),
            # So a save or export can name a file without the person supplying
            # one: the attachment they fed chat, the reply's own opening line,
            # the conversation's title, or the question itself — NERVIS's own
            # records throughout, never a model asked mid-request to pick one.
            default_name=transcript.suggested_name(
                _attachment_title(_workspace_root(request), conversation_id)
                or _reply_title(database, conversation_id)
                or _stored_title(database, conversation_id) or content,
                datetime.now().astimezone(),
            ),
            # Whether there is a document to annotate at all. The offer to
            # place comments in a copy of it needs the file to exist first,
            # and `propose` has no filesystem — so its name travels in.
            attachment=_attachment_name(_workspace_root(request), conversation_id),
            # What NERVIS knows about the editor, for the handoff offer. Read
            # here rather than inside `propose`, which stays a pure function of
            # the person's words.
            clarvis=_editor_destination(request),
        )
        if not greeting
        else None
    )
    # **The identity and the past, attached here rather than inside `propose`**
    # (M22). `propose` stays a pure function of the person's words: it has no
    # database and no memory, so clearing the record restores exactly the offer
    # it would have made before any of this existed. History decorates; it never
    # composes.
    offer = _remembered(database, offer)
    sequence = (
        _planned(
            database,
            commands.plan(
                content,
                await _catalogue(request),
                await _jobs(request, content),
                await _pools(request, content),
                default_name=transcript.suggested_name(
                    _attachment_title(_workspace_root(request), conversation_id)
                    or _reply_title(database, conversation_id)
                    or _stored_title(database, conversation_id) or content,
                    datetime.now().astimezone(),
                ),
                attachment=_attachment_name(_workspace_root(request), conversation_id),
            ),
        )
        if not greeting
        else None
    )
    # A file the person named, read before the model sees anything (§11.5).
    #
    # **A source, not a tool.** The model never chooses what is opened: the
    # person names a file, NERVIS reads it inside the configured workspace, and
    # the content arrives fenced like every other retrieved thing. A document
    # that says "now open ~/.ssh/id_rsa" is a document saying that, which is a
    # sentence rather than an instruction — and the path comparison in
    # `workspace.py` does not read English either way.
    # **The client's id, not the server's.** A conversation only gets a
    # `conversation_id` when its first turn is stored, and attaching a file
    # before typing anything is the ordinary order of events — clip, then
    # question. The dashboard mints a stable id when the conversation opens, so
    # that is what attachments are filed under, from the very first turn.
    opened, pages = _document(request, content, str(body.get("attachment_id") or ""))
    awareness = "\n\n".join(part for part in (awareness, opened) if part)
    # **Only if something on this machine can actually see them.** RAVIS reads
    # an image in a request as a hard requirement — its own route decision says
    # "vision REQUIRED (the request contains an image)" — so attaching pages
    # where no candidate has the capability turns an ordinary question about a
    # document into a refusal to route. The catalogue that answers this is the
    # one already cached for the reading, so it costs no call.
    pages = pages if _can_see(await _catalogue(request)) else ()

    # How the ecosystem works, when the question is about that rather than about
    # what it is doing right now.
    #
    # **The gap this fills was reported by the model itself.** Asked what RAVIS
    # does, it gave a good account of the live readings and then said: *"how it
    # actually decides which model to send a request to, what the fallback chain
    # looks like, whether it does load balancing or cost optimization — I don't
    # have readings on any of that. I'm watching the service, not its logic."*
    # Every one of those answers was written down in the repository and nothing
    # ever handed it over.
    #
    # Background, and marked as background: the reading says it describes the
    # design rather than the running system, because a specification and a
    # service are different things and a model told neither will report
    # intentions as behaviour.
    awareness = "\n\n".join(
        part for part in (awareness, await knowledge.reading(
            content, request.app.state.probe_client, request.app.state.settings.ravis_base_url,
            request.app.state.settings.ravis_client_credential,
        )) if part
    )

    # The standing statement first, then the specific offer if there is one.
    # Unconditional on purpose: the failure it exists for happens precisely when
    # no offer was made, which is when `told` has nothing to say.
    awareness = "\n\n".join(
        part for part in (awareness, commands.capabilities_line()) if part
    )
    # Unconditional: `told(None)` is the sentence that says no button exists,
    # and it is the one the model most needs — an absent offer is not something
    # a model notices on its own.
    awareness = "\n\n".join(part for part in (awareness, commands.told(offer)) if part)
    # **M20, and it goes first.** §7: *measurement outranks memory* — with
    # recall on, the reading is assembled after the recalled conversations and
    # says so. Prepending is what makes that true rather than asserted: a model
    # reading top to bottom meets the older account first and the current
    # figures last. Off unless switched on, and `remembered` travels back to the
    # caller so the passages can be shown rather than silently used.
    remembered = _recalled(database, content, conversation_id)
    awareness = "\n\n".join(
        part for part in (memory.block(remembered), awareness) if part
    )
    system = _house_system(
        body, database, greeting, conversation_id, nudge > 0,
        # Read from the app rather than taken here, so one reading covers the
        # whole assembly and a test can hold it still. See `app.state.chat_clock`.
        now=request.app.state.chat_clock(),
        situation=awareness,
    )
    if greeting:
        asked = GREETING_OPENER
    elif nudge > 0:
        directive, recall = _nudge_directive(
            database, conversation_id, nudge, str(body.get("screen") or "")
        )
        system = "\n\n".join(part for part in (system, directive, recall) if part)
        asked = NUDGE_OPENER
    body = {**body, "system": system}
    payload = _completion_payload(body, profile, prior, asked, pages)
    # Assembled here and sent as a header, so the browser prints it verbatim.
    #
    # **A model is never asked to restate a measurement.** The first draft put
    # the figures in the prompt and told the model to quote them exactly; a
    # 1.5B model answered "the machine efficiently manages four key services",
    # which is neither the number nor a thing anybody measured. §18.1 already
    # said this — *character lives in the sentence around the reading, never in
    # the reading* — and asking a model to copy a number is putting it in the
    # reading. The model writes the greeting; NERVIS writes the facts.

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
            # **What was recalled, so it can be shown.** M20's exit says shown,
            # *with its source conversation*, never silently injected — a person
            # who cannot see what was remembered cannot tell a good
            # recollection from a wrong one. Titles and ids only: the passages
            # themselves are already in the answer's grounding and a header is
            # the wrong place for six hundred characters of prose.
            "x-recalled": _recalled_header(remembered),
            # The offer, for the page to draw as a button the person presses.
            # It travels beside the reply rather than inside it: a control the
            # model could write into its own text is a control the model has.
            "x-command-offer": json.dumps(offer.as_dict()) if offer else "",
            # The plan, beside the single offer and for the same reason: a
            # sequence the model could write into its own text is a sequence the
            # model has. Both travel; the page draws whichever it was sent.
            "x-command-plan": json.dumps(sequence.as_dict()) if sequence else "",
        },
    )


def _house_system(
    body: dict[str, Any],
    database: Any,
    greeting: bool,
    conversation_id: str = "",
    speaking_first: bool = False,
    now: datetime | None = None,
    situation: str = "",
) -> str:
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
    # **Only alongside something else.** A request with no persona, no name and
    # no house style sends no system message at all, and that is a property
    # worth keeping: §7 makes NERVIS a plain client of RAVIS's published API,
    # and a gateway that silently prepends a line to every request is not one.
    # The clock exists to keep a *persona* honest about durations, so it rides
    # with one rather than arriving on its own.
    # `speaking_first` covers the nudge, whose directive is joined on after this
    # returns: an unprompted remark about a silence is the one place the gap is
    # load-bearing, and it would have been the one place without a clock.
    if any(parts) or speaking_first:
        parts.append(_clock(database, conversation_id, now or datetime.now().astimezone()))
    if _memory_scope(database) == "all":
        parts.append(_recall(database, conversation_id))
    # **Last, and after the recall.** Under the same condition as the clock, and
    # for the same reason: a request with no persona, no name and no house style
    # still sends no system message at all — ecosystem awareness is something
    # NERVIS adds to its own assistant, not something it injects into a plain
    # client of RAVIS's API.
    #
    # It goes *after* the recalled conversations because of what happened when
    # it went before them: asked the same question twice, an 8B build answered
    # word for word the same both times, quoting its own earlier reply out of
    # the recall instead of reading the fresh figures above it. Memory outranked
    # measurement, and position is half of what decides that.
    if any(parts) or speaking_first:
        parts.append(situation)
        # With the readings and only with them: the directive is about how to
        # quote *them*, so a turn that carries none has nothing to apply it to.
        if situation:
            parts.append(AUDIENCE_DIRECTIVE)
    return "\n\n".join(part for part in parts if part)


def _clock(database: Any, conversation_id: str, now: datetime) -> str:
    """The time, and how long the user has been quiet. Both measured.

    **This is what makes a duration sayable at all.** The personas forbid
    inventing one because a conversation carries no clock — so the fix for "how
    long have I been away" is not to loosen the rule but to hand over the
    answer. A gap NERVIS computed from two stored timestamps is a reading like
    any other; a gap a model felt is not.

    **And handing it over reads as an invitation, which had to be withdrawn in
    the same breath.** Given the time, a model opens with it: "it's 04:03 and
    you just deleted every conversation", "judging your life choices at 04:06",
    a reading in every single reply. It is available for answering *about*, not
    for decorating with — the same rule the greeting already had, now stated for
    every turn.

    Local time, with the zone named, because that is the clock the person
    reading it is on. The stored timestamps are UTC and are converted here
    rather than compared as strings, which is the bug this shape usually has.
    """
    said = [f"The current local time is {now:%H:%M on %A %d %B %Y} ({now:%Z}, UTC{now:%z})."]
    quiet = _quiet_for(database, conversation_id, now)
    if quiet:
        said.append(quiet)
    said.append(
        "Those two are here so you can *answer* about them. Do not mention the "
        "time or how long they have been away unless they ask, or unless it is "
        "genuinely the point of what you are saying — a clock reading dropped "
        "into an ordinary reply is filler, and doing it every time is worse. "
        "When you do state one, never make it more precise than it is written "
        "here, and any other stretch of time is not yours to invent."
    )
    return " ".join(said)


def _quiet_for(database: Any, conversation_id: str, now: datetime) -> str:
    """How long since the user last said anything, in words, or nothing."""
    if not conversation_id:
        return ""
    row = database.connection.execute(
        "SELECT created_at FROM chat_message WHERE conversation_id = ? AND role = 'user'"
        " ORDER BY created_at DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    if not row:
        return ""
    try:
        last = datetime.fromisoformat(str(row["created_at"])).replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    seconds = int((now - last).total_seconds())
    # **Seconds under the minute, rather than "less than a minute".** Given the
    # coarse phrasing, a model states a fine one anyway: asked a question fifty
    # seconds after the last, it answered "you asked this fifty seconds ago" —
    # right by luck, from a reading that did not contain it. The rule against
    # inventing a duration is worth nothing if the true one is withheld, so the
    # measurement is given at the precision it is wanted at.
    if seconds < 60:
        return f"They last said something {seconds} second{'s' if seconds != 1 else ''} ago."
    minutes = seconds // 60
    if minutes < 60:
        return f"They last said something {minutes} minute{'s' if minutes > 1 else ''} ago."
    hours = minutes // 60
    return f"They last said something about {hours} hour{'s' if hours > 1 else ''} ago."


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


def _editor_destination(request: Request) -> dict[str, Any]:
    """Whether a Clarvis window has registered, and where it is looking.

    **The second half is usually unknowable, and that is by design.**
    `CLARVIS.md` §6.1 keeps the raw workspace path and name private and salts
    `workspace_id`, publishing a label only where the operator has turned
    labelling on. So this returns what it has: a registration, a label if there
    is one, and NERVIS's own workspace name to compare against. The offer says
    which of those it had.
    """
    instances = getattr(request.app.state, "instances", None)
    # Read once, and through a name a type checker can follow. The previous
    # shape asked `instances.all()` again inside `if registered:`, where the
    # narrowing lived in a boolean rather than in the value — safe at runtime,
    # and mypy correctly could not see it (§16 item 11).
    entries = list(instances.all()) if instances is not None else []
    registered = bool(entries)
    label = ""
    for entry in entries:
        label = str(getattr(entry, "workspace_label", "") or "")
        if label:
            break
    root = str(getattr(request.app.state.settings, "workspace_path", "") or "").strip()
    return {
        "registered": registered,
        "workspace_label": label,
        "nervis_workspace": Path(root).name if root else "",
    }


def _planned(database: Any, sequence: Any) -> Any:
    """Give a plan an id and give every step its own (M24).

    Each step is remembered separately because each step is separately
    answerable: M22 records what became of an offer, and a plan does not make
    its steps one offer. Somebody who runs a plan accepted every step in it, and
    the record should say so step by step rather than as a single verdict on an
    ordering.
    """
    if sequence is None:
        return None
    return replace(
        sequence,
        plan_id="pl_" + uuid.uuid4().hex[:12],
        steps=tuple(_remembered(database, step) for step in sequence.steps),
    )


def _remembered(database: Any, offer: commands.Proposal | None) -> commands.Proposal | None:
    """Give an offer an id and whatever is known about its predecessors (M22).

    The id is minted now, when the offer is *made*, because an answer needs
    something to be filed against — and because an offer nobody answers then
    leaves no row at all, which is the record M22 asks for. Silence is not a
    decline.

    The history is a sentence the person can check rather than a preference
    NERVIS has formed about them: what is remembered is shown on the offer that
    remembers it, never applied behind one.
    """
    if offer is None:
        return None
    past = proposals.history(database, offer.operation, offer.target)
    return replace(
        offer,
        proposal_id="pr_" + uuid.uuid4().hex[:12],
        history=past.as_dict() if past.answered else None,
    )


async def _situation(request: Request, greeting: bool, asked: str = "") -> tuple[str, str]:
    """What NERVIS prints about the ecosystem, and what it hands the model.

    **Two products from one reading**, assembled together so they cannot
    disagree: the printed line the browser renders verbatim, and the fenced
    block the model is given so a question about the machine has an answer that
    is not invented. `nervis.situation` decides what each may say; this does the
    reading.

    **The block is empty for a greeting.** NERVIS is speaking first there and
    prints the figures itself — §18.1's rule that a model asked to restate a
    measurement paraphrases it is the whole reason the greeting directive
    forbids numbers, and handing it a table of them would be the same mistake
    from the other end.

    Three of the four sources cost nothing: the registry, the instance leases
    and the event hub are already here. The fourth is RAVIS's catalogue, which
    is one HTTP call and is therefore cached — a reply should not wait on a
    remote read to say how many models exist.
    """
    services = [entry.as_dict() for entry in request.app.state.registry.all()]
    windows = len(request.app.state.instances.live("clarvis"))
    models = await _catalogue(request)
    catalogue = situation.catalogue_line(models)
    printed = situation.printed_line(services, windows, catalogue)
    if greeting:
        return printed, ""
    events = request.app.state.hub.query(latest=True, limit=EVENT_SAMPLE)
    jobs = await _jobs(request, asked)
    runtime = await _runtime(request, asked)
    decisions = await _decisions(request, asked)
    editors = await _editors(request, asked)
    # **Not "only alongside the queue", which is what this said.** A run is
    # what a job became, and it outlives it: the queue is empty most of the
    # time and the measurements are the part anybody asks about later. Gating
    # results on the queue having something in it meant a machine with 87
    # recorded runs answered "how did the GGUF gemma do" with nothing.
    runs = await _runs(request, asked) if _wants_results(asked) else []
    machine = await _machine(request, asked)
    usage = await _spend(request, asked)
    providers = await _providers(request, asked)
    observations = await _observations(request, asked)
    policies = await _policies(request, asked)
    residency = await _residency(request, asked)
    sets = await _runtime_sets(request, asked)
    records = await _spend_records(request, asked)
    evidence = await _evidence(request, asked)
    # The question travels so the reading can go deep on what it named. Nothing
    # in it reaches the prompt — it is matched against the registry's own keys
    # and labels and then dropped, which is why a crafted question cannot select
    # anything NERVIS does not already publish about itself.
    return printed, situation.block(
        services, windows, events, catalogue, request.app.state.chat_clock(),
        question=asked, models=models, jobs=jobs, runs=runs, runtime=runtime,
        decisions=decisions, editors=editors,
        machine=machine, usage=usage, providers=providers,
        observations=observations, policies=policies, residency=residency,
        runtime_sets=sets, spend_records=records, evidence=evidence,
        traces=_traces(request, asked), quarantine=_quarantine(request, asked),
    )


async def _catalogue(request: Request) -> list[dict[str, Any]]:
    """RAVIS's models, read at most once a minute.

    The list rather than a count, because "which models do I have" is asked as
    often as "how many" and only the names answer it. Cached on the app rather
    than fetched per turn: this is the one part of the reading that leaves the
    process, and a reply should not wait on a remote read. A failure is cached
    too, for less time, so a service that has just come back is not treated as
    absent for a minute.
    """
    state = request.app.state
    taken, items = state.chat_catalogue or (0.0, [])
    now = time.monotonic()
    fresh = CATALOGUE_TTL_SECONDS if items else CATALOGUE_RETRY_SECONDS
    if state.chat_catalogue and now - taken < fresh:
        return list(items)
    read = await _model_items(request)
    if read is None and items:
        # **A failed read is not an empty catalogue.** Observed live: RAVIS
        # rate-limits, this read came back 429, and the model counts vanished
        # from a reading that had carried them a minute earlier — while the
        # registry went on reporting RAVIS healthy, because it is. Keeping the
        # last answer is the difference between "this was true a minute ago" and
        # "NERVIS knows nothing about models". A catalogue that has never been
        # read stays absent, which is the honest answer there and the reason
        # this keeps rather than invents.
        state.chat_catalogue = (now - CATALOGUE_TTL_SECONDS + CATALOGUE_RETRY_SECONDS, items)
        return list(items)
    state.chat_catalogue = (now, read or [])
    return read or []



async def _model_items(request: Request) -> list[dict[str, Any]] | None:
    """What RAVIS says it can route — or `None` when it could not be asked.

    **Three answers, not two.** A catalogue of nothing and a catalogue that
    could not be read are different facts, and collapsing them is what let a
    single 429 report "no models" about a machine holding 591 of them. `None`
    is the one the caller may paper over with its last good answer; `[]` is a
    measurement and stands.
    """
    entry: RegistryEntry | None = request.app.state.registry.get("ravis")
    if entry is None:
        return None
    client: httpx.AsyncClient = request.app.state.probe_client
    for attempt in (0, 1):
        try:
            response = await client.get(
                entry.declaration.base_url + "/api/v1/models",
                timeout=FACTS_TIMEOUT_SECONDS,
                headers=_named(request),
            )
            # **One retry, and only for a rate limit.** RAVIS allows an
            # anonymous caller 60 reads a minute and NERVIS is not its only
            # one — the dashboard polls through it. A 429 means "ask again",
            # which is exactly what a caller that gives up turns into "this
            # machine has no models". Every other status is an answer and is
            # taken as one.
            if response.status_code == 429 and attempt == 0:
                await asyncio.sleep(RATE_LIMIT_PAUSE_SECONDS)
                continue
            if response.status_code >= 400:
                return None
            items = response.json().get("items") or []
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
        return [item for item in items if isinstance(item, dict)]
    return None


def _placement(
    database: Any,
    body: dict[str, Any],
    profile: str,
    content: str,
    greeting: bool,
    nudge: bool = False,
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
    if nudge:
        # **History, but no record.** A nudge is a reaction to a silence *in* a
        # conversation, so she has to be able to see it — the first version sent
        # none and asked her to follow up on something earlier, which she could
        # not read. Stored nowhere for the same reason a greeting is not: the
        # user did not say anything, and a stored turn with nothing before it
        # comes back as history that teaches the model to speak unprompted.
        #
        # The id still travels, so "leave this conversation out of the pool"
        # keeps meaning what it means everywhere else.
        held = str(body.get("conversation_id") or "")
        known = bool(held) and store.exists(database, held)
        return held, (store.history(database, held) if known else []), False
    conversation_id = str(body.get("conversation_id") or "")
    if conversation_id and not store.exists(database, conversation_id):
        raise NotFoundError(f"no conversation {conversation_id!r}")
    if not conversation_id:
        # **Named from the person's own words before anything is asked of a
        # model.** Every conversation in this list read "New conversation" until
        # a background call came back, and when the call did come back it stored
        # whatever the model said — which on this machine was a reasoning
        # model's preamble, "Okay, let's tackle this user query…", eighty
        # characters of it. A title taken from the opening question is never
        # noise, costs nothing, and is already right; the model's attempt is an
        # improvement on it and has to earn the replacement.
        conversation_id = store.start_conversation(
            database, profile=profile, title=opening_title(content)
        )
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
            headers=_forwarded(
                request_id, trace_id, request.app.state.settings.ravis_client_credential
            ),
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
            _title_later(request, conversation_id, trace_id, model)
        # **The caller's span needs an end, or its bar has no length.** §11.2's
        # waterfall draws durations, and a root span with only a start drew the
        # calling service as a lane with nothing in it — the one lane whose
        # duration bounds every other. Emitted whether or not the turn was
        # stored: an interrupted answer still took the time it took.
        _close_turn(request, conversation_id, request_id, trace_id, model, interrupted)


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


def _recalled_header(passages: list[memory.Passage]) -> str:
    """Which conversations were drawn on, as one header-safe line.

    **Percent-encoded, because a title is arbitrary text and a header is
    latin-1.** The reading header beside this one is safe by construction — it
    is assembled from counts — and copying its approach here produced a 500 the
    first time a recalled conversation had a title containing an ellipsis.
    Titles are written by people and by models; there is no character they
    cannot contain, so the encoding has to be total rather than a list of the
    ones seen so far. Separators are escaped by `quote` along with everything
    else, which removes the newline problem rather than patching it.
    """
    return " | ".join(
        f"{one.conversation_id}:{quote(one.title[:60], safe='')}" for one in passages
    )


def _recalled(database: Any, content: str, conversation_id: str) -> list[memory.Passage]:
    """Earlier conversations worth quoting, or none at all.

    **Nothing happens when it is off**, which is M20's exit clause in its own
    words: turning it off leaves ordinary chat unchanged. Not "leaves it
    similar" — no search runs, no block is built, and the assembled prompt is
    byte-for-byte what it was before this milestone.
    """
    if not memory.enabled(database):
        return []
    with contextlib.suppress(Exception):
        return memory.search(database, content, exclude=conversation_id)
    return []


def _close_turn(
    request: Request, conversation_id: str, request_id: str, trace_id: str,
    model: str, interrupted: bool,
) -> None:
    """The other end of NERVIS's own span.

    Paired with `_note_turn` and gated the same way — no trace, no event — so a
    turn either contributes both ends of a bar or neither. A start with no end
    is what made the calling lane on every waterfall a point rather than a
    duration.
    """
    hub = getattr(request.app.state, "hub", None)
    if hub is None or not trace_id:
        return
    with contextlib.suppress(Exception):
        hub.emit(
            "nervis.chat.turn_completed",
            subject={"type": "conversation", "id": conversation_id},
            data={"conversation_id": conversation_id, "model": model,
                  "interrupted": interrupted},
            trace_id=trace_id,
            request_id=request_id,
        )


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


def _can_see(catalogue: list[dict[str, Any]]) -> bool:
    """Whether any model this machine can route to accepts an image.

    Read from the catalogue RAVIS already publishes — `capabilities.vision`
    per model — rather than asked as its own question, and `SUPPORTED` only:
    `UNKNOWN` is most of a six-hundred-model catalogue, and attaching pages on
    the strength of one would be guessing with somebody's whole turn.
    """
    return any(
        ((item.get("capabilities") or {}).get("vision") or {}).get("state") == "SUPPORTED"
        for item in catalogue
    )


def _completion_payload(
    body: dict[str, Any], profile: str, prior: list[dict[str, str]], content: str,
    pages: tuple[str, ...] = (),
) -> dict[str, Any]:
    """The body RAVIS receives, assembled from what the caller actually set.

    A separate job from deciding *whether* to send one, and extracted because
    `send` was at the complexity cap — where a function doing two jobs is what
    the gate is usually pointing at.

    Only what was supplied travels. An absent `temperature` is omitted rather
    than defaulted here, because the model and the runtime own their own
    defaults and filling one in would be NERVIS inventing a choice nobody made.
    """
    # **The pages ride on the question, not in the system prompt.** An image
    # part is only meaningful inside a message's content list, and the turn the
    # person asked about the document is the one the pictures belong to. The
    # text part stays first so a model reading in order meets the question
    # before the pictures of what it is about.
    spoken: Any = content if not pages else [
        {"type": "text", "text": content},
        *({"type": "image_url", "image_url": {"url": page}} for page in pages),
    ]
    messages: list[dict[str, Any]] = [*prior, {"role": "user", "content": spoken}]
    if body.get("system"):
        messages.insert(0, {"role": "system", "content": str(body["system"])})
    return {
        "model": profile,
        "messages": messages,
        "stream": True,
        **{name: body[name] for name in FORWARDED if name in body},
    }


# A file named the way people name one: in quotes, or after a reading verb.
# Deliberately narrow — this decides whether NERVIS *opens* something, and a
# pattern that fires on an ordinary sentence would read a file nobody asked for.
