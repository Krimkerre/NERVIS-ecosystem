"""The launcher's lifecycle — what `start` and `stop` may do to a machine, and what they may not.

`tools/run.py start` launches the services detached and writes a PID file; `stop` reads that file
back and signals what it names. Each is a safety property before it is a feature. A `stop` that
trusted a number on disk could kill whatever the operating system handed that number to next; a
`start` that trusted the same file could launch a second copy of something already serving, or
decline to launch something that had quietly died. These were first verified by hand (STATUS.md,
"Starting the thing"); this file holds them now, together with the three holes found while it was
being written on 12 September 2026 — markers that matched more than their own service, a record
without a marker that matched everything, and a `start` that could orphan a service still booting.

**Nothing here reaches the machine.** The owner's stack is usually running while the suite runs,
with a model they loaded, so every door out of the launcher is replaced: the process table,
`os.kill`, `ps`, the clock, the health probe, spawning, SIRVIS, the browser, and `.run/`, where the
PID file lives beside real credentials. The doors that should never be opened at all raise instead
of answering, so a new call that nobody faked fails the test rather than touching a real process
or port.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import NoReturn

import pytest

RUN_PY = Path(__file__).resolve().parents[2] / "tools" / "run.py"

# The two signals `stop` sends, as the numbers it sends them as: POSIX's SIGTERM and SIGKILL.
# Named here rather than taken from `signal`, which has no SIGKILL on Windows.
TERM, KILL = 15, 9

#: (name, command, marker, env, health URL) — the shape of one row of `run._services()`.
Service = tuple[str, list[str], str, dict[str, str], str]

# Rows of the launcher's service table, reduced to what these tests need and shaped like the real
# ones: every Python service is a console script in `ravis/.venv`, so every one of their command
# lines carries `ravis` — which is why a marker has to be more than a service's name. Each health
# URL uses the service's real port, because `stop` asks SIRVIS's health at an address of its own,
# and that has to land on the port the fake machine says SIRVIS holds.
VENV_BIN = "/eco/ravis/.venv/bin"
SIRVIS: Service = (
    "SIRVIS", [f"{VENV_BIN}/sirvis", "serve"], "sirvis serve", {},
    "http://127.0.0.1:8721/ecosystem/health",
)
RAVIS: Service = (
    "RAVIS", [f"{VENV_BIN}/ravis", "serve"], "ravis serve", {}, "http://127.0.0.1:8731/v1/models"
)
NERVIS: Service = (
    "NERVIS", [f"{VENV_BIN}/nervis", "serve"], "nervis serve", {},
    "http://127.0.0.1:8790/api/v1/health",
)
CODE_SERVER: Service = (
    "code-server", ["/opt/homebrew/bin/code-server"], "code-server", {},
    "http://127.0.0.1:8080/healthz",
)


def _as_ps_shows(service: Service) -> str:
    """A Python service's command line as `ps` shows it: the interpreter, then the script."""
    return f"{VENV_BIN}/python {' '.join(service[1])}"


