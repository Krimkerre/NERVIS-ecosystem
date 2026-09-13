"""Looking at Codex's processes while a turn runs, and recording whose they are (design §4.5).

**Every 2 seconds while any turn is active**, and once more whenever a task's turn ends, RAVIS reads
the process table (`ps`, off the event loop), reads the working folder of each process it hasn't
seen before (`lsof`, new pids only), lists each running thread's background terminals, and gives
every one of Codex's processes to a task by `codex/process_table.py`'s rules. A process attributed
for the first time is written to `agent_process` — task, turn, pid, start time, program name and the
rule — so a RAVIS that restarts, or a Stop after its parent exited, still finds it by its pid and
start time together.

**What a look is used for:**
- a task's end sequence signals only what this look attributes to that task, plus what was recorded
  for it earlier (`cleanup.py`); an ambiguous process is reported, never signalled;
- the task's checkout lock file carries its family (`leftover`), so a Clarvis window can end them if
  RAVIS itself is down (design §6.3, "RAVIS down");
- calibration's K6 records which rule found each process.

Metadata only: ids, pids, start times, program names and rule names. The folders read with `lsof`
stay in memory for as long as their process runs; arguments are never stored.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ravis.agent.store import AgentStore
from ravis.codex.process_table import (
    Attributed,
    AttributionResult,
    Identity,
    ProcessRow,
    Snapshot,
    TaskClaim,
    attribute_processes,
    descendants,
    read_cwds,
    snapshot,
)

logger = logging.getLogger("ravis")

#: Lists one thread's background terminals: `thread/backgroundTerminals/list`'s `data`.
Terminals = Callable[[str], Awaitable[list[dict[str, Any]]]]
CwdReader = Callable[[Iterable[int]], "dict[int, str] | None"]


@dataclass(frozen=True)
class SampledTask:
    """A task as a look needs it."""

    session_id: str
    turn_id: str | None
    root: Path
    #: When its current turn started; None while none runs (its folder then claims nothing).
    turn_started: datetime | None
    #: Its thread, when Codex has it loaded: only then are its terminals listed.
    thread_id: str | None


@dataclass(frozen=True)
class Family:
    """One task's share of a look: what may be signalled, and what may only be reported."""

    attributed: tuple[Attributed, ...]
    ambiguous: tuple[ProcessRow, ...]


@dataclass(frozen=True)
class Look:
    rows: tuple[ProcessRow, ...]
    result: AttributionResult

    def family(self, session_id: str) -> Family:
        mine = tuple(a for a in self.result.attributed.values() if a.owner == session_id)
        shared = tuple(a.row for a in self.result.ambiguous.values() if session_id in a.owners)
        return Family(mine, shared)


class ProcessSampler:
    """Reads the table, attributes, records. One per RAVIS; its memory is only a cache."""

    def __init__(
        self,
        store: AgentStore,
        *,
        app_server_pid: Callable[[], int | None],
        terminals: Terminals,
        take_snapshot: Snapshot = snapshot,
        cwds: CwdReader = read_cwds,
    ) -> None:
        self._store = store
        self._app_server_pid = app_server_pid
        self._terminals = terminals
        self._snapshot = take_snapshot
        self._cwds = cwds
        #: Processes attributed by an earlier look, by pid and start time.
        self._known: dict[Identity, Attributed] = {}
        #: Working folders already read, by pid and start time (None: it had none to read).
        self._folders: dict[Identity, str | None] = {}

    async def look(self, tasks: Sequence[SampledTask]) -> Look | None:
        """One look at every task's processes; None when `ps` couldn't say."""
        rows = await asyncio.to_thread(self._snapshot)
        if rows is None:
            return None
        server = self._app_server_pid()
        await self._read_folders(rows, server)
        result = attribute_processes(
            rows, app_server_pid=server,
            tasks={task.session_id: TaskClaim(task.root, task.turn_started) for task in tasks},
            cwds={row.pid: self._folders[row.identity] or "" for row in rows
                  if self._folders.get(row.identity)},
            terminals=await self._terminal_pids(tasks),
            known=self._known,
        )
        self._record(result, tasks)
        self._forget({row.identity for row in rows})
        return Look(tuple(rows), result)

    async def _read_folders(self, rows: Sequence[ProcessRow], server: int | None) -> None:
        """`lsof` for the app-server's descendants not seen before; a failed read is retried."""
        if server is None:
            return
        new = [row for row in descendants(rows, server) if row.identity not in self._folders]
        if not new:
            return
        found = await asyncio.to_thread(self._cwds, [row.pid for row in new])
        if found is None:
            return
        for row in new:
            self._folders[row.identity] = found.get(row.pid)

    async def _terminal_pids(self, tasks: Sequence[SampledTask]) -> dict[int, str]:
        pids: dict[int, str] = {}
        for task in tasks:
            if task.thread_id is None:
                continue
            for terminal in await self._terminals(task.thread_id):
                if isinstance(terminal.get("osPid"), int):
                    pids[terminal["osPid"]] = task.session_id
        return pids

    def _record(self, result: AttributionResult, tasks: Sequence[SampledTask]) -> None:
        turns = {task.session_id: task.turn_id for task in tasks}
        for attributed in result.attributed.values():
            row = attributed.row
            if row.identity in self._known or attributed.owner not in turns:
                continue
            self._known[row.identity] = attributed
            self._store.record_process(attributed.owner, turns[attributed.owner], row.identity,
                                       row.comm, attributed.rule)
        if result.ambiguous:
            logger.info("agent: %d of Codex's processes belong to more than one task; "
                        "reported, never signalled", len(result.ambiguous))

    def _forget(self, present: set[Identity]) -> None:
        """Drop what ended: a pid comes back as a new process, with a new start time."""
        for identity in [i for i in self._folders if i not in present]:
            del self._folders[identity]
        for identity in [i for i in self._known if i not in present]:
            del self._known[identity]
