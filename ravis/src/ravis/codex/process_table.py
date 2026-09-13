"""Which of Codex's processes belong to which task, and ending one task's without the other's.

One Codex process hosts every project's commands (design §2.1, §4.5), and **its process group spans
all of them**, so RAVIS never signals a Codex group. This module holds the parts of §4.5 that a
Stop, a crash, a restart and calibration's K6 all share: reading the process table, deciding which
task each of Codex's processes belongs to, and ending one task's processes one by one.

**What a real Codex child looks like** (calibration run `cal_d2185ed08f50`, K6, Codex 0.154.0).
Every command runs as `/bin/zsh -lc '<command>'` through `/usr/bin/sandbox-exec`, in a
pseudo-terminal of its own session. `sandbox-exec` *replaces itself* with the command, and zsh
replaces itself with a lone command too, so the process table shows `sleep 600` or
`script -q /dev/null sleep 600` as a descendant of the app-server — **never the sandbox's
`WRITABLE_ROOT` parameters**. Codex's `processId` is its own number, not a pid, and
`thread/backgroundTerminals/list` answers `osPid: null`. What does hold: the process is a
descendant of RAVIS's own app-server, and its working folder is the folder Codex ran it in.

**Attribution** (`attribute_processes`). Each process gathers *claims*, one per rule:
1. `terminal` — its pid is a background terminal's `osPid` for that task's thread (none on
   0.154.0, kept for a build that lists them);
2. `sandbox_root` — its own arguments carry a sandbox writable-root parameter
   (`-DWRITABLE_ROOT_0=<root>` as `sandbox-exec` takes it, or `WRITABLE_ROOT_0=<root>`) naming
   exactly one task's root: only ever seen while the wrapper hasn't yet replaced itself;
3. `recorded` — an earlier look already attributed this very process, **by its pid and its start
   time together**, so it keeps its task after its parent exits and launchd adopts it;
4. `parent` — its parent is attributed;
5. `command_cwd` — it is a *command root* (a descendant of the app-server whose parent has no
   task), its working folder lies inside a task's root, and that task's turn was already running
   when the process started. A child never gets a task from its own folder: a command in project A
   that changes into project B and starts something there is still A's, through its parent.

**One task claims it: attributed. Two or more: ambiguous. None: unattributed.** An ambiguous or
unattributed process is **never signalled automatically**; the ambiguous ones are reported as a
task's leftovers (design §4.5 item 4). A child of an ambiguous process shares its ambiguity.

**Residual gap** (as the design words its own): a command root that changes into *another running
task's* root before RAVIS first reads its folder, and runs there without starting a child, is
claimed by that other task. RAVIS reads a new process's folder within two seconds of its start
(`agent/attribution.py`); `ps` can't show another process's environment on macOS, so the task's
own `TMPDIR` can't tell the two apart.

**The end sequence** (`end_processes`): SIGTERM each target **individually**, only if its start
time still matches; wait; SIGKILL the same set, same check; then confirm, polling, that no target
with a matching start time is alive. The survivors are returned, never guessed at.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from ravis.codex.lock_rule import Runner, normalise_start, run_quietly

Attribution = Literal["terminal", "sandbox_root", "recorded", "parent", "command_cwd"]
#: Which rule's name an attributed process carries when several agree on one task.
RULE_ORDER: tuple[Attribution, ...] = (
    "terminal", "sandbox_root", "recorded", "parent", "command_cwd",
)
#: How often the confirmation looks again.
CONFIRM_POLL_SECONDS = 0.1
#: `ps -o lstart=` in the C locale.
LSTART = "%a %b %d %H:%M:%S %Y"
#: `lstart` has whole seconds, so a process started in the turn's first second may read one early.
START_SLACK = timedelta(seconds=1)

#: A process's identity: its pid and its start time. A pid alone is reused; the pair is not.
Identity = tuple[int, str]


@dataclass(frozen=True)
class ProcessRow:
    pid: int
    ppid: int
    #: `lstart`, whitespace collapsed: five words in the C locale.
    start: str
    args: str
    pgid: int = 0

    @property
    def comm(self) -> str:
        """The program's name, from its first argument."""
        return Path(self.args.split(" ", 1)[0]).name if self.args else ""

    @property
    def identity(self) -> Identity:
        return self.pid, self.start


def started_at(start: str) -> datetime | None:
    """When a process started, from its `lstart`, in local time; None when it isn't one."""
    try:
        return datetime.strptime(normalise_start(start), LSTART).astimezone()
    except ValueError:
        return None


@dataclass(frozen=True)
class Attributed:
    row: ProcessRow
    owner: str
    rule: Attribution


