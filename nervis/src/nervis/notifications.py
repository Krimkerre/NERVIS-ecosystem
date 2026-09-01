"""The notification centre — things NERVIS wants to tell the user (M21).

**A record that outlives the reader.** Everything NERVIS notices today reaches
the user only if the user happens to be present: the status bar repaints, the
voice announces, and both are gone the moment the tab is closed. That is fine
for a glance and useless for "what happened while I was out". This is the store
that answers the second question, and from M22 onward it is also where
proposals, plans and background work post what they produced.

**Three rules, and each of them is one of M21's exit criteria.**

*Every note says why it exists.* `reason` is mandatory and `post` refuses an
empty one, the same discipline §4.1 puts on a capability state. Without it a
notification centre becomes a place things appear, and a person who cannot tell
news from noise stops reading either.

*A note produced by a model says which model and what it cost.* The two travel
together or not at all — `post` refuses one without the other. A model's output
that does not name its author reads exactly like something NERVIS observed
directly, and from M25 those are very different claims.

*Dismissing is per-note.* There is deliberately no "dismiss all of these", and
no per-kind mute. Silencing a class is how a person stops seeing the one that
mattered, and the fix for a noisy producer is the producer.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from nervis.storage import Database

# How long a dismissed note is kept before the sweep drops it. Dismissed rather
# than all: an undismissed note is still waiting for the user however old it is,
# and expiring those would mean a fortnight away silently emptied the centre.
DISMISSED_RETENTION_DAYS = 30

# What the screen asks for when it does not say. Enough to scroll, small enough
# that the endpoint stays a single indexed read.
DEFAULT_LIMIT = 50
MAX_LIMIT = 500


@dataclass(frozen=True)
class Notification:
    """One thing worth telling somebody, and its provenance."""

    note_id: str
    kind: str
    title: str
    reason: str
    body: str = ""
    severity: str = "info"
    source: str = ""
    # Empty unless a model produced this. Enforced as a pair by `post`.
    model: str = ""
    cost: str = ""
    created_at: str = field(default_factory=lambda: _now())
    read_at: str = ""
    dismissed_at: str = ""

    @property
    def unread(self) -> bool:
        return not self.read_at and not self.dismissed_at

    def as_dict(self) -> dict[str, Any]:
        return {
            "note_id": self.note_id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "reason": self.reason,
            "severity": self.severity,
            "source": self.source,
            # Reported as a nested object rather than two loose fields, so a
            # reader can ask "did a model write this" in one test instead of
            # inferring it from a truthy string.
            "produced_by": (
                {"model": self.model, "cost": self.cost} if self.model else None
            ),
            "created_at": self.created_at,
            "read_at": self.read_at,
            "dismissed_at": self.dismissed_at,
            "unread": self.unread,
        }


def post(
    database: Database,
    *,
    kind: str,
    title: str,
    reason: str,
    body: str = "",
    severity: str = "info",
    source: str = "",
    model: str = "",
    cost: str = "",
) -> Notification:
    """File a note, refusing one that cannot account for itself.

    The refusals are `ValueError` rather than a logged warning on purpose: both
    are programming errors in a caller inside this codebase, and a note that
    silently lost its provenance is worse than a producer that fails loudly the
    first time it is run.
    """
    if not reason.strip():
        raise ValueError("a notification must say why it exists")
    if not title.strip():
        raise ValueError("a notification must have a title")
    # The pair, in both directions. A cost with no model names a bill with no
    # author, which is the same defect wearing the other shoe.
    if bool(model.strip()) != bool(cost.strip()):
        raise ValueError(
            "a model-produced notification must name both the model and its cost"
        )
    note = Notification(
        note_id="nt_" + uuid.uuid4().hex[:12],
        kind=kind,
        title=title.strip(),
        reason=reason.strip(),
        body=body,
        severity=severity,
        source=source,
        model=model.strip(),
        cost=cost.strip(),
    )
    with database.connection as connection:
        connection.execute(
            """
            INSERT INTO notification
                (note_id, kind, title, body, reason, severity, source,
                 model, cost, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note.note_id, note.kind, note.title, note.body, note.reason,
                note.severity, note.source, note.model, note.cost, note.created_at,
            ),
        )
    return note


def recent(
    database: Database, limit: int = DEFAULT_LIMIT, *, include_dismissed: bool = False
) -> list[Notification]:
    """Newest first. Dismissed notes are hidden but not deleted.

    Kept rather than deleted because "I dealt with this" and "this never
    happened" are different, and only the first one is true.
    """
    clause = "" if include_dismissed else "WHERE dismissed_at = ''"
    rows = database.connection.execute(
        f"SELECT * FROM notification {clause} ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (max(1, min(int(limit), MAX_LIMIT)),),
    ).fetchall()
    return [_from_row(row) for row in rows]


def unread_count(database: Database) -> int:
    """What the badge shows: neither read nor dismissed."""
    row = database.connection.execute(
        "SELECT COUNT(*) FROM notification WHERE read_at = '' AND dismissed_at = ''"
    ).fetchone()
    return int(row[0]) if row else 0


def mark_read(database: Database, note_id: str) -> bool:
    """One note, by id. False if there is no such note.

    **No `mark_all_read`.** It is the bulk silence M21 forbids wearing a gentler
    name: the badge goes to zero and every unseen note goes with it.
    """
    return _stamp(database, note_id, "read_at")


def dismiss(database: Database, note_id: str) -> bool:
    """One note, by id. False if there is no such note."""
    return _stamp(database, note_id, "dismissed_at")


def prune_dismissed(database: Database, *, days: int = DISMISSED_RETENTION_DAYS) -> int:
    """Drop dismissed notes older than `days`, returning how many went.

    On the probe timer with the hub's own retention, for the same reason: a
    bounded DELETE against an indexed column does not need a scheduler.
    """
    with database.connection as connection:
        cursor = connection.execute(
            "DELETE FROM notification "
            "WHERE dismissed_at != '' AND created_at < datetime('now', ?)",
            (f"-{int(days)} days",),
        )
    return int(cursor.rowcount or 0)


def _stamp(database: Database, note_id: str, column: str) -> bool:
    # The column name is interpolated because SQLite cannot parameterise one,
    # and it is safe here because both call sites pass a literal — there is no
    # path from a request body to this argument.
    with database.connection as connection:
        cursor = connection.execute(
            f"UPDATE notification SET {column} = ? WHERE note_id = ? AND {column} = ''",
            (_now(), note_id),
        )
        if cursor.rowcount:
            return True
    # Nothing updated means either no such note or one already stamped. The
    # second is a success from the caller's point of view — dismissing a
    # dismissed note is what a second click on a slow connection looks like.
    row = database.connection.execute(
        "SELECT 1 FROM notification WHERE note_id = ?", (note_id,)
    ).fetchone()
    return row is not None


def _from_row(row: sqlite3.Row) -> Notification:
    return Notification(
        note_id=row["note_id"],
        kind=row["kind"],
        title=row["title"],
        body=row["body"],
        reason=row["reason"],
        severity=row["severity"],
        source=row["source"],
        model=row["model"],
        cost=row["cost"],
        created_at=row["created_at"],
        read_at=row["read_at"],
        dismissed_at=row["dismissed_at"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
