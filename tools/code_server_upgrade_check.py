#!/usr/bin/env python3
"""A few minutes of checking after a code-server upgrade, instead of a Stage 9 re-grade.

**Why this exists.** Stage 9's matrix (`clarvis/docs/code-server-matrix.md`) is 56 cells
and an hour or two of browser work. On 16 September 2026, after code-server 4.135.0 ->
4.137.0, the owner ruled that out as the price of every upgrade. Almost all of what the
matrix graded sits in two places, and both can be checked mechanically:

1. **code-server's own server and the webview host** — its login, proxy, origin checks,
   WebSocket routing and the page webviews load in. These are compared file by file with
   the install the matrix graded. Unchanged means those grades carry over as they stand.
2. **The VS Code extension API** Clarvis runs against — the rest of the matrix. Clarvis's
   own host suite runs on desktop VS Code at *exactly* the Code version code-server bundles.

Then the live state: code-server answers, Clarvis is installed at the version this
checkout ships and is byte-identical to its build, NERVIS reads the new version, and any
editor window with the Bridge on reports the shipped Clarvis and a readable status.

**What it does not cover**, said in its output as well: a browser other than the one the
matrix used, audio, and anything a changed file touches. A route, proxy, login or webview
file that changed fails the check and names the matrix cells to re-run by hand — a
smaller re-grade, only where something moved.

    python3 tools/code_server_upgrade_check.py            # check, print, change nothing
    python3 tools/code_server_upgrade_check.py --record   # and, on a pass, record it for NERVIS

`--record` writes `nervis/src/nervis/code_server_checks.json`, which NERVIS reads to show
that version as checked rather than untested. Standard library only; never reads a
password or any file under `.run/`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLARVIS = REPO.parent / "clarvis"
INSTALLS = Path.home() / ".local" / "lib"
RECORD = REPO / "nervis" / "src" / "nervis" / "code_server_checks.json"
NERVIS = "http://127.0.0.1:8790"
CODE_SERVER = "http://127.0.0.1:8080"

# The install Stage 9's matrix graded. Kept in step with `GRADED_CODE_SERVER` in
# `nervis/src/nervis/adapters.py`; a later full re-grade moves both.
GRADED = "4.135.0"

# Where the webview's own host page and service worker live inside the bundled VS Code.
WEBVIEW_HOST = Path("lib/vscode/out/vs/workbench/contrib/webview/browser/pre")

# code-server's own server files the matrix's proxy, login, origin and WebSocket cells rest
# on, by the cells that would need re-running if one changed. A changed server file not
# listed here is reported for review and does not fail the check: the matrix grades none
# of code-server's other server code.
GRADED_FILES = {
    "out/node/http.js": "origin validation; reverse proxy; base path",
    "out/node/proxy.js": "reverse proxy; large streams; WebSocket upgrade",
    "out/node/wsRouter.js": "WebSocket upgrade through a proxy",
    "out/node/routes/index.js": "base path; authenticated workbench through a proxy",
    "out/node/routes/login.js": "authenticated workbench through a proxy",
    "out/node/routes/vscode.js": "authenticated workbench; webview rendering; base path",
    "out/node/routes/pathProxy.js": "code-server port proxy on the Bridge's path",
    "out/node/routes/domainProxy.js": "code-server port proxy on the Bridge's path",
    "out/node/routes/health.js": "NERVIS's workbench probe",
    "out/node/settings.js": "globalState / workspaceState persistence",
    "out/node/app.js": "authenticated workbench; base path",
    "out/node/util.js": "authenticated workbench through a proxy (password checking)",
    "out/node/vscodeSocket.js": "multiple windows against one server",
}


@dataclass
class Report:
    """What was checked, in order, and whether all of it held."""

    lines: list[tuple[str, str, str]] = field(default_factory=list)
    review: list[str] = field(default_factory=list)

    def add(self, outcome: str, step: str, detail: str) -> None:
        self.lines.append((outcome, step, detail))

    @property
    def passed(self) -> bool:
        return all(outcome != "FAIL" for outcome, _, _ in self.lines)


# ── Pure parts, tested in `nervis/tests/test_code_server_upgrade_check.py` ────


def parse_version(output: str) -> tuple[str, str]:
    """`code-server --version`'s first line -> (code-server version, Code version)."""
    match = re.match(r"\s*(\d+\.\d+\.\d+)\s+\S+\s+with Code\s+(\d+\.\d+\.\d+)", output)
    return (match.group(1), match.group(2)) if match else ("", "")


def satisfies_caret(version: str, requirement: str) -> bool:
    """Whether `version` meets an `engines.vscode` requirement written as `^X.Y.Z`."""
    wanted = re.fullmatch(r"\^(\d+)\.(\d+)\.(\d+)", requirement.strip())
    found = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version.strip())
    if not wanted or not found:
        return False
    low = tuple(int(part) for part in wanted.groups())
    have = tuple(int(part) for part in found.groups())
    return have[0] == low[0] and have >= low