@dataclass(frozen=True)
class Ambiguous:
    """A process two or more tasks claim: reported, never signalled."""

    row: ProcessRow
    owners: tuple[str, ...]


@dataclass(frozen=True)
class TaskClaim:
    """What attribution knows of one task: its root, and when its current turn started."""

    root: Path
    #: None while no turn runs: then its folder claims nothing.
    turn_started: datetime | None = None


@dataclass
class AttributionResult:
    attributed: dict[int, Attributed] = field(default_factory=dict)
    ambiguous: dict[int, Ambiguous] = field(default_factory=dict)
    unattributed: list[ProcessRow] = field(default_factory=list)


# ── Reading the process table and working folders ────────────────────────────


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
            pid=int(words[0]), ppid=int(words[1]), pgid=int(words[2]),
            start=normalise_start(" ".join(words[4:9])), args=" ".join(words[9:]),
        ))
    return rows


def snapshot(run: Runner = run_quietly) -> list[ProcessRow] | None:
    """The whole process table now; None when `ps` couldn't say."""
    # `-ww`: never cut the arguments to a terminal's width, where the sandbox's parameters sit.
    code, output = run(["ps", "-A", "-ww", "-o", "pid=,ppid=,pgid=,stat=,lstart=,args="])
    return parse_rows(output) if code == 0 else None


def parse_cwds(text: str) -> dict[int, str]:
    """`lsof -a -d cwd -Fn -p <pids>`: each process's working folder, by pid.

    The field output is one field a line — `p<pid>`, `fcwd`, `n<path>` — and a path keeps its
    spaces. `lsof`'s warnings (an unreachable network volume, say) go to stderr and never here.
    """
    found: dict[int, str] = {}
    pid: int | None = None
    for line in text.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None and pid not in found:
            found[pid] = line[1:]
    return found


def read_cwds(pids: Iterable[int], run: Runner = run_quietly) -> dict[int, str] | None:
    """The working folders of these processes; None when `lsof` couldn't run at all.

    `lsof` exits 1 when any pid has gone meanwhile, and still lists the others, so the exit code
    alone decides nothing: a process missing from the answer simply has no folder to read.
    """
    wanted = sorted(set(pids))
    if not wanted:
        return {}
    code, output = run(["lsof", "-a", "-d", "cwd", "-Fn", "-p", ",".join(map(str, wanted))])
    if code is None:
        return None
    return parse_cwds(output)


def descendants(rows: Sequence[ProcessRow], ancestor: int) -> list[ProcessRow]:
    """Every descendant of `ancestor`, parents before their children."""
    children: dict[int, list[ProcessRow]] = {}
    for row in rows:
        children.setdefault(row.ppid, []).append(row)
    found: list[ProcessRow] = []
    seen = {ancestor}
    frontier = [ancestor]
    while frontier:
        pid = frontier.pop(0)
        for child in children.get(pid, []):
            if child.pid not in seen:
                seen.add(child.pid)
                found.append(child)
                frontier.append(child.pid)
    return found


# ── Attribution ──────────────────────────────────────────────────────────────


def sandbox_owner(args: str, roots: Mapping[str, Path]) -> str | None:
    """The one task whose root a `WRITABLE_ROOT…=<root>` parameter in `args` names, if exactly one.

    Matched per root rather than by splitting the arguments, because `ps` joins arguments with
    spaces and a project's path may hold one ("NERVIS workspace"). `sandbox-exec` takes the
    parameter as `-DWRITABLE_ROOT_0=<root>`; its read-only carve-outs (`…_RO_0=<root>/.git`) name
    a path inside the root, never the root itself, so they never match.
    """
    owners = {
        owner for owner, root in roots.items()
        if re.search(rf"WRITABLE_ROOT_\d+=['\"]?{re.escape(str(root))}['\"]?(\s|$)", args)
    }
    return next(iter(owners)) if len(owners) == 1 else None


def cwd_owner(cwd: str | None, row: ProcessRow, tasks: Mapping[str, TaskClaim]) -> str | None:
    """The task whose root holds this folder, when that task's turn ran before the process began."""
    if cwd is None:
        return None
    folder = Path(cwd)
    began = started_at(row.start)
    for owner, task in tasks.items():
        if task.turn_started is None or began is None:
            continue
        if not (folder == task.root or task.root in folder.parents):
            continue
        if began >= task.turn_started - START_SLACK:
            return owner
    return None


