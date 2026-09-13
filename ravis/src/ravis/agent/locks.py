"""The project lock as a Codex session needs it — **the seam M29's fourth increment (R4) fills**.

**One writer per project** (runbook §2.2 invariant 1; design §6.3) is two layers: RAVIS's
`project_lock` row, authoritative while RAVIS is up, and the checkout lock file
(`codex/lock_file.py`), written by whoever actually writes — RAVIS for a Codex session. A Codex
session takes both when it is created, and **may start a turn only while it holds them** (final
check F-A1).

**What this increment (R3) builds here**, and nothing more:
- `acquire`: take a free root atomically — the file first (`O_EXCL`), then the row (`root_hash`
  is unique) — refusing `PROJECT_LOCKED` for the same root and `NESTED_PROJECT_LOCKED` for a folder
  inside or around a locked one; or take a lock **with a transfer token** (a switch back, §6.2),
  refusing `LOCK_TRANSFER_INVALID` for a token that is unknown, expired or another root's;
- `holds` and `take_for_turn`: F-A1's check, under the session's action lock — the row names this
  session and is `running`, and the file still names RAVIS — else take it, as above;
- `release` when a session is settled idle or ended, and **keeping** it when settled for a
  transfer, so the destination can take it with its token;
- `heartbeat`: every 15 s the file's heartbeat and "waiting on you", so Clarvis's lock rule sees a
  live holder.

**When RAVIS takes a lock with a token** it replaces the checkout lock file — a new file, a new
inode, holder `codex_session` — rather than editing the window's. The window's own release then
finds the file isn't its own and deletes nothing (Clarvis `projectLock.ts`: "RAVIS should replace
the file").

**Left for R4:** the `/api/v1/project-locks` routes, minting transfer tokens (`transfer`),
takeover, the restart adoption rule and `superseded` rows (only *read* here, to refuse
`LOCK_SUPERSEDED`), and `adopt_file_lock`. Their tests are R4's.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ravis.agent import refusals
from ravis.agent.requests import mapping
from ravis.agent.roots import related
from ravis.agent.store import AgentStore
from ravis.agent.tokens import new_id, token_matches
from ravis.codex.lock_file import (
    HeldLockFile,
    Holder,
    LockFileRead,
    create_lock_file,
    encode,
    iso,
    lock_content,
    lock_file_path,
    read_lock_file,
)
from ravis.codex.lock_rule import (
    JudgedLock,
    Verdict,
    judge_lock,
    observer_awake_seconds,
    probe_process,
    same_start,
)

logger = logging.getLogger("ravis")

#: `project-locks.json` → `lock_view` → `holder.kind`.
CODEX_SESSION = "codex_session"
CLARVIS_RUN = "clarvis_run"


class Claimant(Protocol):
    """What the lock needs to know of the session taking it."""

    @property
    def id(self) -> str: ...
    @property
    def task_id(self) -> str: ...
    @property
    def root(self) -> Path: ...
    @property
    def root_hash(self) -> str: ...
    @property
    def git_dir(self) -> Path | None: ...
    @property
    def trace_id(self) -> str: ...


@dataclass
class _Hold:
    lock_id: str
    file: HeldLockFile


Emit = Callable[[str, str, dict[str, Any]], None]


class ProjectLocks:
    """RAVIS's side of the project lock for Codex sessions (R3's part of design §3.6 and §6.3)."""

    def __init__(
        self,
        store: AgentStore,
        *,
        emit: Emit,
        pid: int | None = None,
        own_start: Callable[[], str | None],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._emit = emit
        self._pid = os.getpid() if pid is None else pid
        self._own_start = own_start
        self._start: str | None = None
        self._now = now
        self._holds: dict[str, _Hold] = {}

    # ── Taking the lock ──────────────────────────────────────────────────────

    def take_for_turn(self, claimant: Claimant, transfer_token: str | None) -> None:
        """F-A1: a turn starts only holding the lock — held already, or taken now, atomically."""
        if transfer_token is not None:
            self.acquire(claimant, transfer_token)
            return
        row = self._store.lock_for_root(claimant.root_hash)
        if row is not None and row["holder_session_id"] == claimant.id:
            if row["state"] == "superseded":
                raise refusals.lock_superseded()
            if row["state"] == "running" and self._file_holds(claimant, row):
                return
            raise refusals.project_locked(self.view(row))
        self.acquire(claimant, None)

    def acquire(self, claimant: Claimant, transfer_token: str | None) -> None:
        if transfer_token is not None:
            self._take_with_token(claimant, transfer_token)
            return
        self._refuse_if_locked(claimant)
        lock_id = new_id("pl_")
        content = self._content(claimant, lock_id)
        path = lock_file_path(claimant.root, claimant.git_dir)
        created = create_lock_file(path, content)
        if created.held is None and self._stale_ravis_file(created.existing):
            path.unlink(missing_ok=True)
            created = create_lock_file(path, content)
        if created.held is None:
            if created.existing is not None:
                raise refusals.project_locked(self._file_view(created.existing, claimant))
            raise refusals.workspace_root_not_allowed(
                "lock_file_unwritable",
                f"RAVIS couldn't write this project's lock file: {created.failure}",
            )
        try:
            self._store.insert_lock(self._row(claimant, lock_id))
        except sqlite3.IntegrityError:
            created.held.release()
            raise refusals.project_locked(self._locked_view(claimant)) from None
        self._holds[claimant.id] = _Hold(lock_id, created.held)
        self._changed(claimant, lock_id, "running")

    def _refuse_if_locked(self, claimant: Claimant) -> None:
        for row in self._store.locks():
            if row["root_hash"] == claimant.root_hash:
                if row["holder_session_id"] == claimant.id and row["state"] == "superseded":
                    raise refusals.lock_superseded()
                raise refusals.project_locked(self.view(row))
            if related(Path(row["workspace_root"]), claimant.root):
                raise refusals.nested_project_locked(self.view(row))

    def _take_with_token(self, claimant: Claimant, token: str) -> None:
        """A switch back (§6.2): the lock moves to this session, and its file is replaced."""
        row = self._store.lock_for_root(claimant.root_hash)
        if row is None or not self._token_valid(row, token):
            raise refusals.lock_transfer_invalid()
        if row["state"] == "superseded":
            raise refusals.lock_superseded()
        hold = self._holds.get(claimant.id)
        if row["holder_session_id"] != claimant.id or hold is None or hold.lock_id != row["id"]:
            # The file is the window's now: replace it, and let go of any old descriptor of ours.
            if hold is not None:
                hold.file.abandon()
            hold = _Hold(row["id"], self._replace_file(claimant, row["id"]))
        stamp = iso(self._now())
        self._store.update_lock(
            row["id"], holder_kind=CODEX_SESSION, holder_session_id=claimant.id,
            holder_window_id=None, holder_host="ravis", holder_pid=self._pid,
            holder_pid_start=self._pid_start(), lease_sha256=None, state="running",
            heartbeat_at=stamp, acquired_at=stamp, transfer_token_sha256=None,
            transfer_expires_at=None,
        )
        self._holds[claimant.id] = hold
        self._changed(claimant, row["id"], "running")

    def _token_valid(self, row: dict[str, Any], token: str) -> bool:
        stored, expires = row["transfer_token_sha256"], row["transfer_expires_at"]
        if not stored or not token_matches(token, stored):
            return False
        return isinstance(expires, str) and expires > iso(self._now())

    def _replace_file(self, claimant: Claimant, lock_id: str) -> HeldLockFile:
        """Write RAVIS's lock file over the window's with a rename, so it is a new file."""
        path = lock_file_path(claimant.root, claimant.git_dir)
        content = self._content(claimant, lock_id)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
        descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, encode(content))
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        return HeldLockFile(os.open(path, os.O_RDWR), path, content)

    # ── Holding, releasing, heartbeats ───────────────────────────────────────

    def holds(self, claimant: Claimant) -> bool:
        row = self._store.lock_for_root(claimant.root_hash)
        return (
            row is not None and row["holder_session_id"] == claimant.id
            and row["state"] == "running" and self._file_holds(claimant, row)
        )

    def _file_holds(self, claimant: Claimant, row: dict[str, Any]) -> bool:
        """Whether the checkout's file still names RAVIS for this session: lost only on evidence."""
        hold = self._holds.get(claimant.id)
        if hold is not None and hold.lock_id == row["id"]:
            return hold.file.check() != "lost"
        read = read_lock_file(lock_file_path(claimant.root, claimant.git_dir))
        if read.kind == "unreadable":
            return True
        holder = read.content.get("holder", {})
        return read.kind == "present" and holder.get("session_id") == claimant.id

    def release(self, claimant: Claimant) -> None:
        """Let the lock go: the row, and the file only while it is still this session's."""
        row = self._store.lock_for_session(claimant.id)
        hold = self._holds.pop(claimant.id, None)
        if row is not None:
            self._store.delete_lock(row["id"])
            self._changed(claimant, row["id"], "released")
        if hold is not None:
            hold.file.release()
        elif row is not None and self._file_holds(claimant, row):
            lock_file_path(claimant.root, claimant.git_dir).unlink(missing_ok=True)

    def heartbeat(self, waiting: Mapping[str, bool]) -> None:
        """Every 15 s: each held file's heartbeat and "waiting on you", and the rows' too."""
        stamp = iso(self._now())
        for session_id, hold in list(self._holds.items()):
            waiting_now = waiting.get(session_id, False)
            hold.file.content = {**hold.file.content, "waitingOnYou": waiting_now}
            if hold.file.heartbeat() == "lost":
                logger.warning("agent: session %s's checkout lock file names someone else now",
                               session_id)
            self._store.update_lock(
                hold.lock_id, heartbeat_at=stamp, waiting_on_you=int(waiting_now)
            )

    def state_for(self, session_id: str) -> dict[str, Any] | None:
        """`SessionView.lock`: `{id, state}` while a row names this session, else null."""
        row = self._store.lock_for_session(session_id)
        return {"id": row["id"], "state": row["state"]} if row is not None else None

    def clarvis_runs(self) -> list[dict[str, Any]]:
        """Clarvis's own writing runs, for `GET /api/v1/codex` → `runs` (rows arrive in R4)."""
        return [row for row in self._store.locks() if row["holder_kind"] == CLARVIS_RUN]

    # ── Views ────────────────────────────────────────────────────────────────

    def view(self, row: dict[str, Any]) -> dict[str, Any]:
        """A `LockView` (`project-locks.json` → `lock_view`) for a row."""
        heartbeat = datetime.fromisoformat(str(row["heartbeat_at"]).replace("Z", "+00:00"))
        age = max(0, int((self._now() - heartbeat).total_seconds()))
        return {
            "id": row["id"],
            "workspace": {"name": Path(row["workspace_root"]).name, "root_hash": row["root_hash"]},
            "holder": {
                "kind": row["holder_kind"], "session_id": row["holder_session_id"],
                "window_id": row["holder_window_id"], "host": row["holder_host"],
                "since": row["acquired_at"],
            },
            "state": row["state"],
            "waiting_on_you": bool(row["waiting_on_you"]),
            "heartbeat_age_seconds": age,
            "verdict": self._verdict(row["holder_pid"], row["holder_pid_start"], age),
            "taken_over_from": row["taken_over_from"],
        }

    def _file_view(self, read: LockFileRead, claimant: Claimant) -> dict[str, Any]:
        """A `LockView` for a lock file no row describes: a window that held it without RAVIS."""
        content = read.content
        holder = mapping(content.get("holder"))
        age = int(read.heartbeat_age_seconds)
        return {
            "id": content.get("ravis_lock_id"),
            "workspace": {"name": claimant.root.name, "root_hash": claimant.root_hash},
            "holder": {name: holder.get(name) for name in
                       ("kind", "session_id", "window_id", "host", "since")},
            "state": content.get("state", "running"),
            "waiting_on_you": content.get("waitingOnYou") is True,
            "heartbeat_age_seconds": age,
            "verdict": self._verdict(holder.get("pid"), holder.get("pid_start"), age),
            "taken_over_from": content.get("takenOverFrom"),
        }

    def _locked_view(self, claimant: Claimant) -> dict[str, Any]:
        row = self._store.lock_for_root(claimant.root_hash)
        if row is not None:
            return self.view(row)
        return {"id": None, "workspace": {"name": claimant.root.name,
                                          "root_hash": claimant.root_hash}}

    def _verdict(self, pid: object, pid_start: object, age: int) -> Verdict:
        """The shared lock rule; a holder RAVIS can't probe counts as alive, never gone."""
        if not isinstance(pid, int) or not isinstance(pid_start, str):
            return "alive"
        if pid == self._pid:
            return "alive"
        probe, awake = probe_process(pid), observer_awake_seconds()
        if probe is None or awake is None:
            return "alive"
        return judge_lock(JudgedLock(pid, pid_start, age), probe, awake)

    def _stale_ravis_file(self, found: LockFileRead | None) -> bool:
        """A lock file a previous RAVIS instance left, whose process is gone: RAVIS may replace it.

        The restart adoption rule (review AB1) in its narrowest form — the file names RAVIS, not
        this instance, and that holder is `gone`. Anyone else's file is never touched. R4 adds the
        rest of the rule (rewriting a live previous instance's file, `superseded` rows).
        """
        if found is None or found.kind != "present":
            return False
        holder = found.content.get("holder", {})
        if holder.get("host") != "ravis" or not isinstance(holder.get("pid"), int):
            return False
        start = self._pid_start() or ""
        if holder["pid"] == self._pid and same_start(str(holder.get("pid_start")), start):
            return False
        verdict = self._verdict(holder["pid"], holder.get("pid_start"), 0)
        return verdict == "gone"

    # ── Rows and file contents ───────────────────────────────────────────────

    def _pid_start(self) -> str | None:
        if self._start is None:
            self._start = self._own_start()
        return self._start

    def _content(self, claimant: Claimant, lock_id: str) -> dict[str, Any]:
        holder = Holder(kind=CODEX_SESSION, session_id=claimant.id, pid=self._pid,
                        pid_start=self._pid_start() or "")
        content = lock_content(holder, task_id=claimant.task_id, now=self._now())
        return {**content, "ravis_lock_id": lock_id}

    def _row(self, claimant: Claimant, lock_id: str) -> dict[str, Any]:
        stamp = iso(self._now())
        return {
            "id": lock_id, "workspace_root": str(claimant.root), "root_hash": claimant.root_hash,
            "holder_kind": CODEX_SESSION, "holder_session_id": claimant.id,
            "holder_window_id": None, "holder_host": "ravis", "holder_pid": self._pid,
            "holder_pid_start": self._pid_start(), "state": "running", "waiting_on_you": 0,
            "heartbeat_at": stamp, "acquired_at": stamp,
        }

    def _changed(self, claimant: Claimant, lock_id: str, state: str) -> None:
        self._emit(
            "ravis.project_lock.changed", claimant.trace_id,
            {"lock_id": lock_id, "state": state, "holder_kind": CODEX_SESSION},
        )