def changed_files(old: Path, new: Path) -> list[str]:
    """Files under `old` and `new` that differ or exist on one side only, relative, sorted.

    Source maps are skipped: they change whenever the file they map does, and say nothing
    the file itself does not.
    """
    names: set[str] = set()
    for root in (old, new):
        if root.is_dir():
            names.update(
                str(path.relative_to(root))
                for path in root.rglob("*")
                if path.is_file() and not path.name.endswith(".map")
            )
    return sorted(
        name
        for name in names
        if not (old / name).is_file()
        or not (new / name).is_file()
        or not filecmp.cmp(old / name, new / name, shallow=False)
    )


def classify(server_changes: list[str], webview_changes: list[str]) -> tuple[list[str], list[str]]:
    """(failures naming the cells to re-run, files to review that fail nothing)."""
    failures = [
        f"{name} changed — re-run: {GRADED_FILES[name]}"
        for name in server_changes
        if name in GRADED_FILES
    ]
    failures += [
        f"{WEBVIEW_HOST / name} changed — re-run: webview rendering, messaging, focus, "
        "streaming, CSP and resources"
        for name in webview_changes
    ]
    review = [name for name in server_changes if name not in GRADED_FILES]
    return failures, review


def host_suite_counts(output: str) -> tuple[int, int]:
    """(passing, failing) from the host suite's Mocha summary."""
    passing = re.search(r"(\d+) passing", output)
    failing = re.search(r"(\d+) failing", output)
    return (int(passing.group(1)) if passing else 0, int(failing.group(1)) if failing else 0)


def recorded(
    records: dict[str, dict[str, object]], version: str, entry: dict[str, object]
) -> dict[str, dict[str, object]]:
    """The record with this version's entry replaced, others kept."""
    return {**records, version: entry}


# ── Steps ────────────────────────────────────────────────────────────────────


def _run(
    command: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False
    )


def _get_json(url: str) -> object:
    with urllib.request.urlopen(url, timeout=5) as answer:
        return json.loads(answer.read().decode("utf-8"))


def installed(report: Report) -> tuple[str, str]:
    binary = shutil.which("code-server")
    if not binary:
        report.add("FAIL", "code-server installed", "no `code-server` on PATH")
        return "", ""
    version, code = parse_version(_run([binary, "--version"]).stdout)
    if not version:
        report.add("FAIL", "code-server installed", f"{binary} did not say its version")
        return "", ""
    report.add(
        "PASS", "code-server installed", f"{version} with Code {code} ({Path(binary).resolve()})"
    )
    return version, code


def compare_with_graded(report: Report, version: str) -> list[str]:
    old, new = INSTALLS / f"code-server-{GRADED}", INSTALLS / f"code-server-{version}"
    if not old.is_dir():
        report.add(
            "FAIL",
            f"compared with {GRADED}",
            f"{old} is gone, so nothing can be carried over — only a full re-grade is left",
        )
        return []
    if not new.is_dir():
        report.add(
            "FAIL", f"compared with {GRADED}", f"{new} not found; install the standalone way"
        )
        return []
    server = changed_files(old / "out", new / "out")
    webview = changed_files(old / WEBVIEW_HOST, new / WEBVIEW_HOST)
    failures, review = classify([f"out/{name}" for name in server], webview)
    for failure in failures:
        report.add("FAIL", "code-server's server and webview host", failure)
    if not failures:
        report.add(
            "PASS",
            "code-server's server and webview host",
            f"the login, proxy, origin, WebSocket and webview-host files match {GRADED}; "
            f"{len(server)} other server file(s) differ",
        )
    report.review.extend(review)
    return review


def clarvis_engine(report: Report, code: str) -> dict[str, object]:
    manifest = json.loads((CLARVIS / "package.json").read_text(encoding="utf-8"))
    wanted = str(manifest.get("engines", {}).get("vscode", ""))
    ok = satisfies_caret(code, wanted)
    report.add(
        "PASS" if ok else "FAIL",
        "Clarvis's engine range",
        f"Code {code} {'meets' if ok else 'does not meet'} engines.vscode {wanted}",
    )
    return manifest


def clarvis_installed(report: Report, shipped: str) -> None:
    listing = _run(["code-server", "--list-extensions", "--show-versions"]).stdout
    found = re.search(r"krimkerre\.clarvis@(\S+)", listing)
    if not found or found.group(1) != shipped:
        report.add(
            "FAIL",
            "Clarvis installed in code-server",
            f"found {found.group(1) if found else 'nothing'}, this checkout ships {shipped}",
        )
        return
    copy = Path.home() / ".local/share/code-server/extensions" / f"krimkerre.clarvis-{shipped}"
    same = all(
        filecmp.cmp(CLARVIS / part, copy / part, shallow=False)
        for part in ("dist/extension.js", "media/chat.js")
    )
    report.add(
        "PASS" if same else "FAIL",
        "Clarvis installed in code-server",
        f"{shipped}, {'byte-identical to' if same else 'different from'} this checkout's build",
    )


