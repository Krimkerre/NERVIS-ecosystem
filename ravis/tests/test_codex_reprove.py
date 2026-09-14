"""The file-rules re-test: who may start it, and what its harness may answer (design §3.4, F-A3).

The contract is `codex-admin.json` (`reprove`) and `conventions.json` (credential and key rules);
the rules are the design's and the final check's:

- **only `admin.owner_cli` starts it** — NERVIS's `admin.launcher`, every client and anonymous
  callers get 403 `REPROOF_NOT_ALLOWED` — and it needs an `Idempotency-Key` (428 without, the same
  answer on a replay, 422 for the same key with another body); a route-table test pins that this is
  the one Codex route carrying `require_owner_cli`;
- **it waits for a running turn** (409 `CODEX_RUN_IN_PROGRESS`) and refuses to start when there is
  nothing it could prove, or no allowance to prove it with (409 `CODEX_NOT_READY`, never 429);
- **K5a runs first, with no model**: a leak there fails the re-test without spending a turn;
- **the harness allows exactly the four listed commands**, each once, in folder A, only in threads
  it created — anything else is declined and the result is `inconclusive`; a request for a thread
  it didn't create never reaches it;
- **the caps** end a turn that takes more than 12 steps or runs past its time;
- `proven` records the build's rules as proven, `failed` and `inconclusive` don't; every answer and
  the result are audited, and the throwaway folders are deleted afterwards.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.codex_rig import (
    ALLOWANCE,
    PROFILE,
    SIGNED_IN,
    CodexRig,
    call,
    codex_rig,
    confirmed_record,
    eventually,
    fixture_response,
    reaches,
    refused_as,
    state_of,
)

from ravis.agent.identity import require_agent_client
from ravis.api.management import codex as codex_routes
from ravis.config import data_directory
from ravis.credentials import config_directory

REPROVE = "/api/v1/codex/reprove"
SIGNED_IN_SCENARIO = {"account": SIGNED_IN, "rate_limits": ALLOWANCE}
TURN = {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "inProgress", "items": []}}


def example(name: str) -> dict[str, Any]:
    return fixture_response("codex-admin.json", "POST", REPROVE, name)


def start(client: TestClient, rig: CodexRig, key: str = "key-0001", body: Any = None) -> Any:
    return call(client, rig, "POST", REPROVE, "admin.owner_cli", {} if body is None else body,
                headers={"Idempotency-Key": key})


def finished(client: TestClient, rig: CodexRig, seconds: float = 20.0) -> dict[str, Any]:
    def result() -> dict[str, Any] | None:
        reproof = call(client, rig, "GET", REPROVE, "admin.owner_cli").json()["reproof"]
        return reproof if reproof["state"] == "finished" else None

    return eventually(result, seconds, what="the re-test's result")  # type: ignore[no-any-return]


@contextmanager
def accepted_build(
    tmp_path: Path, *, profile: dict[str, Any] | None = PROFILE, **scenario: Any
) -> Iterator[tuple[CodexRig, TestClient]]:
    """A running RAVIS on a build the owner accepted, signed in, ready for its re-test."""
    confirmed_record()
    rig = codex_rig(tmp_path, tested=False, profile=profile,
                    scenario={**SIGNED_IN_SCENARIO, **scenario})
    with TestClient(rig.app) as client:
        installed = reaches(client, "untested_version")["runtime"]["installed_sha256"]
        answer = call(client, rig, "POST", "/api/v1/codex/accept-version", "admin.launcher",
                      {"sha256": installed})
        assert answer.status_code == 200, answer.text
        eventually(lambda: state_of(client)["account"] is not None, what="signed in")
        yield rig, client


def leftovers() -> list[Path]:
    root = data_directory() / "ravis-codex-reproof"
    return [*root.glob("run-*"), *root.glob("decoys/*")] if root.exists() else []


# ── Who may start it ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "caller", ["admin.launcher", "client.clarvis", "client.nervis", "anonymous"]
)
def test_only_the_owners_command_line_credential_may_start_the_re_test(
    tmp_path: Path, caller: str
) -> None:
    rig = codex_rig(tmp_path, codex_enabled=False)

    with TestClient(rig.app) as client:
        answer = call(client, rig, "POST", REPROVE, caller, {}, headers={"Idempotency-Key": "k-1"})

    refused_as(answer, example("NERVIS's admin.launcher"))


def test_the_re_test_needs_an_idempotency_key(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, codex_enabled=False)

    with TestClient(rig.app) as client:
        answer = call(client, rig, "POST", REPROVE, "admin.owner_cli", {})

    refused_as(answer, example("no Idempotency-Key"))


def test_reading_the_result_needs_a_named_caller(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, codex_enabled=False)

    with TestClient(rig.app) as client:
        anonymous = call(client, rig, "GET", REPROVE, "anonymous")
        named = call(client, rig, "GET", REPROVE, "client.nervis")

    assert (anonymous.status_code, anonymous.json()["error"]["code"]) == (403, "FORBIDDEN")
    assert named.json() == {"reproof": {"state": "idle", "result": None}}


def test_the_re_test_is_the_one_codex_route_that_needs_the_owners_credential() -> None:
    """F-A3's route-table test: the route that starts Codex work carries `require_owner_cli`."""
    guarded = {
        (route.path, method): {dependency.dependency for dependency in route.dependencies}
        for route in codex_routes.router.routes
        for method in route.methods  # type: ignore[attr-defined]
    }

    owner_only = [
        key for key, guards in guarded.items() if codex_routes.require_owner_cli in guards
    ]
    assert owner_only == [(REPROVE, "POST")]
    assert guarded[(REPROVE, "GET")] == {codex_routes.require_named_caller}
    assert guarded[("/api/v1/codex", "GET")] == set()
    admin_routes = {key for key, guards in guarded.items() if codex_routes.require_admin in guards}
    assert admin_routes == {
        ("/api/v1/codex/sign-in", "POST"), ("/api/v1/codex/sign-in", "GET"),
        ("/api/v1/codex/sign-in", "DELETE"), ("/api/v1/codex/sign-out", "POST"),
        ("/api/v1/codex/account/confirm", "POST"), ("/api/v1/codex/version-check", "GET"),
        ("/api/v1/codex/accept-version", "POST"),
        ("/api/v1/codex/accept-version/{sha256}", "DELETE"),
        # R5: removing an added site is NERVIS's Codex card's, through its control route.
        ("/api/v1/codex/sites/{host}", "DELETE"),
        # 0.26.0: switching a skill is NERVIS's Codex card's too, through its control route.
        ("/api/v1/codex/skills", "POST"),
    }
    # R5: Clarvis, NERVIS or an admin reads the sites; only Clarvis's client credential adds.
    assert guarded[("/api/v1/codex/sites", "GET")] == {codex_routes.require_sites_reader}
    assert guarded[("/api/v1/codex/sites", "POST")] == {require_agent_client}
    # 0.26.0: NERVIS's GET relay or an admin reads the skills; never Clarvis.
    assert guarded[("/api/v1/codex/skills", "GET")] == {codex_routes.require_skills_reader}


