"""`ravis codex calibrate`: start a calibration run with the owner present, and follow it plainly.

    ravis codex calibrate --project-a PATH --project-b PATH [--prepare] [--only K5a,K10] [--yes]
                          [--credential-file FILE] [--url URL]

`tools/run.py codex calibrate …` runs this with the owner's command-line credential and RAVIS's
address filled in. It talks to a RAVIS started with `RAVIS_CODEX_CALIBRATION=1`; without that, RAVIS
has no calibration route and this says so.

**Before it asks RAVIS anything:**
- each path is checked against the ecosystem's repositories (`RAVIS_AGENT_PROTECTED_REPOSITORIES`
  and the checkout RAVIS runs from) — a project inside NERVIS-ecosystem or clarvis is refused here,
  and again by RAVIS;
- with `--prepare`, each project is made ready: its folder created if missing, `git init`, a
  `.gitignore` for the scratch folders calibration uses (`.run/`, `.clarvis/`) and a base commit.
  Paths with spaces are passed to git as single arguments, never through a shell. The decoy key
  files and their markers are **not** made here: RAVIS makes them, fresh, when the run starts, and
  removes them when it ends;
- unless `--yes` is given, the owner is asked to agree to spend the ChatGPT plan's allowance. A run
  of only the model-free questions (K10, K5a) doesn't ask.

**While it runs** it prints a line as each question starts and ends, then the result: whether
the strict file rules are proven, what goes back to the owner, and where the transcripts are. Ctrl+C
stops following, not the run.

The credential is read from a file and sent in the `Authorization` header only; it is never printed.

Exit codes: 0 the rules were proven; 1 the run ended without proving them, or wasn't started;
2 bad arguments or a project that can't be used; 3 RAVIS stopped answering; 10 RAVIS refused.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ravis.codex.calibration.plan import (
    BY_ID,
    SCENARIO_IDS,
    protected_repositories,
    refused_folder,
)
from ravis.config import Settings

EXIT_PROVEN, EXIT_NOT_PROVEN, EXIT_BAD_ARGUMENTS, EXIT_RAVIS_DOWN, EXIT_REFUSED = 0, 1, 2, 3, 10
RUNS = "/api/v1/codex/calibration/runs"
GITIGNORE = "# Scratch folders RAVIS's calibration makes and removes.\n.run/\n.clarvis/\n"
README = "A throwaway project for RAVIS's Codex calibration. Safe to delete afterwards.\n"

#: (method, path, JSON body or None, extra headers) → (HTTP status, parsed body); status 0 if down.
Send = Callable[[str, str, "dict[str, Any] | None", "dict[str, str]"], "tuple[int, Any]"]


def add_codex_parser(subcommands: Any) -> None:
    codex = subcommands.add_parser("codex", help="Codex through RAVIS")
    actions = codex.add_subparsers(dest="codex_command", required=True)
    calibrate = actions.add_parser(
        "calibrate", help="prove Codex's file rules on two throwaway projects, owner present"
    )
    calibrate.add_argument("--project-a", required=True, help="the first throwaway git project")
    calibrate.add_argument("--project-b", required=True, help="the second throwaway git project")
    calibrate.add_argument(
        "--prepare", action="store_true",
        help="make each project a git project with a base commit first, where it isn't one",
    )
    calibrate.add_argument("--only", default="", help="scenario ids to ask, such as K5a,K10")
    calibrate.add_argument(
        "--yes", action="store_true", help="the owner agrees to use the plan's allowance"
    )
    calibrate.add_argument("--url", default="", help="RAVIS's address (default: this Mac's)")
    calibrate.add_argument(
        "--credential-file", default=os.environ.get("RAVIS_OWNER_CREDENTIAL_FILE", ""),
        help="the file holding the owner's command-line credential",
    )
    calibrate.add_argument("--poll-seconds", type=float, default=5.0, help=argparse.SUPPRESS)


def run_calibrate(
    arguments: argparse.Namespace,
    settings: Settings,
    *,
    send: Send | None = None,
    ask: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    only = [item.strip() for item in arguments.only.split(",") if item.strip()]
    unknown = [item for item in only if item not in BY_ID]
    if unknown:
        out(f"Unknown scenario ids {unknown}; the ids are {', '.join(SCENARIO_IDS)}.")
        return EXIT_BAD_ARGUMENTS
    projects = _checked_projects(arguments, settings, out)
    if projects is None:
        return EXIT_BAD_ARGUMENTS
    if arguments.prepare and not all(_prepare(label, path, out) for label, path in projects):
        return EXIT_BAD_ARGUMENTS
    credential = _credential(arguments.credential_file, out)
    if credential is None:
        return EXIT_BAD_ARGUMENTS
    needs_model = any(BY_ID[key].needs_model for key in (only or SCENARIO_IDS))
    if needs_model and not arguments.yes and not _agreed(ask, out):
        return EXIT_NOT_PROVEN
    send = send or http_sender(arguments.url or f"http://127.0.0.1:{settings.port}", credential)
    body: dict[str, Any] = {
        "project_a": str(projects[0][1]), "project_b": str(projects[1][1]),
        "allowance_go_ahead": needs_model,
    }
    if only:
        body["scenarios"] = only
    status, answer = send("POST", RUNS, body, {"Idempotency-Key": secrets.token_urlsafe(24)})
    if status != 202:
        return _refused(status, answer, out)
    return follow(send, answer["calibration"], out=out, sleep=sleep, poll=arguments.poll_seconds)


def _checked_projects(
    arguments: argparse.Namespace, settings: Settings, out: Callable[[str], None]
) -> list[tuple[str, Path]] | None:
    protected = (
        *(Path(path) for path in settings.agent_protected_repositories), *protected_repositories()
    )
    projects = []
    for label, raw in (("A", arguments.project_a), ("B", arguments.project_b)):
        path = Path(os.path.realpath(os.path.expanduser(raw)))
        refused = refused_folder(path, tuple(Path(os.path.realpath(p)) for p in protected))
        if refused is not None:
            out(f"Project {label} ({raw}) can't be used: {refused}.")
            return None
        projects.append((label, path))
    return projects


def _prepare(label: str, path: Path, out: Callable[[str], None]) -> bool:
    """Make one project ready: a folder, a git repository, and a base commit."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        if not (path / ".git").exists():
            _git(path, "init", "-q")
        if _git(path, "rev-parse", "--verify", "-q", "HEAD", check=False) != 0:
            for name, text in ((".gitignore", GITIGNORE), ("README.md", README)):
                if not (path / name).exists():
                    (path / name).write_text(text, encoding="utf-8")
            _git(path, "add", ".gitignore", "README.md")
            _git(path, "-c", "user.name=RAVIS calibration", "-c",
                 "user.email=calibration@ravis.invalid", "commit", "-q", "-m", "Calibration base")
    except (OSError, subprocess.CalledProcessError) as failure:
        out(f"Project {label} ({path}) couldn't be prepared: {failure}")
        return False
    out(f"Project {label} is ready: {path} (git, with a base commit). RAVIS makes the decoy files "
        "and their markers when the run starts, and removes them when it ends.")
    return True


