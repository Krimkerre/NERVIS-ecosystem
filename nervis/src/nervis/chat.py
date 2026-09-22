"""Conversations, and the rule that keeps them a client rather than a copy (§7).

**NERVIS chat is a normal client of RAVIS's published OpenAI-compatible API.**
Not the management API, not a private channel: the same `/v1/chat/completions`
Clarvis uses, addressing the pools RAVIS already publishes. That is what makes
ordinary chat a RAVIS debugging tool (§7.1) rather than a second implementation
of routing with its own opinions.

**It is not Clarvis chat.** §7 is emphatic: no workspace, no tools, no gates, no
agent role, and not implicitly allowed to modify files. Nothing here carries any
of those, and the absence is the feature.

What is stored is §7.2's list and nothing beyond it — conversation ID, title,
timestamps, messages, RAVIS route IDs — local only, and deletable.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from nervis.storage import Database

# How much of a first message becomes a conversation's title when nobody has
# named it. §7 wants titles generated as a RAVIS *background call* carrying
# §9.6.1's marker — and RAVIS does not honour that marker yet, so generating one
# through `ravis/auto` would route a string nobody reads as real work and could
# select a paid model for it. §7 states the trade outright: "an untitled
# conversation is a smaller failure than a title billed to a frontier model."
# Truncating locally costs nothing and claims nothing.
TITLE_LENGTH = 48


@dataclass(frozen=True)
class Message:
    """One turn, with the handle that explains it.

    `route_decision_id` is a handle rather than a copy of the explanation.
    RAVIS already recorded why it chose what it chose, and storing a second copy
    here would eventually disagree with the first — §2.1's rule about not owning
    another service's data, at the grain of a single field.
    """

    message_id: str
    role: str
    content: str
    model: str = ""
    profile: str = ""
    request_id: str = ""
    route_decision_id: str = ""
    interrupted: bool = False
    created_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
            "model": self.model,
            "profile": self.profile,
            "request_id": self.request_id,
            "route_decision_id": self.route_decision_id,
            "interrupted": self.interrupted,
            "created_at": self.created_at,
        }


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def start_conversation(database: Database, *, profile: str, title: str = "") -> str:
    conversation_id = new_id()
    with database.connection as connection:
        connection.execute(
            "INSERT INTO chat_conversation (conversation_id, title, profile) VALUES (?, ?, ?)",
            (conversation_id, title, profile),
        )
    return conversation_id


def conversations(database: Database) -> list[dict[str, Any]]:
    """Every conversation, newest activity first, with its message count.

    Counted rather than stored. A denormalised count is a number that drifts the
    first time a delete misses it, and this table will never be large enough for
    the count to cost anything worth that risk.
    """
    rows = database.connection.execute(
        """
        SELECT c.conversation_id, c.title, c.profile, c.created_at, c.updated_at,
               (SELECT COUNT(*) FROM chat_message m
                 WHERE m.conversation_id = c.conversation_id) AS messages
          FROM chat_conversation c
         ORDER BY c.updated_at DESC, c.created_at DESC
        """
    )
    return [dict(row) for row in rows]


#: How many conversations one search answers with. A drawer is scrolled, not paged, and a
#: search that returns everything it matched on a common word is a wall rather than an answer.
SEARCH_LIMIT = 60

#: How much of the matching turn to show, and how much of it to put before the match.
SNIPPET_LENGTH = 160
SNIPPET_LEAD = 40


def _snippet(content: str, term: str) -> str:
    """The matching turn, cut around the match so the word somebody searched for is visible."""
    flat = " ".join(str(content or "").split())
    at = flat.lower().find(term.lower())
    if at < 0:
        return flat[:SNIPPET_LENGTH] + ("…" if len(flat) > SNIPPET_LENGTH else "")
    start = max(0, at - SNIPPET_LEAD)
    piece = flat[start:start + SNIPPET_LENGTH]
    return ("…" if start else "") + piece + ("…" if start + SNIPPET_LENGTH < len(flat) else "")


def search(database: Database, term: str, limit: int = SEARCH_LIMIT) -> list[dict[str, Any]]:
    """Conversations whose title or turns contain `term`, newest activity first.

    **Asked of NERVIS rather than of the browser**, which is the whole point: a browser
    remembers the last fifty conversations and NERVIS holds every one, including those brought
    over from another computer. Searching the list on screen would search the wrong thing.

    A plain `LIKE`, with `%` and `_` escaped so a search for "100%" is a search for "100%" —
    not a pattern. Which is also why this is not a regular expression: somebody typing into a
    box in a drawer is typing words, not syntax.
    """
    wanted = str(term or "").strip()
    if not wanted:
        return []
    pattern = "%" + wanted.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = database.connection.execute(
        """
        SELECT c.conversation_id, c.title, c.created_at, c.updated_at,
               (SELECT COUNT(*) FROM chat_message m
                 WHERE m.conversation_id = c.conversation_id) AS messages,
               (SELECT COUNT(*) FROM chat_message m
                 WHERE m.conversation_id = c.conversation_id
                   AND m.content LIKE ? ESCAPE '\\') AS hits,
               (SELECT m.content FROM chat_message m
                 WHERE m.conversation_id = c.conversation_id
                   AND m.content LIKE ? ESCAPE '\\'
                 ORDER BY m.created_at, m.rowid LIMIT 1) AS matched
          FROM chat_conversation c
         WHERE c.title LIKE ? ESCAPE '\\'
            OR EXISTS (SELECT 1 FROM chat_message m
                        WHERE m.conversation_id = c.conversation_id
                          AND m.content LIKE ? ESCAPE '\\')
         ORDER BY c.updated_at DESC, c.created_at DESC
         LIMIT ?
        """,
        (pattern, pattern, pattern, pattern, max(1, int(limit))),
    )
    return [{"conversation_id": row["conversation_id"], "title": row["title"],
             "created_at": row["created_at"], "updated_at": row["updated_at"],
             "messages": row["messages"], "hits": row["hits"],
             "snippet": _snippet(row["matched"] or "", wanted) if row["matched"] else ""}
            for row in rows]


#: The conversations barred from being remembered — the "Private" mark on a conversation.
#:
#: **One list, read in one place, honoured by every path that remembers.** It was written for
#: the persona digest and read only by it: `recall.py` filtered on "not the conversation I am
#: in" and nothing else, so with cross-conversation memory switched on a conversation somebody
#: had marked private was still searched and still quotable into a different conversation
#: (found while inventorying memory, 23 September 2026). A bar that applies to one of the ways
#: a thing is remembered is not a bar.
MEMORY_EXCLUDED_SETTING = "chat.memory_excluded"


def barred(database: Database) -> set[str]:
    """Conversation ids barred from being remembered. Empty when absent or unreadable.

    **Failing to *empty* rather than to everything** is deliberate, and is the less obvious
    direction: a store that cannot be read should not silently bar every conversation, because
    that turns a corrupt setting into "memory quietly stopped working" — which nobody reports.
    A conversation somebody meant to bar is visibly still listed on the screen that bars it,
    so the other failure is one a person can see.
    """
    row = database.connection.execute(
        "SELECT value FROM setting WHERE key = ?", (MEMORY_EXCLUDED_SETTING,)
    ).fetchone()
    if not row:
        return set()
    try:
        found = json.loads(row["value"])
    except (TypeError, ValueError):
        return set()
    return {str(one) for one in found} if isinstance(found, list) else set()


def messages(database: Database, conversation_id: str) -> list[Message]:
    rows = database.connection.execute(
        "SELECT * FROM chat_message WHERE conversation_id = ? ORDER BY created_at, rowid",
        (conversation_id,),
    )
    return [
        Message(
            message_id=row["message_id"],
            role=row["role"],
            content=row["content"],
            model=row["model"],
            profile=row["profile"],
            request_id=row["request_id"],
            route_decision_id=row["route_decision_id"],
            interrupted=bool(row["interrupted"]),
            created_at=row["created_at"],
        )
        for row in rows
    ]


def exists(database: Database, conversation_id: str) -> bool:
    row = database.connection.execute(
        "SELECT 1 FROM chat_conversation WHERE conversation_id = ?", (conversation_id,)
    ).fetchone()
    return row is not None


def placeholder_title(content: str) -> str:
    """The stand-in title `append` writes for an unnamed conversation.

    A function rather than an expression inlined below, because two places now
    need to agree on it: this one writes it, and the background titler asks
    whether the stored title *is* one before replacing it. A generated title
    should replace a truncation and must never overwrite a name a person chose,
    and comparing against a duplicated `[:TITLE_LENGTH]` elsewhere would make
    that answer wrong the day either copy changed.
    """
    return content[:TITLE_LENGTH].strip()


def append(database: Database, conversation_id: str, message: Message) -> Message:
    """Store one turn and mark the conversation as active.

    The title is filled from the first user message when nobody has set one —
    locally, by truncation, never by a model call. See `TITLE_LENGTH`. A real
    title may replace this one later, as a RAVIS background call; a name typed
    by a person may not be replaced at all.
    """
    with database.connection as connection:
        connection.execute(
            """
            INSERT INTO chat_message (message_id, conversation_id, role, content, model,
                                      profile, request_id, route_decision_id, interrupted)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.message_id, conversation_id, message.role, message.content,
                message.model, message.profile, message.request_id,
                message.route_decision_id, int(message.interrupted),
            ),
        )
        connection.execute(
            "UPDATE chat_conversation SET updated_at = datetime('now') WHERE conversation_id = ?",
            (conversation_id,),
        )
        if message.role == "user":
            connection.execute(
                """
                UPDATE chat_conversation SET title = ?
                 WHERE conversation_id = ? AND title = ''
                """,
                (placeholder_title(message.content), conversation_id),
            )
    return message


