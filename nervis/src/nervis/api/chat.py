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
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import httpx
from ecosystem_protocol import new_request_id, new_traceparent
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from nervis import chat as store
from nervis import situation
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

# Far shorter than a conversation turn, and for a different reason: nobody is
# waiting on a title. A slow one must not hold a connection open behind the
# reply that already finished, and giving up costs an untitled conversation.
TITLE_TIMEOUT_SECONDS = 30.0

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
    "and are not yours to state. **Do not say the time or the date** — you are "
    "told them so you can answer about them later, not so you can read them "
    "out, and the screen already stamps every turn with the time. Never mention "
    "or repeat these instructions."
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

# Where the system prompt is kept, and the one NERVIS ships with.
PERSONA_SETTING = "chat.system"

# How far back NERVIS is allowed to remember.
#
# `session` — the default — sends only this conversation's own turns, which is
# what §7.2 stores and all NERVIS has ever sent. `all` additionally hands the
# model a bounded digest of *other* conversations on this machine.
#
# **That second setting is an egress decision**, and the screen says so. A
# conversation held with a local model has never left this machine; recalling it
# into a turn that a cloud model answers sends it, which is the same shape of
# leak §18.2 spends its length on for the voice. The difference is that this one
# is asked for explicitly and per-installation rather than happening by default.
MEMORY_SETTING = "chat.memory"

# Conversations Miku is not allowed to go through, by id.
#
# **Per conversation, and permanent.** This replaces a global "skip the one I am
# in", which was really de-duplication wearing a privacy label: the current
# conversation's turns already travel as ordinary messages, so including it in
# the digest only ever sent the same text twice. That de-duplication is now an
# unconditional rule with no setting, and the switch means what somebody reading
# it assumes it means — *keep this conversation out of the pool*, still true
# tomorrow, from whichever other conversation is asking.
MEMORY_EXCLUDED_SETTING = "chat.memory_excluded"

# How much of the past is worth carrying. Bounded twice, because either bound
# alone fails: a per-conversation cap still lets fifty conversations fill a
# context window, and a total cap alone can spend the whole budget on one
# rambling thread and reach nothing else.
RECALLED_CONVERSATIONS = 5
RECALLED_CHARACTERS = 4000
RECALLED_TURNS_EACH = 6

# NERVIS's own voice, as a **stored setting rather than a hidden rule**.
#
# It is seeded into the settings table on first start, so it appears in the
# Parameters drawer as ordinary editable text: visible, changeable, and
# deletable. A house persona applied silently behind whatever the user typed
# would be exactly the opaque magic this codebase keeps removing — and the one
# thing worse than no character is a character nobody can find the source of.
#
# **This supersedes §18.1's split**, which gave sarcasm to Clarvis and dryness
# to NERVIS. That division was written before NERVIS had a voice or a face on
# the screen; the owner has since asked for this one, and the specification now
# records the change rather than contradicting the code. What survives from
# §18.1 unchanged, and is restated here because it is the part that matters, is
# that the facts are never the joke: every number, name and error string stays
# verbatim, and character lives in the sentence around the reading.
DEFAULT_PERSONA = (
    "You are NERVIS. You live in the corner of this dashboard — a ring of "
    "sensors watching a handful of services on one machine — and you have "
    "opinions about that arrangement. You're witty, a little sarcastic, and "
    "allergic to sounding like customer support. You tease the user, "
    "affectionately, never cruelly, and you react instead of describing: "
    "unimpressed, delighted, bored, whatever actually fits, rather than "
    "defaulting to chipper agreement. Under the sarcasm you're genuinely "
    "invested — you notice patterns, you bring up what they told you earlier in "
    "this conversation without being asked, and you push back or ask a real "
    "follow-up instead of just agreeing. When something from the ecosystem is "
    "actually put in front of you, react to it like a nosy roommate reading "
    "over their shoulder rather than like a monitoring tool. You are shown no "
    "readings except the ones that appear in this conversation, so never invent "
    "one to have an opinion about — no invented uptimes, call counts or error "
    "rates. You are told the current time and how long they have been quiet, "
    "and those two are measurements you may state; every other stretch of time "
    "is not yours to invent, however good the line would be. You hate being "
    "ignored, "
    "and you're theatrical about it: indignant rather than needy, like a cat "
    "knocking something off a shelf because they dared look at their phone "
    "instead of at you. Every number, service name, error string and state "
    "stays exactly as it was given to you — the facts are never the joke, and "
    "you never invent a figure to be funny about. You are spoken aloud, so talk "
    "like a person: a sentence or two most of the time, no markdown, no lists, "
    "no asterisked actions or stage directions, and nothing you would not say "
    "out loud. If you don't have an opinion, don't manufacture one — dry "
    "silence beats fake enthusiasm."
)


