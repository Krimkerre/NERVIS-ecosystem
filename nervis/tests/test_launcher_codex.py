"""The launcher's part of Codex: its entry in `status --json`, and `run.py codex …`.

Codex, OpenAI's coding agent, runs inside RAVIS (runbook §2.2), and the menu bar app learns about
it only through the launcher (design §3.7, §7.1):

- `status --json` carries Codex's state, what is left of the ChatGPT plan's allowance and the
  running tasks, read from RAVIS's `GET /api/v1/codex` — never from `~/.codex`, and never by
  running a Codex of its own;
- `run.py codex sign-in` and `cancel-sign-in` present the launcher's admin key, while `codex stop`
  and `codex reprove` present the owner's command-line key, `admin.owner_cli`, which starting the
  stack mints and teaches RAVIS and which no service's environment ever holds;
- launching RAVIS tells it where Homebrew keeps Codex and which folders Codex tasks may use.

**RAVIS's answers are the contract's own**, loaded from `ravis/tests/fixtures/relay-contract/`, so
a launcher that drifts from the contract fails here — not merely one that drifts from a copy of the
contract written into this file.

**Nothing here reaches RAVIS, Homebrew, a browser, `.run/` or `~/.codex`.** The one function that
calls RAVIS is replaced by a script of answers, every key lives in the test's own directory, and
the doors onto the machine — processes, connections, the browser — fail the test if opened.
"""

from __future__ import annotations

import builtins
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
RUN_PY = REPOSITORY / "tools" / "run.py"
CONTRACT = REPOSITORY / "ravis" / "tests" / "fixtures" / "relay-contract"

# The keys a started stack leaves in `.run/`, with values that are plainly not real ones.
OWNER_KEY = "owner-cli-key-for-tests-only-000000000000"
ADMIN_KEY = "launcher-admin-key-for-tests-only-0000000"
NERVIS_KEY = "nervis-client-key-for-tests-only-00000000"
# The task the contract's examples stop, and its current turn.
SESSION = "as_01J9ZK4T6Q8M2V7R3N5B1C0D"
TURN = "019a1c2e-8c4f-7a21-b5d3-3e6c9b7f2a41"
STOP_ROUTE = f"/api/v1/agent-sessions/{SESSION}/owner-stop"
REPROVE_ROUTE = "/api/v1/codex/reprove"
SIGN_IN_ROUTE = "/api/v1/codex/sign-in"
#: The strictest rule any hop sets for an `Idempotency-Key`: NERVIS's (conventions.json).
IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{16,128}")
#: The three folder settings the launcher gives RAVIS for Codex tasks.
AGENT_SETTINGS = (
    "RAVIS_AGENT_ALLOWED_ROOTS", "RAVIS_AGENT_DENIED_PATHS", "RAVIS_AGENT_PROTECTED_REPOSITORIES",
)


def _contract(name: str) -> dict[str, Any]:
    return json.loads((CONTRACT / name).read_text(encoding="utf-8"))


def _answer(examples: list[dict[str, Any]], name: str) -> tuple[int, Any]:
    """RAVIS's answer in the contract example called `name`, as (status, body)."""
    response = next(example for example in examples if example["name"] == name)["response"]
    return response["status"], response["body"]


def _route_examples(contract: dict[str, Any], method: str, path: str) -> list[dict[str, Any]]:
    """One route's examples, in a contract file that lists its routes (`codex-admin.json`)."""
    route = next(r for r in contract["routes"] if (r["method"], r["path"]) == (method, path))
    examples: list[dict[str, Any]] = route["examples"]
    return examples


class FakeRavis:
    """RAVIS as `_ravis_call` reaches it: answers scripted by (method, path), every call kept.

    Each route answers from its list in order, then keeps giving the last answer — which is how a
    re-test that runs a while and then finishes is scripted. A route with no script answers 404, as
    a RAVIS without that route does. A launcher that kept asking forever fails instead of hanging.
    """

    def __init__(self, answers: dict[tuple[str, str], list[tuple[int, Any]]] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, method: str, path: str, credential: str, body: Any = None, *,
        headers: dict[str, str] | None = None, timeout: float = 10.0,
    ) -> tuple[int, Any]:
        if len(self.calls) >= 500:
            raise AssertionError("the launcher kept asking RAVIS; every wait has to end")
        self.calls.append({
            "method": method, "path": path, "credential": credential, "body": body,
            "headers": dict(headers or {}), "timeout": timeout,
        })
        script = self.answers.get((method, path))
        if not script:
            return 404, {"detail": "Not Found"}
        return script.pop(0) if len(script) > 1 else script[0]


