"""Notes NERVIS was told, kept beside the notes it shipped with (M23).

**One file, in the same directory as the hand-written ones.** That is the whole
of "retrieved the same way": `knowledge.sections()` globs `*.md`, so a learned
note is indexed, weighted and quoted by exactly the code that handles every
other note. Nothing about retrieval knows the difference, and there is no second
path to keep in step with the first.

**What is written and what is not.** A note arrives because somebody said
*remember that…* and then pressed a button — §12's closed set, the same
propose-confirm-act road every other operation takes. NERVIS does not decide on
its own what is worth remembering, and nothing a model returns is written here:
the text stored is the person's own sentence.

**Hand-written always wins.** Where a learned note and a shipped one carry the
same heading, `knowledge` ranks the shipped one first and says the learned one
was overruled. It is not deleted and it is not silently dropped — a person who
told NERVIS something that contradicts its own notes should be able to see both
and decide which is wrong.

**And it is a file.** Read it, edit it in any editor, delete lines out of it,
or delete the whole thing. That is the durable form of "a person can change what
NERVIS learned"; the endpoints exist for convenience, not as the only door.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from nervis import knowledge
from nervis.knowledge import KNOWLEDGE

#: The one file NERVIS writes into the knowledge directory. Named so that
#: sorting puts it where a reader expects and so that nobody mistakes it for
#: something a person wrote: everything else there is a subject, this is a log.
LEARNED = "learned.md"

#: The header the file carries when NERVIS creates it. It is a knowledge file
#: like any other, so it needs a title — and the title is the disclaimer.
PREAMBLE = """# What NERVIS has been told

Notes added by NERVIS when somebody asked it to remember something. Each one
carries the date and the sentence that prompted it.

