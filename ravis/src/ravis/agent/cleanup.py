"""Ending one task's command processes, and confirming they're gone (design §4.5).

One Codex process runs every project's commands, and its process group spans all of them, so a
task's Stop can never signal a group. This is the part of §4.5's end sequence after the turn is
interrupted, for **one** task:

| step | what |
|---|---|
| 2 | `thread/backgroundTerminals/terminate` for each terminal the thread lists (3 s) |
| 3-5 | SIGTERM, wait 2 s, SIGKILL: each process **individually**, while its start time matches |
| 6 | confirm, polling up to 3 s, that none is alive, and that the thread lists no terminal |

**What is signalled:** the processes a fresh look (`attribution.py`) gives to this task — by a
terminal's `osPid`, a sandbox root in its arguments, an earlier look, its parent, or, for a command
root, its folder while the task's turn ran — each started no earlier than a second before the
task's current turn; plus every process recorded for this task in `agent_process` that still runs
with its recorded start time; plus, when the owner pressed **Stop them**, the leftovers the chat
named. **What is only reported:** a process two tasks claim. It keeps the task `leftover`, so
nothing is saved while something that may be writing in the project still runs, and it is never
signalled unless the owner asks for exactly it.

**After a restart, and at shutdown**, Codex's process is gone and nothing can be looked up by
thread or folder any more: only recorded processes are signalled (`end_recorded`), by pid and start
time. Unknown is never "gone": when `ps` can't answer, nothing is confirmed.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from ravis.agent.attribution import Look, ProcessSampler, SampledTask
from ravis.agent.store import AgentStore
from ravis.codex.process_table import (
    START_SLACK,
    Identity,
    Kill,
    ProcessRow,
    Snapshot,
    alive,
    end_processes,
    snapshot,
    started_at,
)
from ravis.codex.rpc import CodexRpcError, CodexUnavailableError

Request = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class CleanupTimings:
    terminals_seconds: float = 3.0
    term_wait_seconds: float = 2.0
    confirm_seconds: float = 3.0


@dataclass(frozen=True)
class Confirmation:
    confirmed_gone: bool
    #: Survivors and reported processes, as `session.state` → `processes.leftover` names them.
    leftover: tuple[ProcessRow, ...]
    attributed: int


def leftover_view(rows: Sequence[ProcessRow]) -> list[dict[str, Any]]:
    """`session.state` → `processes.leftover`: `{pid, comm, started_at}`."""
    return [{"pid": row.pid, "comm": row.comm, "started_at": _started_at(row)} for row in rows]


def lock_file_leftover(rows: Iterable[ProcessRow]) -> list[dict[str, Any]]:
    """The checkout lock file's `leftover`: `{pid, start, comm}`, `start` as `ps -o lstart=`.

    The shape a Clarvis window needs to end them itself while RAVIS is down, by pid and start time
    (`lock-rule-cases.json` → `lock_file.leftover_entry`), and the checkpoint's `leftoverProcesses`.
    """
    return [{"pid": row.pid, "start": row.start, "comm": row.comm} for row in rows]


def _started_at(row: ProcessRow) -> str | None:
    """When a process started, as ISO-8601 UTC, or None when `ps`'s date couldn't be read."""
    moment = started_at(row.start)
    return None if moment is None else moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unique(rows: Iterable[ProcessRow]) -> list[ProcessRow]:
    found: dict[Identity, ProcessRow] = {}
    for row in rows:
        found.setdefault(row.identity, row)
    return list(found.values())


class ProcessCleanup:
    def __init__(
        self,
        request: Request,
        sampler: ProcessSampler,
        store: AgentStore,
        tasks: Callable[[], list[SampledTask]],
        timings: CleanupTimings = CleanupTimings(),
        *,
        take_snapshot: Snapshot = snapshot,
        kill: Kill = os.kill,
    ) -> None:
        self._request = request
        self._sampler = sampler
        self._store = store
        self._tasks = tasks
        self._timings = timings
        self._snapshot = take_snapshot
        self._kill = kill

    async def end(self, task: SampledTask, *, also: Sequence[ProcessRow] = ()) -> Confirmation:
        """Steps 2-6 for one task: its terminals, its processes, confirmed — and what it reports."""
        await self.terminate_terminals(task.thread_id)
        look = await self._sampler.look(self._with(task))
        if look is None:
            return Confirmation(False, tuple(also), 0)
        targets, reported = self._targets(look, task, also)
        survivors = await self._end(targets, self._timings) if targets else []
        still = await alive(reported, self._snapshot) if reported else []
        self._confirm(task.session_id, targets, survivors)
        remaining = await self.terminals(task.thread_id)
        leftover = tuple(_unique([*survivors, *still]))
        return Confirmation(not leftover and not remaining, leftover, len(targets))

    async def end_recorded(
        self, session_id: str | None, timings: CleanupTimings | None = None
    ) -> Confirmation:
        """Every recorded process still running with its start time — one task's, or all."""
        records = self._store.live_processes(session_id)
        rows = await asyncio.to_thread(self._snapshot)
        if rows is None:
            return Confirmation(not records, (), len(records))
        now = {row.identity: row for row in rows}
        owners = {(r["pid"], r["start_time"]): r["session_id"] for r in records}
        targets = [now[identity] for identity in owners if identity in now]
        survivors = await self._end(targets, timings or self._timings) if targets else []
        left = {row.identity for row in survivors}
        for identity, owner in owners.items():
            if identity not in left:
                self._store.confirm_gone(owner, [identity])
        return Confirmation(not survivors, tuple(survivors), len(targets))

    async def still_alive(self, rows: Sequence[ProcessRow]) -> tuple[ProcessRow, ...]:
        """Which of an earlier Stop's survivors still run, without signalling any."""
        return tuple(await alive(rows, self._snapshot))

    async def terminals(self, thread_id: str | None) -> list[dict[str, Any]]:
        if thread_id is None:
            return []
        listed = await self._call("thread/backgroundTerminals/list", {"threadId": thread_id})
        data = listed.get("data") if isinstance(listed, dict) else None
        return [entry for entry in data or [] if isinstance(entry, dict)]

    async def terminate_terminals(self, thread_id: str | None) -> None:
        for terminal in await self.terminals(thread_id):
            await self._call("thread/backgroundTerminals/terminate",
                             {"threadId": thread_id, "processId": terminal.get("processId")})

    def _with(self, task: SampledTask) -> list[SampledTask]:
        return [*(t for t in self._tasks() if t.session_id != task.session_id), task]

    def _targets(
        self, look: Look, task: SampledTask, also: Sequence[ProcessRow]
    ) -> tuple[list[ProcessRow], list[ProcessRow]]:
        """(what may be signalled, what may only be reported) for one task."""
        family = look.family(task.session_id)
        recorded = {
            (r["pid"], r["start_time"]) for r in self._store.live_processes(task.session_id)
        }
        running = {row.identity: row for row in look.rows}
        earliest = None if task.turn_started is None else task.turn_started - START_SLACK
        fresh = [
            a.row for a in family.attributed
            if a.row.identity in recorded or earliest is None
            or (started_at(a.row.start) or earliest) >= earliest
        ]
        targets = _unique([
            *fresh, *(running[i] for i in recorded if i in running),
            *(row for row in also if row.identity in running),
        ])
        chosen = {row.identity for row in targets}
        return targets, [row for row in family.ambiguous if row.identity not in chosen]

    async def _end(self, targets: Sequence[ProcessRow], timings: CleanupTimings
                   ) -> list[ProcessRow]:
        return await end_processes(
            targets, term_wait=timings.term_wait_seconds, confirm_wait=timings.confirm_seconds,
            take_snapshot=self._snapshot, kill=self._kill,
        )

    def _confirm(self, session_id: str, targets: Sequence[ProcessRow],
                 survivors: Sequence[ProcessRow]) -> None:
        left = {row.identity for row in survivors}
        gone = [row.identity for row in targets if row.identity not in left]
        if gone:
            self._store.confirm_gone(session_id, gone)

    async def _call(self, method: str, params: dict[str, Any]) -> Any:
        """A terminal call; one that fails moves on to the OS kills (design §4.8's table)."""
        with contextlib.suppress(CodexRpcError, CodexUnavailableError, TimeoutError):
            return await self._request(method, params, timeout=self._timings.terminals_seconds)
        return None
