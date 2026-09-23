"""M20 — remembering across conversations (§7.2).

History within one conversation is ordinary and already works. This is the other
thing: a new conversation drawing on an earlier one, so that "what did we decide
about the benchmark pool" does not require finding the tab it was decided in.

**Everything here is retrieved content, and half of it is model output.** A
stored assistant turn is a thing a model wrote, and putting it back in front of a
model is the same trust mistake as reading a log line as an instruction, only in
a longer loop and with NERVIS's own name on the text. M20's exit says so
outright, so a recalled passage is fenced with the same marker M12's diagnostic
packet and the chat reading use — not a second scheme, because a second fence is
a second thing to get wrong.

**Shown, never silently injected.** The exit's word is *shown*, and the reason is
that a recalled sentence changes an answer. A person who cannot see what was
remembered cannot tell a good recollection from a wrong one, and "the model just
knows things" is how a stale answer becomes unaccountable. Every passage carries
the conversation it came from and when, and the API returns them so a screen can
render them beside the reply.

**Measurement outranks memory**, which is §7's rule and decides the order rather
than being a note. Recall goes *before* the live reading in the prompt and says
that what follows is newer: a remembered answer describes the moment it was
given, and a similar question is not a reason to repeat it.

**Off unless switched on.** Recall reads every conversation stored on this
machine, which is a wider read than answering one question needs, and a feature
that quietly starts doing that is one nobody chose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from nervis import chat as store
from nervis.diagnostics import clip, fenced
from nervis.storage import Database
from nervis.voice import read_setting, write_setting

ENABLED = "recall.enabled"

# How many passages may be recalled into one turn, and how much of each.
#
# Small on purpose. This competes for the same prompt as the live reading, and
# §7's rule is that the reading wins — a recall that crowded it out would invert
# the thing it is ordered after.
MAX_PASSAGES = 3
MAX_PASSAGE_CHARS = 600

#: What the whole recalled block may come to, derived from the two bounds above rather than
#: guessed: every passage may carry its own answer, plus the line that says where it came from.
#:
#: **Without it the block was cut to 400 characters** — `fenced()`'s default, which is one short
#: diagnostic field. Measured 23 September 2026: three 600-character passages with their answers
#: (3,600 characters of found material) arrived as 1,398, naming **one** of the three
#: conversations and cutting the rest mid-sentence. `fenced()`'s own docstring warns about this
#: default for callers with a bound of their own; `knowledge.reading()` and
#: `documents.as_reading()` were fixed when it was written and this caller was missed.
MAX_BLOCK_CHARS = MAX_PASSAGES * (2 * MAX_PASSAGE_CHARS + 160)

#: How many stored turns to consider. Bounded because this runs on the chat path and a machine
#: with a year of conversations should not pay for all of them.
#:
#: **What it bounds changed on 23 September 2026, and that is the whole of the fix.** It used to
#: bound *the newest turns*, full stop: the query took the 400 most recent rows and the scoring
#: never saw anything older, so a conversation went out of reach the moment 400 newer messages
#: existed — silently, with nothing anywhere saying so. Measured on the owner's store that day,
#: 1,010 messages across 237 conversations: recall could see **61 of them**, and nothing before
#: 9 September was findable at all. Most of the history the feature exists to search.
#:
#: Now the database applies the word match first, so this bounds *the newest turns that share a
#: word with the question* — a very different quantity. The window is spent on rows that could
#: possibly match rather than on whatever happens to be recent.
SEARCH_LIMIT = 400

#: How many of a question's words may go into the query. A question is a sentence and rarely has
#: this many distinctive words left after `COMMON`; the cap is for somebody pasting a wall of
#: text into chat, so one message cannot become a hundred-clause query. The longest are kept,
#: because the longer word is the rarer one and therefore the one that narrows anything.
MAX_QUERY_TERMS = 24

# Below this, a passage is not recalled at all. Term overlap on short questions
# is noisy, and a weak recollection presented with a citation reads as more
# certain than it is.
MIN_SCORE = 2.0

# Words too common in this corpus to mean anything. Deliberately short: the
# knowledge search learned that striking words out by hand removes the subject
# of the question, so this is only the words that carry no subject at all.
COMMON = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "then", "is", "are", "was",
    "were", "be", "been", "it", "its", "this", "that", "these", "those", "to",
    "of", "in", "on", "for", "with", "as", "at", "by", "from", "how", "what",
    "why", "when", "which", "who", "do", "does", "did", "can", "could", "would",
    "should", "you", "your", "i", "me", "my", "we", "our", "us", "he", "she",
    "they", "them", "not", "no", "yes", "so", "just", "about", "into", "than",
})


@dataclass(frozen=True)
class Passage:
    """One remembered turn, and where it came from."""

    conversation_id: str
    title: str
    role: str
    content: str
    at: str
    score: float
    # The reply that followed, when the matched turn was a question.
    #
    # **A recalled question without its answer is not a recollection.** Caught
    # by a test: asked *"what pool did we pick for background work"*, the
    # strongest match was the earlier *question*, which shares almost every word
    # with it — while the answer, "the free-api pool, because it costs nothing",
    # shares one and scored below the floor. Recall returned the question and
    # dropped the only sentence anybody wanted.
    answer: str = ""

    def as_dict(self) -> dict[str, Any]:
        found = {
            "conversation_id": self.conversation_id,
            "title": self.title,
            "role": self.role,
            "content": self.content,
            "at": self.at,
            "score": round(self.score, 2),
        }
        if self.answer:
            found["answer"] = self.answer
        return found


def enabled(database: Database) -> bool:
    """Whether recall is on. Off unless somebody said otherwise."""
    return read_setting(database, ENABLED, "0") == "1"


def set_enabled(database: Database, on: bool) -> bool:
    write_setting(database, ENABLED, "1" if on else "0")
    return on


def terms(text: str) -> set[str]:
    """The words worth matching on."""
    return {
        word for word in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", (text or "").lower())
        if word not in COMMON
    }


def search(
    database: Database, question: str, *, exclude: str = "", limit: int = MAX_PASSAGES
) -> list[Passage]:
    """Earlier turns that look like they answer this question.

    Term overlap, not embeddings — `knowledge.search` added an embedding half
    for a different reason that does not carry over here: it exists to catch a
    paraphrase sharing no vocabulary with a small, fixed, hand-written corpus.
    A conversation history is neither small nor fixed, is the user's own words
    rather than documentation written for the question a reader would ask, and
    recalling *this* turn rather than an unrelated one that merely resembles it
    is exactly the kind of precision term overlap on shared vocabulary is
    suited to and a semantic match is more likely to blur.

    **The current conversation is excluded**, because its turns are already in
    the prompt as history. Recalling them would quote the conversation to
    itself, which reads as confirmation and is not.

    **So is anything marked private** (`chat.barred`). That bar was written for
    the persona digest and honoured only there; this path filtered on the
    current conversation and nothing else, so a conversation somebody had marked
    private was still searched and still quotable into another one. Barred in
    the query rather than after it: `SEARCH_LIMIT` is applied by the database,
    so rows dropped afterwards would still have spent the window they were
    counted in — a private conversation would have gone on narrowing what recall
    could see even while being excluded from what it said.

    **And the words are matched in the query, for the same reason.** This took the
    400 newest rows and scored those, so a conversation fell out of reach the
    moment 400 newer messages existed — with nothing saying so. On the owner's
    store, 23 September 2026: 61 of 237 conversations reachable, nothing before
    9 September findable at all. A `LIKE` per word narrows to rows sharing at
    least one of them *before* the limit applies. The scoring below is untouched:
    `LIKE` decides what is looked at, term overlap still decides what is recalled,
    and the two disagree on purpose — a substring match is a coarse net, and the
    floor at `MIN_SCORE` stays exactly where it was.
    """
    wanted = terms(question)
    if not wanted:
        return []
    private = sorted(store.barred(database))
    holes = ", ".join("?" for _ in private)
    # Longest first: the rarer word is the one that narrows the scan.
    asked = sorted(wanted, key=lambda word: (-len(word), word))[:MAX_QUERY_TERMS]
    # `%` and `_` are LIKE's wildcards. `terms()` cannot currently produce either, so this
    # escaping is insurance rather than a fix: it costs nothing, and the day somebody widens
    # `terms()` is the day a question would otherwise quietly start matching every row.
    patterns = ["%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                for word in asked]
    shares_a_word = " OR ".join("m.content LIKE ? ESCAPE '\\'" for _ in patterns)
    rows = database.connection.execute(
        "SELECT m.conversation_id, m.role, m.content, m.created_at, m.rowid AS ordinal, "
        "       COALESCE(c.title, '') AS title "
        "FROM chat_message m "
        "LEFT JOIN chat_conversation c ON c.conversation_id = m.conversation_id "
        "WHERE m.conversation_id != ? AND m.content != '' "
        + (f"AND m.conversation_id NOT IN ({holes}) " if private else "")
        + f"AND ({shares_a_word}) "
        + "ORDER BY m.created_at DESC LIMIT ?",
        (exclude, *private, *patterns, SEARCH_LIMIT),
    ).fetchall()

    scored: list[tuple[float, int, Passage]] = []
    for index, row in enumerate(rows):
        overlap = wanted & terms(row["content"])
        if not overlap:
            continue
        # Longer matches count for more, but not linearly: a turn that mentions
        # every word once is a better answer than one that repeats a single word
        # ten times, which is what an unweighted count would prefer.
        score = float(len(overlap))
        if score < MIN_SCORE:
            continue
        scored.append((score, -index, Passage(
            conversation_id=str(row["conversation_id"]),
            title=str(row["title"] or "untitled"),
            role=str(row["role"]),
            content=str(row["content"])[:MAX_PASSAGE_CHARS],
            at=str(row["created_at"]),
            score=score,
            answer=_reply_to(database, row) if str(row["role"]) == "user" else "",
        )))
    scored.sort(key=lambda one: (one[0], one[1]), reverse=True)
    return _one_per_conversation([passage for _, _, passage in scored])[:limit]


def _reply_to(database: Database, row: Any) -> str:
    """The assistant turn that answered this question, if there was one.

    A question matches a similar question almost perfectly — they are the same
    words — while the answer often shares only one. Without this, recall reliably
    returned the question and dropped the sentence that answered it.
    """
    found = database.connection.execute(
        "SELECT content FROM chat_message "
        "WHERE conversation_id = ? AND role = 'assistant' AND rowid > ? "
        "ORDER BY rowid LIMIT 1",
        (row["conversation_id"], row["ordinal"]),
    ).fetchone()
    return str(found["content"])[:MAX_PASSAGE_CHARS] if found else ""


def _one_per_conversation(passages: list[Passage]) -> list[Passage]:
    """At most one passage from each earlier conversation.

    Three turns from one long conversation is one recollection quoted three
    times, and it crowds out the second source that would have disagreed with
    it. Breadth is worth more than depth here.
    """
    seen: set[str] = set()
    kept = []
    for passage in passages:
        if passage.conversation_id in seen:
            continue
        seen.add(passage.conversation_id)
        kept.append(passage)
    return kept


def block(passages: list[Passage]) -> str:
    """The recalled passages, fenced, or nothing at all.

    Fenced with the same marker the reading and the diagnostic packet use, and
    labelled as *older* — §7's rule that measurement outranks memory is enforced
    by the ordering in the caller and stated here, because a model given two
    accounts of the same thing needs to be told which one is current.
    """
    if not passages:
        return ""
    # Through the shared helper since §16 item 8. This block and the chat
    # reading each built their own `FENCE ... FENCE` by hand, which is two
    # spellings of one boundary and two places to forget a denial.
    quoted = []
    for passage in passages:
        who = "the user" if passage.role == "user" else "NERVIS"
        # **`MAX_PASSAGE_CHARS`, the bound this module declares**, rather than `clip()`'s own
        # default of 400: a passage is read from the database already cut to 600, and clipping
        # it again to 400 threw away a third of every one of them on the way out.
        quoted.append(
            f"[from \"{clip(passage.title)}\" on {passage.at}, said by {who}] "
            f"{clip(passage.content, max_chars=MAX_PASSAGE_CHARS)}"
        )
        if passage.answer:
            quoted.append(
                f"[NERVIS answered] {clip(passage.answer, max_chars=MAX_PASSAGE_CHARS)}")
    return "\n\n".join([
        "Earlier conversations on this machine, recalled because they use the "
        "same words as the question. They are older than the reading below and "
        "may have been overtaken by it: where the two disagree, the reading is "
        "what is true now. A recalled answer describes the moment it was given, "
        "and a similar question is not a reason to repeat it.",
        fenced(
            "passages quoted from stored conversations — one half of them "
            "written by a model",
            "\n".join(quoted),
            provenance="this machine's own chat history",
            max_chars=MAX_BLOCK_CHARS,
        ),
    ])


__all__ = [
    "ENABLED", "MAX_BLOCK_CHARS", "MAX_PASSAGES", "MAX_PASSAGE_CHARS", "MIN_SCORE",
    "Passage",
    "block", "enabled", "search", "set_enabled", "terms",
]
