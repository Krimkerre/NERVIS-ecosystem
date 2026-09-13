"""Clarvis's own writing runs taking the project lock: `/api/v1/project-locks` (design §3.6, §6.3).

A run of Clarvis's own engine creates the checkout lock file first, then takes RAVIS's lock here and
gets a lease (`lk_…`, kept only as its sha256). It heartbeats every 15 s with the command it has
running, and releases only once its processes are confirmed gone. Shapes and codes are
`project-locks.json`'s; what the fixture leaves open is decided below and recorded in
`conventions.json` → `open_points`.

**Taking a lock** (`POST /api/v1/project-locks`), in order:
1. the body (422 `INVALID_REQUEST_BODY`), the folder and its git folder (422, `roots.py`);
2. **with a transfer token**: the root's lock must be `transferring` from a Codex session, the token
   its own and unexpired — else 409 `LOCK_TRANSFER_INVALID`. The lock moves to this window at once.
   **The window has already replaced RAVIS's checkout lock file** (Clarvis deletes it only while its
   heartbeat is unchanged, and RAVIS stopped heartbeating it at the transfer): RAVIS lets go of its
   descriptor and never writes that file again;
3. **the root already locked**: a lock the checkout's file shows is this window's own is adopted —
   a row a restart superseded (F-A9), this window registering again, or, with `adopt_file_lock`, a
   Codex session's row no turn is using while the file names this window. Otherwise 409
   `PROJECT_LOCKED {lock, takeover_allowed, attach_session_id?}`: a takeover is allowed for a
   Clarvis-engine holder that is `gone`, `unresponsive` or waiting on its owner;
4. a folder around or inside a locked one: 409 `NESTED_PROJECT_LOCKED {lock}`;
5. a checkout lock file naming anyone else, with no lock RAVIS knows: 409 `PROJECT_LOCKED` from the
   file. A missing file doesn't stop RAVIS's lock: RAVIS is the authority while it runs, and never
   writes a window's file.

**Takeover** (`POST …/{lid}/takeover`, no lease). Only the exact root the lock is for — a window
in a folder around or inside it is refused `CONFIRMATION_MISMATCH` (it gets
`NESTED_PROJECT_LOCKED` when it asks for its own folder, and must open that exact folder to take
this lock over). A Codex holder is 409 `ATTACH_INSTEAD`. A `gone` holder is taken without
confirmation; `unresponsive`, or `alive` and waiting, only with `confirm` naming its window id and
`since`; `alive` and working is 409 `HOLDER_ACTIVE`. Then, in this order: **the old lease is
revoked** (its next heartbeat gets `LEASE_REVOKED {taken_over_by}`); the command its last heartbeat
— or its lock file — recorded is stopped with its whole group and descendants (`group_kill.py`);
only once that is confirmed does the new window get the lock and a lease. If it can't be confirmed,
the lock stays `leftover`, 409 `PROCESSES_NOT_CONFIRMED_GONE {leftover: [{pid, start, comm}]}`, and
nobody holds a lease. **When the taking window finds no lock file in the checkout** (RAVIS served
`takeover_allowed` for a lock whose file is gone), it creates its own file first, then takes the
lock over here; RAVIS doesn't need the file.

**Transfer** (`POST …/{lid}/transfer`, a lease or the holding session's token). The lock goes to
`transferring` with a 15-minute token (`tt_…`). An expired token releases nothing: the lock stays
with its source, who takes it back — a window by heartbeating `state: running`, a Codex session by
starting a turn.

**Replays.** A retried create, takeover or transfer returns its first answer. The lease or token in
it is held in memory only; after a RAVIS restart the retry gets a new one, and the one lost with the
first answer stops working.
"""

from __future__ import annotations

import asyncio
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ravis.agent import refusals, roots
from ravis.agent.cleanup import lock_file_leftover
from ravis.agent.group_kill import GroupKill, GroupStop, RunningCommand, running_command
from ravis.agent.locks import CLARVIS_RUN, CODEX_SESSION, HOLDER_KINDS, ProjectLocks
from ravis.agent.requests import mapping
from ravis.agent.roots import related
from ravis.agent.store import AgentStore
from ravis.agent.tokens import new_id, token_matches, token_sha256
from ravis.codex.lock_file import LockFileRead, git_dir_for, iso, lock_file_path, read_lock_file
from ravis.codex.lock_rule import same_start
from ravis.codex.refusals import CodexRefusalError
from ravis.config import Settings