class FakeMachine:
    """The operating system as the launcher sees it during a test, and a record of what it did.

    A process table (PID to command line), which PID holds which port, and a clock that moves only
    when the launcher sleeps — so `stop`'s waits and `start`'s pass instantly, and a test can still
    tell how long each one was. Each method stands in for exactly one thing `run.py` calls: `kill`
    for `os.kill`, `run` for `subprocess.run` (which the launcher only uses here for `ps`),
    `monotonic` and `sleep` for the `time` module, `responds` for the health probe, `spawn` for
    `_spawn_detached`.

    **A port answers only while the process holding it is alive**, and only once that process has
    finished booting (`answers_at`, on the fake clock). That is what lets `stop`'s checks see the
    effect of the signals it sent, and `start`'s wait see a service come up part-way through.
    """

    def __init__(self, services: list[Service]) -> None:
        self.services = services
        self.table: dict[int, str] = {}
        self.ports: dict[int, int] = {}
        self.ignores_term: set[int] = set()
        self.answers_at: dict[int, float] = {}
        # Everything the launcher did, in order, as short lines ("signal 501 15", "ps 501",
        # "spawn RAVIS 9000", "DELETE /api/..."), so a test can assert what came before what.
        self.log: list[str] = []
        # Signals meant to stop something, each with the clock reading when it was sent. Signal 0
        # is left out: it only asks whether a PID exists, and delivers nothing to the process.
        self.sent: list[tuple[int, int, float]] = []
        self.now = 0.0
        self.next_pid = 9000
        # How long a service launched during the test takes to answer, by name; 0 unless named.
        self.boot_seconds: dict[str, float] = {}
        # The HTTP status a port answers with once it answers; 200 unless named.
        self.statuses: dict[int, int] = {}

    def process(
        self, pid: int, command: str, port: int | None = None, *,
        ignores_term: bool = False, answers_at: float = 0.0,
    ) -> None:
        """Put a process in the table: holding a port or not, exiting on TERM or not, and
        answering on its port from `answers_at` on the fake clock."""
        self.table[pid] = command
        self.answers_at[pid] = answers_at
        if port is not None:
            self.ports[port] = pid
        if ignores_term:
            self.ignores_term.add(pid)

    def kill(self, pid: int, sig: int) -> None:
        self.log.append(f"signal {pid} {sig}")
        if sig != 0:
            self.sent.append((pid, sig, self.now))
        if pid not in self.table:
            raise ProcessLookupError(pid)
        if sig == KILL or (sig == TERM and pid not in self.ignores_term):
            del self.table[pid]

    def run(self, command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
        if command[:2] != ["ps", "-p"]:
            raise AssertionError(f"the launcher ran a command no test allows: {command}")
        pid = int(command[2])
        self.log.append(f"ps {pid}")
        line = self.table.get(pid)
        # `ps -p PID -o command=` prints the command line and exits 0, or prints nothing and fails.
        return subprocess.CompletedProcess(command, 0 if line else 1, f"{line}\n" if line else "")

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def responds(self, url: str, _timeout: float = 1.5) -> bool:
        pid = self.ports.get(urllib.parse.urlsplit(url).port or 0)
        return pid is not None and pid in self.table and self.now >= self.answers_at[pid]

    def answer_status(self, url: str, _timeout: float = 1.5) -> int | None:
        """`run.answer_status`: the port's status once `responds` says it answers, else None."""
        if not self.responds(url):
            return None
        return self.statuses.get(urllib.parse.urlsplit(url).port or 0, 200)

    def spawn(self, command: list[str], _env: dict[str, str], _log: Path) -> int:
        """Launch a service: a new PID that holds the service's port from the moment it exists."""
        name, _, _, _, url = next(service for service in self.services if service[1] == command)
        pid, self.next_pid = self.next_pid, self.next_pid + 1
        self.process(pid, " ".join(command), urllib.parse.urlsplit(url).port,
                     answers_at=self.now + self.boot_seconds.get(name, 0.0))
        self.log.append(f"spawn {name} {pid}")
        return pid

    def signals(self) -> list[tuple[int, int]]:
        """(PID, signal) for everything sent to stop a process, in the order it was sent."""
        return [(pid, sig) for pid, sig, _ in self.sent]

    def spawned(self) -> list[str]:
        return [line for line in self.log if line.startswith("spawn ")]

    def waited(self, pid: int) -> float:
        """Seconds on the fake clock between asking this PID to stop and forcing it."""
        asked, forced = (when for sent_to, _, when in self.sent if sent_to == pid)
        return forced - asked


class FakeSirvis:
    """SIRVIS as `_sirvis_call` reaches it. Every call is kept, and lands in the machine's log too,
    so it can be ordered against the signals `stop` sends."""

    def __init__(self, machine: FakeMachine) -> None:
        self.machine = machine
        self.asked: list[str] = []

    def __call__(
        self, method: str, path: str, body: object = None, timeout: float = 10.0
    ) -> tuple[int, object]:
        del body, timeout
        self.asked.append(f"{method} {path}")
        self.machine.log.append(f"{method} {path}")
        return 200, {}


def _refuse(what: str) -> Callable[..., NoReturn]:
    """A stand-in that fails the test if it is ever called, naming what was nearly done."""

    def refuse(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError(f"the launcher tried to {what} during a test")

    return refuse


def _load() -> ModuleType:
    """A fresh copy of `run.py`, loaded from the file as the neighbouring launcher tests do, so
    each test's replacements die with its own copy."""
    spec = importlib.util.spec_from_file_location("launcher_lifecycle_under_test", RUN_PY)
    assert spec is not None and spec.loader is not None
    run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run)
    return run


def _seal(monkeypatch: pytest.MonkeyPatch, run: ModuleType, tmp_path: Path) -> None:
    """Close every door from `run` onto this machine that no test means to open.

    The PID file, the menu's session record and the token `responds` would present all live in
    `.run/`, which holds real credentials; this test's own directory stands in for all of it.
    POSIX's branch is taken on every platform, so no result depends on where the suite runs.
    `os.kill`, `subprocess.run`, `Popen` and `urlopen` are replaced on the real modules, because
    that is how `run.py` reaches them, and each one raises; `monkeypatch` restores them after.
    """
    monkeypatch.setattr(run, "RUN", tmp_path)
    monkeypatch.setattr(run, "PIDFILE", tmp_path / "services.json")
    monkeypatch.setattr(run, "MENU_SESSIONS", tmp_path / "menubar-sessions.json")
    monkeypatch.setattr(run, "RAVIS_ADMIN_TOKEN", tmp_path / "ravis-admin.token")
    monkeypatch.setattr(run, "RAVIS_OWNER_TOKEN", tmp_path / "ravis-owner.token")
    monkeypatch.setattr(run, "WINDOWS", False)
    monkeypatch.setattr(run.os, "kill", _refuse("send a real signal"))
    monkeypatch.setattr(run.subprocess, "run", _refuse("run a real command"))
    monkeypatch.setattr(run.subprocess, "Popen", _refuse("start a real process"))
    monkeypatch.setattr(run.urllib.request, "urlopen", _refuse("open a real connection"))
    monkeypatch.setattr(run, "_sirvis_call", _refuse("call SIRVIS"))


def _launcher(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, machine: FakeMachine) -> ModuleType:
    """A sealed copy of `run.py` whose processes, clock, probes and services are `machine`'s."""
    run = _load()
    _seal(monkeypatch, run, tmp_path)
    monkeypatch.setattr(run, "_services", lambda: machine.services)
    monkeypatch.setattr(run, "responds", machine.responds)
    monkeypatch.setattr(run, "answer_status", machine.answer_status)
    monkeypatch.setattr(run, "_spawn_detached", machine.spawn)
    monkeypatch.setattr(run, "time", machine)
    monkeypatch.setattr(run.os, "kill", machine.kill)
    monkeypatch.setattr(run.subprocess, "run", machine.run)
    return run


def _quiet_start(monkeypatch: pytest.MonkeyPatch, run: ModuleType) -> list[str]:
    """Replace everything `start` does besides deciding what to launch; return what it opened.

    Mounting shares, planting RAVIS's credentials, asking Homebrew where Codex is, finding Ollama
    and code-server and naming the default upstreams are each real work against the machine or its
    credential store, and none of them is the question these tests ask.
    """
    opened: list[str] = []
    monkeypatch.setattr(run, "ensure_venv", lambda: None)
    monkeypatch.setattr(run, "FILE_MOUNTS", [])
    monkeypatch.setattr(run, "configured_share", lambda: "")
    monkeypatch.setattr(run, "mount_share", _refuse("mount a share"))
    monkeypatch.setattr(run, "teach_ravis_the_admin_credential", lambda: "(planted)")
    monkeypatch.setattr(run, "teach_ravis_the_owner_credential", lambda: "(planted)")
    monkeypatch.setattr(run, "teach_ravis_the_credential", lambda: "(taught)")
    # Homebrew with nothing to say, so launching RAVIS runs no `brew` (`_with_codex_executable`).
    monkeypatch.setattr(run, "homebrew_codex_link", lambda: "")
    monkeypatch.setattr(run, "dashboard_url", lambda: run.DASHBOARD)
    monkeypatch.setattr(run, "webbrowser", SimpleNamespace(open=opened.append))
    monkeypatch.setattr(run, "ollama_binary", lambda: "")
    monkeypatch.setattr(run, "code_server_binary", lambda: "")
    monkeypatch.setattr(run, "EXTERNAL", [])
    # An upstream the operator declared, so `start` never reads the credential store for defaults.
    monkeypatch.setenv("RAVIS_UPSTREAMS", "[]")
    monkeypatch.setattr(run, "_default_upstreams", _refuse("read the credential store"))
    return opened


def _record(run: ModuleType, records: dict[str, dict[str, object]]) -> None:
    """Write the PID file the way `start` writes it: service name to {"pid", "marker"}."""
    run.PIDFILE.write_text(json.dumps(records), encoding="utf-8")


def test_stop_never_signals_a_recorded_pid_that_now_belongs_to_something_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reused PID is reported "was not running" and left alone.

    PIDs are recycled. RAVIS went away some other way, and the number the PID file still holds has
    since been handed to an unrelated program — a browser, here. `stop` has to read that PID's
    command line, find no `ravis serve` in it, and leave the process be: a file on disk claiming a
    number was ours is not a reason to kill whatever holds it now. This is the PID-reuse guard
    STATUS.md records as tested by hand, and the guard `_alive` exists for.
    """
    machine = FakeMachine([SIRVIS, RAVIS])
    machine.process(4242, "/Applications/Safari.app/Contents/MacOS/Safari")
    run = _launcher(monkeypatch, tmp_path, machine)
    _record(run, {"RAVIS": {"pid": 4242, "marker": "ravis serve"}})

    assert run.stop() == 0

    assert "RAVIS was not running" in capsys.readouterr().out
    assert machine.signals() == []
    assert 4242 in machine.table
    # Refused because its command line was read and did not match, not because the PID was vacant.
    assert "ps 4242" in machine.log


def test_stop_asks_first_and_forces_only_what_outlives_its_wait_longest_for_sirvis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """TERM to each recorded service; KILL only to one still alive when its wait runs out.

    Asking first matters because a service killed outright can leave a half-written SQLite
    journal. Forcing matters because a `stop` that gave up politely would leave a port held, and
    the next `start` would find that service "already running". So NERVIS, which exits on TERM,
    never sees KILL, while RAVIS and SIRVIS, which ignore it, are forced — RAVIS after six seconds
    and SIRVIS after twelve. Since 12 September 2026 SIRVIS's shutdown releases every session and
    unloads every model it loaded, which a slow `lms unload` can stretch past six seconds, and a
    SIRVIS forced mid-unload leaves a model loaded and held by nobody (`GRACE_BEFORE_KILL`).
    """
    machine = FakeMachine([SIRVIS, RAVIS, NERVIS])
    machine.process(500, _as_ps_shows(NERVIS), 8790)
    machine.process(501, _as_ps_shows(RAVIS), 8731, ignores_term=True)
    machine.process(502, _as_ps_shows(SIRVIS), 8721, ignores_term=True)
    run = _launcher(monkeypatch, tmp_path, machine)
    _record(run, {
        "NERVIS": {"pid": 500, "marker": "nervis serve"},
        "RAVIS": {"pid": 501, "marker": "ravis serve"},
        "SIRVIS": {"pid": 502, "marker": "sirvis serve"},
    })

    assert run.stop() == 0

    out = capsys.readouterr().out
    assert machine.signals() == [(500, TERM), (501, TERM), (501, KILL), (502, TERM), (502, KILL)]
    assert "NERVIS did not stop" not in out
    assert "RAVIS did not stop; forcing" in out
    assert "SIRVIS did not stop; forcing" in out
    # Within one polling step of each wait: the clock only moves in the launcher's 0.2 s sleeps.
    assert machine.waited(501) == pytest.approx(6.0, abs=0.25)
    assert machine.waited(502) == pytest.approx(12.0, abs=0.25)
    assert out.rstrip().endswith("Stopped.")


def test_stop_owns_up_when_a_port_still_answers_after_it_has_finished(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """"Still answering after stop" and a failing exit, instead of "Stopped." over a live service.

    The loop can pass over a live service without knowing it. A process that re-execs itself drops
    the command line its marker was taken from, and `stop` then reports it "was not running",
    exactly as it would a reused PID — both print the same line. Before the final health check,
    that printed "Stopped." over a code-server still holding its port: what a stub code-server did
    the first time the path ran, as recorded in `stop` itself. The guard is still right not to
    signal a process it cannot recognise; what the check adds is that `stop` says so and fails.
    """
    machine = FakeMachine([SIRVIS, RAVIS, CODE_SERVER])
    machine.process(601, "node out/node/entry", 8080)
    run = _launcher(monkeypatch, tmp_path, machine)
    _record(run, {"code-server": {"pid": 601, "marker": "code-server"}})

    assert run.stop() == 1

    out = capsys.readouterr().out
    assert "code-server was not running" in out
    assert "Still answering after stop: code-server" in out
    assert "Stopped." not in out
    assert machine.signals() == []


def test_stop_releases_the_menu_bars_models_before_signalling_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The menu bar app's sessions are released first, while SIRVIS can still be asked.

    A SIRVIS from 12 September 2026 on releases every session and unloads every model it loaded as
    it stops, but an older one releases nothing, and a model loaded from the menu would then stay
    in LM Studio after the stack stopped, held by nobody. The menu's own record of its sessions is
    the launcher's to clear either way (`_release_menu_sessions`). The order is the property: once
    SIRVIS has been sent TERM, nobody is left to ask.
    """
    machine = FakeMachine([SIRVIS, RAVIS])
    machine.process(701, _as_ps_shows(SIRVIS), 8721)
    run = _launcher(monkeypatch, tmp_path, machine)
    sirvis = FakeSirvis(machine)
    monkeypatch.setattr(run, "_sirvis_call", sirvis)
    run._save_menu_sessions({"qwen/qwen3.5-9b": "s-1"})
    _record(run, {"SIRVIS": {"pid": 701, "marker": "sirvis serve"}})

    assert run.stop() == 0

    released = "DELETE /api/v1/runtime/sessions/s-1"
    assert sirvis.asked == [released]
    assert machine.log.index(released) < machine.log.index(f"signal 701 {TERM}")
    assert run._menu_sessions() == {}
    assert "released qwen/qwen3.5-9b for the menu bar" in capsys.readouterr().out


def test_stop_does_not_ask_a_sirvis_that_is_not_answering_to_release_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No release is attempted while SIRVIS does not answer, and the rest of `stop` goes ahead.

    A SIRVIS that fails a one-second health check cannot unload anything, and each release is a
    request allowed two minutes, so asking anyway could only hold `stop` up and then print a
    failure for every model the menu holds. Stopping RAVIS does not wait on it.
    """
    machine = FakeMachine([SIRVIS, RAVIS])
    machine.process(801, _as_ps_shows(RAVIS), 8731)
    run = _launcher(monkeypatch, tmp_path, machine)
    sirvis = FakeSirvis(machine)
    monkeypatch.setattr(run, "_sirvis_call", sirvis)
    run._save_menu_sessions({"a/one": "s-1"})
    _record(run, {"RAVIS": {"pid": 801, "marker": "ravis serve"}})

    assert run.stop() == 0

    assert sirvis.asked == []
    assert machine.signals() == [(801, TERM)]


def test_start_launches_nothing_again_when_every_service_already_answers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Health says everything is serving, so nothing is launched — whatever the PID file says.

    There is no PID file at all here, as after a stack started some other way or a `.run/` that
    was cleared. A `start` that believed the file would try to launch a second copy of every
    service onto ports already held.
    """
    machine = FakeMachine([SIRVIS, RAVIS])
    machine.process(900, _as_ps_shows(SIRVIS), 8721)
    machine.process(901, _as_ps_shows(RAVIS), 8731)
    run = _launcher(monkeypatch, tmp_path, machine)
    opened = _quiet_start(monkeypatch, run)

    code = run.start()

    assert machine.spawned() == []
    assert "Already running." in capsys.readouterr().out
    assert code == 0
    assert not run.PIDFILE.exists()
    assert opened == [run.DASHBOARD]


def test_start_launches_a_service_whose_record_names_a_process_no_longer_ours(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Health, not the PID file, decides — and a record naming someone else's process is stale.

    The two disagree in both directions. SIRVIS answers but the file has never heard of it, so it
    is not launched again. RAVIS does not answer, and its record — written before the markers were
    narrowed — names a PID now held by a pytest run from `ravis/.venv`: alive, and carrying the old
    marker `ravis`, but not RAVIS. So RAVIS is launched, and the file names the new process.
    """
    machine = FakeMachine([SIRVIS, RAVIS])
    machine.process(900, _as_ps_shows(SIRVIS), 8721)
    machine.process(700, f"{VENV_BIN}/python -m pytest -q tests/")
    run = _launcher(monkeypatch, tmp_path, machine)
    _quiet_start(monkeypatch, run)
    _record(run, {"RAVIS": {"pid": 700, "marker": "ravis"}})

    code = run.start()

    # What was launched is checked before the exit status: a service wrongly left unlaunched also
    # never answers, so `start` returns 1, and failing on that alone would hide which decision
    # went wrong.
    assert machine.spawned() == ["spawn RAVIS 9000"]
    assert "SIRVIS already running" in capsys.readouterr().out
    recorded = json.loads(run.PIDFILE.read_text(encoding="utf-8"))
    assert recorded["RAVIS"] == {"pid": 9000, "marker": "ravis serve"}
    assert code == 0


def test_start_waits_for_a_recorded_service_still_booting_instead_of_launching_a_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A recorded service that is alive and ours but not answering yet is waited for, not doubled.

    This is the second `start` that arrives while the first is still booting — a double click, or
    the menu bar app starting the stack a moment after the launcher did. The first writes its PID
    file before it waits, so the second can see that RAVIS is already on its way. It launches no
    copy, gives RAVIS the same wait a fresh launch gets, and RAVIS answers ten seconds in.
    """
    machine = FakeMachine([RAVIS])
    machine.process(700, _as_ps_shows(RAVIS), 8731, answers_at=10.0)
    run = _launcher(monkeypatch, tmp_path, machine)
    opened = _quiet_start(monkeypatch, run)
    _record(run, {"RAVIS": {"pid": 700, "marker": "ravis serve"}})

    code = run.start()

    assert machine.spawned() == []
    out = capsys.readouterr().out
    assert "RAVIS already started (pid 700)" in out
    assert f"  {'RAVIS':<11} ready" in out
    recorded = json.loads(run.PIDFILE.read_text(encoding="utf-8"))
    assert recorded["RAVIS"] == {"pid": 700, "marker": "ravis serve"}
    assert code == 0
    assert opened == [run.DASHBOARD]


def test_start_names_a_recorded_service_that_never_answers_and_keeps_it_for_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A recorded service that stays silent through the wait is named, kept, and not killed.

    Killing it could interrupt a slow boot, and launching over it was the old bug: whichever copy
    lost the race for the port exited, and when the survivor was the first one, nothing in the PID
    file could reach it any more. So `start` names the service and its process in words somebody
    can act on, fails, and keeps the record — and the advice works, because that record is what
    lets the next `stop` reach it.
    """
    machine = FakeMachine([RAVIS])
    machine.process(700, _as_ps_shows(RAVIS), 8731, answers_at=float("inf"))
    run = _launcher(monkeypatch, tmp_path, machine)
    opened = _quiet_start(monkeypatch, run)
    _record(run, {"RAVIS": {"pid": 700, "marker": "ravis serve"}})

    code = run.start()

    assert machine.spawned() == []
    assert machine.signals() == []
    assert ("RAVIS (process 700) is running but not answering."
            " Stop the stack, then start it again.") in capsys.readouterr().out
    assert code == 1
    assert opened == []
    assert json.loads(run.PIDFILE.read_text(encoding="utf-8"))["RAVIS"]["pid"] == 700

    assert run.stop() == 0
    assert machine.signals() == [(700, TERM)]


OLLAMA: Service = (
    "Ollama", ["/opt/homebrew/bin/ollama", "serve"], "ollama serve", {},
    "http://127.0.0.1:11434/",
)


def test_start_brings_services_up_in_the_runbooks_order_each_after_the_last_answers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Runbook §12.1: SIRVIS, the local runtimes, RAVIS, NERVIS, then code-server — in sequence.

    The table lists them in another order on purpose, and each takes a few seconds to answer on
    the fake clock, so a `start` that launched in table order, or launched everything before
    waiting, would spawn at the wrong moments. One service never answers: it is named and the rest
    still start, since §12.1 lets independent services run degraded.
    """
    machine = FakeMachine([CODE_SERVER, NERVIS, RAVIS, OLLAMA, SIRVIS])
    machine.boot_seconds = {"SIRVIS": 3.0, "Ollama": float("inf"), "RAVIS": 2.0, "NERVIS": 1.0}
    run = _launcher(monkeypatch, tmp_path, machine)
    _quiet_start(monkeypatch, run)
    launched_at: list[tuple[str, float]] = []
    spawn = machine.spawn

    def timed(command: list[str], env: dict[str, str], log: Path) -> int:
        # Every earlier launch was on record before its wait began, for a second `start` to find.
        on_record = json.loads(run.PIDFILE.read_text()) if run.PIDFILE.exists() else {}
        assert set(on_record) == {name for name, _ in launched_at}
        pid = spawn(command, env, log)
        launched_at.append((machine.log[-1].split()[1], machine.now))
        return pid

    monkeypatch.setattr(run, "_spawn_detached", timed)

    code = run.start()

    names = [name for name, _ in launched_at]
    assert names == ["SIRVIS", "Ollama", "RAVIS", "NERVIS", "code-server"]
    when = dict(launched_at)
    assert when["SIRVIS"] == 0.0
    assert 3.0 <= when["Ollama"] < 3.5, "Ollama waited for SIRVIS to answer"
    wait = run.START_WAIT_SECONDS
    assert when["Ollama"] + wait <= when["RAVIS"] < when["Ollama"] + wait + 0.5, (
        "RAVIS waited out Ollama's full wait, then started anyway"
    )
    assert when["NERVIS"] >= when["RAVIS"] + 2.0
    assert when["code-server"] >= when["NERVIS"] + 1.0
    assert "NOT ready — see .run/ollama.log" in capsys.readouterr().out
    recorded = json.loads(run.PIDFILE.read_text(encoding="utf-8"))
    assert set(recorded) == set(names)
    assert code == 1


def test_start_calls_a_service_ready_only_when_its_health_address_says_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Runbook §12.1: a false readiness is a STOP. Any reply used to count as ready.

    SIRVIS's port answers 404 — a stranger holding it, or the wrong service — and RAVIS's
    answers 401. Neither is ready; each is named with the status it gave, `start` waits its full
    time for both and fails, and NERVIS, answering 200, is still ready.
    """
    machine = FakeMachine([SIRVIS, RAVIS, NERVIS])
    machine.statuses = {8721: 404, 8731: 401}
    run = _launcher(monkeypatch, tmp_path, machine)
    opened = _quiet_start(monkeypatch, run)

    code = run.start()

    out = capsys.readouterr().out
    assert f"  {'SIRVIS':<11} NOT ready — its health address answers HTTP 404" in out
    assert f"  {'RAVIS':<11} NOT ready — its health address answers HTTP 401" in out
    assert f"  {'NERVIS':<11} ready" in out
    assert machine.now >= 2 * run.START_WAIT_SECONDS, "both were waited for"
    assert code == 1
    assert opened == []


def test_stop_takes_services_down_in_the_runbooks_shutdown_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Runbook §12.1's shutdown: code-server, NERVIS, RAVIS, SIRVIS, and the runtimes last.

    The PID file lists them alphabetically, which is the order `stop` used to follow. A record for
    a service the table no longer knows is still left alone, after the rest.
    """
    machine = FakeMachine([SIRVIS, OLLAMA, RAVIS, NERVIS, CODE_SERVER])
    pids = {"SIRVIS": 901, "Ollama": 902, "RAVIS": 903, "NERVIS": 904, "code-server": 905}
    for service in machine.services:
        machine.process(pids[service[0]], _as_ps_shows(service), None)
    run = _launcher(monkeypatch, tmp_path, machine)
    records = {
        name: {"pid": pids[name], "marker": marker} for name, _, marker, _, _ in machine.services
    }
    _record(run, dict(sorted(records.items())))

    assert run.stop() == 0

    assert [pid for pid, _ in machine.signals()] == [905, 904, 903, 901, 902]
    assert run.in_order(["Zeta", "Ollama", "Alpha", "code-server"], run.STOP_ORDER) == [
        "code-server", "Ollama", "Alpha", "Zeta"
    ]


def test_every_marker_is_on_its_own_serve_command_and_on_nothing_else_that_could_hold_its_pid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each marker is on its own service's command line, and on none that could inherit its PID.

    `stop` signals a recorded PID only if its command line carries the service's marker, so the
    marker is the whole of the PID-reuse guard, and it is only as good as it is narrow. Until
    12 September 2026 the markers were the bare names: every Python service runs from
    `ravis/.venv`, so `ravis` was on SIRVIS's and NERVIS's command lines and on every pytest or
    ruff run from that virtualenv — this suite's own included — and a recycled RAVIS PID held by
    any of them would have been stopped. This reads the real table, so a marker changed back, or
    a new service added with a careless one, fails here.

    Nothing in building the table is let out: the credentials it would mint or read, the
    workspace folders it creates and the programs it looks up are all replaced.
    """
    run = _load()
    _seal(monkeypatch, run, tmp_path)
    monkeypatch.setattr(run, "ROOT", tmp_path)
    for minted in ("nervis_ravis_credential", "benchmark_token", "admin_token",
                   "ravis_admin_credential", "clarvis_ravis_credential"):
        monkeypatch.setattr(run, minted, lambda: "(credential)")
    monkeypatch.setattr(run, "events_secret", lambda service: f"({service} events)")
    monkeypatch.setenv("RAVIS_UPSTREAMS", "[]")
    monkeypatch.setattr(run, "_default_upstreams", _refuse("read the credential store"))
    # Both optional programs present, so their markers are held to the same rule.
    install = "/Users/someone/.local/lib/code-server-4.135.0"
    monkeypatch.setattr(run, "ollama_binary", lambda: "/opt/homebrew/bin/ollama")
    monkeypatch.setattr(run, "code_server_binary", lambda: f"{install}/bin/code-server")
    monkeypatch.setattr(run, "code_server_settings", lambda: ([], 8080, "your own config"))

    services = run._services()

    python = run.venv_bin("python")
    own = {
        name: " ".join([str(python), *command] if command[0].startswith(str(run.VENV)) else command)
        for name, command, _, _, _ in services
    }
    # What `ps` shows for code-server once it has re-exec'd itself (measured on 4.135.0).
    reexeced = f"{install}/lib/node {install}"
    elsewhere = [
        f"{python} -m pytest -q -p no:warnings tests/test_launcher_lifecycle.py"
        " ../sirvis/tests ../nervis/tests ../ravis/tests/providers/test_ollama.py",
        f"{run.venv_bin('ruff')} check ../ravis/src ../sirvis/src ../nervis/src ../tools/run.py",
        f"{python} -m nervis doctor",
        f"{run.venv_bin('nervis')} doctor",
        # One of the model runners Ollama spawns and reaps all day.
        "/opt/homebrew/bin/ollama runner --model /Users/someone/.ollama/models/blobs/sha256-0",
    ]
    markers = {name: marker for name, _, marker, _, _ in services}
    assert set(markers) == {"SIRVIS", "RAVIS", "NERVIS", "Ollama", "code-server"}
    assert markers["code-server"] in reexeced
    for name, marker in markers.items():
        assert marker, f"{name} has no marker"
        assert marker in own[name], f"{name}'s marker {marker!r} is not on its own command line"
        assert [other for other, line in own.items() if other != name and marker in line] == []
        assert [line for line in elsewhere if marker in line] == []


def test_a_record_without_a_marker_is_never_signalled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A record with no marker, or an empty one, is left alone however alive its process is.

    An empty string is contained in every command line, so until 12 September 2026 a record
    without a marker confirmed whatever held its PID. `start` always writes one, so such a record
    comes from a hand-edited file or something else entirely, and nobody can vouch for it. It is
    not upgraded to today's marker either — that is for old records that *have* one. Both services
    here really are running, and are left running: `stop` says it could not confirm them, and its
    final check reports them still answering.
    """
    machine = FakeMachine([SIRVIS, RAVIS])
    machine.process(801, _as_ps_shows(RAVIS), 8731)
    machine.process(802, _as_ps_shows(SIRVIS), 8721)
    run = _launcher(monkeypatch, tmp_path, machine)
    _record(run, {"RAVIS": {"pid": 801}, "SIRVIS": {"pid": 802, "marker": ""}})

    assert run._alive(801, "") is False
    assert machine.log == []

    assert run.stop() == 1

    out = capsys.readouterr().out
    assert "RAVIS is recorded without a marker" in out
    assert "SIRVIS is recorded without a marker" in out
    assert machine.signals() == []
    assert "Still answering after stop: SIRVIS, RAVIS" in out


def test_a_runtime_this_launcher_did_not_start_is_left_running_without_failing_the_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ollama as a Linux system service: answering before the stack started, and after it stopped.

    Nothing recorded it, so nothing signalled it, and it is meant to keep running — every stop on
    an Ubuntu desktop ended "Still answering after stop: Ollama" with exit 1 until this
    (18 September 2026). It is still named, as left running.
    """
    machine = FakeMachine([SIRVIS, RAVIS, CODE_SERVER])
    run = _launcher(monkeypatch, tmp_path, machine)
    _record(run, {"SIRVIS": {"pid": 802, "marker": "sirvis"}})
    monkeypatch.setattr(run, "_services", lambda: [
        ("SIRVIS", [], "sirvis", "", "http://127.0.0.1:8721/ecosystem/health"),
        ("Ollama", [], "ollama serve", "", "http://127.0.0.1:11434/api/version"),
    ])
    monkeypatch.setattr(run, "responds", lambda url, _timeout=1.0: "11434" in url)

    assert run.stop() == 0

    out = capsys.readouterr().out
    assert "Ollama left running: this launcher didn't start it" in out
    assert "Still answering after stop" not in out
    assert "Stopped." in out


def test_the_first_stop_after_upgrading_checks_old_records_against_todays_markers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A PID file from before the markers were narrowed still works, without the old weakness.

    A stack started by the old launcher has a PID file saying `ravis` and `nervis`. `stop`
    confirms each record against today's marker instead: RAVIS, still running its serve command,
    is stopped as before, while the number recorded for NERVIS — since inherited by a pytest run
    from `ravis/.venv`, which the old `nervis` and `ravis` would both have matched — is left alone.
    """
    machine = FakeMachine([SIRVIS, RAVIS, NERVIS])
    machine.process(501, _as_ps_shows(RAVIS), 8731)
    machine.process(503, f"{VENV_BIN}/python -m pytest -q ../nervis/tests")
    run = _launcher(monkeypatch, tmp_path, machine)
    _record(run, {
        "NERVIS": {"pid": 503, "marker": "nervis"},
        "RAVIS": {"pid": 501, "marker": "ravis"},
    })

    assert run.stop() == 0

    out = capsys.readouterr().out
    assert machine.signals() == [(501, TERM)]
    assert "RAVIS stopped" in out
    assert "NERVIS was not running" in out
    assert 503 in machine.table


def test_a_quoted_windows_command_line_still_carries_its_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A Windows program path containing a space is quoted, and the marker is found through it.

    Windows puts quotes around such a path in the command line it records, which lands a `"`
    between `ravis.exe` and `serve` — the two halves of RAVIS's marker since 12 September 2026. A
    marker that could no longer be found would make every service on such a machine unstoppable
    ("was not running"), so quotes are ignored when matching. Nothing Windows-specific is reached:
    `tasklist`'s and PowerShell's answers are both supplied.
    """
    run = _load()
    _seal(monkeypatch, run, tmp_path)
    monkeypatch.setattr(run, "WINDOWS", True)
    line = '"C:\\Users\\Jane Doe\\NERVIS-ecosystem\\ravis\\.venv\\Scripts\\ravis.exe" serve\n'

    def windows(command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
        listed = "ravis.exe  4242 Console  1  52,000 K\n" if command[0] == "tasklist" else line
        return subprocess.CompletedProcess(command, 0, listed)

    monkeypatch.setattr(run.subprocess, "run", windows)

    assert run._alive(4242, "ravis.exe serve") is True
    assert run._alive(4242, "sirvis.exe serve") is False


def test_each_events_secret_is_minted_once_privately_and_differs_per_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """NERVIS 0.34.18: one secret per service, so one proves only its own events."""
    import stat

    run = _load()
    _seal(monkeypatch, run, tmp_path)
    ravis, sirvis = run.events_secret("ravis"), run.events_secret("sirvis")
    assert len(ravis) >= 32 and ravis != sirvis
    assert run.events_secret("ravis") == ravis, "minted once, then reused"
    assert stat.S_IMODE((tmp_path / "ravis-events.token").stat().st_mode) == 0o600
