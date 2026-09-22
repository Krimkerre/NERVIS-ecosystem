"""Keeping a long conversation inside what a model can read.

*"Do we need some form of compaction for longer sessions in NERVIS, like we have here?"*
(22 September 2026). Measured before answering: `chat.history` returned **every** prior turn,
so a 138-turn conversation re-sent all 138 with each new message. Cost and latency growing
with the conversation, and eventually a wall — on a local model, a failure or a silent
truncation rather than a warning.

**What is sent is trimmed; what is stored never is.** Compaction changes the *request*, not
the conversation: every turn stays in the database, in the history drawer, in a search, in an
export. What the model receives is the recent turns in full, and one note holding a summary
of everything before them.

**The line is drawn in characters, not turns.** Ten turns of one word and ten turns of an
essay are not the same thing to a model, and a limit that counts turns is a limit that fails
exactly when somebody pastes something long.

**The summary is rolled, not rewritten.** Each fold is told what the summary says so far and
given only the turns that have happened since, so a conversation that runs all evening costs
one short call now and then rather than a re-reading of the whole thing each time.

**Made in the background, after a reply has been sent** (`api/chat_titles.py` has the same
shape for titles, and the reasons are the same): a summary that had to be written before the
model could answer would put a second call's latency in front of somebody's question. The
consequence is deliberate and bounded — on the one turn where a conversation first outgrows
its budget, the oldest turns are not in that request. From the next turn on, they are, as a
summary; and the whole conversation was never further away than the history drawer.
"""

from __future__ import annotations

import json
from typing import Any

from nervis.chat import history
from nervis.storage.database import Database

#: How much of a conversation travels verbatim, in characters. About three thousand tokens —
#: comfortable for an 8k-context local model once a system prompt, the knowledge NERVIS adds
#: and the reply's own room are counted, and the number a person can be told.
RECENT_BUDGET = 12_000

#: How much new material there has to be before summarising again is worth a call. Below this
#: the older turns simply travel as part of the recent ones.
FOLD_AFTER = 2_000

#: A ceiling on the summary itself, so a conversation that runs for weeks cannot grow a note
#: that eats the budget it was made to protect.
SUMMARY_LIMIT = 2_000


#: The setting behind "shorten long conversations". On unless somebody turns it off, because
#: the alternative for a conversation that outgrows its model is a failure mid-answer.
SWITCH_KEY = "chat.compaction"


def switched_on(database: Database) -> bool:
    row = database.connection.execute(
        "SELECT value FROM setting WHERE key = ?", (SWITCH_KEY,)
    ).fetchone()
    if row is None:
        return True
    try:
        return json.loads(row["value"]) is not False
    except (TypeError, ValueError):
        return True