@dataclass(frozen=True)
class _Look:
    """One pass's inputs, gathered so each helper takes one argument instead of five."""

    tasks: Mapping[str, TaskClaim]
    cwds: Mapping[int, str]
    terminals: Mapping[int, str]
    known: Mapping[Identity, Attributed]


def attribute_processes(
    rows: Sequence[ProcessRow],
    *,
    app_server_pid: int | None,
    tasks: Mapping[str, TaskClaim],
    cwds: Mapping[int, str] | None = None,
    terminals: Mapping[int, str] | None = None,
    known: Mapping[Identity, Attributed] | None = None,
) -> AttributionResult:
    """Every candidate process, attributed, ambiguous or unattributed, by the rules above.

    Candidates are the app-server's descendants, any process an earlier look recorded (it may have
    been reparented), and any process whose own arguments carry a task's sandbox root.
    """
    look = _Look(tasks, cwds or {}, terminals or {}, known or {})
    result = AttributionResult()
    for row in _candidates(rows, app_server_pid, look):
        claims = _claims(row, look, result, app_server_pid)
        owners = tuple(sorted({owner for owner, _ in claims}))
        if len(owners) == 1:
            rule = min((rule for _, rule in claims), key=RULE_ORDER.index)
            result.attributed[row.pid] = Attributed(row, owners[0], rule)
        elif owners:
            result.ambiguous[row.pid] = Ambiguous(row, owners)
        else:
            result.unattributed.append(row)
    return result


def _candidates(
    rows: Sequence[ProcessRow], app_server_pid: int | None, look: _Look
) -> list[ProcessRow]:
    tree = descendants(rows, app_server_pid) if app_server_pid is not None else []
    chosen = {row.pid for row in tree}
    roots = {owner: task.root for owner, task in look.tasks.items()}
    extra = [
        row for row in rows
        if row.pid not in chosen and row.pid != app_server_pid
        and (row.identity in look.known or sandbox_owner(row.args, roots) is not None)
    ]
    return [*extra, *tree]


def _claims(
    row: ProcessRow, look: _Look, result: AttributionResult, app_server_pid: int | None
) -> list[tuple[str, Attribution]]:
    claims: list[tuple[str, Attribution]] = []
    if row.pid in look.terminals:
        claims.append((look.terminals[row.pid], "terminal"))
    sandboxed = sandbox_owner(row.args, {o: t.root for o, t in look.tasks.items()})
    if sandboxed is not None:
        claims.append((sandboxed, "sandbox_root"))
    recorded = look.known.get(row.identity)
    if recorded is not None:
        claims.append((recorded.owner, "recorded"))
    claims += _from_parent(row, look, result, app_server_pid)
    return claims


def _from_parent(
    row: ProcessRow, look: _Look, result: AttributionResult, app_server_pid: int | None
) -> list[tuple[str, Attribution]]:
    """The parent's task — or, for a command root, its own folder's."""
    parent = result.attributed.get(row.ppid)
    if parent is not None:
        return [(parent.owner, "parent")]
    shared = result.ambiguous.get(row.ppid)
    if shared is not None:
        return [(owner, "parent") for owner in shared.owners]
    if app_server_pid is None:
        return []
    owner = cwd_owner(look.cwds.get(row.pid), row, look.tasks)
    return [(owner, "command_cwd")] if owner is not None else []


# ── Ending processes one by one ──────────────────────────────────────────────


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
    return await confirm_gone(targets, confirm_wait, take_snapshot)


async def confirm_gone(
    targets: Sequence[ProcessRow], confirm_wait: float, take_snapshot: Snapshot = snapshot
) -> list[ProcessRow]:
    """Poll up to `confirm_wait` seconds; the targets still running as the same process."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + confirm_wait
    while True:
        survivors = await alive(targets, take_snapshot)
        if not survivors or loop.time() >= deadline:
            return survivors
        await asyncio.sleep(CONFIRM_POLL_SECONDS)


async def _signal_matching(
    targets: Sequence[ProcessRow], sent: int, take_snapshot: Snapshot, kill: Kill
) -> None:
    for row in await alive(targets, take_snapshot):
        if row.pid <= 1 or row.pid == os.getpid():
            continue
        try:
            kill(row.pid, sent)
        except (ProcessLookupError, PermissionError):
            continue


async def alive(targets: Sequence[ProcessRow], take_snapshot: Snapshot) -> list[ProcessRow]:
    """The targets still running as the same process: same pid, same start time."""
    rows = await asyncio.to_thread(take_snapshot)
    if rows is None:
        # Not knowing is not "gone": every target counts as still alive.
        return list(targets)
    now = {row.identity for row in rows}
    return [row for row in targets if row.identity in now]