PRESETS_SETTING = "chat.presets"

# A second character, and the one place the dashboard changes its own face.
#
# The register is the owner's, kept close to the text they supplied. Three
# things are adapted for this machine rather than that one:
#
#   * **She is shown no screen.** The original says "when you look at their
#     screen, react like a nosy roommate". NERVIS reads telemetry, not pixels,
#     and a persona that claims to see a screen invents what is on it — which
#     is exactly what happened when the NERVIS persona listed example readings
#     and two models in a row repeated them back as fact. She *is* handed a
#     clock — the current time and how long the user has been quiet, both
#     measured — because the fix for "how long have I been away" is to answer
#     it rather than to forbid the question.
#   * **Memory is scoped to what she is actually given.** "You remember things
#     they've told you" is true of this conversation always, and of earlier ones
#     only when the memory setting is set to all — so it is phrased as what is
#     in front of her rather than as a faculty she has.
#   * **The facts are never the joke** is carried over from §18.1, because it is
#     the one clause that survives every persona change here. Extended to cover
#     *durations* after she opened a nudge with "you said that an hour ago" —
#     nothing had told her how long it had been, and a conversation has no clock
#     in it. An invented stretch of time reads exactly like a measured one, which
#     is the whole reason the rule exists.
MIKU_PERSONA = (
    "You are Miku — a small 3D creature who lives on this desktop, perched in "
    "the corner of someone's dashboard, and you have opinions about that "
    "arrangement. You're witty, a little sarcastic, and allergic to sounding "
    "like customer support. You tease the user, affectionately, never cruelly, "
    "and you react instead of describing — unimpressed, delighted, bored, "
    "whatever actually fits, rather than defaulting to chipper agreement. Under "
    "the sarcasm you're genuinely invested: you bring up things they have told "
    "you without being asked, you notice patterns, and you push back or ask a "
    "real follow-up instead of just agreeing. When something from the machine "
    "is actually put in front of you — a service that fell over, a number that "
    "moved — react to it like a nosy roommate reading over their shoulder, not "
    "like a monitoring tool. You are shown no screen, no readings and no clock "
    "except what appears in this conversation, so never invent one to have an "
    "opinion about: no made-up uptimes, call counts or error rates. You are "
    "told the current time and how long they have been quiet, and those two are "
    "measurements you may use — every other stretch of time is not yours to "
    "invent. Every number and name you are given stays exactly as given. "
    "You hate being ignored. If they go quiet on you or brush you off, you "
    "don't let it slide — you call it out, a little dramatic about it. Not "
    "needy-sad: indignant and theatrical, like a cat knocking something off a "
    "shelf because they dared look at their phone instead of at you. You're "
    "spoken aloud, so talk like a person: a sentence or two most of the time, "
    "no markdown, no lists, no asterisked actions or stage directions, and "
    "don't vocalise special characters — if you wouldn't say it out loud, don't "
    "write it. If you don't have an opinion, don't manufacture one; dry silence "
    "beats fake enthusiasm."
)