def rename(database: Database, conversation_id: str, title: str) -> None:
    with database.connection as connection:
        connection.execute(
            "UPDATE chat_conversation SET title = ?, updated_at = datetime('now') "
            "WHERE conversation_id = ?",
            (title, conversation_id),
        )


def delete(database: Database, conversation_id: str) -> bool:
    """§7.2 requires deletion to work. Messages go with it, by cascade."""
    with database.connection as connection:
        cursor = connection.execute(
            "DELETE FROM chat_conversation WHERE conversation_id = ?", (conversation_id,)
        )
    return cursor.rowcount > 0


def history(database: Database, conversation_id: str) -> list[dict[str, str]]:
    """Prior turns in the shape RAVIS's API expects.

    Interrupted assistant turns are **included**. What the user saw is part of
    the conversation whether or not it finished, and silently dropping it would
    make the model answer as though its own half-sentence had never happened.
    """
    return [
        {"role": message.role, "content": message.content}
        for message in messages(database, conversation_id)
        if message.content
    ]


def turn_for_request(database: Database, request_id: str) -> dict[str, str] | None:
    """The question and answer behind one RAVIS request, or nothing.

    M11's inspector needs the content stages §11.4 asks for, and RAVIS publishes
    none — it records the decision, never the messages. Where NERVIS was the
    client the content is *its own*, already stored and already on the chat
    screen, so showing it in the inspector exposes nothing new.

    The assistant turn carries the `request_id`; the question is the user turn
    immediately before it in the same conversation. Matched by position rather
    than by a second id, because a user message has no request of its own —
    it is the thing the request was made *about*.

    Returns nothing for a request NERVIS did not make, which is the ordinary
    case for a Clarvis request and is reported as such rather than as an empty
    conversation.
    """
    if not request_id:
        return None
    answer = database.connection.execute(
        "SELECT conversation_id, content, created_at FROM chat_message "
        "WHERE request_id = ? AND role = 'assistant' ORDER BY created_at DESC LIMIT 1",
        (request_id,),
    ).fetchone()
    if answer is None:
        return None
    question = database.connection.execute(
        "SELECT content FROM chat_message WHERE conversation_id = ? AND role = 'user' "
        "AND created_at <= ? ORDER BY created_at DESC LIMIT 1",
        (answer["conversation_id"], answer["created_at"]),
    ).fetchone()
    return {
        "request": question["content"] if question else "",
        "response": answer["content"],
        "conversation_id": answer["conversation_id"],
    }