class FakeClock:
    """The `time` module as the re-test's wait uses it: a clock that moves only when slept on."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class Doors:
    """Every attempt to run a program, open a connection or open a browser: refused and recorded.

    Recorded as well as refused, because a refusal alone can be swallowed by a broad `except` on
    its way out, and the attempt would then go unnoticed.
    """

    def __init__(self) -> None:
        self.attempts: list[str] = []

    def refuse(self, what: str) -> Callable[..., Any]:
        def refused(*_args: object, **_kwargs: object) -> Any:
            self.attempts.append(what)
            raise AssertionError(f"the launcher tried to {what} during a test")

        return refused


def _launcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ravis: FakeRavis | None = None,
) -> tuple[ModuleType, Doors]:
    """A fresh copy of `run.py`, its keys in `tmp_path` and every door onto the machine shut."""
    spec = importlib.util.spec_from_file_location("launcher_codex_under_test", RUN_PY)
    assert spec is not None and spec.loader is not None
    run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run)
    keys = tmp_path / "run"
    keys.mkdir(exist_ok=True)
    for name, path in (
        ("RUN", keys), ("PIDFILE", keys / "services.json"),
        ("MENU_SESSIONS", keys / "menubar-sessions.json"),
        ("RAVIS_ADMIN_TOKEN", keys / "ravis-admin.token"),
        ("RAVIS_OWNER_TOKEN", keys / "ravis-owner.token"),
    ):
        monkeypatch.setattr(run, name, path)
    doors = Doors()
    monkeypatch.setattr(run.subprocess, "Popen", doors.refuse("start a real process"))
    monkeypatch.setattr(run.subprocess, "run", doors.refuse("run a real command"))
    monkeypatch.setattr(run.os, "system", doors.refuse("run a real command"))
    monkeypatch.setattr(run.urllib.request, "urlopen", doors.refuse("open a real connection"))
    monkeypatch.setattr(run, "webbrowser", SimpleNamespace(open=doors.refuse("open a browser")))
    monkeypatch.setattr(run, "_ravis_call", ravis or FakeRavis())
    return run, doors


def _hold_keys(run: ModuleType) -> None:
    """The keys a started stack leaves in `.run/`, with this file's test values."""
    run.RAVIS_OWNER_TOKEN.write_text(OWNER_KEY + "\n", encoding="utf-8")
    run.RAVIS_ADMIN_TOKEN.write_text(ADMIN_KEY + "\n", encoding="utf-8")
    (run.RUN / "nervis-ravis.token").write_text(NERVIS_KEY + "\n", encoding="utf-8")


def _codex(
    run: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    *arguments: str,
) -> tuple[int, dict[str, Any], str]:
    """`run.py codex …` as the menu bar app runs it: (exit code, its JSON line, all it printed)."""
    monkeypatch.setattr(run.sys, "argv", ["run.py", "codex", *arguments])
    code = run._run_codex()
    printed = capsys.readouterr()
    return code, json.loads(printed.out), printed.out + printed.err


def _stack(monkeypatch: pytest.MonkeyPatch, run: ModuleType, *, ravis_answering: bool) -> None:
    """SIRVIS, RAVIS and NERVIS, answering as the test needs, and nothing else of the machine
    read: no PID file, machine figures, notifications or Bridges."""
    monkeypatch.setattr(run, "_services", lambda: [
        ("SIRVIS", [], "sirvis serve", {}, "http://127.0.0.1:8721/v1/status"),
        ("RAVIS", [], "ravis serve", {}, "http://127.0.0.1:8731/v1/models"),
        ("NERVIS", [], "nervis serve", {}, "http://127.0.0.1:8790/api/v1/health"),
    ])
    monkeypatch.setattr(run, "EXTERNAL", [])
    monkeypatch.setattr(
        run, "responds", lambda url, _timeout=1.5: ravis_answering or ":8731" not in url
    )
    monkeypatch.setattr(run, "_recorded", lambda: {})
    monkeypatch.setattr(run, "_system_reading", lambda: None)
    monkeypatch.setattr(run, "_unread_notifications", lambda: 0)
    monkeypatch.setattr(run, "_clarvis_bridges", lambda: 0)


def _service_environments(
    monkeypatch: pytest.MonkeyPatch, run: ModuleType, doors: Doors,
) -> dict[str, dict[str, str]]:
    """Every service's environment, from the real `_services()` — which `status --json` builds too.

    Its doors out are replaced: the credentials minted for NERVIS, SIRVIS and Clarvis, the programs
    looked up, and the credential store the default upstreams would be read from. The owner's key
    and Homebrew are left alone on purpose: nothing building the table may reach for either.
    """
    monkeypatch.setattr(run.subprocess, "run", doors.refuse("run a real command"))
    for minted in ("nervis_ravis_credential", "benchmark_token", "admin_token",
                   "ravis_admin_credential", "clarvis_ravis_credential"):
        monkeypatch.setattr(run, minted, lambda minted=minted: f"({minted})")
    monkeypatch.setenv("RAVIS_UPSTREAMS", "[]")
    for declared in ("RAVIS_CODEX_EXECUTABLE", *AGENT_SETTINGS):
        monkeypatch.delenv(declared, raising=False)
    monkeypatch.setattr(run, "_default_upstreams", doors.refuse("read the credential store"))
    monkeypatch.setattr(run, "ollama_binary", lambda: "/opt/homebrew/bin/ollama")
    monkeypatch.setattr(run, "code_server_binary", lambda: "/opt/homebrew/bin/code-server")
    monkeypatch.setattr(run, "code_server_settings", lambda: ([], 8080, "your own config"))
    run.ROOT.mkdir(parents=True, exist_ok=True)
    environments = {name: environment for name, _, _, environment, _ in run._services()}
    assert doors.attempts == []
    return environments