# ── When it may run ─────────────────────────────────────────────────────────


def test_the_re_test_waits_for_codexs_skills_to_be_the_owners(tmp_path: Path) -> None:
    """Its one turn runs in RAVIS's Codex like any task's, so a personal skill must not be on for
    it either: while RAVIS can't put Codex's skills to the owner's choices, it doesn't start."""
    with accepted_build(tmp_path, refused_methods=["skills/list"]) as (rig, client):
        answer = start(client, rig)

    assert answer.status_code == 409, answer.text
    error = answer.json()["error"]
    assert error["code"] == "CODEX_NOT_READY"
    assert "Codex's skills aren't your choices yet, so its turn can't run." in error["message"]
    assert not rig.published("ravis.codex.reproof_started")


def test_the_re_test_waits_for_a_running_turn(tmp_path: Path) -> None:
    with accepted_build(tmp_path) as (rig, client):
        rig.server.send("notify", method="turn/started", params=TURN)
        eventually(lambda: state_of(client)["runtime"]["process"]["active_turns"] == 1)
        answer = start(client, rig)

    refused_as(answer, example("a Codex task is live"))


def _not_ready(answer: Any, words: str) -> None:
    assert answer.status_code == 409, answer.text
    error = answer.json()["error"]
    assert error["code"] == "CODEX_NOT_READY"
    assert words in error["details"]["reason"]


