"""Stopping the command a taken-over Clarvis window had running: its whole group and descendants.

**When** (design §6.3; `project-locks.json` → `group_kill_at_takeover`; final check F-A4). A
window takes a project over from another Clarvis window, and that window's last heartbeat — or its
checkout lock file — recorded the command it had running, `running_command: {pid, pgid, start,
comm}`. Before the new holder writes a byte, RAVIS stops that command. Clarvis starts every command
as the leader of a process group of its own (`commandTools.ts`), so the group is the command's;
killing only the pid would leave its children writing (`npm test` starts workers).

**This is not the Codex rule.** Codex's commands share RAVIS's one app-server group across
projects, so a Codex kill never uses a group (§4.5, `codex/process_table.py`). Only a Clarvis
window's recorded command is ever signalled by group, and only here.

**The contract's order:**
1. snapshot every process (`ps -A -o pid=,ppid=,pgid=,lstart=,…`);
2. is it still that group? Yes if its leader (`pid` = `pgid`) is alive with the recorded start
   time, or if no process has `pid` = `pgid` any more — a group id isn't reused while members
   remain. **No** if a process with that pid has another start time: then nothing is signalled by
   group, and any survivor that can't belong to the new owner of the pid is named;
3. targets: every process in the group, plus every descendant (by `ppid`) of the leader or of a
   member — ones that moved to a group or session of their own included — each started at or after
   the recorded start time;
4. SIGTERM the group (`kill(-pgid)`) while every live member of it is a target, and each other
   target by pid; wait 2 s; SIGKILL the same set, only what still has its start time;
5. confirm, polling up to 3 s, that no target with a matching start time is alive. Otherwise the
   takeover stays `leftover` and names them.

Clarvis's `src/engine/lock/groupKill.ts` does the same when RAVIS is down, and after RAVIS did it,
finds nothing to signal. **Never a pid on its own say-so:** after the first snapshot a signal goes
only to a process whose pid and start time still match a target. This process and launchd are never
targets. Residual gap, as the design records it: a child that fully detached itself (reparented to
launchd) before the snapshot isn't found.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from ravis.codex.lock_rule import same_start
from ravis.codex.process_table import (
    Kill,
    ProcessRow,
    Snapshot,
    alive,
    confirm_gone,
    snapshot,
    started_at,
)


@dataclass(frozen=True)
class RunningCommand:
    """A heartbeat's or lock file's `running_command`."""

    pid: int
    pgid: int
    start: str
    comm: str


def running_command(value: object) -> RunningCommand | None:
    """A `running_command` read from a body or a lock file, or None when it isn't one."""
    if not isinstance(value, dict):
        return None
    pid, pgid, start, comm = (value.get(name) for name in ("pid", "pgid", "start", "comm"))
    numbers = isinstance(pid, int) and isinstance(pgid, int) and pid > 1 and pgid > 1
    if not numbers or not isinstance(start, str) or not isinstance(comm, str):
        return None
    return RunningCommand(int(pid), int(pgid), start, comm)  # type: ignore[arg-type]


@dataclass(frozen=True)
class GroupKillTimings:
    term_wait_seconds: float = 2.0
    confirm_seconds: float = 3.0


@dataclass(frozen=True)
class GroupPlan:
    """What the first snapshot says: the targets, or — the pid reused — survivors to name."""

    targets: tuple[ProcessRow, ...]
    reused: bool = False


@dataclass(frozen=True)
class GroupStop:
    """`gone` True, False (with `survivors`), or None when `ps` couldn't say."""

    gone: bool | None
    survivors: tuple[ProcessRow, ...] = ()
    signalled: int = 0


def plan_group_kill(rows: Sequence[ProcessRow], command: RunningCommand, own_pid: int) -> GroupPlan:
    """Steps 2 and 3, from one snapshot. Pure."""
    since = started_at(command.start)
    live = [row for row in rows if row.pid > 1 and row.pid != own_pid]
    holder = next((row for row in live if row.pid == command.pid), None)
    if holder is not None and not same_start(holder.start, command.start):
        return GroupPlan(_reused_survivors(live, command, holder, since), reused=True)
    members = [row for row in live if row.pgid == command.pgid and _after(row, since)]
    return GroupPlan(tuple(_with_descendants(live, members, command, since)))


def _after(row: ProcessRow, since: datetime | None) -> bool:
    began = started_at(row.start)
    return since is not None and began is not None and began >= since


def _with_descendants(
    live: Sequence[ProcessRow], members: Sequence[ProcessRow], command: RunningCommand,
    since: datetime | None,
) -> list[ProcessRow]:
    chosen = {row.pid: row for row in members}
    parents = {command.pid, command.pgid, *chosen}
    grew = True
    while grew:
        grew = False
        for row in live:
            if row.pid in chosen or row.ppid not in parents or not _after(row, since):
                continue
            chosen[row.pid] = row
            parents.add(row.pid)
            grew = True
    return list(chosen.values())


def _reused_survivors(
    live: Sequence[ProcessRow], command: RunningCommand, holder: ProcessRow,
    since: datetime | None,
) -> tuple[ProcessRow, ...]:
    """After a reuse, what still carries the old group id and started before the pid's new owner."""
    taken = started_at(holder.start)
    return tuple(
        row for row in live
        if row.pid != holder.pid and row.pgid == command.pgid and _after(row, since)
        and taken is not None and (started_at(row.start) or taken) < taken
    )


class GroupKill:
    """Steps 1-5 for one recorded command. Injectable, so tests never signal a real stranger."""

    def __init__(
        self,
        timings: GroupKillTimings = GroupKillTimings(),
        *,
        take_snapshot: Snapshot = snapshot,
        kill: Kill = os.kill,
        own_pid: int | None = None,
    ) -> None:
        self._timings = timings
        self._snapshot = take_snapshot
        self._kill = kill
        self._own_pid = os.getpid() if own_pid is None else own_pid

    async def stop(self, command: RunningCommand) -> GroupStop:
        rows = await asyncio.to_thread(self._snapshot) if started_at(command.start) else None
        if rows is None:
            return GroupStop(None)
        plan = plan_group_kill(rows, command, self._own_pid)
        if plan.reused or not plan.targets:
            return GroupStop(not plan.targets, plan.targets)
        self._signal(plan.targets, rows, command, signal.SIGTERM)
        await asyncio.sleep(self._timings.term_wait_seconds)
        after = await asyncio.to_thread(self._snapshot)
        still = await alive(plan.targets, lambda: after)
        if still and after is not None:
            self._signal(still, after, command, signal.SIGKILL)
        survivors = await confirm_gone(plan.targets, self._timings.confirm_seconds, self._snapshot)
        return GroupStop(not survivors, tuple(survivors), len(plan.targets))

    def _signal(
        self, targets: Sequence[ProcessRow], rows: Sequence[ProcessRow], command: RunningCommand,
        sent: int,
    ) -> None:
        """The group while every live member of it is a target; every other target by pid."""
        ours = {row.pid for row in targets}
        group = [row for row in rows if row.pgid == command.pgid]
        by_group = bool(group) and all(row.pid in ours for row in group)
        if by_group:
            self._send(-command.pgid, sent)
        for row in targets:
            if not by_group or row.pgid != command.pgid:
                self._send(row.pid, sent)

    def _send(self, pid: int, sent: int) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            self._kill(pid, sent)
