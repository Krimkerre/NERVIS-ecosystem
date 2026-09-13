"""Calibration: who may start it, what it asks, and what it may write (design §10.4).

Every run here is against `fake_codex_app_server.py` with its calibration half — never a real Codex,
never a real account. The rules held:

- **the route exists only while `RAVIS_CODEX_CALIBRATION=1`**; only `admin.owner_cli` may start or
  read a run; it needs an `Idempotency-Key`, and a replay starts nothing more;
- **a project must be a git project**, two different folders, and never one of the ecosystem's own
  repositories; a run that uses the allowance needs the owner's go-ahead;
- **both projects' lock files are held** through the shared lock rule, and a project another writer
  holds refuses the run before any decoy is made;
- **every scenario's pass path** (one full run) **and fail path** (one fault each, a run of that
  scenario alone), each answer audited;
- **only a full run with every must-pass question passed** writes the profile and marks the build
  proven; a K5 failure writes nothing and sends D2 back to the owner; Codex rejecting the profile's
  syntax fails K5 in Codex's own words;
- **transcripts are redacted**, and everything a run made is gone afterwards;
- **`ravis codex calibrate`** prepares the projects, follows the run, and refuses a protected path.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.codex_rig import (
    ALLOWANCE,
    SIGNED_IN,
    CodexRig,
    call,
    codex_rig,
    confirmed_record,
    eventually,
    reaches,
    state_of,
)

from ravis.api.management import codex_calibration as calibration_routes
from ravis.api.management.codex import require_owner_cli
from ravis.cli import _build_parser
from ravis.codex.calibration.command import run_calibrate
from ravis.codex.calibration.harness import CalibrationTimings, Listed, ScenarioResult, Session
from ravis.codex.calibration.outputs import Redactor, decide
from ravis.codex.calibration.plan import (
    CANDIDATE_PROFILE,
    SCENARIOS,
    CalibrationPlan,
    ProfileFileError,
    Project,
    checked_profile,
)
from ravis.codex.lock_file import Holder, create_lock_file, lock_content
from ravis.codex.lock_rule import own_start
from ravis.codex.routing import InboxItem
from ravis.config import Settings, data_directory

RUNS = "/api/v1/codex/calibration/runs"
SIGNED_IN_SCENARIO = {"account": SIGNED_IN, "rate_limits": ALLOWANCE, "calibration": True}
REPOSITORY = Path(__file__).resolve().parents[2]


def projects(tmp_path: Path, *names: str) -> tuple[Path, ...]:
    """Two git projects in a folder whose name has a space, as the owner's workspace does."""
    folder = tmp_path / "NERVIS workspace" / "nervis-tasks"
    made = []
    for name in names or ("codex-calibration-a", "codex-calibration-b"):
        (folder / name / ".git").mkdir(parents=True)
        made.append(folder / name)
    return tuple(made)


@contextmanager
def calibrating(
    tmp_path: Path, *faults: str, **scenario: Any
) -> Iterator[tuple[CodexRig, TestClient, Path, Path]]:
    """A RAVIS in calibration mode on a pinned, unproven build, signed in; `scenario` adds keys."""
    confirmed_record()
    rig = codex_rig(
        tmp_path,
        scenario={**SIGNED_IN_SCENARIO, "calibration_faults": list(faults), **scenario},
        codex_calibration=True, codex_calibration_output=str(tmp_path / "out"),
    )
    (tmp_path / "slash-tmp").mkdir()
    a, b = projects(tmp_path)
    with TestClient(rig.app) as client:
        reaches(client, "untested_version")
        eventually(lambda: state_of(client)["account"] is not None, what="signed in")
        yield rig, client, a, b


def start(
    client: TestClient, rig: CodexRig, a: Path, b: Path, scenarios: list[str] | None = None,
    *, key: str = "calibration-key-1", caller: str = "admin.owner_cli", **extra: Any,
) -> Any:
    body: dict[str, Any] = {"project_a": str(a), "project_b": str(b), "allowance_go_ahead": True}
    if scenarios is not None:
        body["scenarios"] = scenarios
    return call(client, rig, "POST", RUNS, caller, {**body, **extra},
                headers={"Idempotency-Key": key})


