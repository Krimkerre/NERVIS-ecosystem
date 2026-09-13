"""A takeover stops the old window's running command: its whole group and descendants (F-A4).

Design §6.3 and `project-locks.json` → `group_kill_at_takeover`. What RAVIS is held to:
- **a detached shell with a background child, and a grandchild that moved to a group of its own,
  are all gone** after the kill — real processes, each this test's own throwaway child;
- **a leader that already exited still has its group killed**;
- **a pid reused by a different group is never signalled**, and what can't be the new owner's is
  named;
- **an unconfirmed kill is reported, never called gone** (the route test then finds the takeover
  `leftover`, `test_project_locks.py`);
- a process older than the recorded command, or RAVIS itself, is never a target.

Nothing here signals a process the test didn't start: the real cases kill only the group the test
spawned, and the planning cases never signal at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from ravis.agent.group_kill import (
    GroupKill,
    GroupKillTimings,
    RunningCommand,
    plan_group_kill,
    running_command,
)
from ravis.codex.lock_rule import probe_process
from ravis.codex.process_table import ProcessRow, snapshot

FAST = GroupKillTimings(term_wait_seconds=0.3, confirm_seconds=3.0)

#: A leader that starts a background child and a second one. While the leader lives, the second
#: moves to a process group of its own, and is found through its parent. When the leader exits
#: first, both stay in its group: a child that left the group *and* lost its parent to launchd is
#: the residual gap the design records, and isn't asked of the kill.
FAMILY = """
import os, subprocess, sys, time
stay = "import time; time.sleep(60)"
regroup = "import os, time; os.setpgid(0, 0); time.sleep(60)"
child = subprocess.Popen([sys.executable, "-c", stay])
grandchild = subprocess.Popen([sys.executable, "-c", regroup if sys.argv[1] == "stay" else stay])
print(child.pid, grandchild.pid, flush=True)
if sys.argv[1] == "exit":
    os._exit(0)
time.sleep(60)
"""


def row(pid: int, ppid: int, pgid: int, start: str, args: str = "sleep 60") -> ProcessRow:
    return ProcessRow(pid, ppid, start, args, pgid)


def test_the_plan_takes_the_group_and_its_descendants_and_nothing_older() -> None:
    command = RunningCommand(48210, 48210, "Sun Sep 13 05:41:07 2026", "npm")
    rows = [
        row(48210, 48123, 48210, "Sun Sep 13 05:41:07 2026", "npm test"),
        row(48211, 48210, 48210, "Sun Sep 13 05:41:08 2026", "node worker"),
        row(48212, 48211, 48999, "Sun Sep 13 05:41:09 2026", "node detached"),
        row(48100, 1, 48210, "Sun Sep 13 05:00:00 2026", "older in the group"),
        row(48123, 1, 48123, "Sun Sep 13 05:10:02 2026", "the window"),
    ]

    plan = plan_group_kill(rows, command, own_pid=48123)

    assert sorted(r.pid for r in plan.targets) == [48210, 48211, 48212]
    assert not plan.reused


def test_a_leader_that_exited_still_leaves_its_group_to_kill() -> None:
    command = RunningCommand(48210, 48210, "Sun Sep 13 05:41:07 2026", "npm")
    rows = [row(48211, 1, 48210, "Sun Sep 13 05:41:08 2026")]

    assert [r.pid for r in plan_group_kill(rows, command, own_pid=1).targets] == [48211]


def test_a_pid_reused_by_another_group_is_never_signalled() -> None:
    command = RunningCommand(48210, 48210, "Sun Sep 13 05:41:07 2026", "npm")
    rows = [
        row(48210, 1, 50000, "Sun Sep 13 06:00:00 2026", "a stranger now"),
        row(48211, 1, 48210, "Sun Sep 13 05:41:08 2026", "the old group's survivor"),
    ]
    sent: list[tuple[int, int]] = []
    kill = GroupKill(FAST, take_snapshot=lambda: rows, kill=lambda p, s: sent.append((p, s)),
                     own_pid=1)

    stopped = asyncio.run(kill.stop(command))

    assert sent == []
    assert (stopped.gone, [r.pid for r in stopped.survivors]) == (False, [48211])


def test_an_unconfirmed_kill_is_reported_not_called_gone() -> None:
    command = RunningCommand(48210, 48210, "Sun Sep 13 05:41:07 2026", "npm")
    rows = [row(48210, 1, 48210, "Sun Sep 13 05:41:07 2026", "npm test")]
    sent: list[tuple[int, int]] = []
    kill = GroupKill(GroupKillTimings(0.0, 0.2), take_snapshot=lambda: rows,
                     kill=lambda p, s: sent.append((p, s)), own_pid=1)

    stopped = asyncio.run(kill.stop(command))

    assert stopped.gone is False and [r.pid for r in stopped.survivors] == [48210]
    assert (-48210, signal.SIGTERM) in sent and (-48210, signal.SIGKILL) in sent
    assert asyncio.run(GroupKill(FAST, take_snapshot=lambda: None).stop(command)).gone is None
    assert running_command({"pid": 1, "pgid": 1, "start": "x", "comm": "y"}) is None


def _family(tmp_path: Path, mode: str) -> tuple[subprocess.Popen[bytes], list[int]]:
    script = tmp_path / "family.py"
    script.write_text(FAMILY)
    leader = subprocess.Popen([sys.executable, str(script), mode], stdout=subprocess.PIPE,
                              start_new_session=True)
    assert leader.stdout is not None
    others = [int(pid) for pid in leader.stdout.readline().split()]
    return leader, [leader.pid, *others]


def _running(pid: int) -> bool:
    """Running, as the kill judges it: in the table, and not a zombie waiting to be collected."""
    return any(row.pid == pid for row in snapshot() or [])


def _stop_family(tmp_path: Path, mode: str) -> tuple[bool | None, list[int]]:
    leader, pids = _family(tmp_path, mode)
    try:
        probe = probe_process(leader.pid)
        assert probe is not None and probe.lstart is not None
        command = RunningCommand(leader.pid, leader.pid, probe.lstart, "python")
        if mode == "exit":
            leader.wait(10)
        deadline = time.monotonic() + 5
        while not all(_running(pid) for pid in pids[1:]) and time.monotonic() < deadline:
            time.sleep(0.05)
        stopped = asyncio.run(GroupKill(FAST, take_snapshot=snapshot).stop(command))
        return stopped.gone, [pid for pid in pids if _running(pid)]
    finally:
        for pid in pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        leader.wait(10)


def test_a_detached_shell_its_background_child_and_a_regrouped_grandchild_are_all_gone(
    tmp_path: Path,
) -> None:
    gone, left = _stop_family(tmp_path, "stay")

    assert (gone, left) == (True, [])


def test_the_group_of_a_leader_that_already_exited_is_still_killed(tmp_path: Path) -> None:
    gone, left = _stop_family(tmp_path, "exit")

    assert (gone, left) == (True, [])