def host_suite(report: Report, code: str) -> None:
    """Clarvis's host specs on desktop VS Code at code-server's own Code version."""
    env = {**os.environ, "CLARVIS_CODE_VERSION": code}
    try:
        done = _run(["npm", "run", "test:host"], cwd=CLARVIS, env=env, timeout=900)
    except subprocess.TimeoutExpired:
        report.add("FAIL", f"Clarvis's host suite on Code {code}", "did not finish in 15 minutes")
        return
    passing, failing = host_suite_counts(done.stdout + done.stderr)
    ok = done.returncode == 0 and passing > 0 and failing == 0
    report.add(
        "PASS" if ok else "FAIL",
        f"Clarvis's host suite on Code {code}",
        f"{passing} passing, {failing} failing" + ("" if ok else f" (exit {done.returncode})"),
    )


def live(report: Report, version: str, shipped: str) -> None:
    try:
        with urllib.request.urlopen(f"{CODE_SERVER}/healthz", timeout=5) as answer:
            report.add("PASS", "code-server answering", f"/healthz {answer.status}")
    except OSError as failure:
        report.add("FAIL", "code-server answering", f"/healthz did not answer ({failure})")
    try:
        services = _get_json(f"{NERVIS}/api/v1/services")
        entry = next(
            (item for item in services.get("items", []) if item.get("key") == "codeserver"), {}
        )  # type: ignore[union-attr]
        reason = json.dumps(entry.get("capability_reasons", {}))
        reads = f"code-server {version}" in reason
        report.add(
            "PASS" if reads else "FAIL",
            "NERVIS reads the new version",
            str(entry.get("detail", "no code-server row"))
            if reads
            else f"NERVIS did not name {version}",
        )
        windows(report, shipped)
    except OSError as failure:
        report.add("SKIP", "NERVIS", f"not answering ({failure}); its checks were not run")


def windows(report: Report, shipped: str) -> None:
    found = _get_json(f"{NERVIS}/api/v1/registry/instances")
    live_windows = [
        item
        for item in found.get("items", [])
        if item.get("service") == "clarvis" and item.get("live")
    ]  # type: ignore[union-attr]
    if not live_windows:
        report.add(
            "SKIP",
            "an editor window",
            "none registered; open one with the Bridge on to check it too",
        )
        return
    for window in live_windows:
        build = str(window.get("build_version", ""))
        status = _get_json(
            f"{NERVIS}/api/v1/registry/instances/clarvis/{window['instance_id']}/status"
        )
        state = status.get("state") if isinstance(status, dict) else None  # type: ignore[union-attr]
        ok = build.startswith(f"{shipped}+") and state not in (None, "unknown")
        report.add(
            "PASS" if ok else "FAIL",
            f"window {window['instance_id'][:8]}",
            f"Clarvis {build.split('+')[0] or '?'}, status {state or 'unread'}",
        )


# ── Running ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--record", action="store_true", help="on a pass, record it for NERVIS")
    parser.add_argument(
        "--skip-host-suite", action="store_true", help="for a quick look only; never recorded"
    )
    args = parser.parse_args(argv)

    report = Report()
    version, code = installed(report)
    if version:
        compare_with_graded(report, version)
        shipped = str(clarvis_engine(report, code).get("version", ""))
        clarvis_installed(report, shipped)
        if args.skip_host_suite:
            report.add("SKIP", "Clarvis's host suite", "skipped on request")
        else:
            host_suite(report, code)
        live(report, version, shipped)

    for outcome, step, detail in report.lines:
        print(f"  {outcome:<5} {step}: {detail}")
    for name in report.review:
        print(
            f"  NOTE  {name} changed too; the matrix grades nothing in it — read its diff if unsure"
        )
    print("  not covered: other browsers, audio, and anything a failing line names")

    if not report.passed:
        print(
            f"\ncode-server {version or '?'}: FAIL — re-run the named matrix cells, then re-check"
        )
        return 1
    print(f"\ncode-server {version}: PASS")
    if args.record and not args.skip_host_suite:
        records = json.loads(RECORD.read_text(encoding="utf-8")) if RECORD.is_file() else {}
        entry = {
            "checked_on": dt.date.today().isoformat(),
            "code": code,
            "carried_from": GRADED,
            "clarvis": shipped,
            "review": report.review,
        }
        RECORD.write_text(
            json.dumps(recorded(records, version, entry), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"recorded in {RECORD.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