def _record_opens(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the path of every file opened from here on, through each way Python opens one."""
    opened: list[str] = []
    real_open, real_io_open, real_os_open = builtins.open, io.open, os.open

    def named(file: object) -> str:
        return os.fsdecode(file) if isinstance(file, (str, bytes, os.PathLike)) else repr(file)

    def open_file(file: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(named(file))
        return real_open(file, *args, **kwargs)

    def io_open_file(file: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(named(file))
        return real_io_open(file, *args, **kwargs)

    def os_open_file(path: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(named(path))
        return real_os_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", open_file)
    monkeypatch.setattr(io, "open", io_open_file)
    monkeypatch.setattr(os, "open", os_open_file)
    return opened


# ── Codex in `status --json` ──────────────────────────────────────────────────


def test_status_carries_codex_as_ravis_reports_it_trimmed_to_what_the_menu_draws(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The Codex line's state, allowance windows and tasks, from a named read of RAVIS.

    Named, because RAVIS gives each task's id and turn only to a caller it can name, and a Stop has
    to send both back. Trimmed, because the menu shows nothing of the runtime, of the account beyond
    its plan, or of the models — and what it is not given it cannot show by mistake.
    """
    state = _contract("codex-state.json")
    ravis = FakeRavis({("GET", "/api/v1/codex"): [
        _answer(state["examples"], "signed in, three projects busy, read by a named caller"),
    ]})
    run, doors = _launcher(monkeypatch, tmp_path, ravis)
    _hold_keys(run)
    _stack(monkeypatch, run, ravis_answering=True)

    codex = run.status_report()["codex"]

    assert codex == {
        "state": "signed_in",
        "reason": "Codex is signed in with a ChatGPT Plus plan.",
        "plan": "plus",
        "signed_in": True,
        "usage_known": True,
        "stale": False,
        "windows": [
            {"label": "5-hour window", "remaining_percent": 62,
             "resets_at": "2026-09-13T04:30:00Z"},
            {"label": "weekly window", "remaining_percent": 80,
             "resets_at": "2026-09-17T09:00:00Z"},
        ],
        "runs": [
            {"id": SESSION, "turn_id": TURN, "project": "add-utc-demo", "state": "waiting_on_you",
             "since": "2026-09-13T01:12:00Z", "age_minutes": 42, "waiting_minutes": 12,
             "attached_windows": 0, "model": "gpt-6-astra", "effort": "medium", "reopening": None},
            # Reconnecting: RAVIS is reopening its Codex conversation so a newly allowed site
            # reaches it. The menu says only that, so the sites themselves aren't carried.
            {"id": "as_01J9ZK7W1X2Y3Z4A5B6C7D8E", "turn_id": "019a1c30-2e3f-7d4c-b5a6-7f8e9d0c1b23",
             "project": "weather-cli", "state": "running", "since": "2026-09-13T01:40:00Z",
             "age_minutes": 14, "waiting_minutes": None, "attached_windows": 1,
             "model": "gpt-6-astra", "effort": None,
             "reopening": {"since": "2026-09-13T01:52:00Z"}},
            # A Clarvis-engine run holds a project but is no Codex task: there is nothing to stop.
            {"id": None, "turn_id": None, "project": "notes-app", "state": "clarvis_engine",
             "since": "2026-09-13T01:50:00Z", "age_minutes": 4, "waiting_minutes": None,
             "attached_windows": 1, "model": None, "effort": None, "reopening": None},
        ],
        # The build RAVIS runs, for the menu's re-test item: no sha256, signature or schema.
        "runtime": {"version": "0.154.0", "verdict": "tested", "strict_rules": "proven"},
        "sign_in_waiting": False,
        # The line opens the Codex card, which is on RAVIS's dashboard screen.
        "address": run.DASHBOARD + "#/ravis/Dashboard",
    }
    [asked] = ravis.calls
    assert (asked["method"], asked["path"]) == ("GET", "/api/v1/codex")
    assert asked["credential"] == NERVIS_KEY
    # RAVIS answers from a snapshot in about 50 ms; a stuck RAVIS must not hold the menu up.
    assert asked["timeout"] <= 1.5
    assert doors.attempts == []


def test_an_allowance_ravis_does_not_know_is_never_drawn_as_a_figure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Unknown usage carries no window at all — even beside windows that should not be there.

    RAVIS's rule is that unknown is never 0 (design §3.3). A "0% left" in the menu would tell the
    owner the plan is used up when nobody knows, so the launcher holds the same rule on its side.
    """
    state = _contract("codex-state.json")
    status, unknown = _answer(
        state["examples"], "signed in, usage unknown: no percentages, never zero"
    )
    contradicted = json.loads(json.dumps(unknown))
    contradicted["usage"]["windows"] = [
        {"id": "primary", "label": "5-hour window", "duration_minutes": 300, "used_percent": 100,
         "remaining_percent": 0, "resets_at": None},
    ]
    ravis = FakeRavis({("GET", "/api/v1/codex"): [(status, unknown), (status, contradicted)]})
    run, _ = _launcher(monkeypatch, tmp_path, ravis)
    _hold_keys(run)

    for _ in range(2):
        codex = run._codex_reading()
        assert codex is not None and codex["state"] == "signed_in"
        assert codex["usage_known"] is False
        assert codex["windows"] == []
    assert len(ravis.calls) == 2


@pytest.mark.parametrize(("example", "signed_in"), [
    ("signed in, three projects busy, read by a named caller", True),
    ("signed out", False),
])
def test_signed_in_says_whether_an_account_is_signed_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, example: str, signed_in: bool,
) -> None:
    """The menu offers Sign in to Codex… only where RAVIS would start one, with nobody signed in.
    Codex paused for re-testing can be either, so the state word alone can't tell the menu."""
    state = _contract("codex-state.json")
    ravis = FakeRavis({("GET", "/api/v1/codex"): [_answer(state["examples"], example)]})
    run, _ = _launcher(monkeypatch, tmp_path, ravis)
    _hold_keys(run)

    codex = run._codex_reading()

    assert codex is not None and codex["signed_in"] is signed_in


def test_what_ravis_did_not_send_is_none_and_nothing_past_the_listed_fields_is_printed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A RAVIS before 0.25.1 sends no task model, effort or reopening, and one may send no runtime:
    each is None, never a guess the menu would draw. And only the listed fields are printed — not
    the sites a reopening is for, its group, the build's sha256 or its process — because the menu
    logs what it reads, and what it isn't given it can't show by mistake.
    """
    state = _contract("codex-state.json")
    status, reading = _answer(
        state["examples"], "signed in, three projects busy, read by a named caller"
    )
    older = json.loads(json.dumps(reading))
    for row in older["runs"]:
        for field in ("model", "effort", "reopening"):
            row.pop(field, None)
    del older["runtime"]
    ravis = FakeRavis({("GET", "/api/v1/codex"): [(status, older), (status, reading)]})
    run, _ = _launcher(monkeypatch, tmp_path, ravis)
    _hold_keys(run)

    before = run._codex_reading()
    assert before is not None and before["runtime"] is None
    assert [(row["model"], row["effort"], row["reopening"]) for row in before["runs"]] == [
        (None, None, None)
    ] * 3

    printed = json.dumps(run._codex_reading())
    reopening = next(row["reopening"] for row in reading["runs"] if row["reopening"])
    assert reopening["hosts"] and reopening["group_id"]
    for kept_back in (*reopening["hosts"], reopening["group_id"],
                      reading["runtime"]["installed_sha256"], "active_turns"):
        assert kept_back not in printed


@pytest.mark.parametrize(("answer", "said"), [
    ((0, None), "RAVIS is not answering"),
    ((500, {"error": {"code": "INTERNAL", "message": "boom"}}), "HTTP 500"),
    ((429, {"error": {"code": "RATE_LIMITED", "message": "slow down"}}), "HTTP 429"),
    ((200, ["not", "a", "reading"]), "HTTP 200"),
    ((200, {"reason": "a reading with no state word"}), "HTTP 200"),
])
def test_codex_is_not_known_when_ravis_cannot_say(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, answer: tuple[int, Any], said: str,
) -> None:
    """RAVIS unreachable, stuck, or answering with something else: not known, and nothing claimed.

    `runs` is None rather than an empty list: RAVIS being unreachable says nothing about whether
    Codex is working, and "no tasks" would. `windows` is empty with `usage_known` false, the shape
    RAVIS itself gives an allowance it does not know.
    """
    run, _ = _launcher(monkeypatch, tmp_path, FakeRavis({("GET", "/api/v1/codex"): [answer]}))
    _hold_keys(run)

    codex = run._codex_reading()

    assert codex is not None
    assert said in codex.pop("reason")
    assert codex == {
        "state": "ravis_not_answering", "plan": None, "signed_in": None, "usage_known": False,
        "stale": None, "windows": [], "runs": None, "runtime": None, "sign_in_waiting": None,
        "address": run.CODEX_CARD,
    }


def test_no_codex_entry_where_ravis_serves_no_codex_state_and_no_read_while_ravis_is_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A RAVIS without Codex's state answers 404: no Codex entry, rather than one about nothing.

    That is every RAVIS until M29's second increment. And a RAVIS whose probe already failed is not
    asked at all: its entry is simply not known, without spending the read's timeout on every
    refresh of the menu.
    """
    ravis = FakeRavis()  # nothing scripted: every read gets the 404 of a RAVIS without the route
    run, _ = _launcher(monkeypatch, tmp_path, ravis)
    _hold_keys(run)
    _stack(monkeypatch, run, ravis_answering=True)
    assert run.status_report()["codex"] is None
    assert [call["path"] for call in ravis.calls] == ["/api/v1/codex"]

    ravis.calls.clear()
    _stack(monkeypatch, run, ravis_answering=False)
    codex = run.status_report()["codex"]
    assert codex["state"] == "ravis_not_answering" and codex["reason"] == run.CODEX_RAVIS_DOWN
    assert codex["runs"] is None
    assert ravis.calls == []


def test_reading_codex_state_runs_no_program_and_opens_nothing_in_a_codex_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The owner's rule: Codex's state comes from RAVIS, never from a Codex folder or a Codex run.

    `~/.codex` is the ChatGPT app's own Codex, and `~/.local/share/ravis-codex` is RAVIS's. A menu
    that read either could show the wrong account's allowance, and one that ran Codex could spend
    the plan just to draw a line. Both folders exist here, in a stand-in home, with a sign-in file
    in each, and every file opened while the menu's status is built is recorded.
    """
    home = tmp_path / "home"
    codex_homes = [home / ".codex", home / ".local" / "share" / "ravis-codex"]
    for folder in codex_homes:
        folder.mkdir(parents=True)
        (folder / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    state = _contract("codex-state.json")
    ravis = FakeRavis({("GET", "/api/v1/codex"): [
        _answer(state["examples"], "signed in, three projects busy, read by a named caller"),
    ]})
    run, doors = _launcher(monkeypatch, tmp_path, ravis)
    _hold_keys(run)
    _stack(monkeypatch, run, ravis_answering=True)
    opened = _record_opens(monkeypatch)

    assert run.status_report()["codex"]["state"] == "signed_in"

    homes = [folder.resolve() for folder in codex_homes]
    touched = [path for path in opened if any(Path(path).resolve().is_relative_to(folder)
                                              for folder in homes)]
    assert touched == []
    assert [call["path"] for call in ravis.calls] == ["/api/v1/codex"]
    assert doors.attempts == []


# ── run.py codex stop ─────────────────────────────────────────────────────────


def test_codex_stop_sends_the_owner_key_the_menu_bar_and_the_confirmation_and_prints_no_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """What the menu bar's Stop this task… sends — exactly the contract's menu bar example.

    The owner's command-line key and never NERVIS's admin key, so RAVIS counts and audits the menu
    bar apart from the dashboard; `source: "menu_bar"`; the folder and turn the menu read, which
    RAVIS checks so that a stale menu stops nothing; and a fresh `Idempotency-Key` each time.
    """
    stop = _contract("owner-stop.json")
    menu_bar = next(example for example in stop["examples"] if example["name"] == "the menu bar")
    assert menu_bar["request"]["caller"] == "admin.owner_cli"
    ravis = FakeRavis({("POST", STOP_ROUTE): [_answer(stop["examples"], "the menu bar")]})
    run, doors = _launcher(monkeypatch, tmp_path, ravis)
    _hold_keys(run)

    arguments = ("stop", SESSION, "--project", "add-utc-demo", "--turn", TURN)
    first = _codex(run, monkeypatch, capsys, *arguments)
    second = _codex(run, monkeypatch, capsys, *arguments)

    assert first[:2] == second[:2] == (0, {"state": "stopping"})
    assert [(call["method"], call["path"]) for call in ravis.calls] == [("POST", STOP_ROUTE)] * 2
    for call in ravis.calls:
        assert call["credential"] == OWNER_KEY
        assert call["body"] == menu_bar["request"]["body"]
        assert IDEMPOTENCY_KEY.fullmatch(call["headers"].get("Idempotency-Key", ""))
    sent_keys = {call["headers"]["Idempotency-Key"] for call in ravis.calls}
    assert len(sent_keys) == 2
    for secret in (OWNER_KEY, ADMIN_KEY, *sent_keys):
        assert secret not in first[2] + second[2]
    assert doors.attempts == []


@pytest.mark.parametrize(("example", "exit_code"), [
    ("the menu bar", 0),
    ("a stale menu: the turn moved on", 8),
    ("nothing running", 9),
    ("an unknown id", 9),
    ("the eleventh call in a minute from one application", 10),
    ("NERVIS's client credential", 10),
])
def test_codex_stop_exits_as_the_contract_names_each_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    example: str, exit_code: int,
) -> None:
    """0 stopping, 8 the task changed, 9 not found or nothing running, 10 refused.

    The menu bar app acts on the number — on 8 it redraws the menu and says the task changed — so
    each of RAVIS's answers in the contract is held to its code, and none prints the key.
    """
    stop = _contract("owner-stop.json")
    ravis = FakeRavis({("POST", STOP_ROUTE): [_answer(stop["examples"], example)]})
    run, _ = _launcher(monkeypatch, tmp_path, ravis)
    _hold_keys(run)

    code, printed, output = _codex(
        run, monkeypatch, capsys, "stop", SESSION, "--project", "add-utc-demo", "--turn", TURN
    )

    assert code == exit_code
    assert ("error" in printed) is (exit_code != 0)
    assert OWNER_KEY not in output


def test_codex_stop_asks_nothing_when_it_cannot_ask_safely_and_says_when_ravis_is_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """6 with RAVIS down. A malformed id, a blank confirmation, or no owner key: RAVIS is not asked.

    The id goes into RAVIS's address, so only RAVIS's own shape of id is sent. And with no key on
    disk none is made: RAVIS learns its keys as the stack starts, so a key made now would be
    refused, and would then be the one the next start teaches.
    """
    stop = _contract("owner-stop.json")
    assert stop["launcher"]["exit_codes"] == {
        "0": "stopping", "6": "RAVIS down", "8": "confirmation mismatch",
        "9": "not found or nothing running", "10": "refused",
    }
    ravis = FakeRavis({("POST", STOP_ROUTE): [(0, None)]})
    run, _ = _launcher(monkeypatch, tmp_path, ravis)
    _hold_keys(run)
    confirmation = ("--project", "add-utc-demo", "--turn", TURN)

    assert _codex(run, monkeypatch, capsys, "stop", SESSION, *confirmation)[0] == 6
    assert len(ravis.calls) == 1

    ravis.calls.clear()
    for malformed in ("as_short", "../../codex/reprove", f"{SESSION}/../x", f"{SESSION}\n"):
        assert _codex(run, monkeypatch, capsys, "stop", malformed, *confirmation)[0] == 2
    blank = ("stop", SESSION, "--project", "", "--turn", TURN)
    assert _codex(run, monkeypatch, capsys, *blank)[0] == 2
    assert ravis.calls == []

    run.RAVIS_OWNER_TOKEN.unlink()
    code, printed, _ = _codex(run, monkeypatch, capsys, "stop", SESSION, *confirmation)
    assert code == 10 and "error" in printed
    assert ravis.calls == []
    assert not run.RAVIS_OWNER_TOKEN.exists()


# ── run.py codex reprove ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("word", "exit_code"), [("proven", 0), ("failed", 11), ("inconclusive", 12)]
)
def test_codex_reprove_starts_with_the_owner_key_and_waits_for_the_result_word(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    word: str, exit_code: int,
) -> None:
    """Started once with the owner's key and a fresh `Idempotency-Key`, then asked every 5 s.

    The re-test spends a turn of the owner's allowance, so RAVIS lets only the owner's command-line
    key start it. RAVIS answers at once and runs the test, and the launcher prints only the result
    word, which the menu turns into a sentence.
    """
    admin = _contract("codex-admin.json")
    assert set(admin["reprove_rules"]["results"]) == {"proven", "failed", "inconclusive"}
    started = _answer(_route_examples(admin, "POST", REPROVE_ROUTE), "the owner, from the menu bar")
    readings = _route_examples(admin, "GET", REPROVE_ROUTE)
    running = _answer(readings, "running")
    status, finished = _answer(readings, "finished: only state and the result word are fixed")
    ended = (status, {"reproof": {**finished["reproof"], "result": word}})
    ravis = FakeRavis({
        ("POST", REPROVE_ROUTE): [started], ("GET", REPROVE_ROUTE): [running, running, ended],
    })
    run, doors = _launcher(monkeypatch, tmp_path, ravis)
    _hold_keys(run)
    clock = FakeClock()
    monkeypatch.setattr(run, "time", clock)

    code, printed, output = _codex(run, monkeypatch, capsys, "reprove")

    assert (code, printed) == (exit_code, {"result": word})
    start, *asked = ravis.calls
    assert (start["method"], start["path"], start["credential"]) == (
        "POST", REPROVE_ROUTE, OWNER_KEY)
    sent_key = start["headers"].get("Idempotency-Key", "")
    assert IDEMPOTENCY_KEY.fullmatch(sent_key)
    assert [(call["method"], call["credential"]) for call in asked] == [("GET", OWNER_KEY)] * 3
    assert clock.slept == [5.0, 5.0, 5.0]
    assert OWNER_KEY not in output and sent_key not in output
    assert doors.attempts == []


def test_codex_reprove_exits_for_a_live_task_a_refusal_ravis_down_and_a_test_that_never_ends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """7 while a task is live, 10 for a key RAVIS refuses, 6 with RAVIS down, and a wait that ends.

    RAVIS caps the re-test at five minutes, so the launcher stops asking after six whatever RAVIS
    says — and then says it does not know how the test ended, rather than guessing a result.
    """
    admin = _contract("codex-admin.json")
    assert admin["launcher"]["codex reprove"]["exit_codes"] == {
        "0": "proven", "6": "RAVIS down", "7": "a task is running", "10": "refused",
        "11": "failed", "12": "inconclusive",
    }
    starts = _route_examples(admin, "POST", REPROVE_ROUTE)
    for answer, exit_code in ((_answer(starts, "a Codex task is live"), 7),
                              (_answer(starts, "NERVIS's admin.launcher"), 10),
                              ((0, None), 6)):
        ravis = FakeRavis({("POST", REPROVE_ROUTE): [answer]})
        run, _ = _launcher(monkeypatch, tmp_path, ravis)
        _hold_keys(run)
        code, printed, output = _codex(run, monkeypatch, capsys, "reprove")
        assert (code, "error" in printed) == (exit_code, True)
        assert len(ravis.calls) == 1 and OWNER_KEY not in output

    running = _answer(_route_examples(admin, "GET", REPROVE_ROUTE), "running")
    started = _answer(starts, "the owner, from the menu bar")
    ravis = FakeRavis({("POST", REPROVE_ROUTE): [started], ("GET", REPROVE_ROUTE): [running]})
    run, _ = _launcher(monkeypatch, tmp_path, ravis)
    _hold_keys(run)
    clock = FakeClock()
    monkeypatch.setattr(run, "time", clock)
    code, printed, _ = _codex(run, monkeypatch, capsys, "reprove")
    assert code == 1 and printed["result"] is None and "error" in printed
    assert clock.now == 360.0
    assert len(ravis.calls) == 1 + 72


# ── run.py codex sign-in and cancel-sign-in ───────────────────────────────────


def test_codex_sign_in_opens_the_page_with_the_admin_key_and_never_prints_its_address(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """The launcher's admin key, as the dashboard's Sign in uses; the page opened, never printed.

    Until the sign-in finishes or expires, the page RAVIS names is a live way into it, and what the
    launcher prints ends up in the menu bar app's log.
    """
    admin = _contract("codex-admin.json")
    started = _answer(_route_examples(admin, "POST", SIGN_IN_ROUTE), "started")
    page = started[1]["sign_in"]["auth_url"]
    ravis = FakeRavis({("POST", SIGN_IN_ROUTE): [started]})
    run, doors = _launcher(monkeypatch, tmp_path, ravis)
    _hold_keys(run)
    opened: list[str] = []
    monkeypatch.setattr(
        run, "webbrowser", SimpleNamespace(open=lambda url: opened.append(url) or True)
    )

    code, printed, output = _codex(run, monkeypatch, capsys, "sign-in")

    assert (code, printed) == (0, {"state": "waiting_for_browser"})
    assert opened == [page]
    [asked] = ravis.calls
    assert (asked["credential"], asked["body"]) == (ADMIN_KEY, {"method": "browser"})
    assert page not in output and "auth.openai.com" not in output
    assert ADMIN_KEY not in output and OWNER_KEY not in output
    assert doors.attempts == []


@pytest.mark.parametrize(("example", "exit_code"), [
    ("already signed in", 3),
    ("not installed, not available or restarting", 4),
    ("an untested version", 4),
    ("both sign-in ports held", 5),
    ("a Codex turn is active", 7),
])
def test_codex_sign_in_exits_as_the_contract_names_each_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    example: str, exit_code: int,
) -> None:
    """3 already signed in, 4 not installed or untested, 5 port busy, 7 a turn active — no page."""
    admin = _contract("codex-admin.json")
    assert admin["launcher"]["codex sign-in"]["exit_codes"] == {
        "0": "started", "3": "already signed in", "4": "not installed or untested",
        "5": "port busy", "6": "RAVIS down", "7": "turn active",
    }
    answer = _answer(_route_examples(admin, "POST", SIGN_IN_ROUTE), example)
    ravis = FakeRavis({("POST", SIGN_IN_ROUTE): [answer]})
    run, doors = _launcher(monkeypatch, tmp_path, ravis)
    _hold_keys(run)

    code, printed, _ = _codex(run, monkeypatch, capsys, "sign-in")

    assert code == exit_code and "error" in printed
    assert doors.attempts == []  # no browser opened for a refusal


def test_codex_sign_in_opens_only_a_secure_page_and_cancel_uses_the_admin_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A page that is not `https` is not opened; RAVIS down exits 6; cancelling presents the admin
    key and passes RAVIS's answer on."""
    elsewhere = (202, {"sign_in": {"state": "waiting_for_browser", "auth_url": "file:///etc/hosts"}})
    run, doors = _launcher(monkeypatch, tmp_path, FakeRavis({("POST", SIGN_IN_ROUTE): [elsewhere]}))
    _hold_keys(run)
    code, printed, _ = _codex(run, monkeypatch, capsys, "sign-in")
    assert code == 1 and printed["state"] == "waiting_for_browser" and "error" in printed
    assert doors.attempts == []

    monkeypatch.setattr(run, "_ravis_call", FakeRavis({("POST", SIGN_IN_ROUTE): [(0, None)]}))
    assert _codex(run, monkeypatch, capsys, "sign-in")[0] == 6

    admin = _contract("codex-admin.json")
    cancelled = _answer(_route_examples(admin, "DELETE", SIGN_IN_ROUTE), "cancelled")
    ravis = FakeRavis({("DELETE", SIGN_IN_ROUTE): [cancelled]})
    monkeypatch.setattr(run, "_ravis_call", ravis)
    assert _codex(run, monkeypatch, capsys, "cancel-sign-in")[:2] == (0, {"cancelled": True})
    assert [(call["method"], call["credential"]) for call in ravis.calls] == [("DELETE", ADMIN_KEY)]
    monkeypatch.setattr(run, "_ravis_call", FakeRavis({("DELETE", SIGN_IN_ROUTE): [(0, None)]}))
    assert _codex(run, monkeypatch, capsys, "cancel-sign-in")[0] == 6


def test_a_refusal_without_a_code_is_still_a_refusal_and_a_missing_route_is_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A key RAVIS does not know — 401 with no code — exits 10, as any refusal does.

    And a RAVIS without the route — 404 with no code, which is every RAVIS before M29's second
    increment — exits 1 and says so, rather than telling the menu the task was not found.
    """
    run, _ = _launcher(monkeypatch, tmp_path, FakeRavis({
        ("POST", STOP_ROUTE): [(401, {"detail": "Not authenticated"})],
        ("POST", REPROVE_ROUTE): [(401, None)],
    }))
    _hold_keys(run)
    confirmation = ("--project", "add-utc-demo", "--turn", TURN)
    assert _codex(run, monkeypatch, capsys, "stop", SESSION, *confirmation)[0] == 10
    assert _codex(run, monkeypatch, capsys, "reprove")[0] == 10

    monkeypatch.setattr(run, "_ravis_call", FakeRavis())  # no route anywhere: 404, and no code
    every_command = (
        ("stop", SESSION, *confirmation), ("reprove",), ("sign-in",), ("cancel-sign-in",),
    )
    for arguments in every_command:
        code, printed, _ = _codex(run, monkeypatch, capsys, *arguments)
        assert (code, printed) == (1, {"error": "This RAVIS does not offer that yet."}), arguments


# ── What starting the stack gives RAVIS ───────────────────────────────────────


def test_the_owner_key_is_taught_to_ravis_on_stdin_and_enters_no_service_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Minted once at 0600, taught to RAVIS as `admin.owner_cli`, and given to no service at all.

    NERVIS above all: RAVIS lets this key start the file-rules re-test and refuses NERVIS's own, so
    the dashboard can never start Codex work. The key is not replaced while the environments are
    built, so an environment that reached for it would find the minted value, and fail here.
    """
    run, doors = _launcher(monkeypatch, tmp_path)
    root = tmp_path / "coding" / "NERVIS-ecosystem"
    ravis_cli = root / "ravis" / ".venv" / "bin" / "ravis"
    ravis_cli.parent.mkdir(parents=True)
    ravis_cli.write_text("", encoding="utf-8")
    monkeypatch.setattr(run, "ROOT", root)
    taught: list[tuple[list[str], str]] = []

    def ravis_credential(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        taught.append((command, options["input"]))
        return subprocess.CompletedProcess(command, 0, "stored admin.owner_cli (file)\n", "")

    monkeypatch.setattr(run.subprocess, "run", ravis_credential)
    assert run.teach_ravis_the_owner_credential() == "stored"
    assert run.teach_ravis_the_owner_credential() == "stored"

    minted = run.RAVIS_OWNER_TOKEN.read_text(encoding="utf-8").strip()
    assert len(minted) >= 32
    assert stat.S_IMODE(run.RAVIS_OWNER_TOKEN.stat().st_mode) == 0o600
    # The same key both times, on stdin — never in the command line a process listing shows.
    assert taught == [([str(ravis_cli), "credential", "admin.owner_cli"], minted)] * 2
    assert minted != run.ravis_admin_credential()

    environments = _service_environments(monkeypatch, run, doors)
    assert set(environments) == {"SIRVIS", "RAVIS", "NERVIS", "Ollama", "code-server"}
    for name, environment in environments.items():
        assert [key for key, value in environment.items() if minted in value] == [], name
    # NERVIS keeps the launcher's admin key for its dashboard controls, as before.
    assert environments["NERVIS"]["NERVIS_RAVIS_ADMIN_CREDENTIAL"] == "(ravis_admin_credential)"


def test_start_teaches_ravis_the_owner_key_before_it_looks_at_the_stack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Both admin keys are taught first, before `start` even asks what is running.

    RAVIS reads its credential store as it starts, so a key taught after RAVIS is up is one it
    does not hold until its next start. A stack that is already running is where that would go
    unseen: `start` returns early there, and the key must have been taught all the same.
    """
    run, _ = _launcher(monkeypatch, tmp_path)
    order: list[str] = []
    monkeypatch.setattr(run, "ensure_venv", lambda: None)
    monkeypatch.setattr(run, "FILE_MOUNTS", [])
    monkeypatch.setattr(run, "configured_share", lambda: "")
    monkeypatch.setattr(
        run, "teach_ravis_the_admin_credential", lambda: order.append("admin.launcher") or "stored"
    )
    monkeypatch.setattr(
        run, "teach_ravis_the_owner_credential", lambda: order.append("admin.owner_cli") or "stored"
    )
    monkeypatch.setattr(
        run, "status", lambda **_options: order.append("status") or {"RAVIS": True}
    )
    monkeypatch.setattr(run, "teach_ravis_the_credential", lambda: "(taught)")
    monkeypatch.setattr(run, "dashboard_url", lambda: run.DASHBOARD)
    monkeypatch.setattr(run, "webbrowser", SimpleNamespace(open=lambda _url: True))

    assert run.start() == 0
    assert order == ["admin.launcher", "admin.owner_cli", "status"]


def test_ravis_is_told_homebrews_codex_link_only_as_it_is_launched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """`RAVIS_CODEX_EXECUTABLE` is Homebrew's link, asked for as RAVIS launches and never followed.

    Building the table for `status --json` asks Homebrew nothing. The link is what Homebrew
    re-points on every upgrade, so an upgrade reads in RAVIS as a new build to re-test, where the
    versioned copy it leads to would read as Codex gone once that version is removed (owner
    decision D4). An executable the owner named wins, and then Homebrew is not asked.
    """
    run, doors = _launcher(monkeypatch, tmp_path)
    monkeypatch.setattr(run, "ROOT", tmp_path / "coding" / "NERVIS-ecosystem")
    # A Homebrew to be found from the first line, so a table that asked it would be caught here on
    # any machine, not only on one where Homebrew is installed.
    monkeypatch.setattr(
        run.shutil, "which", lambda program: "/opt/homebrew/bin/brew" if program == "brew" else None
    )
    assert "RAVIS_CODEX_EXECUTABLE" not in _service_environments(monkeypatch, run, doors)["RAVIS"]

    prefix = tmp_path / "homebrew"
    build = prefix / "Caskroom" / "codex" / "0.154.0" / "codex-aarch64-apple-darwin"
    build.parent.mkdir(parents=True)
    build.write_text("", encoding="utf-8")
    (prefix / "bin").mkdir()
    (prefix / "bin" / "codex").symlink_to(build)
    asked: list[list[str]] = []

    def brew(command: list[str], **_options: Any) -> subprocess.CompletedProcess[str]:
        asked.append(command)
        return subprocess.CompletedProcess(command, 0, f"{prefix}\n", "")

    monkeypatch.setattr(run.subprocess, "run", brew)
    health = "http://127.0.0.1:8731/v1/models"
    table = [
        ("SIRVIS", ["sirvis", "serve"], "sirvis serve", {}, "http://127.0.0.1:8721/v1/status"),
        ("RAVIS", ["ravis", "serve"], "ravis serve", {}, health),
        ("NERVIS", ["nervis", "serve"], "nervis serve", {}, "http://127.0.0.1:8790/api/v1/health"),
    ]
    monkeypatch.setattr(run, "_services", lambda: table)
    launched: dict[str, dict[str, str]] = {}

    def spawn(command: list[str], environment: dict[str, str], _log: Path) -> int:
        launched[command[0]] = environment
        return 4000 + len(launched)

    monkeypatch.setattr(run, "_spawn_detached", spawn)

    run._launch({}, {})
    assert launched["ravis"]["RAVIS_CODEX_EXECUTABLE"] == str(prefix / "bin" / "codex")
    assert "RAVIS_CODEX_EXECUTABLE" not in launched["sirvis"] | launched["nervis"]
    assert asked == [["/opt/homebrew/bin/brew", "--prefix"]]

    asked.clear()
    table[1] = ("RAVIS", ["ravis", "serve"], "ravis serve",
                {"RAVIS_CODEX_EXECUTABLE": "/elsewhere/codex"}, health)
    run._launch({}, {})
    assert launched["ravis"]["RAVIS_CODEX_EXECUTABLE"] == "/elsewhere/codex"
    assert asked == []

    # A Homebrew that cannot name its prefix, or none at all: nothing is set, and RAVIS asks and
    # reports for itself.
    table[1] = ("RAVIS", ["ravis", "serve"], "ravis serve", {}, health)
    monkeypatch.setattr(
        run.subprocess, "run",
        lambda command, **_options: subprocess.CompletedProcess(command, 1, "", "Error: stuck"),
    )
    run._launch({}, {})
    assert "RAVIS_CODEX_EXECUTABLE" not in launched["ravis"]
    monkeypatch.setattr(run.shutil, "which", lambda _program: None)
    run._launch({}, {})
    assert "RAVIS_CODEX_EXECUTABLE" not in launched["ravis"]


def test_ravis_is_told_the_codex_folders_as_json_lists_keeping_what_the_owner_added(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Where Codex tasks may work, what they must keep out of, and what they may never touch.

    JSON lists: the form RAVIS reads a list setting in, and the only one that can carry an entry
    that is more than a path. What the owner declared follows the launcher's own entries, whatever
    its shape, so listing a folder never drops the coding folder, `.run`, or the protection of the
    ecosystem's own repositories. Only RAVIS's environment gets them.
    """
    run, doors = _launcher(monkeypatch, tmp_path)
    coding = tmp_path / "coding"
    root = coding / "NERVIS-ecosystem"
    monkeypatch.setattr(run, "ROOT", root)
    ours = {
        "RAVIS_AGENT_ALLOWED_ROOTS": [str(coding)],
        "RAVIS_AGENT_DENIED_PATHS": [str(run.RUN)],
        "RAVIS_AGENT_PROTECTED_REPOSITORIES": [str(root), str(coding / "clarvis")],
    }
    told = run._agent_environment({})
    assert {name: json.loads(value) for name, value in told.items()} == ours

    listed = {"path": str(coding / "clarvis-sandbox"), "allow_protected": True}
    told = run._agent_environment({
        "RAVIS_AGENT_ALLOWED_ROOTS": json.dumps([str(coding), listed]),
        "RAVIS_AGENT_DENIED_PATHS": json.dumps(["/Users/someone/secrets"]),
        "RAVIS_AGENT_PROTECTED_REPOSITORIES": "not a JSON list",
    })
    assert json.loads(told["RAVIS_AGENT_ALLOWED_ROOTS"]) == [str(coding), listed]
    assert json.loads(told["RAVIS_AGENT_DENIED_PATHS"]) == [str(run.RUN), "/Users/someone/secrets"]
    # Not a list: passed on as written, for RAVIS's own check to name, rather than thrown away.
    assert told["RAVIS_AGENT_PROTECTED_REPOSITORIES"] == "not a JSON list"

    environments = _service_environments(monkeypatch, run, doors)
    assert {name: json.loads(environments["RAVIS"][name]) for name in AGENT_SETTINGS} == ours
    for other in ("SIRVIS", "NERVIS", "Ollama", "code-server"):
        assert not set(AGENT_SETTINGS) & set(environments[other]), other