def split(turns: list[dict[str, Any]], budget: int = RECENT_BUDGET
          ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(older, recent): the turns that must be summarised, and the ones sent as they are.

    Walked from the newest backwards, because the end of a conversation is the part being
    talked about. **The newest turn is always kept**, however long it is: a budget that could
    drop the question being asked would be a budget that breaks the conversation to protect it.
    """
    recent: list[dict[str, Any]] = []
    spent = 0
    for turn in reversed(turns):
        cost = len(str(turn.get("content", "")))
        if recent and spent + cost > budget:
            break
        recent.append(turn)
        spent += cost
    recent.reverse()
    return turns[: len(turns) - len(recent)], recent


def stored_summary(database: Database, conversation_id: str) -> dict[str, Any]:
    """What is already summarised for this conversation, and how far it reaches."""
    row = database.connection.execute(
        "SELECT summary, through_message_id, covered FROM chat_summary WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        return {"summary": "", "through_message_id": "", "covered": 0}
    return {"summary": row["summary"], "through_message_id": row["through_message_id"],
            "covered": row["covered"]}


def remember_summary(database: Database, conversation_id: str, summary: str,
                     through_message_id: str, covered: int) -> None:
    with database.connection as connection:
        connection.execute(
            """
            INSERT INTO chat_summary (conversation_id, summary, through_message_id, covered,
                                      updated_at)
                 VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(conversation_id) DO UPDATE SET
                 summary = excluded.summary,
                 through_message_id = excluded.through_message_id,
                 covered = excluded.covered,
                 updated_at = excluded.updated_at
            """,
            (conversation_id, summary[:SUMMARY_LIMIT], through_message_id, covered),
        )


#: How the summary is introduced to the model. Named as what it is — a summary of turns it is
#: not being shown — because a model told "this is the conversation" will answer as though the
#: summarised part had been said in those words.
SUMMARY_PREFACE = ("Earlier in this conversation, summarised because it is too long to send in "
                   "full. Treat it as your own memory of what was said:\n\n")


def folded(turns: list[dict[str, Any]], summary: str, budget: int = RECENT_BUDGET
           ) -> tuple[list[dict[str, Any]], int]:
    """What to send, and how many turns it stands in for.

    With nothing to summarise this is the conversation exactly as it is. With a summary, the
    note goes first, as a `system` turn: it is context about the conversation rather than
    something anybody said in it.
    """
    older, recent = split(turns, budget)
    if not older:
        return turns, 0
    if not summary:
        # Nothing to say about them yet — the first fold is still to happen. The recent turns
        # go on their own rather than a request being held up for a summary.
        return recent, len(older)
    note = {"role": "system", "content": SUMMARY_PREFACE + summary[:SUMMARY_LIMIT]}
    return [note, *recent], len(older)


def needs_folding(turns: list[dict[str, Any]], held: dict[str, Any],
                  budget: int = RECENT_BUDGET, after: int = FOLD_AFTER) -> list[dict[str, Any]]:
    """The turns a new summary would have to cover, or nothing when one is not worth a call.

    A fold is worth making when the older turns that no summary covers add up to more than
    `after` characters — below that, they are close enough to the budget's edge that the next
    turn or two will bring them in anyway.
    """
    older, _ = split(turns, budget)
    if not older:
        return []
    covered = int(held.get("covered") or 0)
    uncovered = older[covered:] if covered <= len(older) else []
    if not uncovered:
        return []
    if sum(len(str(turn.get("content", ""))) for turn in uncovered) < after and held.get("summary"):
        return []
    return uncovered


def fold_prompt(summary: str, uncovered: list[dict[str, Any]]) -> str:
    """What the model is asked, to roll a summary forward.

    It is given what the summary says so far and only what has happened since — the rolling
    part — and told what the summary is *for*, because a summary written for a reader is a
    different thing from one written to be somebody's memory of a conversation.
    """
    said = "\n\n".join(f"{turn.get('role', 'user')}: {str(turn.get('content', ''))[:4000]}"
                       for turn in uncovered)
    sofar = (f"The summary so far:\n{summary}\n\n" if summary else "")
    return (
        "You are keeping a running summary of a conversation, so that its earlier parts can be "
        "remembered after they become too long to include in full.\n\n"
        f"{sofar}New part of the conversation to fold in:\n{said}\n\n"
        "Write the updated summary. Keep every decision, preference, name, number and unfinished "
        "thread; drop pleasantries and repetition. Write it as notes to yourself, in the third "
        f"person, under {SUMMARY_LIMIT // 5} words. Reply with the summary alone."
    )


def what_to_send(database: Database, conversation_id: str) -> list[dict[str, Any]]:
    """The prior turns as they should travel: whole, or recent plus one summarising note.

    One place, because both ways into a conversation — a new message and a reopened one —
    have to send the same thing, and a request that carried the whole conversation once and a
    summary the next time would be a model told two different stories.
    """
    turns = history(database, conversation_id)
    if not switched_on(database):
        return turns
    return folded(turns, stored_summary(database, conversation_id)["summary"])[0]
