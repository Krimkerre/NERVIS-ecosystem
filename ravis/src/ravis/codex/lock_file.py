"""The checkout lock file: one writer per project, even while RAVIS is down (design §6.3).

**Why a file as well as RAVIS's lock.** RAVIS's project lock (M29's fourth increment) is the
authority while RAVIS is up. The file is the floor underneath it: Clarvis's own engine stays safe
when RAVIS isn't running, and RAVIS and a window agree on who holds a checkout without a network
call. It lives in the checkout's git folder — `<git_dir>/clarvis-engine.lock`, or
`<root>/.clarvis/engine.lock` without git — and is written by the process that actually writes to
the project: RAVIS for a Codex session (and for calibration's scenarios), the window for a run of
Clarvis's own engine. The contents are `lock-rule-cases.json` → `lock_file.example`, and Clarvis's
`src/engine/lock/fileLock.ts` reads and writes the same bytes.

**The create is the lock.** `O_CREAT | O_EXCL` with mode 0600 either makes the file or fails because
it exists, atomically, whoever else tries at the same moment. There is no "check, then create".

**Heartbeats go through the descriptor that created the file,** never by opening the path again. If
another writer has since replaced the file (a takeover of a holder it judged gone), a write by path
would overwrite the new holder's lock with the old one's; a write through the old descriptor lands
in the old, unlinked file, harming nobody. Each write is checked first, so a holder whose file
was replaced or deleted learns that it has lost the lock.

**Lost means evidence, never doubt** (review AH1). `check()` answers `lost` only when the file is
gone, is a different file, or names another holder. A file that can't be read right now is
`unknown`, which a holder treats as still holding.

**Padding.** The JSON is padded with spaces to a whole KiB, so successive heartbeats are the
same size and a reader never catches the file between a rewrite and a truncate. JSON allows trailing
whitespace (C1's notes: allow trailing spaces; an unreadable file is unknown, not lost).

**The git folder is validated without running git** (design §3.5.1, review AM9): `<root>/.git` is a
directory, or a worktree's `gitdir:` file whose target points back at it. RAVIS never runs git.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

FILE_NAME = "clarvis-engine.lock"
PAD_TO_BYTES = 1024
#: How often a holder rewrites its heartbeat (design §6.3: "heartbeat both every 15 s").
HEARTBEAT_SECONDS = 15.0
LOCK_FILE_VERSION = 3

HoldCheck = Literal["holds", "lost", "unknown"]


def git_dir_for(root: Path) -> Path | None:
    """The checkout's git folder, validated by its shape; None for a root without a usable one."""
    dot_git = root / ".git"
    if dot_git.is_dir() and not dot_git.is_symlink():
        return Path(os.path.realpath(dot_git))
    if not dot_git.is_file():
        return None
    return _worktree_git_dir(root, dot_git)


def _worktree_git_dir(root: Path, dot_git: Path) -> Path | None:
    """`gitdir: <path>` naming `<main>/.git/worktrees/<name>`, whose `gitdir` file points back."""
    try:
        first = dot_git.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not first.startswith("gitdir:"):
        return None
    target = Path(os.path.realpath(root / first.removeprefix("gitdir:").strip()))
    if target.parent.name != "worktrees" or target.parent.parent.name != ".git":
        return None
    try:
        back = (target / "gitdir").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return target if os.path.realpath(back) == os.path.realpath(dot_git) else None


def lock_file_path(root: Path, git_dir: Path | None) -> Path:
    """Where a checkout's lock file lives: in its git folder, or `.clarvis/` without one."""
    return git_dir / FILE_NAME if git_dir is not None else root / ".clarvis" / "engine.lock"


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Holder:
    """Who holds a checkout: the lock file's `holder` block."""

    kind: str
    session_id: str | None
    pid: int
    pid_start: str
    window_id: str | None = None
    host: str = "ravis"
    since: str = ""


def lock_content(
    holder: Holder, *, task_id: str, engine: str = "codex", now: datetime | None = None
) -> dict[str, Any]:
    """A fresh lock file's contents, in `lock-rule-cases.json` → `lock_file.example`'s shape."""
    stamp = iso(now or datetime.now(UTC))
    return {
        "version": LOCK_FILE_VERSION,
        "ravis_lock_id": None,
        "taskId": task_id,
        "engine": engine,
        "state": "running",
        "holder": {
            "kind": holder.kind, "session_id": holder.session_id, "pid": holder.pid,
            "pid_start": holder.pid_start, "window_id": holder.window_id, "host": holder.host,
            "since": holder.since or stamp,
        },
        "heartbeatAt": stamp,
        "waitingOnYou": False,
        "leftover": [],
        "takenOverFrom": None,
        "running_command": None,
    }


