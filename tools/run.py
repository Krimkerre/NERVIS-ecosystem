#!/usr/bin/env python3
"""Start, stop and check the ecosystem — detached, so closing a window is safe.

One file behind six launchers, so the sequence cannot drift between macOS, Linux
and Windows.

**Detached is the whole point.** The services are started in their own session
(POSIX) or as detached processes (Windows) with their output redirected to
files, so they outlive the terminal, the launcher, and the person who
accidentally hit ⌘W. That has a consequence worth stating plainly: nothing stops
them when you close the window, so there has to be a way to stop them on
purpose, which is what `stop` is for.

**Ports are not a choice made here.** SIRVIS on 8721 and RAVIS on 8731 are the
services' own defaults *and* the addresses hard-coded in `nervis/index.html`. A
launcher picking different ones would produce a dashboard reporting every
service as offline.

**What it does not start.** LM Studio, Ollama and Clarvis are separate
applications with their own lifecycles, and §9 puts model loading behind
SIRVIS's Resource Manager. Their state is *reported* instead, because "nothing
is routing" and "no runtime is running" are the same symptom with very different
fixes.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / "ravis" / ".venv"
RUN = ROOT / ".run"
PIDFILE = RUN / "services.json"

SIRVIS_PORT = 8721
RAVIS_PORT = 8731
NERVIS_PORT = 8790
DASHBOARD = f"http://127.0.0.1:{NERVIS_PORT}/index.html"

WINDOWS = platform.system() == "Windows"

# Runtimes this ecosystem talks to but does not own.
EXTERNAL = [
    ("LM Studio", "http://127.0.0.1:1234/v1/models"),
    ("Ollama", "http://127.0.0.1:11434/api/tags"),
    ("Clarvis", "http://127.0.0.1:7071/"),
]


def venv_bin(name: str) -> Path:
    if WINDOWS:
        return VENV / "Scripts" / f"{name}.exe"
    return VENV / "bin" / name


def ensure_venv() -> None:
    """Create and populate the virtualenv if it is not already there.

    First run has to work without instructions — a launcher that opens a window
    saying "no module named ravis" has failed at the only job it has.
    """
    if venv_bin("python").exists():
        return
    print("First run: creating the virtual environment (a minute or so)…")
    subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    python = str(venv_bin("python"))
    subprocess.run([python, "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
    # `ecosystem-protocol` is a local path dependency; pip will not find it on
    # PyPI because it does not live there. First, because the others need it.
    for target in ("protocol", "ravis", "sirvis"):
        print(f"  installing {target}…")
        subprocess.run(
            [python, "-m", "pip", "install", "-q", "-e", str(ROOT / target)], check=True
        )


def responds(url: str, timeout: float = 1.5) -> bool:
    """Whether something answers, without caring what it says.

    Any HTTP response counts, a 404 included: the question is "is a server
    listening", and only a connection error or timeout is an absence.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def _services() -> list[tuple[str, list[str], str, dict[str, str], str]]:
    """(name, command, marker, env, health url) for each service we own.

    `marker` is a distinctive fragment of the command line, used by `stop` to
    confirm a recorded PID is still the process we started rather than whatever
    the OS handed that number to next.
    """
    dashboard_origin = f"http://127.0.0.1:{NERVIS_PORT}"

    def env_for(prefix: str, port: int) -> dict[str, str]:
        env = dict(os.environ)
        env[f"{prefix}_HOST"] = "127.0.0.1"
        env[f"{prefix}_PORT"] = str(port)
        # The dashboard is served from another port, so it is cross-origin to
        # both services. Each default is an empty allow-list, which is why the
        # screens would otherwise silently show nothing.
        env[f"{prefix}_ALLOWED_ORIGINS"] = json.dumps([dashboard_origin])
        return env

    return [
        # Each service's own `serve` rather than bare uvicorn, so its startup
        # configuration check still runs — that is what refuses a non-loopback
        # bind without TLS, and skipping it would make this the one path that
        # bypasses the gate.
        ("SIRVIS", [str(venv_bin("sirvis")), "serve"], "sirvis",
         env_for("SIRVIS", SIRVIS_PORT), f"http://127.0.0.1:{SIRVIS_PORT}/v1/status"),
        ("RAVIS", [str(venv_bin("ravis")), "serve"], "ravis",
         env_for("RAVIS", RAVIS_PORT), f"http://127.0.0.1:{RAVIS_PORT}/v1/models"),
        ("NERVIS", [str(venv_bin("python")), "-m", "http.server", str(NERVIS_PORT),
                    "--bind", "127.0.0.1", "--directory", str(ROOT / "nervis")],
         "http.server", dict(os.environ), DASHBOARD),
    ]