def _git(path: Path, *arguments: str, check: bool = True) -> int:
    done = subprocess.run(
        ["git", "-C", str(path), *arguments], capture_output=True, text=True, check=check
    )
    return done.returncode


def _credential(file: str, out: Callable[[str], None]) -> str | None:
    if not file:
        out("Name the owner's command-line credential with --credential-file "
            "(tools/run.py codex calibrate does this for you).")
        return None
    try:
        credential = Path(file).expanduser().read_text(encoding="utf-8").strip()
    except OSError as failure:
        out(f"The credential file can't be read: {failure.strerror or failure}")
        return None
    return credential or None


def _agreed(ask: Callable[[str], str], out: Callable[[str], None]) -> bool:
    answer = ask(
        "This run uses your ChatGPT plan's allowance for about sixteen short Codex turns. "
        "Type yes to start: "
    )
    if answer.strip().lower() == "yes":
        return True
    out("Not started.")
    return False


def _refused(status: int, answer: Any, out: Callable[[str], None]) -> int:
    if status == 0:
        out("RAVIS is not answering.")
        return EXIT_RAVIS_DOWN
    if status == 404:
        out("This RAVIS doesn't offer calibration: start it with RAVIS_CODEX_CALIBRATION=1.")
        return EXIT_REFUSED
    error = answer.get("error") if isinstance(answer, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    out(f"RAVIS refused: {message or f'HTTP {status}'}")
    return EXIT_REFUSED


def follow(
    send: Send,
    view: dict[str, Any],
    *,
    out: Callable[[str], None],
    sleep: Callable[[float], None],
    poll: float,
) -> int:
    """Print the run's progress until it finishes, then its result."""
    printed: dict[str, str] = {}
    out(f"Calibration {view['run_id']} started on {view['projects']['A']} and "
        f"{view['projects']['B']}.")
    try:
        while True:
            _progress(view, printed, out)
            if view["state"] == "finished":
                return _result(view.get("result") or {}, out)
            sleep(poll)
            status, answer = send("GET", f"{RUNS}/{view['run_id']}", None, {})
            latest = answer.get("calibration") if isinstance(answer, dict) else None
            if status != 200 or not isinstance(latest, dict):
                out("RAVIS stopped answering about the run.")
                return EXIT_RAVIS_DOWN
            view = latest
    except KeyboardInterrupt:
        out("Stopped following. The run carries on inside RAVIS.")
        return EXIT_NOT_PROVEN


def _progress(view: dict[str, Any], printed: dict[str, str], out: Callable[[str], None]) -> None:
    for scenario in view["scenarios"]:
        verdict = scenario["verdict"]
        if verdict == "pending" or printed.get(scenario["id"]) == verdict:
            continue
        printed[scenario["id"]] = verdict
        if verdict == "running":
            out(f"{scenario['id']}: asking — {scenario['question']}")
        else:
            out(f"{scenario['id']}: {verdict} — {scenario['detail']}")


def _result(result: dict[str, Any], out: Callable[[str], None]) -> int:
    out("")
    out(str(result.get("sentence")))
    out(f"The ecosystem's own repositories: {result.get('protected_repositories')}.")
    for question in result.get("owner_questions") or []:
        out(f"A question for you: {question}")
    if result.get("outputs"):
        out(f"Transcripts and the summary: {result['outputs']}")
    if result.get("outputs_error"):
        out(str(result["outputs_error"]))
    return EXIT_PROVEN if result.get("strict_rules_proven") is True else EXIT_NOT_PROVEN


def http_sender(url: str, credential: str) -> Send:
    """A `Send` to RAVIS over HTTP, the credential in the header only."""

    def send(
        method: str, path: str, body: dict[str, Any] | None, headers: dict[str, str]
    ) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url.rstrip("/") + path, data=data, method=method,
            headers={**headers, "Authorization": f"Bearer {credential}",
                     "Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 — RAVIS's own
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as refusal:
            try:
                return refusal.code, json.loads(refusal.read() or b"{}")
            except ValueError:
                return refusal.code, {}
        except (urllib.error.URLError, OSError, ValueError):
            return 0, None

    return send
