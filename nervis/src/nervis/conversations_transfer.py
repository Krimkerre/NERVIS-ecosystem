"""Carrying conversations from the owner's other computer to this one.

*"What I'm also missing is conversation history — that was the main thing I wanted to sync"*
(22 September 2026). Settings were the easy half; this is the half that matters.

**A pull somebody asks for, one conversation at a time.** The same decision as for settings,
for a sharper reason: a mirror has to answer what happens when a conversation is deleted on
one computer, and both answers are wrong — it comes back, or the deletion spreads and takes
the only copy with it. Asking, seeing the list, ticking what you want and pressing a button
has neither, and it is also how somebody actually thinks about this: *that* conversation,
not "everything since Tuesday".

**Nothing is overwritten, ever.** Conversations and messages carry the identifiers they were
born with — `uuid4`, so two computers cannot mint the same one — and every insert here is an
`INSERT OR IGNORE`. Bringing the same conversation twice adds nothing; a conversation that
has grown on the other computer since gains only the turns this one has never seen. What is
here is never edited and never deleted by a pull.

**Times come across as they were.** `chat.append` stamps "now", which is right for a turn
being spoken and wrong for one being carried: a conversation from Sunday must not arrive
looking like it happened during the import. So this writes `created_at` itself.
"""

from __future__ import annotations

from typing import Any

from nervis.chat import conversations as our_conversations
from nervis.chat import messages as our_messages
from nervis.storage.database import Database

#: What a conversation carries across, beyond its turns. `profile` is which persona it was
#: held with — a name, not a machine path, so it means the same on both computers.
CONVERSATION_FIELDS = ("conversation_id", "title", "profile", "created_at", "updated_at")

#: What one turn carries. Everything the table holds except which conversation it belongs to,
#: which the transfer already knows.
MESSAGE_FIELDS = ("message_id", "role", "content", "model", "profile", "request_id",
                  "route_decision_id", "interrupted", "created_at")


def listing(database: Database) -> list[dict[str, Any]]:
    """Every conversation here, with its message count and no message bodies.

    The list is what a screen shows before anybody chooses, and a history of hundreds of
    conversations would be megabytes if each one arrived whole just to be counted.
    """
    return [{**{field: row.get(field) for field in CONVERSATION_FIELDS},
             "messages": row.get("messages", 0)}
            for row in our_conversations(database)]


def one(database: Database, conversation_id: str) -> dict[str, Any] | None:
    """One conversation with its turns, or None when there is no such conversation."""
    found = next((row for row in our_conversations(database)
                  if row["conversation_id"] == conversation_id), None)
    if found is None:
        return None
    return {
        "conversation": {field: found.get(field) for field in CONVERSATION_FIELDS},
        "messages": [{field: getattr(message, field) for field in MESSAGE_FIELDS}
                     for message in our_messages(database, conversation_id)],
    }


def differences(theirs: list[dict[str, Any]], ours: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What a pull would add, one row per conversation, in a shape a screen can render.

    `state` is `new` for a conversation this computer has never seen and `more` for one it
    has with fewer turns than the other. Conversations that already match are left out and
    counted instead — and a conversation that is *ahead* here is not a difference, because a
    pull only ever adds.
    """
    held = {row.get("conversation_id"): row for row in ours}
    changes: list[dict[str, Any]] = []
    for row in theirs:
        mine = held.get(row.get("conversation_id"))
        theirs_count = int(row.get("messages") or 0)
        if mine is None:
            changes.append({**row, "state": "new", "ours": 0, "adds": theirs_count})
        elif theirs_count > int(mine.get("messages") or 0):
            changes.append({**row, "state": "more", "ours": int(mine.get("messages") or 0),
                            "adds": theirs_count - int(mine.get("messages") or 0)})
    return changes


def _text(value: Any, limit: int = 4096) -> str:
    return str(value or "")[:limit] if not isinstance(value, str) else value[:limit]


def take(database: Database, payload: Any) -> dict[str, Any]:
    """Store one conversation that came from another computer. Adds only.

    Returns what it actually added, which is not the same as what it was given: a second
    pull of the same conversation adds nothing and says so, rather than reporting success
    for work it did not do.
    """
    if not isinstance(payload, dict):
        return {"ok": False, "detail": "that is not a conversation"}
    conversation = payload.get("conversation")
    messages = payload.get("messages")
    if not isinstance(conversation, dict) or not isinstance(messages, list):
        return {"ok": False, "detail": "that is not a conversation"}
    conversation_id = _text(conversation.get("conversation_id"), 64)
    if not conversation_id:
        return {"ok": False, "detail": "that conversation has no identifier"}
    with database.connection as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO chat_conversation
                        (conversation_id, title, profile, created_at, updated_at)
                 VALUES (?, ?, ?, COALESCE(?, datetime('now')), COALESCE(?, datetime('now')))
            """,
            (conversation_id, _text(conversation.get("title"), 300),
             _text(conversation.get("profile"), 120),
             _text(conversation.get("created_at"), 40) or None,
             _text(conversation.get("updated_at"), 40) or None),
        )
        added = 0
        for message in messages:
            if not isinstance(message, dict):
                continue
            message_id = _text(message.get("message_id"), 64)
            role = _text(message.get("role"), 20)
            if not message_id or role not in ("user", "assistant", "system"):
                continue
            done = connection.execute(
                """
                INSERT OR IGNORE INTO chat_message
                            (message_id, conversation_id, role, content, model, profile,
                             request_id, route_decision_id, interrupted, created_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))
                """,
                (message_id, conversation_id, role, _text(message.get("content"), 1_000_000),
                 _text(message.get("model"), 200), _text(message.get("profile"), 120),
                 _text(message.get("request_id"), 64),
                 _text(message.get("route_decision_id"), 64),
                 int(bool(message.get("interrupted"))),
                 _text(message.get("created_at"), 40) or None),
            )
            added += done.rowcount or 0
    return {"ok": True, "conversation_id": conversation_id, "added": added,
            "title": _text(conversation.get("title"), 300)}
