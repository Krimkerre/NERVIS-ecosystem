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

from nervis import background, documents
from nervis import chat as store
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
    name = _attachment_name(root, conversation_id)
    return f"{Path(name).stem}-annotated" if name else ""


def _attachment_name(root: str, conversation_id: str) -> str:
    """The newest attachment's filename, or nothing — what an annotate offer
    needs to know exists before it can be made at all."""
    place = documents.attachment_dir(Path(root), conversation_id) if root else None
    if place is None:
        return ""
    found = documents.list_files(place)
    return found[0].name if found else ""


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
    heuristic: the stored title either *is* one of the two stand-ins made from
    the first message, or somebody typed it.
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
    # **Either stand-in counts.** A new conversation is created named
    # `opening_title` — the first six words and "…" — while `append` fills an
    # unnamed one with `placeholder_title`. Checking only the second made every
    # conversation that opened with more than six words look hand-named, so it
    # was never titled: 121 of 223 on this machine, on 11 September 2026.
    if stored and stored not in (store.placeholder_title(opening), opening_title(opening)):
        return ""
    return opening


# **Room to finish, not a length for the title.** This was 24, on the reading
# that a title is short — and the budget is what a reasoning model spends
# before it writes anything. Replayed against the running RAVIS on 11 September
# 2026: `ravis/free-api` answered with nothing, cut off at the limit, three
# times in three at 24; at 400 the same pool answered in 1.5 seconds. The title
# itself is still a handful of tokens, and `_title_from` still refuses a sentence.
TITLE_MAX_TOKENS = 400

TITLE_PROMPT = (
    "Write a short title, at most six words, for a conversation that begins "
    "with the message below. Reply with the title alone: no quotes, no "
    "punctuation at the end, no preamble.\n\n"
)

async def _generate_title(
    request: Request, conversation_id: str, opening: str, trace_id: str, served: str = ""
) -> None:
    """Title a conversation with a RAVIS background call (NERVIS.md §7, RAVIS §9.6.1).

    **Where it asks is the operator's choice.** `background.route` walks the pool
    chosen under Settings → Unattended work (`ravis/free-api` unless changed),
    then the model that just answered — already in memory, so a local one costs
    no second load — then this machine's own models. Somebody who wants titles
    private picks `ravis/private` or `ravis/local` there; somebody who wants none
    switches titles off there.

    **Only the pool and the switch are shared with unattended thinking** — not
    its switch, its interval or its daily ceiling, and nothing here is written to
    the ledger those runs are counted from. A title is part of a conversation
    somebody is having, so it is written after the first reply, every time.

    **Every failure is silent, and every call is marked `background`.** An
    untitled conversation is a smaller failure than a title billed to a frontier
    model (NERVIS.md §7): the marker makes RAVIS refuse any model that costs
    money — a hosted one that answered the conversation included — with a 422
    and no tokens billed, and the walk moves on. No credential, no RAVIS, or
    nothing usable from any step leaves the stand-in, which is already right.
    """
    settings = request.app.state.settings
    entry: RegistryEntry | None = request.app.state.registry.get("ravis")
    if not settings.ravis_client_credential or entry is None or not entry.is_usable:
        return
    database = request.app.state.database
    config = background.settings(database)
    if not config.titles:
        return
    for model in background.route(config, served):
        title = await _title_by(request, entry, model, opening, trace_id,
                                background.marker(config))
        if title:
            break
    else:
        return
    if store.exists(database, conversation_id):
        store.rename(database, conversation_id, title)


async def _title_by(
    request: Request, entry: RegistryEntry, model: str, opening: str, trace_id: str,
    said: dict[str, object],
) -> str:
    """One model's title for a conversation, or nothing when it had none to give."""
    client: httpx.AsyncClient = request.app.state.probe_client
    payload = {
        "model": model,
        "max_tokens": TITLE_MAX_TOKENS,
        "messages": [{"role": "user", "content": TITLE_PROMPT + opening[:600]}],
        # The opening of somebody's conversation travels in this body, so what
        # may serve it is declared rather than left to the chain (`background.marker`).
        "metadata": said,
    }
    headers = _forwarded(
        new_request_id(), trace_id, request.app.state.settings.ravis_client_credential
    )
    try:
        response = await client.post(
            entry.declaration.base_url + "/v1/chat/completions",
            json=payload, headers=headers, timeout=TITLE_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            return ""
        return _title_from(response.json())
    except (httpx.HTTPError, ValueError):
        return ""


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