def encode(content: dict[str, Any]) -> bytes:
    """The bytes written: the JSON, padded with spaces to a whole KiB."""
    text = json.dumps(content, indent=2) + "\n"
    size = -(-len(text.encode("utf-8")) // PAD_TO_BYTES) * PAD_TO_BYTES
    return (text + " " * (size - len(text.encode("utf-8")))).encode("utf-8")


@dataclass(frozen=True)
class LockFileRead:
    """What a read found: no file, a lock file with its heartbeat's age, or something unreadable."""

    kind: Literal["absent", "present", "unreadable"]
    content: dict[str, Any] = field(default_factory=dict)
    heartbeat_age_seconds: float = 0.0
    detail: str = ""


def read_lock_file(path: Path, now: datetime | None = None) -> LockFileRead:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return LockFileRead("absent")
    except OSError as failure:
        return LockFileRead("unreadable", detail=failure.strerror or str(failure))
    content = parse_lock_file(text)
    if content is None:
        return LockFileRead("unreadable", detail="not a lock file")
    try:
        beat = datetime.fromisoformat(str(content["heartbeatAt"]).replace("Z", "+00:00"))
    except ValueError:
        return LockFileRead("unreadable", detail="its heartbeat isn't a time")
    age = ((now or datetime.now(UTC)) - beat).total_seconds()
    return LockFileRead("present", content=content, heartbeat_age_seconds=max(0.0, age))


def parse_lock_file(text: str) -> dict[str, Any] | None:
    """The contents, when the text is a lock file: an object with a holder and a heartbeat."""
    try:
        value = json.loads(text)
    except ValueError:
        return None
    holder = value.get("holder") if isinstance(value, dict) else None
    if not isinstance(holder, dict) or not isinstance(value.get("heartbeatAt"), str):
        return None
    if not isinstance(holder.get("pid"), int) or not isinstance(holder.get("pid_start"), str):
        return None
    return dict(value)


@dataclass(frozen=True)
class CreateOutcome:
    """A create: the lock held, or the file that was already there, or why it failed."""

    held: HeldLockFile | None = None
    existing: LockFileRead | None = None
    failure: str | None = None


def create_lock_file(path: Path, content: dict[str, Any]) -> CreateOutcome:
    """Take the lock by creating the file: of any number of simultaneous callers, one wins."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return CreateOutcome(existing=read_lock_file(path))
    except OSError as failure:
        return CreateOutcome(failure=failure.strerror or str(failure))
    try:
        _write_through(descriptor, content)
    except OSError as failure:
        # Nobody else can have this file yet: this call created it a moment ago.
        os.close(descriptor)
        path.unlink(missing_ok=True)
        return CreateOutcome(failure=failure.strerror or str(failure))
    return CreateOutcome(held=HeldLockFile(descriptor, path, content))


def _write_through(descriptor: int, content: dict[str, Any]) -> None:
    data = encode(content)
    os.pwrite(descriptor, data, 0)
    os.ftruncate(descriptor, len(data))


class HeldLockFile:
    """A lock file this process created and still has open."""

    def __init__(self, descriptor: int, path: Path, content: dict[str, Any]) -> None:
        self._descriptor: int | None = descriptor
        self.path = path
        self.content = content

    def check(self) -> HoldCheck:
        """Whether the file at the path is still the one this holder created, still naming it."""
        if self._descriptor is None:
            return "lost"
        try:
            mine, there = os.fstat(self._descriptor), os.stat(self.path)
        except FileNotFoundError:
            return "lost"
        except OSError:
            return "unknown"
        if (mine.st_dev, mine.st_ino) != (there.st_dev, there.st_ino):
            return "lost"
        read = read_lock_file(self.path)
        if read.kind == "unreadable":
            return "unknown"
        same = read.kind == "present" and read.content.get("holder") == self.content["holder"]
        return "holds" if same else "lost"

    def heartbeat(self, now: datetime | None = None) -> Literal["written", "lost", "unknown"]:
        """Record a heartbeat, if the lock is still held."""
        check = self.check()
        if check != "holds" or self._descriptor is None:
            return "lost" if check == "holds" else check
        self.content = {**self.content, "heartbeatAt": iso(now or datetime.now(UTC))}
        _write_through(self._descriptor, self.content)
        return "written"

    def release(self) -> Literal["released", "lost", "unknown"]:
        """Delete the file — only while it is still this holder's (the fence).

        A holder that lost it closes its descriptor and deletes nothing: the file now belongs to
        someone else. While the answer is `unknown`, nothing is done, so the caller can try again.
        """
        check = self.check()
        if check == "unknown":
            return check
        if check == "holds":
            self.path.unlink(missing_ok=True)
        self.abandon()
        return "released" if check == "holds" else "lost"

    def abandon(self) -> None:
        """Close the descriptor and delete nothing."""
        if self._descriptor is not None:
            os.close(self._descriptor)
        self._descriptor = None
