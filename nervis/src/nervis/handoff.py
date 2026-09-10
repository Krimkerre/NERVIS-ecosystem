"""A coding task, written where Clarvis will find it (M27).

**The interface is a document, and that is the whole design.** `CLARVIS.md` §6.7
forbids NERVIS invoking a tool, resolving a gate or changing a setting, and
closes with *"an agent asked to add such control must stop."* None of that is
needed: Clarvis's build flow already reads a plan from disk every time —
`pendingBuild` re-reads it precisely because a person may have edited it — so a
task authored elsewhere arrives through the door a hand-edited one already
comes through.

**The test that keeps this from becoming remote control**: with the Bridge
stopped, all of it still works. Anything that stops working without the Bridge
is §6.7's case wearing a different name.

**Provenance is the security story on the other side.** A brief somebody wrote
themselves and one that arrived from another program deserve different
scepticism at the moment of approval, and the person approving is the only one
who can apply it. So the file says where it came from, in a header Clarvis reads
and shows before anything runs — and §9's rule holds throughout: the file is
evidence of what was asked for, never an instruction Clarvis follows unreviewed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Where a handed-over task is written. One file, overwritten rather than
#: accumulated: a queue of tasks nobody read is a queue, and this is a handoff.
#: A second task before the first is picked up replaces it, which is what the
#: person who typed it meant.
TASK_FILE = "clarvis-task.md"

#: Where tasks go, inside the folder the editor opens: **one new folder per
#: task** beneath this one.
#:
#: Asked for on 10 September 2026, and the reason is Clarvis's own layout: it
#: keeps `plan.md` and its build state at the root of whatever folder it has
#: open. Tasks sharing one folder would share one plan, so a second task would
#: open onto the first task's `plan.md`. Each folder is opened in Clarvis as
#: that task's workspace, and Clarvis reads `clarvis-task.md` from its root
#: exactly as it always has — which is why nothing on the Clarvis side changed.
#: Visible rather than a dot-folder, because NERVIS's Files tab refuses
#: dot-segments and these are folders a person opens.
TASK_FOLDER = "nervis-tasks"

#: The marker Clarvis matches on. Deliberately in the body rather than only in
#: the filename: a file renamed by hand still says what it is, and a file
#: somebody wrote themselves and named this by accident does not claim to be
#: something it is not.
MARKER = "<!-- authored-by: nervis -->"

MAX_TASK = 2_000


@dataclass(frozen=True)
class Task:
    """One brief, and where it came from."""

    task: str
    asked_on: str
    conversation: str = ""
    #: The task's own folder, relative to the editor room — what to open in Clarvis.
    folder: str = ""

    def as_markdown(self) -> str:
        """The file Clarvis reads.

        Markdown with a marker comment rather than a bespoke format, because the
        other half of "a person can check this" is that they can open it. It
        reads as a note to the operator that Clarvis happens to parse.
        """
        return (
            f"{MARKER}\n"
            f"# Task from NERVIS\n\n"
            f"{self.task}\n\n"
            f"---\n\n"
            f"_Handed over from NERVIS chat on {self.asked_on}"
            + (f", conversation `{self.conversation}`" if self.conversation else "")
            + ". You asked for this in conversation rather than in the editor, so"
            " read it before approving — Clarvis will show it to you first and it"
            " is editable._\n"
        )

    def as_dict(self) -> dict[str, Any]:
        return {"task": self.task, "asked_on": self.asked_on,
                "conversation": self.conversation}


def _new_task_folder(root: Path, asked_on: str, task: str) -> Path:
    """A folder nothing has used yet: when it was asked, then its first few words.

    Sorts by date in any file listing and says what it is at a glance. A second
    task in the same minute with the same opening words gets a number rather
    than landing in the first one's folder, which is the whole point of having
    folders.
    """
    stamp = re.sub(r"[^0-9]+", "-", asked_on).strip("-")
    slug = "-".join(re.findall(r"[a-z0-9]+", task.lower())[:6])[:40].strip("-") or "task"
    parent = root / TASK_FOLDER
    candidate = parent / f"{stamp}-{slug}"
    number = 2
    while candidate.exists() or candidate.is_symlink():
        candidate = parent / f"{stamp}-{slug}-{number}"
        number += 1
    return candidate


def write(root: Path, task: str, *, conversation: str = "", today: str = "") -> Task:
    """Write the brief into a new folder of its own, opened in Clarvis as its workspace."""
    cleaned = " ".join(str(task or "").split())[:MAX_TASK].strip()
    if len(cleaned) < 3:
        raise ValueError("a handed-over task needs something to say")
    asked_on = today or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    folder = _new_task_folder(root, asked_on, cleaned)
    # **Checked after symlinks are followed, and spelled out here rather than
    # imported.** `nervis.workspace.still_inside` is the same four lines, but
    # §6.7 keeps this module free of every `nervis` import — a module that
    # cannot reach anything cannot be one edit from reaching the editor, and
    # `test_nothing_here_reaches_the_editor` enforces it. Four duplicated lines
    # are the cheaper half of that trade.
    #
    # The names are fixed and this code built the path; neither says anything
    # about what is *at* it. A symlink left where the task file goes — or, now
    # that there is a folder, where the folder goes — is followed silently, and
    # the handover lands wherever it points.
    #
    # **Checked before the folder is created, not after.** `mkdir(exist_ok=True)`
    # on a link to an existing directory succeeds without a word, and on a
    # dangling one raises a `FileExistsError` that reads like a filesystem fault
    # rather than a refusal. Resolving first catches both as what they are.
    destination = folder / TASK_FILE
    base = root.expanduser().resolve(strict=False)
    if not destination.expanduser().resolve(strict=False).is_relative_to(base):
        raise ValueError(f"{TASK_FOLDER}/{folder.name} resolves outside the handover directory")
    folder.mkdir(parents=True)
    written = Task(
        task=cleaned, asked_on=asked_on, conversation=conversation,
        folder=f"{TASK_FOLDER}/{folder.name}",
    )
    destination.write_text(written.as_markdown(), encoding="utf-8")
    return written


def waiting(root: Path) -> Task | None:
    """The task currently waiting, if NERVIS wrote one and nobody took it.

    Parsed back out of the file rather than remembered, for `pendingBuild`'s own
    reason: the file is the record, and a person who edited or deleted it has
    changed what is waiting.
    """
    found = root / TASK_FILE
    if not found.is_file():
        return None
    text = found.read_text(encoding="utf-8", errors="replace")
    if MARKER not in text:
        # Somebody else's file under the same name. Not ours to report, and
        # certainly not ours to overwrite silently — `write` will, which is why
        # the marker is checked before anything reads this as a handoff.
        return None
    body = text.split("# Task from NERVIS", 1)[-1].split("\n---", 1)[0].strip()
    # **Bounded to the stamp's own shape.** This read `[^,_]*`, which stops at
    # the comma before the conversation id — and when there is no conversation
    # there is no comma, so it swallowed the rest of the sentence. Found by
    # parsing a real handoff with Clarvis's own reader rather than a fixture,
    # which had always had a conversation in it.
    when = re.search(r"on (\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC)", text)
    conversation = re.search(r"conversation `([^`]+)`", text)
    return Task(
        task=body,
        asked_on=(when.group(1).strip() if when else ""),
        conversation=(conversation.group(1) if conversation else ""),
    )


def forget(root: Path) -> bool:
    """Drop the waiting task. Used when it has been handed to the editor."""
    found = root / TASK_FILE
    if not found.is_file():
        return False
    found.unlink()
    return True


__all__ = [
    "MARKER", "MAX_TASK", "TASK_FILE", "TASK_FOLDER", "Task",
    "forget", "waiting", "write",
]