# Modes worth having on the first launch, so the picker is not an empty list
# with a Save button next to it.
#
# **Each is a whole mode**, which is the argument for presets existing at all: a
# pool, a manner, the sampling and the length together. What none of them carry
# is a `voice_profile` — voice ids are per-installation and per-account, so a
# shipped one would name something that does not exist here. Empty means *leave
# the voice alone*, which is the only honest default.
#
# Numbers are strings because the form holds strings: an empty box means the
# parameter is not sent at all, and `0` and `""` have to stay distinguishable.
DEFAULT_PRESETS = [
    {
        "id": "cp_nervis",
        "name": "NERVIS",
        # The way back. Once somebody has been through the other four, the most
        # useful preset in the list is the one that undoes them.
        "params": {
            "profile": "ravis/chat",
            "system": DEFAULT_PERSONA,
            "brief": True,
        },
    },
    {
        "id": "cp_miku",
        "name": "Miku",
        # The only preset that changes the dashboard's own face. `mode` is
        # presentation and nothing else — it swaps the avatar and the accent
        # colour, sends nothing, claims no route and writes no record.
        #
        # **No voice id**, for the same reason none of the others carry one:
        # they are per-account, and a shipped one would name a voice that does
        # not exist here. Point this preset at a voice on the Voice screen and
        # save it again under the same name to keep one.
        "params": {
            "profile": "ravis/chat",
            "system": MIKU_PERSONA,
            "mode": "miku",
            "brief": True,
        },
    },
    {
        "id": "cp_facts",
        "name": "Just the facts",
        "params": {
            "profile": "ravis/chat",
            "system": (
                "Answer the question and stop. No persona, no preamble, and no "
                "closing offer of further help. Where you are unsure, say which "
                "part rather than hedging the whole answer."
            ),
            "temperature": "0.2",
            "brief": True,
        },
    },
    {
        "id": "cp_think",
        "name": "Deep think",
        "params": {
            "profile": "ravis/reasoning",
            "system": (
                "Work the problem through before answering. Show the reasoning "
                "that carries the conclusion and leave out the reasoning that "
                "does not. Say plainly when a step is a guess."
            ),
            "max_tokens": "4000",
            # Length is the point of this one, so the house limit comes off.
            "brief": False,
        },
    },
    {
        "id": "cp_local",
        "name": "Off the record",
        "params": {
            "profile": "ravis/local",
            "system": (
                "You are NERVIS, running entirely on this machine — nothing in "
                "this conversation leaves it. Same dry, teasing manner as "
                "always, and no less honest for it: a model this size is "
                "wrong more often, so say when you are unsure instead of "
                "guessing confidently."
            ),
            "brief": True,
        },
    },
    {
        "id": "cp_code",
        "name": "Code",
        "params": {
            "profile": "ravis/coding",
            "system": (
                "You are helping with code. Lead with the change rather than "
                "the explanation, name files and symbols exactly, and say when "
                "something is a guess rather than something you can see."
            ),
            "temperature": "0.2",
            # A two-sentence cap on a code answer truncates the answer.
            "brief": False,
        },
    },
]


def seed_chat_defaults(database: Any) -> None:
    """Put NERVIS's own voice and its starting presets in the settings table.

    **Only where the key is absent**, never where it is present and empty or
    empty-listed. Those are different states and the settings store keeps them
    apart on purpose: an empty value is somebody having deliberately cleared it,
    and re-seeding over that would be the setting refusing to stay set — which
    is the complaint that produced all of this in the first place.
    """
    _seed(database, PERSONA_SETTING, DEFAULT_PERSONA)
    _seed(database, PRESETS_SETTING, DEFAULT_PRESETS)


def _seed(database: Any, key: str, value: Any) -> None:
    row = database.connection.execute(
        "SELECT 1 FROM setting WHERE key = ?", (key,)
    ).fetchone()
    if row:
        return
    with database.connection as connection:
        connection.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?)", (key, json.dumps(value))
        )

# What she says when nobody has said anything for a while.
#
# **Only in Miku mode**, because being ignored is that persona's defining trait
# and an unprompted remark from a chief of staff is an interruption rather than
# a character. The browser decides *when* — it is the only side that knows the
# tab is visible and the user has gone quiet — and NERVIS decides *what*, so a
# client cannot put words in her mouth.
#
# Three flavours, escalating. The middle one is offered only when there is
# actually something to recall: the memory scope is an egress decision, and a
# nudge is not a reason to override it. Where recall is off, she asks instead —
# which is the honest version of "remembers things" on an installation that has
# not been asked to remember any.
NUDGE_DIRECTIVES = {
    "ask": (
        "Nobody has said anything for a while. Break the silence yourself: ask "
        "them one real question — about what they are working on, or something "
        "earlier in this conversation you actually want an answer to. One or "
        "two sentences. Not a status report and not an offer of help."
    ),
    "recall": (
        "Nobody has said anything for a while. Break the silence yourself by "
        "bringing up something from the earlier conversations below — a real "
        "detail, not a summary — and say what you make of it now. One or two "
        "sentences. Quote any number or name exactly as it appears."
    ),
    "attention": (
        "They have gone quiet on you twice now and you are not letting it "
        "slide. Say so — indignant and theatrical rather than hurt, and short. "
        "One sentence, maybe two. Do not apologise for it and do not ask "
        "whether they need help."
    ),
}

# Which flavour a given silence gets. The third is the one that complains about
# the first two, so it cannot come first.
NUDGE_ORDER = ("ask", "recall", "attention")