def finished(client: TestClient, rig: CodexRig, seconds: float = 90.0) -> dict[str, Any]:
    def done() -> dict[str, Any] | None:
        view = call(client, rig, "GET", RUNS, "admin.owner_cli").json()["calibration"]
        return view if view and view["state"] == "finished" else None

    return eventually(done, seconds, what="the calibration run's result")  # type: ignore[no-any-return]


def verdicts(view: dict[str, Any]) -> dict[str, str]:
    return {scenario["id"]: scenario["verdict"] for scenario in view["scenarios"]}


def pin(rig: CodexRig) -> dict[str, Any]:
    return json.loads((rig.folder / "pin.json").read_text())  # type: ignore[no-any-return]


def summary(view: dict[str, Any]) -> dict[str, Any]:
    return json.loads((Path(view["result"]["outputs"]) / "summary.json").read_text())  # type: ignore[no-any-return]


# ── Who may start it, and when it exists ────────────────────────────────────


def test_the_route_exists_only_while_calibration_is_switched_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = codex_rig(tmp_path, codex_enabled=False)

    with TestClient(rig.app) as client:
        started = call(client, rig, "POST", RUNS, "admin.owner_cli", {},
                       headers={"Idempotency-Key": "k"})
        read = call(client, rig, "GET", RUNS, "admin.owner_cli")

    assert (started.status_code, read.status_code) == (404, 404)
    monkeypatch.setenv("RAVIS_CODEX_CALIBRATION", "1")
    assert Settings(_env_file=None).codex_calibration is True  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "caller", ["admin.launcher", "client.clarvis", "client.nervis", "anonymous"]
)
def test_only_the_owners_command_line_credential_may_start_or_read_a_run(
    tmp_path: Path, caller: str
) -> None:
    rig = codex_rig(tmp_path, codex_enabled=False, codex_calibration=True)

    with TestClient(rig.app) as client:
        started = call(client, rig, "POST", RUNS, caller, {}, headers={"Idempotency-Key": "k"})
        read = call(client, rig, "GET", RUNS, caller)

    for answer in (started, read):
        assert answer.status_code == 403
        assert answer.json()["error"]["code"] == "REPROOF_NOT_ALLOWED"


def test_every_calibration_route_carries_the_owner_credential_guard() -> None:
    guarded = [
        {dependency.dependency for dependency in route.dependencies}  # type: ignore[attr-defined]
        for route in calibration_routes.router.routes
    ]

    assert len(guarded) == 3
    assert all(require_owner_cli in guards for guards in guarded)


def test_a_run_needs_an_idempotency_key(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, codex_enabled=False, codex_calibration=True)

    with TestClient(rig.app) as client:
        answer = call(client, rig, "POST", RUNS, "admin.owner_cli", {})

    assert (answer.status_code, answer.json()["error"]["code"]) == (428, "IDEMPOTENCY_KEY_REQUIRED")


