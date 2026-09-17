#!/usr/bin/env python3
"""Runbook §10, rehearsed against the running stack: a service absent at startup, and a crash.

`tools/check_degradation.py` scores §10's nineteen conditions from their evidence. Two of them
were proved live for only part of the stack — a service absent at startup for one starting order,
a crash mid-operation for SIRVIS alone — and this does the rest on the owner's machine, through
the launcher, with nothing but the stack's own ports and processes:

* **Absent at startup** (`absent_at_startup`), for SIRVIS, RAVIS and NERVIS in turn: the stack is
  stopped, the service's port is held (bound, never listening, so the service cannot bind and a
  connection to it waits and times out, as to a hung host), and the launcher starts the rest. The launcher must name the service not
  ready and the others ready; the others must answer their own reads; NERVIS, when it is up, must
  report the absent one unreachable. Then the port is released, the launcher starts it, and NERVIS
  must report it healthy again.
* **Crash and restart** (`crash_and_restart`), for RAVIS, NERVIS and code-server in turn: the
  process is killed outright (SIGKILL). NERVIS must report it unreachable, the others must keep
  answering, the launcher must bring it back, and NERVIS must report it healthy again. A killed
  service's database must pass SQLite's integrity check, and NERVIS's stored events must not shrink.

Each step is timed against NERVIS's probe interval, which is the detection interval §10 asks
readiness to be truthful within. Nothing is read from `.run/`; no model is loaded or called.

    python3 tools/failure_rehearsal.py            # everything, about four minutes
    python3 tools/failure_rehearsal.py --only crash

Needs the stack started with `tools/run.py`, and nothing in flight — it refuses otherwise. Stops and
starts the stack several times. Exit 0 when every check held, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = [sys.executable, str(ROOT / "tools" / "run.py")]
NERVIS = "http://127.0.0.1:8790"

#: Each service the rehearsal touches: its port, how NERVIS's registry names it, a read that only
#: answers when the service itself is working, and its database (if any).
SERVICES: dict[str, dict[str, Any]] = {
    "SIRVIS": {"port": 8721, "key": "sirvis", "read": "/api/v1/system",
               "database": ROOT / "sirvis" / "sirvis.db"},
    "RAVIS": {"port": 8731, "key": "ravis", "read": "/v1/models",
              "database": ROOT / "ravis" / "ravis.db"},
    "NERVIS": {"port": 8790, "key": "nervis", "read": "/api/v1/services",
               "database": ROOT / "nervis" / "nervis.db"},
    "code-server": {"port": 8080, "key": "codeserver", "read": "/healthz", "database": None},
}
#: NERVIS's probe interval (`Settings.probe_interval_seconds`) plus its probe deadline and one
#: pass of slack: how long a state change may take to show before the check calls it untruthful.
DETECTION_SECONDS = 20.0 + 5.0 + 10.0
#: What NERVIS says about a service that is not there. `discovering` — not asked yet — is not one
#: of them: counting it would pass a NERVIS that had not looked.
DOWN = frozenset({"unreachable", "stale", "stopped"})
RESTART_SECONDS = 120.0

failures: list[str] = []
notes: list[str] = []


def status(url: str, timeout: float = 3.0) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as answer:
            return int(answer.status)
    except urllib.error.HTTPError as refused:
        return int(refused.code)
    except Exception:  # noqa: BLE001 - every other failure is "not answering"
        return None


def read_json(url: str, timeout: float = 5.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as answer:
        return json.load(answer)


def service_ok(name: str) -> bool:
    spec = SERVICES[name]
    return status(f"http://127.0.0.1:{spec['port']}{spec['read']}") == 200


def nervis_says(key: str) -> str:
    try:
        items = read_json(f"{NERVIS}/api/v1/services")["items"]
    except Exception:  # noqa: BLE001
        return "nervis not answering"
    return next((str(one.get("state")) for one in items if one.get("key") == key), "absent")


def wait(what: str, check: Callable[[], bool], seconds: float) -> float | None:
    """Seconds until `check` holds, or None (and a failure) if it never did."""
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        if check():
            return round(time.monotonic() - started, 1)
        time.sleep(1.0)
    failures.append(f"{what}: not within {seconds:.0f} s")
    return None


def expect(what: str, held: bool) -> None:
    if not held:
        failures.append(what)


def launcher(*arguments: str) -> tuple[int, str]:
    done = subprocess.run([*LAUNCHER, *arguments], capture_output=True, text=True,
                          timeout=600, check=False, cwd=ROOT)
    return done.returncode, done.stdout + done.stderr


def pid_on(port: int) -> int | None:
    done = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                          capture_output=True, text=True, check=False)
    pids = [int(one) for one in done.stdout.split() if one.strip().isdigit()]
    return pids[0] if pids else None


def integrity(database: Path | None) -> str:
    if database is None:
        return "ok"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        return str(connection.execute("pragma quick_check").fetchone()[0])


def event_count() -> int:
    with sqlite3.connect(f"file:{SERVICES['NERVIS']['database']}?mode=ro", uri=True) as con:
        return int(con.execute("select count(*) from event").fetchone()[0])


def nothing_in_flight() -> str:
    """Why the rehearsal must not run now, or ''."""
    if not all(service_ok(name) for name in ("SIRVIS", "RAVIS", "NERVIS")):
        return "the stack is not fully up; start it with tools/run.py first"
    jobs = read_json("http://127.0.0.1:8721/api/v1/benchmark-jobs").get("items", [])
    if any(job.get("state") in ("queued", "running") for job in jobs):
        return "a benchmark job is queued or running"
    residency = read_json("http://127.0.0.1:8721/api/v1/runtime/residency")
    if residency.get("leases") or residency.get("holdings"):
        return "SIRVIS holds a model or a runtime session is open"
    codex = read_json(f"{NERVIS}/api/v1/relay/ravis/api/v1/codex")
    if any(run.get("state") not in ("idle", "ended") for run in codex.get("runs", [])):
        return "a Codex task is running"
    return ""


# ── Absent at startup ────────────────────────────────────────────────────────


def absent_at_startup(absent: str) -> None:
    spec = SERVICES[absent]
    others = [name for name in ("SIRVIS", "RAVIS", "NERVIS") if name != absent]
    launcher("stop")
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # SO_REUSEADDR so the connections the stopped service leaves in TIME_WAIT do not block this
    # bind; without SO_REUSEPORT the service's own bind to the same address still fails.
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", spec["port"]))  # bound, never listening: connections time out
    try:
        code, output = launcher("start")
        expect(f"{absent} absent: the launcher did not fail", code != 0)
        expect(f"{absent} absent: the launcher did not name it not ready",
               any(line.strip().startswith(f"{absent} ") and "NOT ready" in line
                   for line in output.splitlines()))
        for name in others:
            expect(f"{absent} absent: the launcher did not call {name} ready",
                   any(line.strip().startswith(f"{name} ") and line.strip().endswith("ready")
                       and "NOT" not in line for line in output.splitlines()))
            expect(f"{absent} absent: {name} does not answer its own read", service_ok(name))
        if absent != "NERVIS":
            seen = wait(f"{absent} absent: NERVIS reporting it unreachable",
                        lambda: nervis_says(spec["key"]) in DOWN, DETECTION_SECONDS)
            notes.append(f"{absent} absent at startup: NERVIS said {nervis_says(spec['key'])!r} "
                         f"after {seen} s; {', '.join(others)} answered")
        else:
            notes.append("NERVIS absent at startup: SIRVIS and RAVIS started and answered")
    finally:
        holder.close()
    code, output = launcher("start")
    expect(f"{absent} returned: the launcher failed ({code})", code == 0)
    back = wait(f"{absent} returned: NERVIS reporting it healthy",
                lambda: nervis_says(spec["key"]) == "healthy", DETECTION_SECONDS)
    notes.append(f"{absent} released: healthy in NERVIS after {back} s")


# ── Crash and restart ────────────────────────────────────────────────────────


def crash_and_restart(victim: str) -> None:
    spec = SERVICES[victim]
    pid = pid_on(spec["port"])
    if pid is None:
        failures.append(f"{victim} crash: nothing listens on {spec['port']}")
        return
    events_before = event_count()
    os.kill(pid, signal.SIGKILL)
    # Gone, or replaced: code-server's wrapper can start a new server process on its own.
    gone = wait(f"{victim} crash: the process gone", lambda: pid_on(spec["port"]) != pid, 10.0)
    others = [name for name in ("SIRVIS", "RAVIS", "NERVIS") if name != victim]
    if victim != "NERVIS":
        seen = wait(f"{victim} crash: NERVIS reporting it unreachable",
                    lambda: nervis_says(spec["key"]) in DOWN, DETECTION_SECONDS)
    else:
        seen = None
    for name in others:
        expect(f"{victim} crash: {name} stopped answering its own read", service_ok(name))
    code, _ = launcher("start")
    expect(f"{victim} crash: the launcher failed to bring it back ({code})", code == 0)
    back = wait(f"{victim} crash: NERVIS reporting it healthy again",
                lambda: nervis_says(spec["key"]) == "healthy", RESTART_SECONDS)
    check = integrity(spec["database"])
    expect(f"{victim} crash: its database failed the integrity check ({check})", check == "ok")
    events_after = event_count()
    expect(f"{victim} crash: NERVIS's stored events shrank ({events_before} → {events_after})",
           events_after >= events_before)
    notes.append(
        f"{victim} killed (pid {pid}): gone after {gone} s"
        + (f", unreachable in NERVIS after {seen} s" if seen is not None else "")
        + f"; back and healthy after {back} s; database {check}; "
        f"events {events_before} → {events_after}; {', '.join(others)} answered throughout"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", choices=["absent", "crash"], default=None)
    arguments = parser.parse_args()
    refusal = nothing_in_flight()
    if refusal:
        print(f"not rehearsing: {refusal}")
        return 1
    started = time.monotonic()
    try:
        if arguments.only in (None, "absent"):
            for name in ("SIRVIS", "RAVIS", "NERVIS"):
                absent_at_startup(name)
        if arguments.only in (None, "crash"):
            for name in ("RAVIS", "NERVIS", "code-server"):
                crash_and_restart(name)
    finally:
        # Whatever happened above, the stack is left running: start only starts what is missing.
        launcher("start")
    final = {name: service_ok(name) for name in SERVICES}
    expect(f"the stack did not end up whole: {final}", all(final.values()))
    print(f"Failure rehearsal, {time.monotonic() - started:.0f} s")
    for line in notes:
        print(f"  - {line}")
    if failures:
        print(f"\n{len(failures)} check(s) failed:")
        for line in failures:
            print(f"  ✗ {line}")
        return 1
    print("\nEvery check held.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