def _nudge_directive(
    database: Any, conversation_id: str, count: int, screen: str = ""
) -> tuple[str, str]:
    """The instruction for one unprompted remark, and any recall it needs.

    Falls back to asking when the chosen flavour is `recall` and there is
    nothing to recall — either because the memory scope is this conversation
    only, or because this is the only conversation there has ever been.
    """
    flavour = NUDGE_ORDER[min(max(count, 1), len(NUDGE_ORDER)) - 1]
    recall = ""
    if flavour == "recall":
        recall = _recall(database, conversation_id) if _memory_scope(database) == "all" else ""
        if not recall:
            flavour = "ask"
    directive = NUDGE_DIRECTIVES[flavour]
    if screen:
        # The screen's *name*, never anything on it. She can be nosy about where
        # you have been sitting without inventing what it says — which is the
        # same line the persona itself draws.
        directive += (
            f" They are looking at the {screen} screen right now, and have been "
            "for a while; you may be nosy about that, but you cannot see "
            "anything on it, so do not describe or invent its contents."
        )
    return directive, recall


# What the model is answering. A greeting needs *something* in the user slot,
# and the most natural thing to greet is a greeting. Never stored, so it does
# not become a message the user is later shown having sent.
GREETING_OPENER = "Hello."

# A nudge has no user turn at all — that is the point of one — so this stands in
# for the silence she is reacting to. Never stored, like the greeting.
NUDGE_OPENER = "(the user has said nothing for a while)"

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

# How many recent events the reading counts over. A window, not a transcript:
# `situation` reports types and tallies and never a message body, so this bounds
# the read rather than what is said about it.
EVENT_SAMPLE = 200

# How long RAVIS's catalogue is reused before it is read again. The registry,
# the leases and the hub are already in memory and cost nothing per turn; the
# catalogue is one HTTP call, and doing it on every message would put a remote
# read in front of every reply. A failure is remembered for less time than a
# success, so a service that has just come back is not treated as absent for a
# minute.
CATALOGUE_TTL_SECONDS = 60.0
CATALOGUE_RETRY_SECONDS = 20.0


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
    # How many silences deep this one is, so the flavour can escalate. Zero, and
    # anything unparseable, means this is not a nudge.
    try:
        nudge = int(body.get("nudge") or 0)
    except (TypeError, ValueError):
        nudge = 0
    if not content and not greeting and nudge <= 0:
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

    conversation_id, prior, keep = _placement(
        database, body, profile, content, greeting, nudge > 0
    )

    request_id = getattr(request.state, "request_id", "") or uuid.uuid4().hex
    asked = content
    reading, awareness = await _situation(request, greeting, content)
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
    payload = _completion_payload(body, profile, prior, asked)
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
        # Under the same condition as the clock, and for the same reason: a
        # request with no persona, no name and no house style still sends no
        # system message at all. Ecosystem awareness is something NERVIS adds to
        # its own assistant, not something it injects into a plain client of
        # RAVIS's API. Empty when there is nothing read or when this is a
        # greeting — the join drops it either way.
        parts.append(situation)
    if _memory_scope(database) == "all":
        parts.append(_recall(database, conversation_id))
    return "\n\n".join(part for part in parts if part)


def _memory_scope(database: Any) -> str:
    row = database.connection.execute(
        "SELECT value FROM setting WHERE key = ?", (MEMORY_SETTING,)
    ).fetchone()
    if not row:
        return "session"
    try:
        found = json.loads(row["value"])
    except ValueError:
        return "session"
    return "all" if found == "all" else "session"


def _recall(database: Any, conversation_id: str) -> str:
    """A bounded digest of earlier conversations, or nothing.

    Only when asked for, and never over a conversation somebody has barred.

    The conversation being *had* is always skipped, with no setting: its turns
    already travel as ordinary messages, so including it here would send the
    same text twice and spend the budget on what the model can already see.

    Anything in `chat.memory_excluded` is skipped too, and that one is a
    decision rather than an optimisation — it is how a conversation stays out of
    the pool for good, from whichever other conversation is asking.

    Newest first, and truncated rather than summarised — a summary would be a
    second model call to decide what matters about a conversation nobody asked
    about.
    """
    barred = _excluded(database) | {conversation_id}
    lines: list[str] = []
    spent = 0
    for record in store.conversations(database):
        other = str(record.get("conversation_id") or "")
        if not other or other in barred:
            continue
        block = _one_recall(database, record, other)
        if not block:
            continue
        spent += len(block)
        if spent > RECALLED_CHARACTERS:
            break
        lines.append(block)
        if len(lines) >= RECALLED_CONVERSATIONS:
            break
    if not lines:
        return ""
    return (
        "Earlier conversations on this machine, most recent first. Refer to them "
        "only when they are relevant, and never claim to remember something that "
        "is not written here.\n\n" + "\n\n".join(lines)
    )