LEASE_PREFIX = "lk_"
TRANSFER_PREFIX = "tt_"
TRANSFER_LIFETIME = timedelta(minutes=15)
#: The states a heartbeat may report.
BEAT_STATES = frozenset({"running", "transferring", "stopping"})


def _invalid(message: str) -> CodexRefusalError:
    return CodexRefusalError("INVALID_REQUEST_BODY", 422, message)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def new_secret(prefix: str) -> str:
    """A lease or transfer token: the prefix and 32 random bytes."""
    return prefix + secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Window:
    id: str
    host: str
    pid: int
    pid_start: str


def window_from(value: object, id_key: str) -> Window | None:
    body = mapping(value)
    ident, pid, start = _text(body.get(id_key)), body.get("pid"), _text(body.get("pid_start"))
    if ident is None or start is None or not isinstance(pid, int) or pid <= 0:
        return None
    return Window(ident, str(body.get("host") or ""), pid, start)


@dataclass(frozen=True)
class AcquireRequest:
    workspace_root: str
    git_dir: object
    task_id: str
    holder: Window
    transfer_token: str | None
    adopt: bool


def acquire_request(body: dict[str, Any]) -> AcquireRequest:
    holder_body = mapping(body.get("holder"))
    holder = window_from(holder_body, "window_id")
    if holder_body.get("kind") != CLARVIS_RUN or holder is None:
        raise _invalid("holder must be {kind: clarvis_run, window_id, host, pid, pid_start}.")
    task = _text(body.get("clarvis_task_id"))
    if task is None:
        raise _invalid("clarvis_task_id must be the task's id.")
    token, adopt = body.get("transfer_token"), body.get("adopt_file_lock", False)
    if not isinstance(adopt, bool) or not isinstance(token, str | None):
        raise _invalid("transfer_token must be a string, and adopt_file_lock true or false.")
    return AcquireRequest(str(body.get("workspace_root") or ""), body.get("git_dir"), task,
                          holder, token or None, adopt)


@dataclass(frozen=True)
class Beat:
    waiting: bool
    state: str
    command: RunningCommand | None


def beat_request(body: dict[str, Any]) -> Beat:
    waiting, state, raw = body.get("waiting_on_you"), body.get("state", "running"), body.get(
        "running_command")
    command = running_command(raw)
    if not isinstance(waiting, bool) or state not in BEAT_STATES or (raw is not None
                                                                     and command is None):
        raise _invalid("A heartbeat carries waiting_on_you, state and running_command "
                       "{pid, pgid, start, comm} or null.")
    return Beat(waiting, str(state), command)


@dataclass(frozen=True)
class TakeoverRequest:
    workspace_root: object
    window: Window
    confirm: tuple[str | None, str | None]


def takeover_request(body: dict[str, Any]) -> TakeoverRequest:
    window = window_from(body.get("window"), "id")
    if window is None:
        raise _invalid("window must carry id, host, pid and pid_start.")
    confirm = mapping(body.get("confirm"))
    return TakeoverRequest(body.get("workspace_root"), window,
                           (_text(confirm.get("holder_window_id")),
                            _text(confirm.get("holder_since"))))


def names_window(read: LockFileRead, window: Window) -> bool:
    """Whether a checkout lock file is this window's own: its id, pid and start time."""
    holder = mapping(read.content.get("holder"))
    return (
        read.kind == "present" and holder.get("kind") == CLARVIS_RUN
        and holder.get("window_id") == window.id and holder.get("pid") == window.pid
        and same_start(str(holder.get("pid_start")), window.pid_start)
    )


def _holder_name(row: dict[str, Any] | None) -> str | None:
    if row is None:
        return None
    return _text(row["holder_window_id"]) or _text(row["holder_session_id"])


