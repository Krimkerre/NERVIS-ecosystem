"""What became of each offer, and what the next one may say about it (M22).

**The gap this closes.** NERVIS proposes, a person presses a button, and until
now that was the end of it: the same offer was composed identically the tenth
time it was declined as the first. Nothing was learned because nothing was
written down.

**Three outcomes and no fourth.** `accepted` and `declined` are a person
answering. `edited` is a person answering *differently* — they took the offer
but changed its target — and it is the only one of the three that carries what
they actually wanted, which is why `edited_to` exists.

**Silence is not one of them.** M22 says so outright: somebody who closed the
tab did not decline. So an offer nobody answered has no row here, and that
absence is the honest record rather than a gap to be filled in. It is also why
the id is minted when the offer is *made*: the answer has to have something to
be filed against, and an offer that is never answered simply never arrives.

**What is learned is shown where it is used.** `history()` returns counts and a
sentence, and the sentence is rendered on the offer itself. Nothing here silently
changes what NERVIS proposes — a preference the person cannot see is one they
cannot argue with, and M22 rules it out in as many words. Where the record would
change a default, the offer says so and still offers it.

**And it is erasable.** `forget()` empties the table, after which every proposal
is composed exactly as it was before any of this existed, because history is only
ever read to *decorate* an offer, never to build one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from nervis.storage import Database

ACCEPTED = "accepted"
DECLINED = "declined"
EDITED = "edited"
OUTCOMES = (ACCEPTED, DECLINED, EDITED)

# Below this, an offer's past is not worth a sentence on it. One decline is a
# person who did not want it that once; the point at which it starts being a
# pattern is the point at which saying so stops being noise.
NOTEWORTHY_DECLINES = 2


@dataclass(frozen=True)
class History:
    """What has happened to this exact offer before, and how to say it."""

    accepted: int = 0
    declined: int = 0
    edited: int = 0
    # The target somebody last changed this offer to, if they ever did. The
    # single most useful thing in the record: it is what they wanted when NERVIS
    # guessed wrong.
    last_edited_to: str = ""

    @property
    def answered(self) -> int:
        return self.accepted + self.declined + self.edited

    def sentence(self) -> str:
        """One line for the offer card, or nothing.

        Deliberately descriptive rather than predictive. "You declined this
        twice" is a fact the person can check; "you probably do not want this"
        is NERVIS having an opinion about them.
        """
        if self.last_edited_to:
            return f"last time you changed this to {self.last_edited_to}"
        if self.declined >= NOTEWORTHY_DECLINES and not self.accepted:
            return f"you have declined this {self.declined} times"
        if self.accepted and not self.declined:
            return f"you have accepted this {self.accepted} time" + (
                "s" if self.accepted > 1 else ""
            )
        return ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "declined": self.declined,
            "edited": self.edited,
            "last_edited_to": self.last_edited_to,
            "answered": self.answered,
            "sentence": self.sentence(),
        }


def record(
    database: Database,
    *,
    proposal_id: str,
    operation: str,
    outcome: str,
    target: str = "",
    edited_to: str = "",
    conversation_id: str = "",
) -> None:
    """File one answer, refusing an outcome that is not one of the three.

    `INSERT OR REPLACE` rather than a plain insert: pressing Run twice on a slow
    connection is one decision, not two, and counting it twice would make the
    record say the person was keener than they were.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"{outcome!r} is not one of {OUTCOMES}")
    if outcome == EDITED and not edited_to.strip():
        # An edit that does not say what it was edited to records that something
        # changed while throwing away the only part worth keeping.
        raise ValueError("an edited outcome must say what it was changed to")
    if not proposal_id.strip() or not operation.strip():
        raise ValueError("an outcome must name the proposal and its operation")
    with database.connection as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO proposal_outcome
                (proposal_id, operation, target, outcome, edited_to,
                 conversation_id, decided_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal_id.strip(), operation.strip(), target, outcome,
                edited_to.strip(), conversation_id, _now(),
            ),
        )


def history(database: Database, operation: str, target: str = "") -> History:
    """What happened the last times this exact offer was made.

    Keyed on operation *and* target, because "you declined this" is only true of
    the thing that was declined. Having refused to benchmark one model says
    nothing about another, and a record that generalised would be NERVIS
    inventing a preference nobody expressed.
    """
    rows = database.connection.execute(
        "SELECT outcome, edited_to FROM proposal_outcome "
        "WHERE operation = ? AND target = ? ORDER BY decided_at",
        (operation, target),
    ).fetchall()
    counts = {name: 0 for name in OUTCOMES}
    edited_to = ""
    for row in rows:
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
        if row["outcome"] == EDITED and row["edited_to"]:
            edited_to = row["edited_to"]
    return History(
        accepted=counts[ACCEPTED], declined=counts[DECLINED],
        edited=counts[EDITED], last_edited_to=edited_to,
    )


def recent(database: Database, limit: int = 100) -> list[dict[str, Any]]:
    """The record itself, newest first — what a person clearing it is clearing.

    A control that erases something must be able to show what it is about to
    erase, or "clear" is a button people press hopefully.
    """
    rows = database.connection.execute(
        "SELECT * FROM proposal_outcome ORDER BY decided_at DESC LIMIT ?",
        (max(1, min(int(limit), 1000)),),
    ).fetchall()
    return [dict(row) for row in rows]


def forget(database: Database) -> int:
    """Erase the whole record, returning how many answers went.

    All of it, never a slice. A partial forget leaves a record whose shape is
    itself a preference — the ones somebody chose to keep — and nobody can see
    that from an offer card.
    """
    with database.connection as connection:
        cursor = connection.execute("DELETE FROM proposal_outcome")
    return int(cursor.rowcount or 0)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