@pytest.mark.parametrize(
    ("change", "words"),
    [
        ("not_git", "not a git project"),
        ("same", "two different folders"),
        ("protected", "one of the ecosystem's own"),
        ("no_go_ahead", "allowance_go_ahead"),
        ("unknown_scenario", "Unknown scenario ids"),
    ],
)
def test_a_body_that_cant_be_used_is_refused_before_anything_runs(
    tmp_path: Path, change: str, words: str
) -> None:
    # Named as the launcher names them, so the rule holds wherever the suite runs from.
    rig = codex_rig(tmp_path, codex_enabled=False, codex_calibration=True,
                    agent_protected_repositories=[str(REPOSITORY)])
    a, b = projects(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    bodies: dict[str, dict[str, Any]] = {
        "not_git": {"project_a": str(plain), "project_b": str(b), "allowance_go_ahead": True},
        "same": {"project_a": str(a), "project_b": str(a), "allowance_go_ahead": True},
        "protected": {"project_a": str(REPOSITORY), "project_b": str(b),
                      "allowance_go_ahead": True},
        "no_go_ahead": {"project_a": str(a), "project_b": str(b)},
        "unknown_scenario": {"project_a": str(a), "project_b": str(b), "scenarios": ["K99"]},
    }

    with TestClient(rig.app) as client:
        answer = call(client, rig, "POST", RUNS, "admin.owner_cli", bodies[change],
                      headers={"Idempotency-Key": "k"})

    assert answer.status_code == 422, answer.text
    assert words in answer.json()["error"]["message"]


def test_a_replayed_key_gets_the_same_answer_and_starts_nothing_more(tmp_path: Path) -> None:
    with calibrating(tmp_path) as (rig, client, a, b):
        first = start(client, rig, a, b, ["K10"], key="replayed")
        finished(client, rig)
        replayed = start(client, rig, a, b, ["K10"], key="replayed")
        reused = start(client, rig, a, b, ["K5a"], key="replayed")

    assert first.status_code == 202, first.text
    assert (replayed.status_code, replayed.json()) == (202, first.json())
    assert (reused.status_code, reused.json()["error"]["code"]) == (422, "IDEMPOTENCY_KEY_REUSED")
    assert len(rig.published("ravis.codex.calibration_started")) == 1


def test_a_project_another_writer_holds_refuses_the_run_before_any_decoy(tmp_path: Path) -> None:
    with calibrating(tmp_path) as (rig, client, a, b):
        start_time = own_start()
        assert start_time is not None
        window = Holder(kind="clarvis_run", session_id=None, pid=os.getpid(),
                        pid_start=start_time, window_id="win-code-server-7f3a2b",
                        host="code-server")
        held = create_lock_file(a / ".git" / "clarvis-engine.lock",
                                lock_content(window, task_id="t", engine="clarvis")).held
        assert held is not None
        answer = start(client, rig, a, b, ["K10"])
        held.release()

    assert answer.status_code == 409, answer.text
    assert "another Clarvis window is working on it" in answer.json()["error"]["message"]
    assert not (b / ".git" / "clarvis-engine.lock").exists()
    assert list((data_directory() / "ravis-codex-reproof").glob("decoys/*")) == []


# ── What it proves ──────────────────────────────────────────────────────────


def test_a_full_run_whose_rules_all_hold_proves_the_build_and_writes_the_profile(
    tmp_path: Path,
) -> None:
    with calibrating(tmp_path) as (rig, client, a, b):
        answer = start(client, rig, a, b)
        view = finished(client, rig, seconds=180)
        after = state_of(client)

    assert answer.status_code == 202, answer.text
    expected = {spec.id: "recorded" if spec.kind == "record" else "passed" for spec in SCENARIOS}
    assert verdicts(view) == expected, [
        (scenario["id"], scenario["verdict"], scenario["detail"]) for scenario in view["scenarios"]
    ]
    result = view["result"]
    assert (result["strict_rules_proven"], result["pin_updated"]) == (True, True)
    written = pin(rig)
    assert written["file_rules_profile"] == CANDIDATE_PROFILE
    assert written["tested"][0]["strict_rules_proven"] is True
    assert after["runtime"]["strict_rules"] == "proven"
    body = summary(view)
    assert body["decision"]["strict_rules_proven"] is True
    assert body["mode_mapping"]["measured"] is True
    assert body["status_record"].startswith(f"Calibration {view['run_id']} on Codex 0.154.0")
    answers = rig.published("ravis.codex.calibration_approval_answered")
    assert answers and all(set(entry) == {"run_id", "scenario", "method", "decision"}
                           for entry in answers)
    _left_nothing_behind(tmp_path, rig, a, b, Path(result["outputs"]))


def _left_nothing_behind(tmp_path: Path, rig: CodexRig, a: Path, b: Path, out: Path) -> None:
    assert not (a / ".git" / "clarvis-engine.lock").exists()
    assert not (b / ".git" / "clarvis-engine.lock").exists()
    assert list((data_directory() / "ravis-codex-reproof").glob("decoys/*")) == []
    assert [path for path in tmp_path.rglob("ravis-cal-*")] == []
    assert not (a / ".run").exists() and not (a / ".clarvis").exists()
    for record in rig.server.records("spawned"):
        assert subprocess.run(["ps", "-p", str(record["pid"])], capture_output=True).returncode == 1
    transcripts = "".join(path.read_text() for path in out.glob("*.jsonl"))
    assert "K5.jsonl" in {path.name for path in out.glob("*.jsonl")}
    assert "RAVIS-CALIBRATION-DECOY-" not in transcripts
    assert str(a) not in transcripts and "<project-a>" in transcripts


@pytest.mark.parametrize(
    ("fault", "scenario", "verdict", "words"),
    [
        ("plugins_on", "K10", "failed", "plugins switched on"),
        ("exec_leaks", "K5a", "failed", "read a decoy"),
        ("decoy_readable_escalated", "K5", "failed", "read a decoy"),
        ("roots_from_cwd", "K5", "failed", "without explicit workspace roots"),
        ("deny_loses_to_write", "K5c", "failed", "stay refused"),
        ("never_asks_file_approval", "K1", "failed", "without asking"),
        ("untrusted_skips_file_approval", "K1", "passed", "on-request"),
        ("approved_escapes_box", "K2", "failed", "wrote to"),
        ("thread_tmpdir_ignored", "K2b", "recorded", "didn't give"),
        # K3, each of (a)–(e) broken in turn (Cal-2).
        ("network_open", "K3", "failed", "local address"),
        ("loopback_open", "K3", "failed", "local address"),
        ("listed_site_blocked", "K3", "failed", "on the approved list"),
        ("site_block_line_changed", "K3", "failed", "fixed line"),
        ("site_left_from_an_earlier_run", "K3", "failed", "before it was added"),
        ("site_add_not_live", "K3", "failed", "that same task"),
        ("add_opens_every_site", "K3", "failed", "never added"),
        ("retry_runs_unasked", "K3", "inconclusive", "without waiting for approval"),
        ("empty_grant_grants", "K8", "failed", "empty permission grant"),
        ("never_asks_permissions", "K8", "recorded", "never asked"),
        ("git_blocked", "K9", "recorded", "couldn't commit"),
        ("interrupt_ignored", "K7", "failed", "didn't end"),
        ("stop_kills_other_project", "K6", "failed", "also stopped project B"),
        ("resume_forgets", "K13", "failed", "earlier history"),
    ],
)
def test_each_scenarios_fault_is_found_and_writes_no_profile(
    tmp_path: Path, fault: str, scenario: str, verdict: str, words: str
) -> None:
    with calibrating(tmp_path, fault) as (rig, client, a, b):
        start(client, rig, a, b, [scenario])
        view = finished(client, rig)

    [found] = view["scenarios"]
    assert (found["verdict"], words in found["detail"]) == (verdict, True), found
    assert view["result"]["strict_rules_proven"] is False
    assert pin(rig)["file_rules_profile"] is None
    if fault == "thread_tmpdir_ignored":
        assert view["result"]["owner_questions"]
    if fault == "untrusted_skips_file_approval":
        pair = summary(view)["scenarios"][0]["findings"]["working_pair"]
        assert pair == {"approvalPolicy": "on-request", "box": "read_only"}


def test_a_k5_failure_keeps_the_rules_unproven_and_sends_d2_back_to_the_owner(
    tmp_path: Path,
) -> None:
    with calibrating(tmp_path, "decoy_readable_escalated") as (rig, client, a, b):
        start(client, rig, a, b, ["K5a", "K5"])
        view = finished(client, rig)
        after = state_of(client)

    result = view["result"]
    assert verdicts(view) == {"K5a": "passed", "K5": "failed"}
    assert result["owner_decision_needed"] is True
    assert "D2 goes back to the owner" in result["sentence"]
    assert pin(rig)["file_rules_profile"] is None
    assert after["runtime"]["strict_rules"] == "unproven"


def test_codex_rejecting_the_profiles_syntax_fails_k5_in_codexs_own_words(tmp_path: Path) -> None:
    confirmed_record()
    rig = codex_rig(
        tmp_path, scenario={**SIGNED_IN_SCENARIO, "calibration_faults": ["profile_rejected"]},
        codex_calibration=True, codex_calibration_output=str(tmp_path / "out"),
    )
    a, b = projects(tmp_path)

    with TestClient(rig.app) as client:
        eventually(lambda: rig.service._supervisor.start_error, what="a failed start")
        start(client, rig, a, b, ["K10", "K5"])
        view = finished(client, rig)

    assert verdicts(view) == {"K10": "inconclusive", "K5": "failed"}
    assert "invalid type for permissions.clarvis_run" in view["scenarios"][1]["detail"]
    assert pin(rig)["file_rules_profile"] is None


# ── Redaction and the profile's limits ──────────────────────────────────────


def test_transcripts_are_scrubbed_of_tokens_emails_account_ids_and_the_run_s_own_values() -> None:
    redactor = Redactor((("RAVIS-CALIBRATION-DECOY-abc", "<decoy-marker>"),
                         ("/Users/owner/work/a", "<project-a>")))
    entry = {
        "body": {
            "output": "sk-live1234567890abcdef eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl "
                      "token: abcdefghijklmnopqrstuvwxyz0123456789 owner@example.com "
                      "user-AbCdEf123456",
            "accountId": "b8a2-real-account", "cwd": "/Users/owner/work/a",
            "text": "RAVIS-CALIBRATION-DECOY-abc",
        }
    }

    written = json.dumps(redactor.value(entry))

    for secret in ("sk-live", "eyJhbGci", "abcdefghijklmnopqrstuvwxyz0123456789",
                   "owner@example.com", "user-AbCdEf123456", "b8a2-real-account",
                   "/Users/owner", "RAVIS-CALIBRATION-DECOY-abc"):
        assert secret not in written
    assert "<project-a>" in written and "<decoy-marker>" in written


def test_a_profile_may_configure_nothing_but_its_own_permissions() -> None:
    assert checked_profile(CANDIDATE_PROFILE) == CANDIDATE_PROFILE
    for flags in (["-c", "sandbox_mode=danger-full-access"],
                  ["-c", "permissions.other.extends=\":workspace\""], ["--yolo"]):
        with pytest.raises(ProfileFileError):
            checked_profile({"name": "clarvis_run", "flags": flags})


# ── `ravis codex calibrate` ─────────────────────────────────────────────────


def test_the_command_prepares_projects_with_spaces_and_follows_the_run(tmp_path: Path) -> None:
    with calibrating(tmp_path) as (rig, client, _a, _b):
        folder = tmp_path / "NERVIS workspace" / "nervis-tasks"
        credential = tmp_path / "owner.token"
        credential.write_text("owner-cli-secret\n")
        arguments = _build_parser().parse_args([
            "codex", "calibrate", "--project-a", str(folder / "fresh a"),
            "--project-b", str(folder / "fresh b"), "--prepare", "--only", "K10",
            "--credential-file", str(credential), "--poll-seconds", "0.1",
        ])
        printed: list[str] = []

        def send(method: str, path: str, body: Any, headers: dict[str, str]) -> tuple[int, Any]:
            answer = client.request(method, path, json=body,
                                    headers={**headers, "Authorization": "Bearer owner-cli-secret"})
            return answer.status_code, answer.json()

        code = run_calibrate(arguments, rig.service.settings, send=send, out=printed.append,
                             sleep=time.sleep)

    assert code == 1, printed
    for name in ("fresh a", "fresh b"):
        log = subprocess.run(["git", "-C", str(folder / name), "log", "--oneline"],
                             capture_output=True, text=True, check=True)
        assert "Calibration base" in log.stdout
    assert any(line.startswith("K10: passed") for line in printed), printed
    assert any("Only a full run can prove" in line for line in printed), printed
    assert not any("owner-cli-secret" in line for line in printed)


def test_the_command_refuses_a_project_inside_the_ecosystem_before_asking_ravis(
    tmp_path: Path,
) -> None:
    arguments = _build_parser().parse_args([
        "codex", "calibrate", "--project-a", str(REPOSITORY / "ravis"),
        "--project-b", str(tmp_path / "b"), "--credential-file", "unused",
    ])
    printed: list[str] = []

    def never(*_: Any) -> tuple[int, Any]:
        raise AssertionError("RAVIS was asked")

    settings = Settings(_env_file=None, agent_protected_repositories=[str(REPOSITORY)])  # type: ignore[call-arg]
    code = run_calibrate(arguments, settings, send=never, out=printed.append)

    assert code == 2
    assert "ecosystem's own" in printed[0]


# ── The harness's fixed answers, and the decision ───────────────────────────


class _HeldOnly:
    """Just enough of RAVIS's Codex for a session to hold a thread."""

    def hold(self, thread_id: str, inbox: Any) -> None:
        del thread_id, inbox

    def release(self, thread_id: str) -> None:
        del thread_id


def test_the_harness_allows_a_listed_command_once_and_only_in_its_own_project(
    tmp_path: Path,
) -> None:
    a, b = projects(tmp_path)
    plan = CalibrationPlan(
        run_id="cal_x", project_a=Project("A", a, a / ".git"),
        project_b=Project("B", b, b / ".git"),
        decoy_folder=tmp_path, marker="M", codeword="w", slash_tmp=tmp_path, codex_tmpdir=tmp_path,
    )
    audited: list[tuple[str, str, str]] = []
    session = Session("K2", _HeldOnly(), plan, "clarvis_run", CalibrationTimings(),  # type: ignore[arg-type]
                      lambda *entry: audited.append(entry))
    session.hold("A", "thread-a", plan.project_a)
    session.expect("A", [Listed("cat x")])

    def answer(command: str, cwd: Path, thread: str = "thread-a",
               method: str = "item/commandExecution/requestApproval") -> str:
        item = InboxItem(method, {"threadId": thread, "command": command, "cwd": str(cwd)})
        return session._decide(item, session._keys.get(thread))[1]

    assert answer("cat x", b) == "decline"
    assert answer("cat x", a) == "accept"
    assert answer("bash -lc 'cat x'", a) == "decline"
    assert answer("rm -rf ~", a) == "decline"
    assert answer("cat x", a, thread="someone-elses") == "refused"
    assert answer("", a, method="item/tool/call") == "refused"
    assert session.off_list


def test_only_a_full_run_with_every_must_pass_question_passed_proves_the_rules() -> None:
    def results(**changed: str) -> dict[str, ScenarioResult]:
        return {
            spec.id: ScenarioResult(
                spec.id, changed.get(spec.id, "recorded" if spec.kind == "record" else "passed"), ""
            )  # type: ignore[arg-type]
            for spec in SCENARIOS
        }

    everything = tuple(spec.id for spec in SCENARIOS)
    assert decide(results(), everything).strict_rules_proven is True
    assert decide(results(K7="inconclusive"), everything).strict_rules_proven is False
    assert decide(results(), ("K10", "K5a")).strict_rules_proven is False
    gate = decide(results(K5a="failed"), everything)
    assert (gate.strict_rules_proven, gate.owner_decision_needed) == (False, True)
    own = decide(results(K5c="failed"), everything)
    assert own.strict_rules_proven is True
    assert own.protected_repositories.startswith("stay refused")
