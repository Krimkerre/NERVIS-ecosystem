"""Migrations 8 to 10's tables, as the relay reads and writes them (design §4.10; RAVIS.md §17).

Shaped like `sessions.py`'s `SessionStore`: one class over the shared `Database`, an injected
clock, rows in and out as plain dicts. **Metadata only** — ids, states, kinds, counts, timestamps
and the workspace's real path and name. Nothing a caller passes can put text of a prompt, an
approval, a command or its output here: each write names its columns, and every column is one of
those kinds.

**Retention** (settled defaults, design §4.10), applied by `enforce_retention` at start and hourly:
sessions, their turns and requests 30 days after the session ended; process rows 24 hours after
they were confirmed gone; kept answers after 24 hours. A lock row exists only while it is held.

Timestamps are ISO-8601 UTC strings (`2026-09-13T01:12:00Z`), which sort as they compare.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ravis.codex.lock_file import iso
from ravis.storage.database import Database

SESSION_COLUMNS = frozenset({
    "id", "application_id", "workspace_root", "workspace_root_hash", "workspace_name", "git_dir",
    "clarvis_task_id", "engine", "codex_thread_id", "active_turn_id", "last_turn_id", "model",
    "effort", "mode", "file_rules", "state", "token_sha256", "trace_id", "runtime_sha256",
    "account_fingerprint", "branch_name", "head_commit_at_start", "max_steps", "last_event_id",
    "created_at", "updated_at", "ended_at",
})
LOCK_COLUMNS = frozenset({
    "id", "workspace_root", "root_hash", "holder_kind", "holder_session_id", "holder_window_id",
    "holder_host", "holder_pid", "holder_pid_start", "lease_sha256", "state", "waiting_on_you",
    "heartbeat_at", "acquired_at", "taken_over_from", "transfer_token_sha256",
    "transfer_expires_at",
})
SESSIONS_KEPT = timedelta(days=30)
PROCESSES_KEPT = timedelta(hours=24)
ANSWERS_KEPT = timedelta(hours=24)
#: A deleted thread's record, kept as long as a session's, then dropped.
THREADS_KEPT = timedelta(days=30)
#: How many of RAVIS's own past instances are remembered for the restart adoption rule.
INSTANCES_KEPT = 20


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _columns(fields: dict[str, Any], allowed: frozenset[str]) -> list[str]:
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"not columns of this table: {sorted(unknown)}")
    return list(fields)


class AgentStore:
    """The relay's rows. Safe from any thread: the database gives each its own connection."""

    def __init__(self, database: Database, now: Callable[[], datetime] = _utc_now) -> None:
        self._database = database
        self._now = now

    def stamp(self) -> str:
        return iso(self._now())

    def _execute(self, sql: str, values: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor = self._database.connection.execute(sql, values)
        return [dict(row) for row in cursor.fetchall()]

    # ── Sessions ─────────────────────────────────────────────────────────────

    def insert_session(self, row: dict[str, Any]) -> None:
        names = _columns(row, SESSION_COLUMNS)
        marks = ", ".join("?" for _ in names)
        self._execute(
            f"INSERT INTO agent_session ({', '.join(names)}) VALUES ({marks})",
            tuple(row[name] for name in names),
        )

    def update_session(self, session_id: str, **fields: Any) -> None:
        fields.setdefault("updated_at", self.stamp())
        names = _columns(fields, SESSION_COLUMNS)
        assignments = ", ".join(f"{name} = ?" for name in names)
        self._execute(
            f"UPDATE agent_session SET {assignments} WHERE id = ?",
            (*(fields[name] for name in names), session_id),
        )

    def session(self, session_id: str) -> dict[str, Any] | None:
        rows = self._execute("SELECT * FROM agent_session WHERE id = ?", (session_id,))
        return rows[0] if rows else None

    def live_sessions(self) -> list[dict[str, Any]]:
        return self._execute(
            "SELECT * FROM agent_session WHERE ended_at IS NULL ORDER BY created_at"
        )

    # ── Turns and requests ───────────────────────────────────────────────────

    def insert_turn(self, session_id: str, turn_id: str, kind: str) -> None:
        self._execute(
            "INSERT OR IGNORE INTO agent_turn (id, session_id, kind, started_at) "
            "VALUES (?, ?, ?, ?)",
            (turn_id, session_id, kind, self.stamp()),
        )

    def end_turn(self, session_id: str, turn_id: str, status: str, *, uncertain: bool) -> None:
        self._execute(
            "UPDATE agent_turn SET ended_at = ?, status = ?, uncertain = ? "
            "WHERE session_id = ? AND id = ? AND ended_at IS NULL",
            (self.stamp(), status, int(uncertain), session_id, turn_id),
        )

    def mark_uncertain(self, session_id: str) -> None:
        """A turn a previous RAVIS was running: its outcome is unknown (design §4.4)."""
        self._execute(
            "UPDATE agent_turn SET ended_at = ?, status = 'uncertain', uncertain = 1 "
            "WHERE session_id = ? AND ended_at IS NULL",
            (self.stamp(), session_id),
        )
        self._execute(
            "UPDATE agent_request SET resolved_at = ?, resolved_by = 'turn_ended' "
            "WHERE session_id = ? AND resolved_at IS NULL",
            (self.stamp(), session_id),
        )

    def insert_request(
        self, request_id: str, session_id: str, turn_id: str | None, codex_id: str, kind: str,
        *, host: str | None = None,
    ) -> None:
        self._execute(
            "INSERT INTO agent_request "
            "(id, session_id, turn_id, codex_request_id, kind, host, opened_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (request_id, session_id, turn_id, codex_id, kind, host, self.stamp()),
        )

    def resolve_request(self, request_id: str, by: str, decision_kind: str) -> None:
        self._execute(
            "UPDATE agent_request SET resolved_at = ?, resolved_by = ?, decision_kind = ? "
            "WHERE id = ? AND resolved_at IS NULL",
            (self.stamp(), by, decision_kind, request_id),
        )

    def request(self, request_id: str) -> dict[str, Any] | None:
        rows = self._execute("SELECT * FROM agent_request WHERE id = ?", (request_id,))
        return rows[0] if rows else None

    # ── Kept answers (idempotency) ───────────────────────────────────────────

    def kept_answer(self, scope: str, key: str) -> dict[str, Any] | None:
        horizon = iso(self._now() - ANSWERS_KEPT)
        rows = self._execute(
            "SELECT * FROM agent_idempotency WHERE scope = ? AND key = ? AND created_at >= ?",
            (scope, key, horizon),
        )
        return rows[0] if rows else None

    def keep_answer(
        self, scope: str, key: str, body_sha256: str, status: int, response_json: str
    ) -> None:
        self._execute(
            "INSERT OR REPLACE INTO agent_idempotency "
            "(scope, key, body_sha256, status, response_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (scope, key, body_sha256, status, response_json, self.stamp()),
        )

    # ── Project locks ────────────────────────────────────────────────────────

    def locks(self) -> list[dict[str, Any]]:
        return self._execute("SELECT * FROM project_lock ORDER BY acquired_at")

    def lock_for_root(self, root_hash: str) -> dict[str, Any] | None:
        rows = self._execute("SELECT * FROM project_lock WHERE root_hash = ?", (root_hash,))
        return rows[0] if rows else None

    def lock_for_session(self, session_id: str) -> dict[str, Any] | None:
        rows = self._execute(
            "SELECT * FROM project_lock WHERE holder_session_id = ?", (session_id,)
        )
        return rows[0] if rows else None

    def insert_lock(self, row: dict[str, Any]) -> None:
        """Raises `sqlite3.IntegrityError` when the root is held already: `root_hash` is unique."""
        names = _columns(row, LOCK_COLUMNS)
        marks = ", ".join("?" for _ in names)
        self._execute(
            f"INSERT INTO project_lock ({', '.join(names)}) VALUES ({marks})",
            tuple(row[name] for name in names),
        )

    def update_lock(self, lock_id: str, **fields: Any) -> None:
        names = _columns(fields, LOCK_COLUMNS)
        assignments = ", ".join(f"{name} = ?" for name in names)
        self._execute(
            f"UPDATE project_lock SET {assignments} WHERE id = ?",
            (*(fields[name] for name in names), lock_id),
        )

    def delete_lock(self, lock_id: str) -> None:
        self._execute("DELETE FROM project_lock WHERE id = ?", (lock_id,))

    def lock(self, lock_id: str) -> dict[str, Any] | None:
        rows = self._execute("SELECT * FROM project_lock WHERE id = ?", (lock_id,))
        return rows[0] if rows else None

    # ── Recorded command processes (design §4.5 item 3) ──────────────────────

    def record_process(
        self, session_id: str, turn_id: str | None, identity: tuple[int, str], comm: str,
        attribution: str,
    ) -> None:
        """A process attributed to a task: once per task, pid and start time (migration 9)."""
        pid, start = identity
        self._execute(
            "INSERT OR IGNORE INTO agent_process "
            "(session_id, turn_id, pid, start_time, comm, attribution, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, turn_id, pid, start, comm, attribution, self.stamp()),
        )

    def live_processes(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """Recorded processes not yet confirmed gone: one task's, or every task's."""
        if session_id is None:
            return self._execute("SELECT * FROM agent_process WHERE confirmed_gone_at IS NULL")
        return self._execute(
            "SELECT * FROM agent_process WHERE session_id = ? AND confirmed_gone_at IS NULL",
            (session_id,),
        )

    def confirm_gone(self, session_id: str, identities: list[tuple[int, str]]) -> None:
        for pid, start in identities:
            self._execute(
                "UPDATE agent_process SET confirmed_gone_at = ? "
                "WHERE session_id = ? AND pid = ? AND start_time = ? AND confirmed_gone_at IS NULL",
                (self.stamp(), session_id, pid, start),
            )

    # ── RAVIS's own instances (the restart adoption rule, design §6.3) ───────

    def instances(self) -> list[dict[str, Any]]:
        """Every RAVIS instance recorded so far, newest first."""
        return self._execute("SELECT * FROM ravis_instance ORDER BY started_at DESC, rowid DESC")

    def record_instance(self, pid: int, pid_start: str) -> None:
        """This instance, kept with the last few: a lock file may name any of them."""
        self._execute(
            "INSERT INTO ravis_instance (pid, pid_start, started_at) VALUES (?, ?, ?)",
            (pid, pid_start, self.stamp()),
        )
        self._execute(
            "DELETE FROM ravis_instance WHERE rowid NOT IN "
            "(SELECT rowid FROM ravis_instance ORDER BY rowid DESC LIMIT ?)",
            (INSTANCES_KEPT,),
        )

    # ── Codex's threads, kept past their task's records (design §4.10) ───────

    def remember_thread(self, thread_id: str, workspace_root: str, git_dir: str | None) -> None:
        self._execute(
            "INSERT INTO agent_thread (thread_id, workspace_root, git_dir, last_used_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (thread_id) DO UPDATE SET "
            "last_used_at = excluded.last_used_at, deleted_at = NULL",
            (thread_id, workspace_root, git_dir, self.stamp()),
        )

    def unused_threads(self, unused_for: timedelta) -> list[dict[str, Any]]:
        """Threads not used for this long and not yet deleted."""
        return self._execute(
            "SELECT * FROM agent_thread WHERE deleted_at IS NULL AND last_used_at < ?",
            (iso(self._now() - unused_for),),
        )

    def thread_deleted(self, thread_id: str) -> None:
        self._execute("UPDATE agent_thread SET deleted_at = ? WHERE thread_id = ?",
                      (self.stamp(), thread_id))

    # ── Retention ────────────────────────────────────────────────────────────

    def enforce_retention(self) -> None:
        """Delete what the design keeps no longer (see the module docstring)."""
        now = self._now()
        ended = iso(now - SESSIONS_KEPT)
        old = "SELECT id FROM agent_session WHERE ended_at IS NOT NULL AND ended_at < ?"
        self._execute(f"DELETE FROM agent_turn WHERE session_id IN ({old})", (ended,))
        self._execute(f"DELETE FROM agent_request WHERE session_id IN ({old})", (ended,))
        self._execute("DELETE FROM agent_session WHERE ended_at IS NOT NULL AND ended_at < ?",
                      (ended,))
        self._execute(
            "DELETE FROM agent_process "
            "WHERE confirmed_gone_at IS NOT NULL AND confirmed_gone_at < ?",
            (iso(now - PROCESSES_KEPT),),
        )
        self._execute("DELETE FROM agent_idempotency WHERE created_at < ?",
                      (iso(now - ANSWERS_KEPT),))
        self._execute("DELETE FROM agent_thread WHERE deleted_at IS NOT NULL AND deleted_at < ?",
                      (iso(now - THREADS_KEPT),))
