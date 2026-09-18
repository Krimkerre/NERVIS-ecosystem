"""Starting and stopping the services NERVIS started, and nothing else (§12, M16).

**The rule this file exists to enforce is a negative one.** §12: supervision uses
explicit executable identity, *"must not recursively kill broad process groups,
or anything it did not start"*, and must *"never assume a process exists from a
stale PID"*. Everything here is shaped by those three sentences, and the tests
that matter are the ones proving an external service is untouched.

**Configuration permits supervision; it does not create it.** A service declared
`nervis_managed` is one NERVIS *may* start. It becomes something NERVIS may stop
only once NERVIS has started it and recorded what it started — so on a machine
where a launcher script brought everything up, NERVIS owns nothing and every
control operation correctly refuses. That is not a gap; it is the rule.

**A PID is not an identity.** Operating systems reissue them, and a stop that
trusts a bare PID eventually kills a stranger. What is recorded is the PID, the
executable behind it, and the exact moment it began — and all three must still
agree before anything is signalled. `psutil` gives the create time, which is what
makes this checkable rather than hopeful.

**The operation set is closed.** Three verbs against registered services, with no
free-form command, no script and no process-selection path. An operation outside
the set does not exist rather than failing validation, which is §12's wording and
means the surface cannot be enumerated by probing it for near misses.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import psutil

from nervis.errors import RefusedError
from nervis.storage import Database
from nervis.voice import read_setting, write_setting

#: The switch. §12: *"each family of control operations carries its own switch,
#: every switch defaults to off"*. One family here — process control — so one
#: switch, and it is off until somebody turns it on.
ENABLED = "supervision.enabled"

#: The closed set. Adding a verb means adding it here; anything else does not
#: exist. `start` is separate from `restart` because they answer different
#: questions — one about a service that is down, one about a service that is up.
OPERATIONS = ("start", "stop", "restart")

#: How long a stopped process is given to go before anything harsher is
#: considered, and how long a started one has to become answerable.
GRACE_SECONDS = 10.0
READY_SECONDS = 30.0

#: How close two create times must be to count as the same process.
#:
#: **A millisecond, and it was a second, and the test caught it.** A second
#: sounds cautious and is the opposite: two processes starting within a second
#: of each other is *precisely* the PID-reuse window on a busy machine, so a
#: tolerance that wide makes the one case this check exists for invisible. The
#: reused-PID test failed against it — the imposter was 0.4s later and matched.
#:
#: The kernel's start time for a given process does not move between reads, so
#: the tolerance is only absorbing float representation, and a millisecond is
#: several orders of magnitude more than that needs.
#:
#: **On Linux the kernel's own resolution is coarser than this**: a start time
#: is counted in clock ticks, 10 ms at the usual 100 Hz, so two processes begun
#: in the same tick carry the same time and this check cannot tell them apart.
#: It does not need to. A PID is reused only after its first owner has died and
#: the counter has come round, which is never within one tick of that first
#: owner's start — the case this guards is always a later process, and a later
#: process always lands in a later tick. Measured 18 September 2026: three
#: `sleep`s started 50 ms and 300 ms apart read .65, .71 and .01 of a second.
CREATED_TOLERANCE = 0.001

#: §12's crash-loop limit. Three failed control attempts against one service
#: open its circuit, and only an operator clears it — a limit that reset itself
#: would let a crash-loop be re-entered by retrying.
FAILURE_LIMIT = 3

#: The environment a supervised process inherits. **An allowlist**, because the
#: alternative is handing a child every secret in NERVIS's own environment. §12
#: asks for exactly this.
ENV_ALLOWED = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "USER", "SHELL")


#: A control operation that may not happen. **`RefusedError`, not a new type**:
#: every one of these is a rule declining rather than something going wrong —
#: not owned, not started here, circuit open, switch off — and the errors module
#: already has the type that says so and answers 409. A second one would make
#: the same fact arrive as two different shapes depending on which module raised
#: it.
Refused = RefusedError


@dataclass(frozen=True)
class Launched:
    """What NERVIS recorded when it started something.

    The three fields that make an identity: which process, which program, and
    when it began. A stop compares all three.
    """

    service: str
    pid: int
    executable: str
    created_at: float
    started_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "service": self.service, "pid": self.pid,
            "executable": self.executable, "created_at": self.created_at,
            "started_at": self.started_at,
        }


def enabled(database: Database) -> bool:
    return read_setting(database, ENABLED, "0") == "1"


def enable(database: Database, on: bool) -> None:
    write_setting(database, ENABLED, "1" if on else "0")


# ── Identity ────────────────────────────────────────────────────────────────


def still_running(record: Launched) -> bool:
    """Whether the process NERVIS started is the one holding that PID *now*.

    **Three checks, and each has killed somebody's unrelated process in some
    other program.** The PID must exist; the executable behind it must be the
    one recorded; and its create time must match, because a reused PID running
    the same binary is otherwise indistinguishable from the original.

    Any doubt answers `False`. A stop that does nothing is a supervision failure
    somebody notices; a stop that signals a stranger is one they do not.
    """
    try:
        process = psutil.Process(record.pid)
        if abs(process.create_time() - record.created_at) > CREATED_TOLERANCE:
            return False
        return _same_executable(process, record.executable)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def _same_executable(process: psutil.Process, expected: str) -> bool:
    """Whether this process is running the program that was recorded.

    Falls back to the command line's first word where the executable is not
    readable — macOS refuses `exe()` for some processes — and answers `False`
    when neither can be read. Refusing to identify is not identifying.
    """
    try:
        found = process.exe()
    except (psutil.AccessDenied, psutil.ZombieProcess):
        try:
            argv = process.cmdline()
        except (psutil.AccessDenied, psutil.ZombieProcess, psutil.NoSuchProcess):
            return False
        found = argv[0] if argv else ""
    return bool(found) and os.path.realpath(found) == os.path.realpath(expected)


# ── The record ──────────────────────────────────────────────────────────────


def remember(database: Database, record: Launched) -> None:
    with database.connection as connection:
        connection.execute(
            "INSERT OR REPLACE INTO supervised_process "
            "(service, pid, executable, created_at, started_at, stopped_at, stopped_why) "
            "VALUES (?, ?, ?, ?, ?, '', '')",
            (record.service, record.pid, record.executable,
             record.created_at, record.started_at),
        )


def launched(database: Database, service: str) -> Launched | None:
    """What NERVIS started for this service, if it has not been stopped."""
    row = database.connection.execute(
        "SELECT * FROM supervised_process WHERE service = ? AND stopped_at = ''",
        (service,),
    ).fetchone()
    if row is None:
        return None
    return Launched(
        service=row["service"], pid=int(row["pid"]), executable=row["executable"],
        created_at=float(row["created_at"]), started_at=row["started_at"],
    )


def note_stopped(database: Database, service: str, why: str) -> None:
    with database.connection as connection:
        connection.execute(
            "UPDATE supervised_process SET stopped_at = ?, stopped_why = ? "
            "WHERE service = ? AND stopped_at = ''",
            (_now(), why, service),
        )


def history(database: Database, limit: int = 50) -> list[dict[str, Any]]:
    rows = database.connection.execute(
        "SELECT * FROM supervised_process ORDER BY started_at DESC LIMIT ?",
        (max(1, min(int(limit), 500)),),
    ).fetchall()
    return [dict(row) for row in rows]


# ── The circuit ─────────────────────────────────────────────────────────────


def circuit(database: Database, service: str) -> dict[str, Any]:
    row = database.connection.execute(
        "SELECT * FROM supervision_circuit WHERE service = ?", (service,)
    ).fetchone()
    return dict(row) if row else {"service": service, "failures": 0,
                                  "opened_at": "", "reason": ""}


def note_failure(database: Database, service: str, reason: str) -> None:
    """Count one failed control attempt, opening the circuit at the limit."""
    held = circuit(database, service)
    failures = int(held["failures"]) + 1
    opened = held["opened_at"] or (_now() if failures >= FAILURE_LIMIT else "")
    with database.connection as connection:
        connection.execute(
            "INSERT OR REPLACE INTO supervision_circuit "
            "(service, failures, opened_at, reason) VALUES (?, ?, ?, ?)",
            (service, failures, opened, reason),
        )


def clear_circuit(database: Database, service: str) -> None:
    """An operator's act, and deliberately the only way out.

    §12: the circuit stays open *"until an operator with control authority
    clears it, so a crash-loop cannot be re-entered by retry"*. Nothing in this
    module calls this.
    """
    with database.connection as connection:
        connection.execute(
            "DELETE FROM supervision_circuit WHERE service = ?", (service,)
        )


def _forget_failures(database: Database, service: str) -> None:
    """A success clears the count — but never an open circuit."""
    held = circuit(database, service)
    if held["opened_at"]:
        return
    with database.connection as connection:
        connection.execute(
            "DELETE FROM supervision_circuit WHERE service = ?", (service,)
        )


# ── The gate every operation passes ─────────────────────────────────────────


def may_control(database: Database, service: str, mode: str) -> None:
    """Raise `Refused` unless this service may be controlled right now.

    Every check is a separate sentence because every one is a different
    conversation with the person reading it: a switch to turn on, a mode that
    forbids this outright, or a circuit somebody has to clear.
    """
    if not enabled(database):
        raise Refused("supervision is switched off")
    if mode == "external":
        raise Refused(
            f"{service} is external — NERVIS observes it and never controls it"
        )
    if mode == "user_managed":
        raise Refused(
            f"{service} is user-managed: NERVIS can say where the control is "
            "and may not use it"
        )
    if mode != "nervis_managed":
        raise Refused(f"{service} has no ownership NERVIS recognises")
    held = circuit(database, service)
    if held["opened_at"]:
        raise Refused(
            f"{service}'s supervision circuit is open after "
            f"{held['failures']} failures ({held['reason']}) — an operator has "
            "to clear it"
        )


# ── What a service is configured as ─────────────────────────────────────────


@dataclass(frozen=True)
class Adapter:
    """How NERVIS would start one service, if it is allowed to.

    **This is the "explicitly configured" half of §3.1's clause.** A service
    with no adapter cannot be `nervis_managed` however anybody labels it,
    because there is no executable identity to record and nothing to verify a
    running process against.
    """

    service: str
    executable: str = ""
    args: tuple[str, ...] = ()
    cwd: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.executable)

    def as_dict(self) -> dict[str, Any]:
        return {"service": self.service, "executable": self.executable,
                "args": list(self.args), "cwd": self.cwd,
                "configured": self.configured}


def adapter(database: Database, service: str) -> Adapter:
    held = read_setting(database, f"supervision.adapter.{service}", "")
    if not held:
        return Adapter(service)
    try:
        found = json.loads(held)
    except ValueError:
        return Adapter(service)
    return Adapter(
        service=service,
        executable=str(found.get("executable") or ""),
        args=tuple(str(a) for a in (found.get("args") or [])),
        cwd=str(found.get("cwd") or ""),
    )


def configure(database: Database, service: str, executable: str,
              args: Sequence[str] = (), cwd: str = "") -> Adapter:
    """Declare how a service is started.

    **Refuses a path that is not an executable file right now.** A configuration
    that names something unrunnable is one that fails at the worst moment — when
    somebody is restarting a service because it is already down.
    """
    # **An empty executable revokes.** Configuring supervision had no inverse:
    # a service could be made controllable and never made uncontrollable again
    # without editing the database. For a surface whose whole design is about
    # what NERVIS may not touch, being unable to withdraw permission is the
    # wrong asymmetry — found by cleaning up after a test.
    if not executable.strip():
        write_setting(database, f"supervision.adapter.{service}", "")
        return adapter(database, service)
    resolved = os.path.realpath(executable)
    if not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
        raise Refused(f"{executable} is not an executable file")
    write_setting(database, f"supervision.adapter.{service}", json.dumps(
        {"executable": resolved, "args": list(args), "cwd": cwd}))
    return adapter(database, service)


def mode_of(database: Database, service: str, declared: str) -> str:
    """The ownership mode in force, which configuration alone cannot grant.

    **`nervis_managed` is earned.** A service declared owned but with no adapter
    is one NERVIS has no way to start, so reporting it as managed would promise
    a control that does not exist. It reads as `user_managed` instead: NERVIS
    can say where the switch is and may not use it.
    """
    if declared != "nervis_managed":
        return declared
    return "nervis_managed" if adapter(database, service).configured else "user_managed"


# ── The three verbs ─────────────────────────────────────────────────────────


def _environment() -> dict[str, str]:
    """What a supervised process inherits — an allowlist, never the parent's.

    NERVIS holds peer credentials; a child that inherited its whole environment
    would hold them too, which is §12.1's *"an admin credential in a control
    plane is a control plane whose compromise is total"* arriving by accident.
    """
    return {name: os.environ[name] for name in ENV_ALLOWED if name in os.environ}


def start(
    database: Database, service: str, mode: str, executable: str,
    args: Sequence[str] = (), cwd: str = "",
) -> Launched:
    """Launch a service and record what was launched.

    **Refuses when something is already running under this service's record.**
    §12's gate asks that a restart not create a duplicate, and the way that
    holds is that starting is not idempotent-by-overwriting: an existing live
    record is a refusal, not a second process.

    The executable is resolved and checked before anything is spawned, because
    the recorded identity has to be the path that was actually run — a relative
    name resolved by the shell later is not an identity.
    """
    may_control(database, service, mode)
    held = launched(database, service)
    if held is not None and still_running(held):
        raise Refused(
            f"{service} is already running as pid {held.pid}; stop it before "
            "starting another"
        )

    resolved = os.path.realpath(executable)
    if not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
        note_failure(database, service, "the configured executable is not runnable")
        raise Refused(f"{executable} is not an executable file NERVIS can run")

    try:
        # **No shell.** A shell would turn the argument list into a string
        # somebody could put a `;` in, which is the free-form command path §12
        # says must not exist. `start_new_session` gives the child its own group
        # so nothing NERVIS signals can travel back up to NERVIS itself — and
        # signalling that group is still never done, per §12.
        child = subprocess.Popen(  # noqa: S603 - argv list, no shell, fixed executable
            [resolved, *args],
            cwd=cwd or None,
            env=_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as failure:
        note_failure(database, service, f"could not start: {failure}")
        raise Refused(f"{service} could not be started: {failure}") from failure

    try:
        created = psutil.Process(child.pid).create_time()
    except psutil.Error:
        # It exited between spawning and being looked at. That is a partial
        # start, and the honest record is none at all.
        note_failure(database, service, "the process exited immediately")
        raise Refused(f"{service} exited immediately after starting") from None

    record = Launched(service, child.pid, resolved, created, _now())
    remember(database, record)
    _forget_failures(database, service)
    return record


def stop(database: Database, service: str, mode: str, why: str = "asked") -> str:
    """Stop the process NERVIS started, and only that one.

    **Signals one PID, never a group.** §12 forbids recursive group kills, and
    the child was given its own session precisely so that a group signal would
    be possible — which is why it is not used. `SIGTERM`, a grace period, and
    `SIGKILL` only against a process that has been re-verified as still the same
    one in the meantime.
    """
    may_control(database, service, mode)
    held = launched(database, service)
    if held is None:
        raise Refused(
            f"NERVIS did not start {service}, so it will not stop it — it may "
            "be running, and stopping something this process did not launch is "
            "what §12 forbids"
        )
    if not still_running(held):
        # The record is stale: the process is gone, or its PID belongs to
        # something else now. Either way there is nothing here to signal.
        note_stopped(database, service, "already gone")
        return "already gone"

    os.kill(held.pid, signal.SIGTERM)
    deadline = time.monotonic() + GRACE_SECONDS
    while time.monotonic() < deadline:
        if not still_running(held):
            note_stopped(database, service, why)
            _forget_failures(database, service)
            return "stopped"
        time.sleep(0.2)

    # **Re-verified before escalating.** Between the term and now the process
    # may have exited and its PID been reused; killing on the strength of a
    # check made ten seconds ago is the stale-PID failure with extra steps.
    if still_running(held):
        os.kill(held.pid, signal.SIGKILL)
        time.sleep(0.3)
    note_stopped(database, service, f"{why} (escalated)")
    _forget_failures(database, service)
    return "killed"


def restart(
    database: Database, service: str, mode: str, executable: str,
    args: Sequence[str] = (), cwd: str = "",
) -> Launched:
    """Stop then start, in that order, with the stop proven before the start.

    §12's gate: *a restart does not create a duplicate process*. The stop is not
    optimistic — `stop` returns only once the process is gone or was already
    gone, so reaching the start means there is nothing left to duplicate.
    """
    may_control(database, service, mode)
    held = launched(database, service)
    if held is not None:
        stop(database, service, mode, why="restarting")
    return start(database, service, mode, executable, args, cwd)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


__all__ = [
    "Adapter", "adapter", "configure", "mode_of",
    "restart", "start", "stop",
    "CREATED_TOLERANCE", "ENABLED", "ENV_ALLOWED", "FAILURE_LIMIT", "GRACE_SECONDS", "Launched",
    "OPERATIONS", "READY_SECONDS", "Refused", "circuit", "clear_circuit",
    "enable", "enabled", "history", "launched", "may_control", "note_failure",
    "note_stopped", "remember", "still_running",
]
