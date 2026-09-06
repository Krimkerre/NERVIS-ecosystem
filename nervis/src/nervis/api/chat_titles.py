"""Naming a conversation, and refusing to when the name would be a lie.

A title is written twice over: once from the opening question, immediately, so
the list is never full of "New conversation"; and once by a model, in the
background, when there is enough of an exchange to summarise.

**Most of this file is the second one declining.** A model asked for a title
answers with a sentence *about* the task — "Okay, let's tackle this user query"
was the stored title on this machine — and every rule here was written against
something observed in a real conversation list rather than imagined. An
untitled conversation is a smaller failure than a wrong title, and a much
smaller one than a title billed to a frontier model.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from pathlib import Path
from typing import Any

import httpx
from ecosystem_protocol import new_request_id
from fastapi import Request

from nervis import chat as store
from nervis import documents
from nervis.api.chat_calls import _forwarded
from nervis.registry import RegistryEntry

#: How long a title call may take. Generous next to `FACTS_TIMEOUT_SECONDS`,
#: because nobody is waiting on it — and bounded, because it is unattended.
TITLE_TIMEOUT_SECONDS = 30.0

def _title_later(
    request: Request, conversation_id: str, trace_id: str, served: str = ""
) -> None:
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
            _generate_title(request, conversation_id, opening, trace_id, served)
        )

def _stored_title(database: Any, conversation_id: str) -> str:
    """This conversation's own title, or empty on the turn that creates it.

    Empty is ordinary rather than a failure: the first message of a conversation
    is proposed against before the conversation is stored, so the caller falls
    back to the question itself — which is what the title will be taken from
    anyway.
    """
    if not conversation_id:
        return ""
    return next(
        (str(row.get("title") or "") for row in store.conversations(database)
         if row["conversation_id"] == conversation_id),
        "",
    )


def _attachment_title(root: str, conversation_id: str) -> str:
    """The newest attachment's own name, for a save offer with nothing typed.

    A person who fed chat a file and asked for changes to it has already named
    the thing better than a conversation title ever will — "annotated" says
    what happened to it without inventing a word for what it is.
    """
    place = documents.attachment_dir(Path(root), conversation_id) if root else None
    if place is None:
        return ""
    found = documents.list_files(place)
    return f"{Path(found[0].name).stem}-annotated" if found else ""


def _reply_title(database: Any, conversation_id: str) -> str:
    """The last reply's own opening line, for a document made from nothing.

    Still NERVIS's own record, not a model asked mid-request to name a file:
    the reply already exists, already reviewed by the person before they said
    save it, and its first line is usually the heading it gave the thing anyway.
    """
    written = [
        m for m in store.messages(database, conversation_id)
        if m.role in ("clarvis", "assistant")
    ]
    if not written:
        return ""
    opening = next((line.strip() for line in written[-1].content.splitlines() if line.strip()), "")
    return opening.lstrip("#").strip()


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


# Where a title goes when the model that answered is not known.
#
# **The model that answered is asked first, and this is the fallback.** Naming
# `ravis/cheap` used to be the whole policy, on the reading that a title is not
# worth money — and in a local deployment that reading has a cost the accounting
# does not show. `ravis/cheap` has a $0 ceiling, so it admits only local models,
# and the size tiebreak picked a 2.4B build that was not the one already in
# memory. Two models resident to answer one question and name it, and the second
# one loaded to produce twenty-four tokens that were then discarded, because it
# was a reasoning build that spent the budget thinking.
#
# Reusing the answering model costs a fraction of a cent on a hosted route and
# nothing at all on a local one, since it is already loaded. That is a better
# trade than a free call that loads a second model.
TITLE_POOL = "ravis/cheap"

# Short, because a title is a title. A model that needs more than this is
# writing a summary, and NERVIS.md §7 asks for a title.
#
# It is also the reason a reasoning model cannot do this job: the budget goes on
# thinking and the answer never arrives. `_title_from` throws the truncation
# away rather than storing it, so the conversation keeps its stand-in — which is
# the correct outcome and still a wasted call. Reusing the answering model does
# not change that; it means the failure needs no second model in memory.
TITLE_MAX_TOKENS = 24

TITLE_PROMPT = (
    "Write a short title, at most six words, for a conversation that begins "
    "with the message below. Reply with the title alone: no quotes, no "
    "punctuation at the end, no preamble.\n\n"
)

async def _generate_title(
    request: Request, conversation_id: str, opening: str, trace_id: str, served: str = ""
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
        # **The model that just answered, by name — not a pool.** A pool is a
        # request for RAVIS to choose, and choosing is exactly what put a second
        # model in memory: the answer came from one build and the title from
        # another, both resident, for one turn of conversation. Naming the
        # served model asks for the one already loaded.
        #
        # The pool is the fallback for the case where nothing was served — an
        # interrupted first turn, or a store that recorded no model.
        "model": served or TITLE_POOL,
        "max_tokens": TITLE_MAX_TOKENS,
        "messages": [{"role": "user", "content": TITLE_PROMPT + opening[:600]}],
    }
    if not served:
        # RAVIS §9.6.1's declared marker, on the fallback path only. Never
        # inferred by RAVIS from the shape of a request, which is why the client
        # has to say it.
        #
        # **It cannot be sent alongside a named model, and the reason is the
        # marker doing its job.** §9.6.1 makes a background call refuse any
        # provider not known to be free — "declared a background call, and
        # {provider} is not known to be free" — so a title pinned to the hosted
        # model that just answered would be excluded by the very marker meant to
        # protect it, and RAVIS would route to a free local build instead. That
        # is the second model load this change exists to stop.
        #
        # So the marker guards the case where NERVIS does not know what answered
        # and has to let RAVIS choose. Where it does know, the choice is already
        # made and there is nothing to protect against: the model is loaded, the
        # call is sixty-six tokens, and reusing it is cheaper in memory than any
        # free alternative that is not already resident.
        payload["metadata"] = {"background": True}
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


# How a model starts a sentence *about* the task instead of doing it. Every one
# of these was observed in this conversation list rather than imagined: the
# stored title on this machine was "Okay, let\'s tackle this user query. They
# want a short title for a conversation s".
_NOT_A_TITLE = (
    "okay", "ok,", "sure", "certainly", "here", "here's", "the user", "user wants",
    "let's", "let me", "we need", "i need", "i'll", "first,", "alright", "title:",
    "hmm", "so,", "this conversation", "based on",
)

# Reasoning models put their thinking in the content, fenced. Removed rather
# than reasoned about: what is inside is not the answer, and a title budget of
# twenty-four tokens is spent before the model reaches one.
_THINKING = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.IGNORECASE | re.DOTALL)
_UNCLOSED_THINKING = re.compile(r"<(think|thinking|reasoning)>.*", re.IGNORECASE | re.DOTALL)


def opening_title(question: str) -> str:
    """A conversation name taken from its first message.

    Not a summary and not trying to be. Six words of what the person actually
    asked identifies a conversation in a list better than a model's guess at a
    theme, and it is available the instant the conversation exists — which is
    the difference between a list of names and a list of "New conversation".
    """
    words = " ".join(str(question or "").split())[:200].split(" ")
    # A question mark is kept: "how is RAVIS doing today?" is a better name for
    # a conversation than the same words with the question filed off. Only the
    # punctuation that reads as a fragment is trimmed.
    title = " ".join(words[:6]).strip(" ,.;:-—\"'")
    return (title + ("…" if len(words) > 6 else ""))[:80]


def _title_from(body: dict[str, Any]) -> str:
    """The title in a completion — or nothing, when what came back is not one.

    **Checked, not merely trimmed.** The old version cleaned quotes and a
    "Title:" prefix and stored whatever remained, which on a reasoning model is
    its thinking: `ravis/cheap` admits models that spend most of their output
    reasoning, and with twenty-four tokens to work in they never reach the
    title at all. RAVIS's own route explanation had already noticed the shape of
    this — it ranked one candidate lower for "spending 99% of its output on
    reasoning, which at max_tokens=24 leaves about 0 tokens for the answer" —
    and NERVIS stored the answer from the next one along anyway.

    An empty return is not a failure here. The conversation already carries a
    name taken from its opening message, and keeping that is strictly better
    than replacing it with a sentence about the request.
    """
    choices = body.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    text = str(message.get("content") or "")
    text = _THINKING.sub(" ", text)
    # An unclosed block means the budget ran out mid-thought. Everything after
    # the opening tag is thinking, and there is no title behind it.
    text = _UNCLOSED_THINKING.sub(" ", text)
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    line = line.removeprefix("Title:").strip().strip("\"'").strip()
    if not _is_a_title(line):
        return ""
    return line[:80]


def _is_a_title(line: str) -> bool:
    """Whether a line is a name for a conversation rather than talk about one."""
    if not line or len(line) < 3:
        return False
    lowered = line.lower()
    if lowered.startswith(_NOT_A_TITLE):
        return False
    # Six words was the instruction; twelve is the generous reading of it. Past
    # that it is a sentence, and a sentence in this column is what the person
    # reported as broken.
    return len(line.split()) <= 12