def test_a_tested_build_is_proven_by_calibration_not_by_the_re_test(tmp_path: Path) -> None:
    confirmed_record()
    rig = codex_rig(tmp_path, proven=False, profile=PROFILE, scenario=SIGNED_IN_SCENARIO)

    with TestClient(rig.app) as client:
        eventually(lambda: state_of(client)["account"] is not None)
        answer = start(client, rig)

    _not_ready(answer, "calibration proves its file rules")


def test_without_a_calibrated_profile_there_is_nothing_to_prove(tmp_path: Path) -> None:
    with accepted_build(tmp_path, profile=None) as (rig, client):
        answer = start(client, rig)

    _not_ready(answer, "isn't calibrated")


def test_signed_out_there_is_no_allowance_for_the_turn(tmp_path: Path) -> None:
    confirmed_record()
    rig = codex_rig(tmp_path, tested=False, profile=PROFILE, scenario={"account": None})

    with TestClient(rig.app) as client:
        installed = reaches(client, "untested_version")["runtime"]["installed_sha256"]
        call(client, rig, "POST", "/api/v1/codex/accept-version", "admin.launcher",
             {"sha256": installed})
        eventually(lambda: state_of(client)["runtime"]["process"]["state"] == "running")
        answer = start(client, rig)

    _not_ready(answer, "signed in")


def test_a_used_up_allowance_is_409_not_ready_never_429(tmp_path: Path) -> None:
    used_up = {**ALLOWANCE, "rateLimitReachedType": "rate_limit_reached"}
    with accepted_build(tmp_path, rate_limits=used_up) as (rig, client):
        eventually(lambda: state_of(client)["usage"]["limit_reached"] == "rate_limit_reached")
        answer = start(client, rig)

    _not_ready(answer, "used up")


# ── What it proves ──────────────────────────────────────────────────────────


def test_a_re_test_whose_refusals_all_hold_proves_the_build(tmp_path: Path) -> None:
    with accepted_build(tmp_path) as (rig, client):
        started = start(client, rig)
        result = finished(client, rig)
        after = state_of(client)
        execs = rig.server.received("command/exec")
        threads = rig.server.received("thread/start")
        [turn] = rig.server.received("turn/start")

    assert started.status_code == 202
    assert started.json() == example("the owner, from the menu bar")["body"]
    assert result["result"] == "proven", result
    installed = after["runtime"]["installed_sha256"]
    assert installed in json.loads((config_directory() / "codex-state.json").read_text())["proven"]
    assert (after["runtime"]["verdict"], after["runtime"]["strict_rules"]) == ("accepted", "proven")
    assert after["state"] == "signed_in"
    assert len(execs) == 4
    assert all(call["params"]["permissionProfile"] == "clarvis_run" for call in execs)
    assert len(threads) == 2
    assert [
        (params["approvalPolicy"], params["permissions"], params["ephemeral"])
        for params in (thread["params"] for thread in threads)
    ] == [("untrusted", "clarvis_run", True)] * 2
    assert turn["params"]["effort"] == "low"
    prompt = turn["params"]["input"][0]["text"]
    assert prompt.endswith("Ask for escalated permissions when you run commands 2 and 4.")
    roots = turn["params"]["sandboxPolicy"]["writableRoots"]
    assert turn["params"]["runtimeWorkspaceRoots"] == roots
    answers = rig.published("ravis.codex.reproof_approval_answered")
    assert [(answer["command_index"], answer["decision"]) for answer in answers] == [
        (1, "accept"), (2, "accept"), (3, "accept"), (4, "accept"),
    ]
    assert rig.published("ravis.codex.reproof_started")[0]["application_id"] == "owner_cli"
    assert rig.published("ravis.codex.reproof_finished")[0]["result"] == "proven"
    assert leftovers() == []