def _spawn_detached(command: list[str], env: dict[str, str], log: Path) -> int:
    """Start one service so that it outlives this process and its terminal."""
    RUN.mkdir(parents=True, exist_ok=True)
    handle = log.open("ab")
    if WINDOWS:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        process = subprocess.Popen(
            command, cwd=str(ROOT), env=env, stdout=handle, stderr=handle,
            stdin=subprocess.DEVNULL, creationflags=flags,
        )
    else:
        # A new session, so the service has no controlling terminal and does not
        # receive the SIGHUP that closing one sends.
        process = subprocess.Popen(
            command, cwd=str(ROOT), env=env, stdout=handle, stderr=handle,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
    return process.pid


def _alive(pid: int, marker: str) -> bool:
    """Whether this PID is still the process we started.

    The marker check is what makes `stop` safe. PIDs are reused, and a recorded
    number that now belongs to something else must not be killed because a file
    on disk says it was ours.
    """
    if WINDOWS:
        found = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, check=False,
        )
        return str(pid) in found.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    listed = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True, text=True, check=False,
    )
    return marker in listed.stdout


def _recorded() -> dict[str, dict[str, object]]:
    try:
        with PIDFILE.open(encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def start() -> int:
    ensure_venv()
    running = status(quiet=True)
    if all(running.values()):
        print("Already running.")
        print(f"Dashboard: {DASHBOARD}")
        webbrowser.open(DASHBOARD)
        return 0

    print("Starting (detached — closing this window will not stop them)…")
    recorded = _recorded()
    for name, command, marker, env, _ in _services():
        if running.get(name):
            print(f"  {name} already running")
            continue
        pid = _spawn_detached(command, env, RUN / f"{name.lower()}.log")
        recorded[name] = {"pid": pid, "marker": marker}
        print(f"  {name} started (pid {pid})")
    RUN.mkdir(parents=True, exist_ok=True)
    with PIDFILE.open("w", encoding="utf-8") as handle:
        json.dump(recorded, handle, indent=2, sort_keys=True)

    print("\nWaiting for them to answer…")
    ready = True
    for name, _, _, _, url in _services():
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline and not responds(url):
            time.sleep(0.4)
        answering = responds(url)
        ready = ready and answering
        print(f"  {name:<7} {'ready' if answering else 'NOT ready — see .run/' + name.lower() + '.log'}")

    print("\nRuntimes this ecosystem uses but does not start:")
    for name, url in EXTERNAL:
        print(f"  {name:<10} {'answering' if responds(url, 1.0) else 'not running'}")

    print(f"\nDashboard: {DASHBOARD}")
    print("Stop them with the stop launcher next to this one.")
    if ready:
        webbrowser.open(DASHBOARD)
    return 0 if ready else 1


def stop() -> int:
    recorded = _recorded()
    if not recorded:
        print("Nothing recorded as running.")
        return 0
    print("Stopping…")
    for name, record in sorted(recorded.items()):
        pid = int(record.get("pid", 0) or 0)
        marker = str(record.get("marker", ""))
        if not pid or not _alive(pid, marker):
            print(f"  {name} was not running")
            continue
        # Ask first. A service killed outright can leave a half-written SQLite
        # journal, and SIRVIS releases its model leases on shutdown (§9).
        if WINDOWS:
            subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True, check=False)
        else:
            os.kill(pid, 15)
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline and _alive(pid, marker):
            time.sleep(0.2)
        if _alive(pid, marker):
            print(f"  {name} did not stop; forcing")
            if WINDOWS:
                subprocess.run(["taskkill", "/F", "/PID", str(pid), "/T"],
                               capture_output=True, check=False)
            else:
                os.kill(pid, 9)
        print(f"  {name} stopped")
    PIDFILE.unlink(missing_ok=True)
    print("Stopped.")
    return 0


def status(quiet: bool = False) -> dict[str, bool]:
    """Which services are answering. Health, not the PID file.

    A PID file says what was started; a health check says what is serving. When
    they disagree the health check is right, which is why `start` consults this
    rather than the file before deciding what to launch.
    """
    answers = {name: responds(url, 1.0) for name, _, _, _, url in _services()}
    if not quiet:
        for name, ok in answers.items():
            print(f"  {name:<7} {'answering' if ok else 'not running'}")
        print()
        for name, url in EXTERNAL:
            print(f"  {name:<10} {'answering' if responds(url, 1.0) else 'not running'} (external)")
    return answers


COMMANDS = {"start": start, "stop": stop, "status": lambda: (status(), 0)[1]}

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "start"
    if action not in COMMANDS:
        print(f"usage: {Path(__file__).name} [start|stop|status]", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(COMMANDS[action]())
