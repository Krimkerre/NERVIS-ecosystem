"""The project lock as a Codex session holds it (design §3.6, §6.3).

**One writer per project** (runbook §2.2 invariant 1) is two layers: RAVIS's `project_lock` row,
authoritative while RAVIS is up, and the checkout lock file (`codex/lock_file.py`), written by
whoever actually writes — RAVIS for a Codex session, the window for a run of Clarvis's own engine.
A Codex session takes both when it is created, and **may start a turn only while it holds them**
(final check F-A1). Clarvis's own runs take theirs through `/api/v1/project-locks`
(`lock_api.py`); both share the rows, the views and the verdicts here.

**A Codex session's side:**
- `acquire`: take a free root atomically — the file first (`O_EXCL`), then the row (`root_hash` is
  unique) — refusing `PROJECT_LOCKED` for the same root, `NESTED_PROJECT_LOCKED` for a folder inside
  or around a locked one, and `LOCK_SUPERSEDED` for a root whose row a restart superseded; or take a
  lock **with a transfer token** (a switch back, §6.2), refusing `LOCK_TRANSFER_INVALID`;
- `holds` and `take_for_turn`: F-A1's check, under the session's action lock. A session whose own
  lock is `transferring` and whose file still names RAVIS takes it back (the switch didn't happen);
- `release` when a session is settled idle or ended; **kept** when settled for a transfer;
- `heartbeat`: every 15 s the file's heartbeat, "waiting on you" and the task's recorded command
  processes (`leftover`, `{pid, start, comm}`), so a window can judge the holder and, if RAVIS is
  down, end what it left.

**A transfer from Codex to a window** (§6.2 Codex → Clarvis). `POST …/transfer` puts the row in
`transferring` and **RAVIS stops heartbeating the file**, having written `state: transferring` once:
the destination window deletes RAVIS's file only while its heartbeat is unchanged, so a heartbeat
in between would lose it the race. When the window then takes the lock with the token, RAVIS lets go
of its descriptor (`hand_over`) and never writes that file again; the window's new file replaces it.

**When a Codex session takes a lock with a token** it replaces the checkout lock file — a new
file, a new inode, holder `codex_session` — rather than editing the window's. The window's own
release then finds the file isn't its own and deletes nothing (Clarvis `projectLock.ts`: "RAVIS
should replace the file").

**Superseded rows** are written by the restart adoption rule (`reconcile.py`) and read here.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import uuid
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
    ProcessProbe,
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
HOLDER_KINDS = frozenset({CODEX_SESSION, CLARVIS_RUN})


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


#: The MEP publisher's `emit`, as `AgentSessions` adapts it: (event, trace id, data).
Emit = Callable[[str, str, dict[str, Any]], None]


class ProjectLocks:
    """RAVIS's side of the project lock for Codex sessions, and the views both engines share."""

    def __init__(
        self,
        store: AgentStore,
        *,
        emit: Emit,
        pid: int | None = None,
        own_start: Callable[[], str | None],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        probe: Callable[[int], ProcessProbe | None] = probe_process,
        awake: Callable[[], float | None] = observer_awake_seconds,
    ) -> None:
        self._store = store
        self._emit = emit
        self._pid = os.getpid() if pid is None else pid
        self._own_start = own_start
        self._start: str | None = None
        self._now = now
        self._probe = probe
        self._awake = awake
        self._holds: dict[str, _Hold] = {}
        #: Sessions whose lock is `transferring`: their file is left for the destination.
        self._paused: set[str] = set()

    @property
    def pid(self) -> int:
        return self._pid

    # ── Taking the lock ──────────────────────────────────────────────────────

    def take_for_turn(self, claimant: Claimant, transfer_token: str | None) -> None:
        """F-A1: a turn starts only holding the lock — held already, or taken now, atomically."""
        if transfer_token is not None:
            self.acquire(claimant, transfer_token)
            return
        row = self._store.lock_for_root(claimant.root_hash)
        if row is None or row["holder_session_id"] != claimant.id:
            self.acquire(claimant, None)
            return
        if row["state"] == "superseded":
            raise refusals.lock_superseded()
        if row["state"] not in ("running", "transferring") or not self._file_holds(claimant, row):
            raise refusals.project_locked(self.view(row))
        if row["state"] == "transferring":
            self._take_back(claimant, row)

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
        self.changed(lock_id, "running", CODEX_SESSION, claimant.trace_id)

    def _refuse_if_locked(self, claimant: Claimant) -> None:
        for row in self._store.locks():
            if row["root_hash"] == claimant.root_hash:
                if row["state"] == "superseded":
                    raise refusals.lock_superseded()
                raise refusals.project_locked(self.view(row))
            if related(Path(row["workspace_root"]), claimant.root):
                raise refusals.nested_project_locked(self.view(row))

    def _take_with_token(self, claimant: Claimant, token: str) -> None:
        """A switch back (§6.2): the lock moves to this session, and its file is replaced."""
        row = self._store.lock_for_root(claimant.root_hash)
        if row is None or row["state"] != "transferring" or not self.token_valid(row, token):
            raise refusals.lock_transfer_invalid()
        hold = self._holds.get(claimant.id)
        if row["holder_session_id"] != claimant.id or hold is None or hold.lock_id != row["id"]:
            # The file is the window's now: replace it, and let go of any old descriptor of ours.
            if hold is not None:
                hold.file.abandon()
            hold = _Hold(row["id"], self.replace_file(claimant, row["id"]))
        stamp = iso(self._now())
        self._store.update_lock(
            row["id"], holder_kind=CODEX_SESSION, holder_session_id=claimant.id,
            holder_window_id=None, holder_host="ravis", holder_pid=self._pid,
            holder_pid_start=self._pid_start(), lease_sha256=None, state="running",
            heartbeat_at=stamp, acquired_at=stamp, transfer_token_sha256=None,
            transfer_expires_at=None, taken_over_from=None,
        )
        self._holds[claimant.id] = hold
        self._paused.discard(claimant.id)
        self.changed(row["id"], "running", CODEX_SESSION, claimant.trace_id)

    def _take_back(self, claimant: Claimant, row: dict[str, Any]) -> None:
        """This session's own transfer didn't happen: the lock is running again, and heartbeats."""
        self._store.update_lock(row["id"], state="running", transfer_token_sha256=None,
                                transfer_expires_at=None)
        self._paused.discard(claimant.id)
        hold = self._holds.get(claimant.id)
        if hold is not None:
            hold.file.content = {**hold.file.content, "state": "running"}
            hold.file.heartbeat()
        self.changed(row["id"], "running", CODEX_SESSION, claimant.trace_id)

    def token_valid(self, row: dict[str, Any], token: str) -> bool:
        stored = row["transfer_token_sha256"]
        return bool(stored) and token_matches(token, stored) and self.token_live(row)

    def token_live(self, row: dict[str, Any]) -> bool:
        expires = row["transfer_expires_at"]
        return isinstance(expires, str) and expires > iso(self._now())

    def replace_file(self, claimant: Claimant, lock_id: str, state: str = "running"
                     ) -> HeldLockFile:
        """Write RAVIS's lock file over another with a rename, so it is a new file."""
        path = lock_file_path(claimant.root, claimant.git_dir)
        content = {**self._content(claimant, lock_id), "state": state}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
        descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, encode(content))
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        return HeldLockFile(os.open(path, os.O_RDWR), path, content)

    def hold(self, session_id: str, lock_id: str, held: HeldLockFile, *, paused: bool) -> None:
        """A file RAVIS holds for a session again, after a restart (`reconcile.py`)."""
        old = self._holds.pop(session_id, None)
        if old is not None:
            old.file.abandon()
        self._holds[session_id] = _Hold(lock_id, held)
        if paused:
            self._paused.add(session_id)

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
        self._paused.discard(claimant.id)
        if row is not None:
            self._store.delete_lock(row["id"])
            self.changed(row["id"], "released", CODEX_SESSION, claimant.trace_id)
        if hold is not None:
            hold.file.release()
        elif row is not None and self._file_holds(claimant, row):
            lock_file_path(claimant.root, claimant.git_dir).unlink(missing_ok=True)

    def pause_for_transfer(self, session_id: str) -> None:
        """The lock is `transferring`: say so in the file once, then leave it to the destination."""
        hold = self._holds.get(session_id)
        if hold is not None and session_id not in self._paused:
            hold.file.content = {**hold.file.content, "state": "transferring"}
            hold.file.heartbeat()
        self._paused.add(session_id)

    def hand_over(self, session_id: str | None) -> None:
        """A window took this session's lock with its token: forget the file, write nothing."""
        if session_id is None:
            return
        hold = self._holds.pop(session_id, None)
        if hold is not None:
            hold.file.abandon()
        self._paused.discard(session_id)

    def heartbeat(self, waiting: Mapping[str, bool]) -> None:
        """Every 15 s: each held file's heartbeat and "waiting on you", and the rows' too."""
        stamp = iso(self._now())
        for session_id, hold in list(self._holds.items()):
            if session_id in self._paused:
                continue
            waiting_now = waiting.get(session_id, False)
            hold.file.content = {**hold.file.content, "waitingOnYou": waiting_now}
            if hold.file.heartbeat() == "lost":
                logger.warning("agent: session %s's checkout lock file names someone else now",
                               session_id)
            self._store.update_lock(
                hold.lock_id, heartbeat_at=stamp, waiting_on_you=int(waiting_now)
            )

    def record_family(self, session_id: str, entries: list[dict[str, Any]]) -> None:
        """The task's command processes in its lock file's `leftover`, when they changed."""
        hold = self._holds.get(session_id)
        if hold is None or session_id in self._paused:
            return
        if hold.file.content.get("leftover") == entries:
            return
        hold.file.content = {**hold.file.content, "leftover": entries}
        hold.file.heartbeat()

    def state_for(self, session_id: str) -> dict[str, Any] | None:
        """`SessionView.lock`: `{id, state}` while a row names this session, else null."""
        row = self._store.lock_for_session(session_id)
        return {"id": row["id"], "state": row["state"]} if row is not None else None

    def clarvis_runs(self) -> list[dict[str, Any]]:
        """Clarvis's own writing runs, for `GET /api/v1/codex` → `runs`."""
        return [row for row in self._store.locks() if row["holder_kind"] == CLARVIS_RUN]

    # ── Views and verdicts ───────────────────────────────────────────────────

    def view(self, row: dict[str, Any]) -> dict[str, Any]:
        """A `LockView` (`project-locks.json` → `lock_view`) for a row."""
        age = self.heartbeat_age(row)
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

    def heartbeat_age(self, row: dict[str, Any]) -> int:
        heartbeat = datetime.fromisoformat(str(row["heartbeat_at"]).replace("Z", "+00:00"))
        return max(0, int((self._now() - heartbeat).total_seconds()))

    def verdict(self, row: dict[str, Any]) -> Verdict:
        return self._verdict(row["holder_pid"], row["holder_pid_start"], self.heartbeat_age(row))

    def file_view(self, read: LockFileRead, name: str, root_hash: str) -> dict[str, Any]:
        """A `LockView` for a lock file no row describes: a window that held it without RAVIS."""
        content = read.content
        holder = mapping(content.get("holder"))
        age = int(read.heartbeat_age_seconds)
        return {
            "id": content.get("ravis_lock_id"),
            "workspace": {"name": name, "root_hash": root_hash},
            "holder": {key: holder.get(key) for key in
                       ("kind", "session_id", "window_id", "host", "since")},
            "state": content.get("state", "running"),
            "waiting_on_you": content.get("waitingOnYou") is True,
            "heartbeat_age_seconds": age,
            "verdict": self._verdict(holder.get("pid"), holder.get("pid_start"), age),
            "taken_over_from": content.get("takenOverFrom"),
        }

    def _file_view(self, read: LockFileRead, claimant: Claimant) -> dict[str, Any]:
        return self.file_view(read, claimant.root.name, claimant.root_hash)

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
        if pid == self._pid and same_start(pid_start, self._pid_start() or ""):
            return "alive"
        probe, awake = self._probe(pid), self._awake()
        if probe is None or awake is None:
            return "alive"
        return judge_lock(JudgedLock(pid, pid_start, age), probe, awake)

    def names_ravis_instance(self, holder: Mapping[str, Any],
                             instances: list[tuple[int, str]]) -> bool:
        """Whether a lock file's holder is one of RAVIS's own recorded instances."""
        pid, start = holder.get("pid"), holder.get("pid_start")
        if holder.get("host") != "ravis" or not isinstance(pid, int) or not isinstance(start, str):
            return False
        return any(pid == known and same_start(start, begun) for known, begun in instances)

    def _stale_ravis_file(self, found: LockFileRead | None) -> bool:
        """A lock file a previous RAVIS instance left, whose process is gone: RAVIS may replace it.

        The restart adoption rule (review AB1) as a create meets it: the file names RAVIS, not this
        instance, and that holder is `gone`. Anyone else's file is never touched.
        """
        if found is None or found.kind != "present":
            return False
        holder = found.content.get("holder", {})
        if holder.get("host") != "ravis" or not isinstance(holder.get("pid"), int):
            return False
        start = self._pid_start() or ""
        if holder["pid"] == self._pid and same_start(str(holder.get("pid_start")), start):
            return False
        return self._verdict(holder["pid"], holder.get("pid_start"), 0) == "gone"

    # ── Rows, file contents and events ───────────────────────────────────────

    def _pid_start(self) -> str | None:
        if self._start is None:
            self._start = self._own_start()
        return self._start

    def pid_start(self) -> str | None:
        return self._pid_start()

    def _content(self, claimant: Claimant, lock_id: str) -> dict[str, Any]:
        holder = Holder(kind=CODEX_SESSION, session_id=claimant.id, pid=self._pid,
                        pid_start=self._pid_start() or "")
        content = lock_content(holder, task_id=claimant.task_id, now=self._now())
        return {**content, "ravis_lock_id": lock_id}

    def create_for(self, claimant: Claimant, lock_id: str, state: str = "running"
                   ) -> HeldLockFile | None:
        """RAVIS's file for a session's lock, created atomically; None when someone has one."""
        created = create_lock_file(lock_file_path(claimant.root, claimant.git_dir),
                                   {**self._content(claimant, lock_id), "state": state})
        return created.held

    def _row(self, claimant: Claimant, lock_id: str) -> dict[str, Any]:
        stamp = iso(self._now())
        return {
            "id": lock_id, "workspace_root": str(claimant.root), "root_hash": claimant.root_hash,
            "holder_kind": CODEX_SESSION, "holder_session_id": claimant.id,
            "holder_window_id": None, "holder_host": "ravis", "holder_pid": self._pid,
            "holder_pid_start": self._pid_start(), "state": "running", "waiting_on_you": 0,
            "heartbeat_at": stamp, "acquired_at": stamp,
        }

    def changed(self, lock_id: str, state: str, holder_kind: str, trace_id: str | None = None
                ) -> None:
        """`ravis.project_lock.changed {lock_id, state, holder_kind}`: ids and states only."""
        self._emit(
            "ravis.project_lock.changed", trace_id or uuid.uuid4().hex,
            {"lock_id": lock_id, "state": state, "holder_kind": holder_kind},
        )
