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
LM_STUDIO = "http://127.0.0.1:1234"

EXTERNAL = [
    ("LM Studio", f"{LM_STUDIO}/v1/models"),
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
    #
    # Installed **with the dev extra**, which costs about 140 MB and is worth it:
    # there is one virtualenv at one path, and the verification commands the
    # repository documents — `.venv/bin/pytest`, `ruff`, `mypy` — live in it. A
    # runtime-only install produced a working application in which every command
    # in STATUS.md's own "verify this yourself" block was missing, which is the
    # kind of gap that only shows up to somebody who just cloned the thing.
    for target in ("protocol", "ravis", "sirvis", "nervis"):
        print(f"  installing {target}…")
        subprocess.run(
            [python, "-m", "pip", "install", "-q", "-e", f"{ROOT / target}[dev]"], check=True
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
        # Absolute, because both services default to a *relative* database path
        # and this launcher runs them from the repository root. Left alone, a
        # first run creates empty databases beside this file and the services
        # come up healthy, empty, and disconnected from every benchmark ever
        # recorded — which looks exactly like a working install with no data.
        # Found by the UI sweep on 2026-08-24: 81 runs and 2 runtime sets had
        # been orphaned this way.
        package = prefix.lower()
        env[f"{prefix}_DATABASE_PATH"] = str(ROOT / package / f"{package}.db")
        if prefix == "RAVIS":
            # Point RAVIS at SIRVIS. Without this RAVIS starts healthy with an
            # empty evidence store, its Evidence screen reads zero records, and
            # every routing decision falls back to advertised capabilities — all
            # of which looks like "SIRVIS has no evidence" rather than "nobody
            # told RAVIS where SIRVIS is". §13.4 makes the source optional, so
            # nothing errors; it just quietly does less.
            env["RAVIS_SIRVIS_BASE_URL"] = f"http://127.0.0.1:{SIRVIS_PORT}"
            # And at a runtime, when the operator has not named one. RAVIS with
            # no upstream has no models, therefore no candidates, therefore
            # nothing to ask SIRVIS about — so the evidence store reads "fresh,
            # 0 records" and every screen downstream looks empty for a reason
            # that is three steps away from what it shows.
            #
            # Only as a default: anything the operator set in the environment
            # wins, because guessing over a stated choice would be worse than
            # not guessing at all.
            if not env.get("RAVIS_UPSTREAM_BASE_URL") and not env.get("RAVIS_UPSTREAMS"):
                env["RAVIS_UPSTREAM_BASE_URL"] = LM_STUDIO
                env["RAVIS_UPSTREAM_KIND"] = "lmstudio"
                env["RAVIS_DEFAULTED_UPSTREAM"] = "1"
        if prefix == "NERVIS":
            # Where its peers are. Nothing is probed until M2, but `doctor`
            # prints these and getting them wrong here would make the first
            # thing anyone runs point at the wrong ports.
            env["NERVIS_RAVIS_BASE_URL"] = f"http://127.0.0.1:{RAVIS_PORT}"
            env["NERVIS_SIRVIS_BASE_URL"] = f"http://127.0.0.1:{SIRVIS_PORT}"
            # No allow-list: NERVIS serves the dashboard and the dashboard's own
            # API from one origin, so nothing it answers is ever cross-origin.
            return env
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
         _with_results(env_for("SIRVIS", SIRVIS_PORT)),
         f"http://127.0.0.1:{SIRVIS_PORT}/v1/status"),
        ("RAVIS", [str(venv_bin("ravis")), "serve"], "ravis",
         env_for("RAVIS", RAVIS_PORT), f"http://127.0.0.1:{RAVIS_PORT}/v1/models"),
        # NERVIS's own service since M0, replacing the `http.server` that stood
        # in for it. Same port, same URL, same dashboard file — what changes is
        # that the thing serving it now has a database, an identity and an
        # `/ecosystem/*` surface, which is what makes settings and conversations
        # able to outlive a browser profile.
        ("NERVIS", [str(venv_bin("nervis")), "serve"], "nervis",
         env_for("NERVIS", NERVIS_PORT),
         f"http://127.0.0.1:{NERVIS_PORT}/api/v1/health"),
    ]


def _with_results(env: dict[str, str]) -> dict[str, str]:
    """SIRVIS's raw-result directory, absolute for the same reason as its database.

    §11.9's per-run directory is written relative to the working directory, so a
    launcher running from the repository root would scatter results somewhere
    the CLI never looks.
    """
    env["SIRVIS_RESULTS_PATH"] = str(ROOT / "sirvis" / "results")
    return env


def dashboard_token() -> str:
    """A runtime-scoped token for the dashboard, minted once and kept.

    **Why this exists.** §4.5 requires a scope on every mutating endpoint, so
    without a token the dashboard can look but not load a model. Getting one was
    a terminal exercise: change directory, run a CLI, copy 43 characters, paste
    them into a field. That is not a one-click application, and the screen's own
    instructions for doing it were wrong for months — which is what a manual
    step nobody exercises tends to become.

    So the launcher does it. The token is minted on first start and cached at
    mode `0600` beside the logs, then handed to the page in the URL *fragment*.

    **Why the fragment.** Everything after `#` is never sent to a server — not
    to the static file server, not in a `Referer`, not into an access log. The
    page reads it, keeps it in memory, and erases it from the address bar before
    anything else runs. A query string would have been in the server log the
    moment the page loaded.

    This is a local, loopback-only, runtime-scoped credential for a service the
    person running this launcher already controls. It is not a password, and the
    threat it answers — another process on the machine calling a mutating
    endpoint — is unchanged by the launcher being the one to mint it.
    """
    cached = RUN / "dashboard.token"
    try:
        existing = cached.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    env = dict(os.environ)
    env["SIRVIS_DATABASE_PATH"] = str(ROOT / "sirvis" / "sirvis.db")
    try:
        minted = subprocess.run(
            [str(venv_bin("sirvis")), "token", "--mint", "nervis-dashboard",
             "--scopes", "read runtime"],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if minted.returncode != 0:
        return ""
    # The token is the last non-empty line; everything above it is the label,
    # the scopes and the warning that it is printed once.
    lines = [line.strip() for line in minted.stdout.splitlines() if line.strip()]
    token = lines[-1] if lines else ""
    if not token or " " in token:
        return ""
    RUN.mkdir(parents=True, exist_ok=True)
    handle = os.open(cached, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(token + "\n")
    return token


def dashboard_url() -> str:
    """The dashboard address, with the token and a cache key.

    The `v=` is the dashboard file's own modification time. A browser that has
    the page cached will otherwise happily keep serving it after an edit, which
    is not a theoretical concern — it cost a debugging detour while this handoff
    was being written, with the new code served and the old code running.
    """
    token = dashboard_token()
    try:
        version = int((ROOT / "nervis" / "index.html").stat().st_mtime)
    except OSError:
        version = 0
    return f"{DASHBOARD}?v={version}" + (f"#token={token}" if token else "")


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
        webbrowser.open(dashboard_url())
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

    if not os.environ.get("RAVIS_UPSTREAM_BASE_URL") and not os.environ.get("RAVIS_UPSTREAMS"):
        print(f"\nRAVIS upstream defaulted to LM Studio at {LM_STUDIO}.")
        print("  Set RAVIS_UPSTREAM_BASE_URL (or RAVIS_UPSTREAMS) to override.")
    print(f"\nDashboard: {DASHBOARD}")
    print("Stop them with the stop launcher next to this one.")
    if ready:
        # The fragment is never sent to a server. The page consumes it and
        # clears the address bar immediately.
        webbrowser.open(dashboard_url())
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
