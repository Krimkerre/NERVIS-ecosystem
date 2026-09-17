#!/usr/bin/env python3
"""Runbook §13, rehearsed across a database format change: upgrade, refused downgrade, restore.

`OPERATOR_RUNBOOK.md`'s rollback rehearsal went back and forth between two releases with the same
database format. The case that can lose data is the other one — an upgrade that converts the
database, then a way back — and this rehearses it for each of NERVIS, RAVIS and SIRVIS, on copies,
never on the live data:

1. **Find the change.** The commit that added the service's newest migration, and the release just
   before it (its parent), are read from git; the backup the live service wrote before applying
   that migration (`<db>.v<N-1>.bak`, beside its database) is the starting data. A service with no
   such backup is skipped and says so.
2. **The older release runs** on a copy of that backup: healthy, its own version, format N-1.
3. **The current release upgrades it**: healthy, format N, no table lost a row, and the backup it
   takes before migrating (`.v<N-1>.bak`) is there.
4. **The older release refuses** the converted database rather than running on it.
5. **The older release's `restore-database` puts the backup back**: format N-1, every table exactly
   as in step 2, integrity intact.
6. **The older release runs again**, and **the current release upgrades again** — the same data,
   once more, to show the round trip is repeatable.

Each copy runs from its own unpacked source, on a spare port, with its own settings folder, the
keychain switched off, and every model and peer address pointed at a port where nothing listens, so
it cannot read the owner's keys or touch the running stack. Every database is checked with SQLite's
integrity check after each step. Nothing is read from `.run/`; no model is loaded or called.

    python3 tools/upgrade_rehearsal.py              # all three, about two minutes
    python3 tools/upgrade_rehearsal.py --only ravis

Exit 0 when every check held, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / "ravis" / ".venv" / "bin" / "python"
SERVICES = ("nervis", "ravis", "sirvis")
DEAD = "http://127.0.0.1:9"
START_SECONDS = 90.0

failures: list[str] = []
notes: list[str] = []


def git(*arguments: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *arguments], capture_output=True,
                          text=True, check=True).stdout.strip()


def newest_migration(service: str) -> tuple[int, str, str]:
    """(its number, the commit that added it, the commit before it)."""
    path = ROOT / service / "src" / service / "storage" / "database.py"
    numbers = [int(n) for n in re.findall(r"^        (\d+),$", path.read_text(), re.MULTILINE)]
    newest = max(numbers)
    relative = str(path.relative_to(ROOT))
    added = git("log", "--format=%H", "-G", f"^        {newest},$", "--reverse", "--", relative)
    commit = added.splitlines()[0]
    return newest, commit, git("rev-parse", f"{commit}^")


def unpack(commit: str, into: Path) -> Path:
    into.mkdir(parents=True)
    archive = subprocess.run(["git", "-C", str(ROOT), "archive", commit],
                             capture_output=True, check=True).stdout
    with tempfile.TemporaryFile() as handle:
        handle.write(archive)
        handle.seek(0)
        with tarfile.open(fileobj=handle) as bundle:
            bundle.extractall(into, filter="data")
    return into


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def environment(service: str, database: Path, port: int, work: Path, code: Path) -> dict[str, str]:
    prefix = service.upper()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("NERVIS_", "RAVIS_", "SIRVIS_"))}
    env.update({
        "PYTHONPATH": f"{code / service / 'src'}{os.pathsep}{code / 'protocol' / 'src'}",
        "XDG_CONFIG_HOME": str(work / "config"), "APPDATA": str(work / "config"),
        "RAVIS_CREDENTIAL_KEYRING": "0",
        f"{prefix}_DATABASE_PATH": str(database), f"{prefix}_PORT": str(port),
    })
    if service == "nervis":
        env.update({f"NERVIS_{peer}_BASE_URL": DEAD
                    for peer in ("RAVIS", "SIRVIS", "CLARVIS", "CODE_SERVER", "LMSTUDIO", "OLLAMA")})
        env["NERVIS_WORKSPACE_PATH"] = str(work / "workspace")
    if service == "sirvis":
        env.update({"SIRVIS_LMSTUDIO_BASE_URL": DEAD, "SIRVIS_LMSTUDIO_CLI_PATH": "/nonexistent",
                    "SIRVIS_RESULTS_PATH": str(work / "results")})
    return env


def command(service: str, *arguments: str) -> list[str]:
    return [str(PYTHON), "-c",
            f"import sys; from {service}.cli import main; sys.exit(main(sys.argv[1:]))",
            *arguments]


def serve(service: str, code: Path, database: Path, work: Path) -> dict[str, Any]:
    """Start one build, wait for it to answer, read its version, stop it. Or say why not."""
    port = free_port()
    env = environment(service, database, port, work, code)
    log = work / f"{service}-{int(time.time() * 1000)}.log"
    with log.open("w") as output:
        process = subprocess.Popen(command(service, "serve"), env=env, cwd=work,
                                   stdout=output, stderr=subprocess.STDOUT)
    version, answered = "", False
    started = time.monotonic()
    while time.monotonic() - started < START_SECONDS and process.poll() is None:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/ecosystem/version",
                                        timeout=2) as answer:
                version = json.load(answer).get("build_version", "")
                answered = True
                break
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(0.5)
    exited = process.poll()
    if exited is None:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    return {"answered": answered, "version": version, "exited": exited,
            "log": log.read_text(errors="replace")[-600:]}


def state(database: Path) -> dict[str, Any]:
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        tables = [row[0] for row in connection.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
        rows = {t: connection.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}
        version = connection.execute("select max(version) from applied_migration").fetchone()[0]
        check = connection.execute("pragma integrity_check").fetchone()[0]
    return {"version": int(version or 0), "rows": rows, "integrity": check}


def expect(what: str, held: bool) -> None:
    if not held:
        failures.append(what)


def rehearse(service: str, work: Path) -> None:
    newest, _added, before = newest_migration(service)
    live = ROOT / service / f"{service}.db"
    backup = live.with_name(f"{live.name}.v{newest - 1}.bak")
    if not backup.is_file():
        notes.append(f"{service}: skipped — no {backup.name} beside its database to start from")
        return
    old = unpack(before, work / "old")
    new = ROOT
    old_version = git("show", f"{before}:{service}/pyproject.toml").split('version = "')[1].split('"')[0]
    database = work / "data" / f"{service}.db"
    database.parent.mkdir(parents=True)
    # A plain copy: the backup is a finished file nothing writes to, and opening it read-only
    # fails when its journal mode wants side files the directory has not got.
    shutil.copyfile(backup, database)
    (work / "workspace").mkdir()
    label = f"{service} {old_version} (format {newest - 1}) ↔ current (format {newest})"

    # 2. The older release on the older data.
    first = serve(service, old, database, work)
    start = state(database)
    # **Which code ran is shown by the data, not by the version it reports**: a service reports
    # the version *installed* in the environment, so the older source reports the current
    # number. The older code leaves the database at N-1 — the current code would convert it —
    # and step 4 shows it refusing format N, which the current code never does.
    expect(f"{label}: the older release did not answer on its own data ({first['log'][-200:]})",
           first["answered"])
    expect(f"{label}: after the older release ran the copy is at format {start['version']}, "
           f"not {newest - 1} — was it really the older code?", start["version"] == newest - 1)

    # 3. The current release upgrades it.
    upgraded = serve(service, new, database, work)
    after = state(database)
    lost = {t: (n, after["rows"].get(t)) for t, n in start["rows"].items()
            if after["rows"].get(t, -1) < n}
    written_backup = database.with_name(f"{database.name}.v{newest - 1}.bak")
    expect(f"{label}: the current release did not answer after upgrading ({upgraded['log'][-200:]})",
           upgraded["answered"])
    expect(f"{label}: upgraded to format {after['version']}, not {newest}", after["version"] == newest)
    expect(f"{label}: the upgrade lost rows: {lost}", not lost)
    expect(f"{label}: no {written_backup.name} was taken before migrating", written_backup.is_file())
    expect(f"{label}: integrity after the upgrade: {after['integrity']}", after["integrity"] == "ok")

    # 4. The older release refuses the converted database.
    refused = serve(service, old, database, work)
    expect(f"{label}: the older release ran on the newer database", not refused["answered"])
    expect(f"{label}: the older release's refusal does not say the database is newer",
           "newer" in refused["log"])

    # 5. The older release's restore command puts the backup back.
    restore = subprocess.run(
        command(service, "restore-database", "--version", str(newest - 1)),
        env=environment(service, database, free_port(), work, old), cwd=work,
        capture_output=True, text=True, timeout=120, check=False)
    back = state(database)
    expect(f"{label}: restore-database failed ({restore.returncode}: {restore.stdout[-200:]}"
           f"{restore.stderr[-200:]})", restore.returncode == 0)
    expect(f"{label}: restored to format {back['version']}, not {newest - 1}",
           back["version"] == newest - 1)
    expect(f"{label}: the restored data differs from the start", back["rows"] == start["rows"])
    expect(f"{label}: integrity after the restore: {back['integrity']}", back["integrity"] == "ok")

    # 6. The older release runs again, and the upgrade repeats.
    again_old = serve(service, old, database, work)
    expect(f"{label}: the older release did not answer after the restore", again_old["answered"])
    again_new = serve(service, new, database, work)
    final = state(database)
    expect(f"{label}: the second upgrade did not answer", again_new["answered"])
    expect(f"{label}: the second upgrade reached format {final['version']}", final["version"] == newest)
    expect(f"{label}: integrity after the second upgrade: {final['integrity']}",
           final["integrity"] == "ok")
    notes.append(
        f"{label}: older release answered, leaving format {start['version']}; upgraded (reporting "
        f"{upgraded['version']}) with "
        f"{sum(after['rows'].values())} rows over {len(after['rows'])} tables, none lost; "
        f"older release refused the converted database; restored to format {back['version']} "
        f"exactly; older release answered again; upgraded again to format {final['version']}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", choices=SERVICES, default=None)
    arguments = parser.parse_args()
    started = time.monotonic()
    for service in [arguments.only] if arguments.only else SERVICES:
        work = Path(tempfile.mkdtemp(prefix=f"upgrade-{service}-"))
        try:
            rehearse(service, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    print(f"Upgrade rehearsal, {time.monotonic() - started:.0f} s")
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