This file is safe to edit or delete. Where a note here disagrees with one of the
hand-written files beside it, the hand-written one wins and NERVIS says so.
"""

# A heading may not contain a newline or a `#`, which would make one note look
# like two. Length is bounded because a heading is an index entry, not the note.
MAX_HEADING = 80
MAX_BODY = 2_000


@dataclass(frozen=True)
class Note:
    """One thing NERVIS was told, and where it came from."""

    heading: str
    body: str
    learned_on: str
    prompted_by: str

    def as_markdown(self) -> str:
        """The note as it sits in the file.

        The provenance line is *inside* the section rather than in a sidecar,
        because the file is meant to be read on its own — a date kept somewhere
        else is a date nobody sees when they open the thing they are editing.
        """
        return (
            f"\n## {self.heading}\n\n{self.body}\n\n"
            f"_Learned {self.learned_on} from: “{self.prompted_by}”_\n"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "heading": self.heading,
            "body": self.body,
            "learned_on": self.learned_on,
            "prompted_by": self.prompted_by,
        }


def path(root: Path | None = None) -> Path:
    return (root or KNOWLEDGE) / LEARNED


def remember(
    heading: str, body: str, prompted_by: str, *, root: Path | None = None,
    today: str = "",
) -> Note:
    """Append one note, creating the file if this is the first.

    Appending rather than rewriting, and that is the milestone's word: **it
    never overwrites**. Two notes under one heading are two notes, both kept.
    Deciding that a later note supersedes an earlier one is a judgement, and
    making it silently at write time would throw away the earlier one before
    anybody could disagree.
    """
    note = Note(
        heading=_clean(heading, MAX_HEADING),
        body=_clean(body, MAX_BODY),
        learned_on=today or date.today().isoformat(),
        prompted_by=_clean(prompted_by, MAX_HEADING * 3),
    )
    if not note.heading or not note.body:
        raise ValueError("a learned note needs both a heading and something to say")
    file = path(root)
    file.parent.mkdir(parents=True, exist_ok=True)
    if not file.exists():
        file.write_text(PREAMBLE, encoding="utf-8")
    with file.open("a", encoding="utf-8") as handle:
        handle.write(note.as_markdown())
    # **Written, then immediately knowable.** `knowledge` caches its index, and
    # without this NERVIS would agree to remember something and then not know it
    # until the next restart — which looks exactly like the feature not working.
    knowledge.forget_cached()
    return note


def note_id(heading: str, body: str) -> str:
    """A short, stable name for one note, the same on both computers.

    Derived from what the note *says* rather than from where it sits in a file: the file is
    append-only and hand-editable, so any position-based identifier would name a different
    note the moment somebody tidied it. Used to tick one note in a list on this computer and
    have the other computer know which one was meant.
    """
    digest = hashlib.sha1(f"{_clean(heading, MAX_HEADING)}\u0000{_clean(body, MAX_BODY)}"
                          .encode()).hexdigest()
    return digest[:12]


def differences(theirs: list[dict[str, Any]], ours: list[Note]) -> list[dict[str, Any]]:
    """The other computer's notes that this one does not have, in a shape a screen can render.

    `state` is `new` when nothing here shares the heading, and `another` when the heading is
    here already but this note is not — which is an ordinary thing rather than a conflict: the
    file is append-only on purpose, and two notes under one heading are two notes.

    Matched on heading *and* body, so the same fact taught to both computers on different days
    is recognised as the same note even though its date and the sentence that prompted it
    differ. What is compared is what was said, not when.
    """
    mine = {note_id(note.heading, note.body) for note in ours}
    headings = {_clean(note.heading, MAX_HEADING).lower() for note in ours}
    changes: list[dict[str, Any]] = []
    for note in theirs:
        heading, body = str(note.get("heading", "")), str(note.get("body", ""))
        if not heading or not body:
            continue
        identifier = note_id(heading, body)
        if identifier in mine:
            continue
        changes.append({
            "id": identifier, "heading": heading, "body": body,
            "learned_on": str(note.get("learned_on", "")),
            "prompted_by": str(note.get("prompted_by", "")),
            "state": "another" if _clean(heading, MAX_HEADING).lower() in headings else "new",
        })
    return changes


def take(chosen: list[dict[str, Any]], *, root: Path | None = None) -> list[Note]:
    """Write notes that came from another computer, keeping what they already carried.

    **The date and the sentence that prompted it travel with the note.** A fact learned on
    Sunday is a fact from Sunday wherever it is read, and rewriting either would turn somebody
    else's note into this computer's own — the same rule conversations cross under.
    """
    written: list[Note] = []
    for note in chosen:
        try:
            written.append(remember(
                str(note.get("heading", "")), str(note.get("body", "")),
                str(note.get("prompted_by", "")), root=root,
                today=str(note.get("learned_on", "")),
            ))
        except ValueError:
            continue
    return written


def notes(root: Path | None = None) -> list[Note]:
    """Every note in the file, oldest first.

    Parsed back out of the markdown rather than kept in a database beside it.
    The file is the record — a person who edits it by hand has changed what
    NERVIS knows, and a parallel store would quietly disagree with them.
    """
    file = path(root)
    if not file.is_file():
        return []
    found: list[Note] = []
    for part in file.read_text(encoding="utf-8", errors="replace").split("\n## ")[1:]:
        heading, _, rest = part.partition("\n")
        learned_on, prompted_by, body = _provenance(rest.strip())
        found.append(Note(heading.strip(), body, learned_on, prompted_by))
    return found


def forget(heading: str, root: Path | None = None) -> bool:
    """Drop every note under one heading, rewriting the file.

    By heading rather than by index: an index is a position in a file a person
    may have edited between reading it and pressing the button, and acting on a
    stale position deletes the wrong note.
    """
    file = path(root)
    kept = [note for note in notes(root) if note.heading != heading]
    if len(kept) == len(notes(root)):
        return False
    file.write_text(
        PREAMBLE + "".join(note.as_markdown() for note in kept), encoding="utf-8"
    )
    knowledge.forget_cached()
    return True


def forget_all(root: Path | None = None) -> int:
    """Delete the file. Retrieval then behaves exactly as it did before M23."""
    file = path(root)
    if not file.is_file():
        return 0
    count = len(notes(root))
    file.unlink()
    knowledge.forget_cached()
    return count


_PROVENANCE = re.compile(
    r"_Learned (?P<on>\d{4}-\d{2}-\d{2}) from: “(?P<from>.*)”_\s*$",
    re.DOTALL,
)


def _provenance(text: str) -> tuple[str, str, str]:
    """Split a note's body from its provenance line, tolerating its absence.

    A note somebody hand-edited may have lost the line, and a parser that
    refused it would make the file unreadable the first time anybody tidied it —
    which is the opposite of "a person can edit this".
    """
    found = _PROVENANCE.search(text)
    if not found:
        return "", "", text.strip()
    return found.group("on"), found.group("from"), text[: found.start()].strip()


def _clean(text: str, limit: int) -> str:
    """One line's worth of somebody's own words, bounded.

    `#` is stripped rather than escaped: a heading containing one would split a
    note in two the next time the file was parsed, and the parser is the same
    one that indexes every other knowledge file.
    """
    flattened = " ".join(str(text or "").split()).replace("#", "").strip()
    return flattened[:limit].strip()


__all__ = ["LEARNED", "Note", "forget", "forget_all", "notes", "path", "remember"]
