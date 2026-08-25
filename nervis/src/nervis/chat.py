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


def append(database: Database, conversation_id: str, message: Message) -> Message:
    """Store one turn and mark the conversation as active.

    The title is filled from the first user message when nobody has set one —
    locally, by truncation, never by a model call. See `TITLE_LENGTH`.
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
                (message.content[:TITLE_LENGTH].strip(), conversation_id),
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