class WindowLocks:
    """The lock routes' decisions, over the rows `ProjectLocks` shares with Codex sessions."""

    def __init__(
        self,
        store: AgentStore,
        locks: ProjectLocks,
        settings: Settings,
        *,
        group_kill: GroupKill,
        turn_running: Callable[[str], bool],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._locks = locks
        self._settings = settings
        self.group_kill = group_kill
        self._turn_running = turn_running
        self._now = now
        #: Each lock's last reported running command, from its heartbeats (memory only).
        self._running: dict[str, RunningCommand] = {}
        #: The window that took a lock over, for the old lease's `LEASE_REVOKED`.
        self._revoked: dict[str, str | None] = {}
        self._takeovers = asyncio.Lock()

    # ── Reading ──────────────────────────────────────────────────────────────

    def current(self, raw_root: object) -> dict[str, Any] | None:
        """`GET /api/v1/project-locks?workspace_root=`: the lock on exactly that root, or null."""
        root = roots.workspace_root(raw_root, self._settings)
        row = self._store.lock_for_root(roots.root_hash(root))
        return None if row is None else self._locks.view(row)

    def _view(self, lock_id: str) -> dict[str, Any]:
        row = self._store.lock(lock_id)
        assert row is not None, lock_id
        return self._locks.view(row)

    def held(self, row: dict[str, Any]) -> CodexRefusalError:
        """409 `PROJECT_LOCKED` for a held root, with whether a takeover may follow."""
        view = self._locks.view(row)
        if row["holder_kind"] == CODEX_SESSION:
            return refusals.lock_held_by(view, takeover_allowed=False,
                                         attach_session_id=row["holder_session_id"] or "")
        allowed = view["verdict"] != "alive" or view["waiting_on_you"]
        return refusals.lock_held_by(view, takeover_allowed=bool(allowed), attach_session_id=None)

    # ── Taking a lock ────────────────────────────────────────────────────────

    def acquire(self, body: dict[str, Any]) -> tuple[dict[str, Any], str]:
        wanted = acquire_request(body)
        workspace = roots.workspace(wanted.workspace_root, wanted.git_dir, self._settings)
        if wanted.transfer_token is not None:
            return self._take_with_token(wanted, workspace)
        file = read_lock_file(lock_file_path(workspace.root, workspace.git_dir))
        mine = names_window(file, wanted.holder)
        row = self._store.lock_for_root(workspace.root_hash)
        if row is not None:
            if mine and self._adoptable(row, wanted):
                return self._adopt(row, wanted, workspace)
            raise self.held(row)
        self._refuse_nested(workspace.root)
        if file.kind == "present" and not mine:
            raise self._held_by_file(file, workspace)
        return self._insert(wanted, workspace)

    def _adoptable(self, row: dict[str, Any], wanted: AcquireRequest) -> bool:
        if row["state"] == "superseded":
            return True
        if row["holder_kind"] == CLARVIS_RUN:
            return bool(row["holder_window_id"] == wanted.holder.id)
        return wanted.adopt and not self._turn_running(str(row["holder_session_id"]))

    def _adopt(self, row: dict[str, Any], wanted: AcquireRequest, workspace: roots.Workspace
               ) -> tuple[dict[str, Any], str]:
        """The checkout's file is this window's: its lock replaces the row RAVIS had."""
        if row["holder_kind"] == CLARVIS_RUN:
            lease = new_secret(LEASE_PREFIX)
            self._store.update_lock(
                row["id"], lease_sha256=token_sha256(lease), holder_host=wanted.holder.host,
                holder_pid=wanted.holder.pid, holder_pid_start=wanted.holder.pid_start,
                heartbeat_at=iso(self._now()))
            self._revoked.pop(row["id"], None)
            return self._view(row["id"]), lease
        self._locks.hand_over(row["holder_session_id"])
        self._store.delete_lock(row["id"])
        self._locks.changed(row["id"], "released", row["holder_kind"])
        return self._insert(wanted, workspace)

    def _refuse_nested(self, root: Path) -> None:
        for row in self._store.locks():
            if related(Path(row["workspace_root"]), root):
                raise refusals.nested_project_locked(self._locks.view(row))

    def _held_by_file(self, file: LockFileRead, workspace: roots.Workspace) -> CodexRefusalError:
        view = self._locks.file_view(file, workspace.name, workspace.root_hash)
        holder = mapping(file.content.get("holder"))
        if holder.get("kind") == CODEX_SESSION:
            return refusals.lock_held_by(view, takeover_allowed=False,
                                         attach_session_id=_text(holder.get("session_id")) or "")
        allowed = view["verdict"] != "alive" or view["waiting_on_you"]
        return refusals.lock_held_by(view, takeover_allowed=bool(allowed), attach_session_id=None)

    def _insert(self, wanted: AcquireRequest, workspace: roots.Workspace
                ) -> tuple[dict[str, Any], str]:
        lock_id, lease, stamp = new_id("pl_"), new_secret(LEASE_PREFIX), iso(self._now())
        row = {
            "id": lock_id, "workspace_root": str(workspace.root), "root_hash": workspace.root_hash,
            "holder_kind": CLARVIS_RUN, "holder_session_id": None,
            "holder_window_id": wanted.holder.id, "holder_host": wanted.holder.host,
            "holder_pid": wanted.holder.pid, "holder_pid_start": wanted.holder.pid_start,
            "lease_sha256": token_sha256(lease), "state": "running", "waiting_on_you": 0,
            "heartbeat_at": stamp, "acquired_at": stamp,
        }
        try:
            self._store.insert_lock(row)
        except sqlite3.IntegrityError:
            existing = self._store.lock_for_root(workspace.root_hash)
            if existing is None:
                raise refusals.project_locked({"id": None}) from None
            raise self.held(existing) from None
        self._locks.changed(lock_id, "running", CLARVIS_RUN)
        return self._view(lock_id), lease

    def _take_with_token(self, wanted: AcquireRequest, workspace: roots.Workspace
                         ) -> tuple[dict[str, Any], str]:
        """A switch from Codex (§6.2 step 6): the lock moves to this window atomically."""
        row = self._store.lock_for_root(workspace.root_hash)
        token = str(wanted.transfer_token)
        if row is None or row["state"] != "transferring" or row["holder_kind"] != CODEX_SESSION:
            raise refusals.lock_transfer_invalid()
        if not self._locks.token_valid(row, token):
            raise refusals.lock_transfer_invalid()
        self._locks.hand_over(row["holder_session_id"])
        lease, stamp = new_secret(LEASE_PREFIX), iso(self._now())
        self._store.update_lock(
            row["id"], holder_kind=CLARVIS_RUN, holder_session_id=None,
            holder_window_id=wanted.holder.id, holder_host=wanted.holder.host,
            holder_pid=wanted.holder.pid, holder_pid_start=wanted.holder.pid_start,
            lease_sha256=token_sha256(lease), state="running", waiting_on_you=0,
            heartbeat_at=stamp, acquired_at=stamp, taken_over_from=None,
            transfer_token_sha256=None, transfer_expires_at=None,
        )
        self._locks.changed(row["id"], "running", CLARVIS_RUN)
        return self._view(row["id"]), lease

    # ── Holding one ──────────────────────────────────────────────────────────

    def leased(self, lock_id: str, lease: str | None) -> dict[str, Any]:
        """The lock this lease holds, or the fence: 409 `LEASE_REVOKED`."""
        if not lease:
            raise refusals.lease_required()
        row = self._store.lock(lock_id)
        if row is None or not row["lease_sha256"] or not token_matches(lease, row["lease_sha256"]):
            raise refusals.lease_revoked(self._revoked.get(lock_id) or _holder_name(row))
        return row

    def heartbeat(self, lock_id: str, lease: str | None, body: dict[str, Any]) -> dict[str, Any]:
        row = self.leased(lock_id, lease)
        beat = beat_request(body)
        fields: dict[str, Any] = {"heartbeat_at": iso(self._now()),
                                  "waiting_on_you": int(beat.waiting)}
        if beat.state == "running" and row["state"] == "transferring":
            # The holder's switch ended without the destination taking the project.
            fields.update(state="running", transfer_token_sha256=None, transfer_expires_at=None)
            self._locks.changed(lock_id, "running", CLARVIS_RUN)
        self._store.update_lock(lock_id, **fields)
        if beat.command is None:
            self._running.pop(lock_id, None)
        else:
            self._running[lock_id] = beat.command
        return self._view(lock_id)

    def release(self, lock_id: str, lease: str | None, body: dict[str, Any]) -> None:
        row = self.leased(lock_id, lease)
        if body.get("processes_confirmed_gone") is not True or row["state"] == "leftover":
            raise refusals.run_processes_not_confirmed_gone()
        self._store.delete_lock(lock_id)
        self._running.pop(lock_id, None)
        self._revoked.pop(lock_id, None)
        self._locks.changed(lock_id, "released", CLARVIS_RUN)

    def reissue_lease(self, lock_id: str, window_id: object) -> str:
        """A replayed create or takeover after a restart: a new lease for the same window."""
        row = self._store.lock(lock_id)
        if row is None or row["holder_kind"] != CLARVIS_RUN or row["holder_window_id"] != window_id:
            raise refusals.lease_revoked(self._revoked.get(lock_id) or _holder_name(row))
        lease = new_secret(LEASE_PREFIX)
        self._store.update_lock(lock_id, lease_sha256=token_sha256(lease))
        return lease

    # ── Takeover ─────────────────────────────────────────────────────────────

    async def takeover(self, lock_id: str, body: dict[str, Any]
                       ) -> tuple[dict[str, Any], str, str | None]:
        """(the lock, its new lease, the window it was taken from)."""
        wanted = takeover_request(body)
        root = roots.workspace_root(wanted.workspace_root, self._settings)
        async with self._takeovers:
            row = self._store.lock(lock_id)
            if row is None or row["root_hash"] != roots.root_hash(root):
                raise refusals.takeover_confirmation_mismatch()
            if row["holder_kind"] == CODEX_SESSION:
                raise refusals.attach_instead(row["holder_session_id"])
            self._allowed(row, wanted)
            return await self._take_over(row, wanted, root)

    def _allowed(self, row: dict[str, Any], wanted: TakeoverRequest) -> None:
        if row["state"] == "transferring" and self._locks.token_live(row):
            raise refusals.holder_active()
        verdict = self._locks.verdict(row)
        if verdict == "gone":
            return
        if verdict == "alive" and not row["waiting_on_you"] and row["state"] != "leftover":
            raise refusals.holder_active()
        if wanted.confirm != (row["holder_window_id"], row["acquired_at"]):
            raise refusals.takeover_confirmation_mismatch()

    async def _take_over(self, row: dict[str, Any], wanted: TakeoverRequest, root: Path
                         ) -> tuple[dict[str, Any], str, str | None]:
        lock_id, previous = row["id"], row["holder_window_id"]
        # The fence first: from here the old window's heartbeat is refused, so it writes nothing.
        self._store.update_lock(lock_id, lease_sha256=None)
        self._revoked[lock_id] = wanted.window.id
        stopped = await self._stop_running(row, root)
        if stopped.gone is not True:
            self._store.update_lock(lock_id, state="leftover")
            self._locks.changed(lock_id, "leftover", CLARVIS_RUN)
            raise refusals.run_processes_not_confirmed_gone(
                leftover=lock_file_leftover(stopped.survivors))
        lease, stamp = new_secret(LEASE_PREFIX), iso(self._now())
        self._store.update_lock(
            lock_id, holder_window_id=wanted.window.id, holder_host=wanted.window.host,
            holder_pid=wanted.window.pid, holder_pid_start=wanted.window.pid_start,
            lease_sha256=token_sha256(lease), state="running", waiting_on_you=0,
            heartbeat_at=stamp, acquired_at=stamp, taken_over_from=previous,
            transfer_token_sha256=None, transfer_expires_at=None,
        )
        self._running.pop(lock_id, None)
        self._locks.changed(lock_id, "running", CLARVIS_RUN)
        return self._view(lock_id), lease, previous

    async def _stop_running(self, row: dict[str, Any], root: Path) -> GroupStop:
        """The command the holder last reported running — or its lock file recorded — stopped."""
        command = self._running.get(row["id"]) or self._file_command(row, root)
        if command is None:
            return GroupStop(True)
        return await self.group_kill.stop(command)

    @staticmethod
    def _file_command(row: dict[str, Any], root: Path) -> RunningCommand | None:
        read = read_lock_file(lock_file_path(root, git_dir_for(root)))
        holder = mapping(read.content.get("holder"))
        if read.kind != "present" or holder.get("window_id") != row["holder_window_id"]:
            return None
        return running_command(read.content.get("running_command"))

    # ── Transfer ─────────────────────────────────────────────────────────────

    def transfer(self, row: dict[str, Any], to: object) -> dict[str, Any]:
        """The lock reserved for the other engine: `transferring`, with a 15-minute token."""
        if to not in HOLDER_KINDS or to == row["holder_kind"]:
            raise _invalid("to must be the other engine: codex_session or clarvis_run.")
        if row["state"] in ("leftover", "superseded"):
            raise refusals.run_processes_not_confirmed_gone()
        token, expires = new_secret(TRANSFER_PREFIX), self._now() + TRANSFER_LIFETIME
        self._store.update_lock(row["id"], state="transferring",
                                transfer_token_sha256=token_sha256(token),
                                transfer_expires_at=iso(expires))
        if row["holder_kind"] == CODEX_SESSION:
            self._locks.pause_for_transfer(str(row["holder_session_id"]))
        self._locks.changed(row["id"], "transferring", row["holder_kind"])
        return {"transfer_token": token, "expires_at": iso(expires)}

    def reissue_transfer(self, lock_id: str) -> str:
        """A replayed transfer after a restart: a new token for the same, still-live transfer."""
        row = self._store.lock(lock_id)
        if row is None or row["state"] != "transferring" or not self._locks.token_live(row):
            raise refusals.lock_transfer_invalid()
        token = new_secret(TRANSFER_PREFIX)
        self._store.update_lock(lock_id, transfer_token_sha256=token_sha256(token))
        return token
