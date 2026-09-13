"""Ending one task's command processes, and confirming they're gone — **the R4 seam** (design §4.5).

One Codex process runs every project's commands, and its process group spans all of them, so a
task's Stop can never signal a group. This is the part of §4.5's end sequence after the turn is
interrupted, for **one** task:

| step | what |
|---|---|
| 2 | `thread/backgroundTerminals/terminate` for each terminal the thread lists (3 s) |
| 3-5 | SIGTERM, wait 2 s, SIGKILL: each process **individually**, while its start time matches |
| 6 | confirm, polling up to 3 s, that none is alive, and that the thread lists no terminal |

**Only strong attribution is signalled** (review AM5): a background terminal's `osPid`; a process
whose own arguments carry `WRITABLE_ROOT…=<this task's root>` (the parameter Codex's sandbox is
started with); and the descendants of either. Each must have started no earlier than a second
before the task's current turn. A process found only by its working folder is never killed, and a
process of another project never matches: nested roots can't both be locked.

Found from the whole process table rather than only the app-server's descendants, so a crash that
orphaned a sandboxed command still finds it. `process_table.py` (built for calibration's K6) does
the reading, the signals and the confirmation.

**What this increment leaves to R4:** recording attributed processes in `agent_process` while
turns run (sampled every 2 s), so a restart can kill what the last RAVIS left; the residual gap K6
measures (a child that detached itself between samples, outside the sandbox and the root).
Unknown is never "gone": when `ps` can't answer, nothing is confirmed.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ravis.codex.process_table import (
    Kill,
    ProcessRow,
    Snapshot,
    descendants,
    end_processes,
    sandbox_owner,
    snapshot,
)
from ravis.codex.rpc import CodexRpcError, CodexUnavailableError

Request = Callable[..., Awaitable[Any]]
LSTART = "%a %b %d %H:%M:%S %Y"


@dataclass(frozen=True)
class CleanupTimings:
    terminals_seconds: float = 3.0
    term_wait_seconds: float = 2.0
    confirm_seconds: float = 3.0


@dataclass(frozen=True)
class Confirmation:
    confirmed_gone: bool
    #: Survivors, as `session.state` → `processes.leftover` names them.
    leftover: tuple[ProcessRow, ...]
    attributed: int


def leftover_view(rows: Sequence[ProcessRow]) -> list[dict[str, Any]]:
    return [{"pid": row.pid, "comm": row.comm, "started_at": _started_at(row)} for row in rows]


def _started(row: ProcessRow) -> datetime | None:
    try:
        return datetime.strptime(row.start, LSTART).astimezone()
    except ValueError:
        return None


def _started_at(row: ProcessRow) -> str | None:
    """When a process started, as ISO-8601 UTC, or None when `ps`'s date couldn't be read."""
    moment = _started(row)
    return None if moment is None else moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def strong_targets(
    rows: Sequence[ProcessRow], root: Path, terminal_pids: set[int], since: datetime | None
) -> list[ProcessRow]:
    """The processes this task's Stop may signal, by the strong rules above."""
    seeds = [
        row for row in rows
        if row.pid in terminal_pids or sandbox_owner(row.args, {"task": root}) == "task"
    ]
    found: dict[int, ProcessRow] = {}
    for seed in seeds:
        found.setdefault(seed.pid, seed)
        for child in descendants(rows, seed.pid):
            found.setdefault(child.pid, child)
    found.pop(os.getpid(), None)
    if since is None:
        return list(found.values())
    earliest = since - timedelta(seconds=1)
    return [row for row in found.values() if (_started(row) or earliest) >= earliest]


class ProcessCleanup:
    def __init__(
        self,
        request: Request,
        timings: CleanupTimings = CleanupTimings(),
        *,
        take_snapshot: Snapshot = snapshot,
        kill: Kill = os.kill,
    ) -> None:
        self._request = request
        self._timings = timings
        self._snapshot = take_snapshot
        self._kill = kill

    async def end(
        self, thread_id: str | None, root: Path, since: datetime | None
    ) -> Confirmation:
        """Steps 2-6 for one task: its terminals, its strongly attributed processes, confirmed."""
        terminals = await self._terminals(thread_id)
        for terminal in terminals:
            await self._call("thread/backgroundTerminals/terminate",
                             {"threadId": thread_id, "processId": terminal.get("processId")})
        rows = await asyncio.to_thread(self._snapshot)
        if rows is None:
            return Confirmation(False, (), 0)
        pids = {
            terminal["osPid"] for terminal in terminals if isinstance(terminal.get("osPid"), int)
        }
        targets = strong_targets(rows, root, pids, since)
        survivors = await end_processes(
            targets, term_wait=self._timings.term_wait_seconds if targets else 0.0,
            confirm_wait=self._timings.confirm_seconds, take_snapshot=self._snapshot,
            kill=self._kill,
        ) if targets else []
        remaining = await self._terminals(thread_id)
        return Confirmation(not survivors and not remaining, tuple(survivors), len(targets))

    async def still_alive(self, rows: Sequence[ProcessRow]) -> tuple[ProcessRow, ...]:
        """Which of an earlier Stop's survivors still run, without signalling any."""
        table = await asyncio.to_thread(self._snapshot)
        if table is None:
            return tuple(rows)
        now = {(row.pid, row.start) for row in table}
        return tuple(row for row in rows if (row.pid, row.start) in now)

    async def _terminals(self, thread_id: str | None) -> list[dict[str, Any]]:
        if thread_id is None:
            return []
        listed = await self._call("thread/backgroundTerminals/list", {"threadId": thread_id})
        data = listed.get("data") if isinstance(listed, dict) else None
        return [entry for entry in data or [] if isinstance(entry, dict)]

    async def _call(self, method: str, params: dict[str, Any]) -> Any:
        """A terminal call; one that fails moves on to the OS kills (design §4.8's table)."""
        with contextlib.suppress(CodexRpcError, CodexUnavailableError, TimeoutError):
            return await self._request(method, params, timeout=self._timings.terminals_seconds)
        return None