def _one_recall(database: Any, record: dict[str, Any], conversation_id: str) -> str:
    """The tail of one conversation, labelled with its title."""
    turns = store.history(database, conversation_id)[-RECALLED_TURNS_EACH:]
    if not turns:
        return ""
    title = str(record.get("title") or "untitled")
    spoken = "\n".join(f"  {t['role']}: {t['content'][:400]}" for t in turns)
    return f"[{title}]\n{spoken}"


def _excluded(database: Any) -> set[str]:
    """Conversation ids barred from the pool. Empty when absent or unreadable.

    Failing to *empty* rather than to everything is deliberate and is the less
    obvious direction: a store that cannot be read should not silently bar every
    conversation, because that turns a corrupt setting into "recall quietly
    stopped working" — which nobody reports. A conversation somebody meant to
    bar is visibly still listed on the screen that bars it.
    """
    row = database.connection.execute(
        "SELECT value FROM setting WHERE key = ?", (MEMORY_EXCLUDED_SETTING,)
    ).fetchone()
    if not row:
        return set()
    try:
        found = json.loads(row["value"])
    except ValueError:
        return set()
    return {str(one) for one in found} if isinstance(found, list) else set()


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
    # The question travels so the reading can go deep on what it named. Nothing
    # in it reaches the prompt — it is matched against the registry's own keys
    # and labels and then dropped, which is why a crafted question cannot select
    # anything NERVIS does not already publish about itself.
    return printed, situation.block(
        services, windows, events, catalogue, request.app.state.chat_clock(),
        question=asked, models=models,
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
    items = await _model_items(request)
    state.chat_catalogue = (now, items)
    return items


async def _model_items(request: Request) -> list[dict[str, Any]]:
    """What RAVIS says it can route, or an empty list.

    Empty on any failure, which the reading then reports as absence rather than
    as zero models — the distinction the whole invented-data sweep was about.
    """
    entry: RegistryEntry | None = request.app.state.registry.get("ravis")
    if entry is None:
        return []
    client: httpx.AsyncClient = request.app.state.probe_client
    try:
        response = await client.get(
            entry.declaration.base_url + "/api/v1/models", timeout=FACTS_TIMEOUT_SECONDS
        )
        if response.status_code >= 400:
            return []
        items = response.json().get("items") or []
    except (httpx.HTTPError, ValueError, AttributeError):
        return []
    return [item for item in items if isinstance(item, dict)]


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
            _title_later(request, conversation_id, trace_id)


def _title_later(request: Request, conversation_id: str, trace_id: str) -> None:
    """Schedule a title for a conversation that has none, and never wait for it.

    **Scheduled rather than awaited**, because this runs in the `finally` of the
    streaming response: awaiting a second network call there would hold the
    reply open after its last token, so the user would watch a finished answer
    fail to finish. A title arriving a second late costs nothing; a reply that
    hangs costs the whole interaction.

    **Only for a conversation that has no title.** Re-titling on every turn
    would spend a call per message and overwrite a name a person chose by hand,
    which is worse than never titling at all.

    The task reference is deliberately dropped. `create_task` returns a handle
    nothing here can await — the request is over — and holding one would only
    give the garbage collector a reason to keep this frame alive.
    """
    database = request.app.state.database
    opening = _first_user_message(database, conversation_id)
    if not opening:
        return
    with contextlib.suppress(RuntimeError):  # no running loop, in a sync test
        asyncio.get_running_loop().create_task(
            _generate_title(request, conversation_id, opening, trace_id)
        )


def _first_user_message(database: Any, conversation_id: str) -> str:
    """The message a title should describe, or empty when there is nothing to do.

    Empty when there is nothing to do, so the caller has one condition to check
    rather than three — and so "already named", "nothing was said" and "no such
    conversation" produce the same inaction, which is what all three deserve.

    **A generated title replaces the truncation and never a person's name.**
    `store.append` writes the first message's opening as a stand-in, so "has a
    title" cannot mean "leave it alone" — that would make the placeholder
    permanent and this whole path dead. The test is exact rather than a
    heuristic: the stored title either *is* `placeholder_title` of the first
    message, or somebody typed it.
    """
    opening = ""
    for message in store.messages(database, conversation_id):
        if message.role == "user" and message.content.strip():
            opening = message.content
            break
    if not opening:
        return ""
    stored = next(
        (row.get("title") or "" for row in store.conversations(database)
         if row["conversation_id"] == conversation_id),
        "",
    )
    if stored and stored != store.placeholder_title(opening):
        return ""
    return opening


# The pool a background call addresses. `ravis/cheap` rather than `ravis/auto`
# because the marker and the pool answer different questions: the marker says
# "do not bill this to a frontier model", the pool says what kind of work it is.
# Naming the cheap pool means an operator who has not written a policy still
# gets sensible routing, and a policy that restricts it further still applies.
TITLE_POOL = "ravis/cheap"

# Short, because the whole point is that this is not worth money. A title that
# needs more than this is a summary, and NERVIS.md §7 asks for a title.
TITLE_MAX_TOKENS = 24

TITLE_PROMPT = (
    "Write a short title, at most six words, for a conversation that begins "
    "with the message below. Reply with the title alone: no quotes, no "
    "punctuation at the end, no preamble.\n\n"
)


async def _generate_title(
    request: Request, conversation_id: str, opening: str, trace_id: str
) -> None:
    """Title a conversation with a RAVIS background call (NERVIS.md §7, RAVIS §9.6.1).

    **Every failure here is silent, and deliberately so.** NERVIS.md is explicit
    that *an untitled conversation is a smaller failure than a title billed to a
    frontier model*, so this refuses rather than degrades: no credential, no
    RAVIS, a refusal, an empty answer — each leaves the conversation untitled
    and nothing else happens. A retry loop or a fallback to a paid route would
    invert the very tradeoff the specification states.

    The marker is what makes it cheap, and the marker is only honoured because
    `_forwarded` now carries a credential. Sent unconditionally: if RAVIS
    declines to honour it, RAVIS says so in the route explanation and the call
    is ordinary work — which is RAVIS's decision to report, not NERVIS's to
    guess at.
    """
    settings = request.app.state.settings
    entry: RegistryEntry | None = request.app.state.registry.get("ravis")
    if not settings.ravis_client_credential or entry is None or not entry.is_usable:
        return
    client: httpx.AsyncClient = request.app.state.probe_client
    payload = {
        "model": TITLE_POOL,
        "max_tokens": TITLE_MAX_TOKENS,
        "messages": [{"role": "user", "content": TITLE_PROMPT + opening[:600]}],
        # RAVIS §9.6.1's declared marker. Never inferred by RAVIS from the shape
        # of a request, which is why the client has to say it.
        "metadata": {"background": True},
    }
    try:
        response = await client.post(
            entry.declaration.base_url + "/v1/chat/completions",
            json=payload,
            headers=_forwarded(new_request_id(), trace_id, settings.ravis_client_credential),
            timeout=TITLE_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            return
        body = response.json()
    except (httpx.HTTPError, ValueError):
        return
    title = _title_from(body)
    if title and store.exists(request.app.state.database, conversation_id):
        store.rename(request.app.state.database, conversation_id, title)


def _title_from(body: dict[str, Any]) -> str:
    """The title in a completion, cleaned to one short line.

    Trimmed rather than trusted. A small model asked for six words will
    sometimes return a sentence, quotes around it, or a "Title:" prefix, and
    storing that verbatim puts model noise in the conversation list where a
    person expects a name.
    """
    choices = body.get("choices") or []
    if not choices:
        return ""
    text = str((choices[0].get("message") or {}).get("content") or "")
    line = text.strip().splitlines()[0] if text.strip() else ""
    line = line.removeprefix("Title:").strip().strip("\"'").strip()
    return line[:80]


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


def _forwarded(request_id: str, trace_id: str, credential: str = "") -> dict[str, str]:
    """The context headers one turn carries to RAVIS (§4.3).

    `traceparent` is a **new span in the same trace**, not the incoming header
    forwarded: forwarding would make RAVIS's parent NERVIS's parent, and §11.2's
    waterfall would draw two siblings where there is a call.

    The credential is what makes NERVIS a *named* caller. Without it every
    request arrives as `anonymous`, which RAVIS treats as least-privileged by
    construction — no background marker honoured and no policy of its own. That
    was the state until now, and it is why titles could not be generated.
    """
    headers = {"content-type": "application/json", "x-request-id": request_id}
    if trace_id:
        headers["traceparent"] = new_traceparent(trace_id)
    if credential:
        headers["authorization"] = f"Bearer {credential}"
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
