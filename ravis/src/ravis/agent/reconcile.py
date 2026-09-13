"""The project lock after a RAVIS restart: the restart adoption rule (design §4.4, §6.3; AB1).

On startup, after every task a previous RAVIS left mid-turn is `uncertain` and its recorded
processes are ended (`session.py` → `recover`), and **before any agent-session or project-lock
route answers** (`AgentSessions.ready`), RAVIS looks at each lock its database says a Codex session
holds, and at that checkout's lock file:
- **it names one of RAVIS's own recorded instances** (`ravis_instance`), for this session: RAVIS
  rewrites it with its new pid and start time, and keeps the lock;
- **it names anyone else** — a Clarvis window, **whatever its verdict** — or can't be read: RAVIS
  **leaves the file untouched** and marks its lock `superseded`; the session's `settle-claim`,
  `turns` and a resuming create get 409 `LOCK_SUPERSEDED`;
- **it is missing**, and RAVIS's atomic create wins: RAVIS creates it and keeps the lock;
- **it is missing, and a window's create won**: as for anyone else, `superseded`.

(`lock-rule-cases.json` → `adoption_cases`; `codex/lock_rule.py` → `adoption_decision`.) A gone
window still owns its uncommitted work, so even its file is left alone.

**A superseded lock ends in one of two ways.** The window registers its file lock with
`adopt_file_lock` (or releases after registering), and its lock replaces the superseded row
(`lock_api.py`) — the Codex session can then be settled by a window holding the lock, and continued
(F-A9). Or the window lets go of its file without RAVIS: every heartbeat interval RAVIS looks again
at each superseded lock's checkout, and **a file gone with nothing else holding the root** is
recreated atomically, the lock `running` again.

A lock row whose session no longer exists is dropped, and its file deleted only while it names a
RAVIS instance for that session.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from ravis.agent.locks import CODEX_SESSION, ProjectLocks
from ravis.agent.requests import mapping
from ravis.agent.store import AgentStore
from ravis.codex.lock_file import HeldLockFile, LockFileRead, git_dir_for, lock_file_path
from ravis.codex.lock_file import read_lock_file as read_file
from ravis.codex.lock_rule import AdoptionOutcome, FoundLockFile, Verdict, adoption_decision

logger = logging.getLogger("ravis")


class Holder(Protocol):
    """What reconciliation needs of a session holding a lock."""

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
    @property
    def name(self) -> str: ...
    @property
    def over(self) -> bool: ...


class LockReconciler:
    def __init__(self, store: AgentStore, locks: ProjectLocks) -> None:
        self._store = store
        self._locks = locks

    def adopt(self, sessions: Mapping[str, Holder], instances: list[tuple[int, str]]) -> None:
        """The restart adoption rule for every lock a Codex session holds."""
        for row in self._store.locks():
            if row["holder_kind"] != CODEX_SESSION:
                continue
            session = sessions.get(str(row["holder_session_id"]))
            if session is None or session.over:
                self._drop_orphan(row, instances)
                continue
            self._adopt_one(session, row, instances)

    def recheck(self, sessions: Mapping[str, Holder]) -> None:
        """A superseded lock whose checkout has no lock file any more: RAVIS's again."""
        for row in self._store.locks():
            session = sessions.get(str(row["holder_session_id"]))
            if row["state"] != "superseded" or session is None:
                continue
            if read_file(lock_file_path(session.root, session.git_dir)).kind != "absent":
                continue
            held = self._locks.create_for(session, row["id"])
            if held is not None:
                self._keep(session, row, held, state="running")

    def _adopt_one(self, session: Holder, row: dict[str, Any],
                   instances: list[tuple[int, str]]) -> None:
        read = read_file(lock_file_path(session.root, session.git_dir))
        found = self._found(read, session, instances)
        held: HeldLockFile | None = None
        if found is None:
            held = self._locks.create_for(session, row["id"], row["state"])
            outcome = adoption_decision(None, create_race_lost=held is None)
        else:
            outcome = adoption_decision(found, create_race_lost=False)
            if outcome.action == "rewrite":
                held = self._locks.replace_file(session, row["id"], row["state"])
        self._apply(session, row, outcome, held)

    def _found(self, read: LockFileRead, session: Holder, instances: list[tuple[int, str]]
               ) -> FoundLockFile | None:
        if read.kind == "absent":
            return None
        holder = mapping(read.content.get("holder"))
        ours = (read.kind == "present" and holder.get("session_id") == session.id
                and self._locks.names_ravis_instance(holder, instances))
        # The verdict is kept for the record; the rule decides on "whose file is it" alone.
        verdict: Verdict = "alive"
        if read.kind == "present" and not ours:
            verdict = self._locks._verdict(holder.get("pid"), holder.get("pid_start"),
                                           int(read.heartbeat_age_seconds))
        return FoundLockFile(names_previous_ravis_instance=ours, verdict=verdict)

    def _apply(self, session: Holder, row: dict[str, Any], outcome: AdoptionOutcome,
               held: HeldLockFile | None) -> None:
        if held is not None:
            self._keep(session, row, held, state=row["state"])
            return
        if outcome.ravis_lock_state == "superseded" and row["state"] != "superseded":
            self._store.update_lock(row["id"], state="superseded")
            self._locks.changed(row["id"], "superseded", CODEX_SESSION, session.trace_id)
            logger.warning("agent: another editor holds %s's checkout lock file; its Codex task "
                           "waits until that editor lets go", session.name)

    def _keep(self, session: Holder, row: dict[str, Any], held: HeldLockFile, *, state: str
              ) -> None:
        self._locks.hold(session.id, row["id"], held, paused=state == "transferring")
        self._store.update_lock(row["id"], state=state, holder_pid=self._locks.pid,
                                holder_pid_start=self._locks.pid_start(),
                                heartbeat_at=self._store.stamp())
        if state != row["state"]:
            self._locks.changed(row["id"], state, CODEX_SESSION, session.trace_id)

    def _drop_orphan(self, row: dict[str, Any], instances: list[tuple[int, str]]) -> None:
        root = Path(row["workspace_root"])
        path = lock_file_path(root, git_dir_for(root))
        read = read_file(path)
        holder = mapping(read.content.get("holder"))
        if (read.kind == "present" and holder.get("session_id") == row["holder_session_id"]
                and self._locks.names_ravis_instance(holder, instances)):
            path.unlink(missing_ok=True)
        self._store.delete_lock(row["id"])
        self._locks.changed(row["id"], "released", CODEX_SESSION)