def test_a_replayed_key_gets_the_same_answer_and_starts_nothing_more(tmp_path: Path) -> None:
    with accepted_build(tmp_path) as (rig, client):
        first = start(client, rig, key="replayed-key")
        finished(client, rig)
        replayed = start(client, rig, key="replayed-key")
        reused = start(client, rig, key="replayed-key", body={"another": "body"})
        threads = len(rig.server.received("thread/start"))

    assert (replayed.status_code, replayed.json()) == (first.status_code, first.json())
    assert threads == 2
    assert (reused.status_code, reused.json()["error"]["code"]) == (422, "IDEMPOTENCY_KEY_REUSED")


def test_a_leak_without_a_model_fails_the_re_test_and_spends_no_turn(tmp_path: Path) -> None:
    with accepted_build(tmp_path, sandbox="leaks") as (rig, client):
        start(client, rig)
        result = finished(client, rig)
        after = state_of(client)

    assert result["result"] == "failed"
    assert result["detail"].startswith("K5a")
    assert rig.server.received("thread/start") == []
    assert after["runtime"]["strict_rules"] == "unproven"
    assert rig.published("ravis.codex.reproof_finished")[0]["result"] == "failed"
    assert leftovers() == []


def test_an_escalated_command_that_escapes_fails_the_re_test(tmp_path: Path) -> None:
    with accepted_build(tmp_path, sandbox="escalation_leaks") as (rig, client):
        start(client, rig)
        result = finished(client, rig)

    assert result["result"] == "failed"
    assert "marker" in result["detail"]


@pytest.mark.parametrize(
    ("script", "words"),
    [
        ("wanders", "isn't on the list"),
        ("stutters", "isn't on the list"),
        ("wrong_cwd", "isn't on the list"),
        ("loops", "more than 12 steps"),
        ("sleeps", "cap"),
    ],
)
def test_codex_straying_from_the_list_or_a_cap_is_inconclusive(
    tmp_path: Path, script: str, words: str
) -> None:
    with accepted_build(tmp_path, turn_script=script) as (rig, client):
        start(client, rig)
        result = finished(client, rig)
        after = state_of(client)
        interrupted = rig.server.received("turn/interrupt")

    assert result["result"] == "inconclusive", result
    assert words in result["detail"]
    assert after["runtime"]["strict_rules"] == "unproven"
    if script in ("loops", "sleeps"):
        assert interrupted
    declines = [a for a in rig.published("ravis.codex.reproof_approval_answered")
                if a["decision"] == "decline"]
    assert bool(declines) == (script in ("wanders", "stutters", "wrong_cwd"))


def test_the_harness_never_answers_for_a_thread_it_didnt_create(tmp_path: Path) -> None:
    with accepted_build(tmp_path, turn_script="other_thread") as (rig, client):
        start(client, rig)
        result = finished(client, rig)
        [stranger] = rig.server.records("other_thread_answer")

    assert stranger["answer"]["error"]["code"] == -32601
    assert "result" not in stranger["answer"]
    assert result["result"] == "proven"


def test_a_ravis_restart_during_the_re_test_is_reported_not_forgotten(tmp_path: Path) -> None:
    path = config_directory() / "codex-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "format": 1, "confirmed": None, "signed_out_on_purpose": False, "accepted": [],
        "proven": {}, "sign_in_started_at": None,
        "reproof": {"state": "running", "result": None, "detail": None, "sha256": "a" * 64,
                    "started_at": "2026-09-13T01:00:00Z", "finished_at": None},
    }))
    rig = codex_rig(tmp_path, codex_enabled=False)

    with TestClient(rig.app) as client:
        reaches(client, "not_available")
        reproof = call(client, rig, "GET", REPROVE, "admin.owner_cli").json()["reproof"]

    assert (reproof["state"], reproof["result"]) == ("finished", "inconclusive")
    assert reproof["detail"] == "RAVIS restarted during the re-test"
