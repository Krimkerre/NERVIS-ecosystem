"""Which of Codex's processes belong to which task, and ending one task's without the other's.

One Codex process hosts every project's commands (design §2.1, §4.5), and **its process group spans
all of them**, so RAVIS never signals a group. This module holds the two halves of §4.5 that
calibration's K6 measures and M29's later increments reuse for Stop:

**Attribution** (`attribute`). From one snapshot of the process table
(`ps -A -o pid=,ppid=,pgid=,lstart=,args=`, in the C locale), every descendant of the app-server is
given to a task by the first rule that holds, in order:
1. `terminal` — its pid is a background terminal's `osPid` for that task's thread;
2. `sandbox_root` — its own arguments, or its nearest ancestor's, carry a `WRITABLE_ROOT…=<root>`
   parameter naming exactly that task's root (the parameter `sandbox-exec` is given; K6 records the
   exact argument format a real Codex uses);
3. `parent` — its parent is already attributed.
A descendant no rule finds is *unattributed*: reported, never signalled. The working-folder rule
(`cwd_only`) never leads to a kill (review AM5), so it isn't sampled here.

**The end sequence** (`end_processes`): SIGTERM each target **individually**, only if its start
time still matches the snapshot; wait; SIGKILL the same set, same check; then confirm, polling, that
no target with a matching start time is alive. The survivors are returned, never guessed at.

Only processes that were descendants of the app-server when the snapshot was taken can be targets:
a pid reused by an unrelated program in the meantime has another start time and is left alone.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ravis.codex.lock_rule import Runner, normalise_start, run_quietly

Attribution = Literal["terminal", "sandbox_root", "parent"]
#: How often the confirmation looks again.
CONFIRM_POLL_SECONDS = 0.1


@dataclass(frozen=True)
class ProcessRow:
    pid: int
    ppid: int
    #: `lstart`, whitespace collapsed: five words in the C locale.
    start: str
    args: str

    @property
    def comm(self) -> str:
        """The program's name, from its first argument."""
        return Path(self.args.split(" ", 1)[0]).name if self.args else ""


@dataclass(frozen=True)
class Attributed:
    row: ProcessRow
    owner: str
    rule: Attribution


def parse_rows(text: str) -> list[ProcessRow]:
    """Rows of `ps -A -o pid=,ppid=,pgid=,stat=,lstart=,args=`; unparseable lines are skipped.

    **A zombie is not a process that runs.** One that has exited but whose parent hasn't collected
    it yet keeps its pid and start time in the table, and can't be signalled; counting it would
    make a confirmed Stop look like a survivor. So a row whose state starts with `Z` is left out.
    """
    rows = []
    for line in text.splitlines():
        words = line.split()
        if len(words) < 9 or not all(word.isdigit() for word in words[:3]):
            continue
        if words[3].startswith("Z"):
            continue
        rows.append(ProcessRow(
            pid=int(words[0]), ppid=int(words[1]),
            start=normalise_start(" ".join(words[4:9])), args=" ".join(words[9:]),
        ))
    return rows


def snapshot(run: Runner = run_quietly) -> list[ProcessRow] | None:
    """The whole process table now; None when `ps` couldn't say."""
    # `-ww`: never cut the arguments to a terminal's width, where the sandbox's parameters sit.
    code, output = run(["ps", "-A", "-ww", "-o", "pid=,ppid=,pgid=,stat=,lstart=,args="])
    return parse_rows(output) if code == 0 else None


def descendants(rows: Sequence[ProcessRow], ancestor: int) -> list[ProcessRow]:
    """Every descendant of `ancestor`, parents before their children."""
    children: dict[int, list[ProcessRow]] = {}
    for row in rows:
        children.setdefault(row.ppid, []).append(row)
    found: list[ProcessRow] = []
    frontier = [ancestor]
    while frontier:
        pid = frontier.pop(0)
        for child in children.get(pid, []):
            if child.pid != ancestor and child not in found:
                found.append(child)
                frontier.append(child.pid)
    return found


def attribute(
    rows: Sequence[ProcessRow],
    *,
    app_server_pid: int,
    roots: Mapping[str, Path],
    terminals: Mapping[int, str],
) -> tuple[dict[int, Attributed], list[ProcessRow]]:
    """(the attributed descendants by pid, the unattributed ones), by the rules above."""
    attributed: dict[int, Attributed] = {}
    unattributed: list[ProcessRow] = []
    for row in descendants(rows, app_server_pid):
        found = _attributed(row, attributed, roots, terminals)
        if found is None:
            unattributed.append(row)
        else:
            attributed[row.pid] = found
    return attributed, unattributed


def _attributed(
    row: ProcessRow,
    known: Mapping[int, Attributed],
    roots: Mapping[str, Path],
    terminals: Mapping[int, str],
) -> Attributed | None:
    if row.pid in terminals:
        return Attributed(row, terminals[row.pid], "terminal")
    owner = sandbox_owner(row.args, roots)
    if owner is not None:
        return Attributed(row, owner, "sandbox_root")
    parent = known.get(row.ppid)
    return Attributed(row, parent.owner, "parent") if parent is not None else None


def sandbox_owner(args: str, roots: Mapping[str, Path]) -> str | None:
    """The one task whose root a `WRITABLE_ROOT…=<root>` parameter in `args` names, if exactly one.

    Matched per root rather than by splitting the arguments, because `ps` joins arguments with
    spaces and a project's path may hold one ("NERVIS workspace").
    """
    owners = {
        owner for owner, root in roots.items()
        if re.search(rf"WRITABLE_ROOT[A-Z0-9_]*=['\"]?{re.escape(str(root))}['\"]?(\s|$)", args)
    }
    return next(iter(owners)) if len(owners) == 1 else None


Kill = Callable[[int, int], None]
Snapshot = Callable[[], "list[ProcessRow] | None"]


async def end_processes(
    targets: Sequence[ProcessRow],
    *,
    term_wait: float,
    confirm_wait: float,
    take_snapshot: Snapshot = snapshot,
    kill: Kill = os.kill,
) -> list[ProcessRow]:
    """SIGTERM, wait, SIGKILL, confirm; the targets still alive with their start time, if any."""
    await _signal_matching(targets, signal.SIGTERM, take_snapshot, kill)
    await asyncio.sleep(term_wait)
    await _signal_matching(targets, signal.SIGKILL, take_snapshot, kill)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + confirm_wait
    while True:
        survivors = await _alive(targets, take_snapshot)
        if not survivors or loop.time() >= deadline:
            return survivors
        await asyncio.sleep(CONFIRM_POLL_SECONDS)


async def _signal_matching(
    targets: Sequence[ProcessRow], sent: int, take_snapshot: Snapshot, kill: Kill
) -> None:
    for row in await _alive(targets, take_snapshot):
        try:
            kill(row.pid, sent)
        except ProcessLookupError:
            continue
        except PermissionError:
            continue


async def _alive(targets: Sequence[ProcessRow], take_snapshot: Snapshot) -> list[ProcessRow]:
    """The targets still running as the same process: same pid, same start time."""
    rows = await asyncio.to_thread(take_snapshot)
    if rows is None:
        # Not knowing is not "gone": every target counts as still alive.
        return list(targets)
    now = {(row.pid, row.start) for row in rows}
    return [row for row in targets if (row.pid, row.start) in now]
