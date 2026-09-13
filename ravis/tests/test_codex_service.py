"""RAVIS's one Codex process, and the state it reports (design §3.3, §4.1, §4.4; RAVIS.md §15.1.2).

Each test starts a whole RAVIS through its lifespan, the fake Codex programs standing in for the
real ones (`tests/codex_rig.py`, `tests/fake_codex_app_server.py`) and the service's clocks
shortened. What they hold RAVIS to:

- **one app-server**, started with the designed command, an allow-listed environment and RAVIS's own
  Codex home — and **none at all** for a build neither tested nor accepted, or with Codex off;
- the process that answers must be the Codex the check verified, or Codex is not available;
- a crash or a hang restarts it with backoff, and five failures in the window stop the restarts;
- a new binary on disk is swapped in only while no turn runs;
- shutdown closes Codex's input, so it exits;
- `GET /api/v1/codex` has the contract's shape in each state, answers from memory, and keeps the
  allowance an allowance: unknown never zero, used up distinct from throttled, polling paused
  while turns run.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from tests.codex_fakes import FakeAppServer, FakeCodex, pin_entry
from tests.codex_rig import (
    ALLOWANCE,
    FAST,
    SIGNED_IN,
    codex_rig,
    confirmed_record,
    eventually,
    reaches,
    state_of,
)

from ravis.config import data_directory
from ravis.ecosystem.capabilities import BUILD_VERSION

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "relay-contract"
SIGNED_IN_SCENARIO = {"account": SIGNED_IN, "rate_limits": ALLOWANCE}
TURN = {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "inProgress", "items": []}}
TURN_DONE = {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "completed", "items": []}}
IDLE_SIGN_IN = {"state": "idle", "started_at": None, "expires_at": None, "error": None}


def example(name: str) -> dict[str, Any]:
    document = json.loads((FIXTURES / "codex-state.json").read_text())
    return next(case["response"]["body"] for case in document["examples"] if case["name"] == name)


def keys(value: Any) -> Any:
    """A body's shape: every key, however deep, with each list read from its first item."""
    if isinstance(value, dict):
        return {key: keys(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [keys(value[0])] if value else []
    return None


def without_runs(body: dict[str, Any]) -> dict[str, Any]:
    """The body with `runs` emptied: no run can exist until M29's third increment."""
    return {**body, "runs": []}


# ── The process ─────────────────────────────────────────────────────────────


def test_one_app_server_starts_with_the_designed_command_home_and_environment(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("RAVIS_SOMETHING", "ravis-setting")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
    monkeypatch.setenv("SOME_SERVICE_TOKEN", "not-a-real-token")
    monkeypatch.setenv("AN_UNLISTED_VARIABLE", "harmless, and still not passed")
    monkeypatch.setenv("LC_ALL", "C")
    rig = codex_rig(tmp_path, scenario={"account": None})

    with TestClient(rig.app) as client:
        reaches(client, "untested_version")  # a tested build whose file rules aren't proven yet
        [start] = eventually(rig.server.starts)
        eventually(lambda: rig.server.received("initialized"))
        initialize = rig.server.received("initialize")[0]

    assert start["argv"] == [
        "app-server", "--listen", "stdio://",
        "-c", 'cli_auth_credentials_store="file"',
        "-c", "analytics.enabled=false",
        "-c", "features.plugins=false",
        "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
        "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
        "-c", "features.network_proxy=true",
    ]
    environment = start["env"]
    home = tmp_path / "codex-home"
    assert environment["CODEX_HOME"] == str(home)
    assert environment["TMPDIR"] == str(home / "tmp")
    assert environment["TERM"] == "dumb"
    assert environment["LC_ALL"] == "C"
    assert not [name for name in environment if name.startswith(("RAVIS_", "OPENAI_"))]
    assert "SOME_SERVICE_TOKEN" not in environment
    assert "AN_UNLISTED_VARIABLE" not in environment  # an allow-list, not a list of removals
    assert home.stat().st_mode & 0o777 == 0o700
    assert initialize["params"] == {
        "clientInfo": {"name": "ravis", "title": "RAVIS", "version": BUILD_VERSION},
        "capabilities": {"experimentalApi": True},
    }


def test_a_build_neither_tested_nor_accepted_gets_no_process(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, tested=False, scenario={"account": None})

    with TestClient(rig.app) as client:
        body = reaches(client, "untested_version")
        time.sleep(0.5)

    # Only the version check's throwaway handshake may have run, never in RAVIS's own home.
    assert rig.supervised_starts() == []
    assert body["runtime"]["verdict"] == "untested"
    assert body["runtime"]["process"]["state"] == "not_started"


def test_codex_switched_off_gets_no_process(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, codex_enabled=False)

    with TestClient(rig.app) as client:
        body = reaches(client, "not_available")
        time.sleep(0.3)

    assert rig.server.starts() == []
    assert "switched off" in body["reason"]


def test_a_process_that_isnt_the_checked_codex_is_not_available_and_not_retried(
    tmp_path: Path,
) -> None:
    """Design §4.1 item 6: `initialize`'s version and home must be the checked binary's."""
    rig = codex_rig(tmp_path, proven=True, scenario={"user_agent_version": "0.155.0"})

    with TestClient(rig.app) as client:
        body = reaches(client, "not_available")
        time.sleep(0.8)

    assert "0.155.0" in body["reason"]
    assert len(rig.server.starts()) == 1


def test_a_process_using_another_codex_home_is_not_available(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, proven=True, scenario={"codex_home": str(tmp_path / "elsewhere")})

    with TestClient(rig.app) as client:
        body = reaches(client, "not_available")

    assert "not RAVIS's" in body["reason"]


def test_a_crash_restarts_the_process_and_is_counted(tmp_path: Path) -> None:
    confirmed_record()
    rig = codex_rig(tmp_path, proven=True, scenario=SIGNED_IN_SCENARIO)

    with TestClient(rig.app) as client:
        reaches(client, "signed_in")
        rig.server.send("crash", status=3)
        eventually(lambda: len(rig.server.starts()) == 2, what="a second start")
        body = reaches(client, "signed_in")

    assert body["runtime"]["process"]["restarts_24h"] == 1


def test_a_process_that_stops_answering_is_restarted(tmp_path: Path) -> None:
    """Three missed local health checks with no turn running mean a hang (design §4.4, AL6)."""
    confirmed_record()
    rig = codex_rig(tmp_path, proven=True, scenario=SIGNED_IN_SCENARIO)

    with TestClient(rig.app) as client:
        reaches(client, "signed_in")
        rig.server.send("stop_answering")
        eventually(lambda: len(rig.server.starts()) == 2, what="a restart after the hang")
        reaches(client, "signed_in")

    assert rig.server.received("thread/loaded/list")[0]["params"] == {"limit": 1}


def test_five_failures_in_the_window_stop_the_restarts(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, proven=True, scenario={"exit_on_start": 3})

    with TestClient(rig.app) as client:
        body = reaches(client, "runtime_down")
        eventually(lambda: "five times" in state_of(client)["reason"], what="giving up")
        time.sleep(1.0)
        after = state_of(client)

    assert len(rig.server.starts()) == 5
    assert after["runtime"]["process"]["state"] == "failed"
    assert body["runtime"]["running_sha256"] is None


def test_shutting_down_closes_codexs_input_so_it_exits(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, proven=True, scenario={"account": None})

    with TestClient(rig.app) as client:
        reaches(client, "signed_out")

    eventually(lambda: rig.server.records("stdin_closed"), seconds=3, what="Codex's input closed")


def _replace_binary(tmp_path: Path, build: str, server: FakeAppServer) -> FakeCodex:
    return FakeCodex.install(tmp_path / f"bin-{build}" / "codex", build=build, app_server=server)


def test_a_new_tested_binary_is_swapped_in_once_no_turn_is_running(tmp_path: Path) -> None:
    confirmed_record()
    server = FakeAppServer.create(tmp_path / "app-server", **SIGNED_IN_SCENARIO)
    two = _replace_binary(tmp_path, "two", server)
    rig = codex_rig(
        tmp_path, proven=True, scenario=SIGNED_IN_SCENARIO,
        extra_entries=(pin_entry(two, proven=True),),
    )

    with TestClient(rig.app) as client:
        first = reaches(client, "signed_in")["runtime"]["running_sha256"]
        rig.server.send("notify", method="turn/started", params=TURN)
        eventually(lambda: state_of(client)["runtime"]["process"]["active_turns"] == 1)
        os.replace(two.path, rig.codex.path)
        eventually(lambda: state_of(client)["runtime"]["installed_sha256"] != first)
        time.sleep(0.8)
        starts_during_the_turn = len(rig.server.starts())
        rig.server.send("notify", method="turn/completed", params=TURN_DONE)
        eventually(lambda: len(rig.server.starts()) == 2, what="the swap once idle")
        body = reaches(client, "signed_in")

    assert starts_during_the_turn == 1
    assert body["runtime"]["running_sha256"] == body["runtime"]["installed_sha256"] != first


def test_a_new_untested_binary_stops_the_process_and_none_replaces_it(tmp_path: Path) -> None:
    server = FakeAppServer.create(tmp_path / "app-server", account=None)
    two = _replace_binary(tmp_path, "two", server)
    rig = codex_rig(tmp_path, proven=True, scenario={"account": None})

    with TestClient(rig.app) as client:
        reaches(client, "signed_out")
        os.replace(two.path, rig.codex.path)
        body = reaches(client, "untested_version")
        eventually(lambda: rig.server.records("stdin_closed"), what="the old process ended")
        time.sleep(0.5)

    assert len(rig.supervised_starts()) == 1
    assert body["runtime"]["verdict"] == "untested"


def test_leftover_throwaway_folders_are_swept_when_codex_starts(tmp_path: Path) -> None:
    data = data_directory()
    leftovers = [
        data / "ravis-codex-scratch" / "check-left-by-a-kill" / "home",
        data / "ravis-codex-reproof" / "run-left-by-a-kill" / "a",
    ]
    for folder in leftovers:
        folder.mkdir(parents=True)
    decoy = data / "ravis-codex-reproof" / "decoys" / "credentials.json"
    decoy.parent.mkdir(parents=True)
    decoy.write_text("{}")
    rig = codex_rig(tmp_path, proven=True, scenario={"account": None})

    with TestClient(rig.app) as client:
        reaches(client, "signed_out")

    assert not any(folder.parent.exists() for folder in leftovers)
    assert not decoy.exists()


# ── The state endpoint ──────────────────────────────────────────────────────


def test_signed_out_has_the_fixtures_shape_and_no_allowance_or_models(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, proven=True, scenario={"account": None})

    with TestClient(rig.app) as client:
        body = reaches(client, "signed_out")

    assert keys(body) == keys(example("signed out"))
    assert body["account"] is None
    assert body["models"] == []
    assert body["runs"] == []
    assert body["usage"]["known"] is False
    assert body["sign_in"] == IDLE_SIGN_IN
    assert body["runtime"]["process"]["state"] == "running"
    assert body["runtime"]["running_sha256"] == body["runtime"]["installed_sha256"]
    assert body["home"]["fingerprint"].startswith("sha256:")


def test_signed_in_has_the_fixtures_shape_with_a_hint_and_never_an_email(tmp_path: Path) -> None:
    confirmed_record()
    rig = codex_rig(tmp_path, proven=True, scenario=SIGNED_IN_SCENARIO)

    with TestClient(rig.app) as client:
        body = eventually(
            lambda: (found := state_of(client))["state"] == "signed_in"
            and found["usage"]["known"] and found,
        )
        anonymous = client.get("/api/v1/codex").json()
        named = client.get("/api/v1/codex", headers=rig.caller("client.nervis")).json()

    fixture = example("signed in, three projects busy, read by a named caller")
    assert keys(without_runs(body)) == keys(without_runs(fixture))
    assert body["reason"] == "Codex is signed in with a ChatGPT Plus plan."
    assert body["account"]["email_hint"] == "o…@example.com"
    account = body["account"]
    assert (account["fingerprint"], account["fingerprint_matches"]) == ("strong", True)
    assert "owner@example.com" not in json.dumps(body)
    assert [model["id"] for model in body["models"]] == ["gpt-6-astra", "gpt-5.6-sol"]
    assert [window["remaining_percent"] for window in body["usage"]["windows"]] == [62, 80]
    assert body["usage"]["allowance_not_cost"] is True
    assert keys(anonymous) == keys(named) == keys(body)


def test_usage_codex_cant_read_is_unknown_never_zero(tmp_path: Path) -> None:
    confirmed_record()
    scenario = {**SIGNED_IN_SCENARIO, "silent_methods": ["account/rateLimits/read"]}
    quick = dataclasses.replace(FAST, rate_limits_read_seconds=0.2)
    rig = codex_rig(tmp_path, proven=True, scenario=scenario, timings=quick)

    with TestClient(rig.app) as client:
        reaches(client, "signed_in")
        eventually(lambda: rig.server.received("account/rateLimits/read"), what="a read asked")
        time.sleep(0.6)  # past the read's own deadline, so its failure has been handled
        body = state_of(client)

    assert keys(body) == keys(example("signed in, usage unknown: no percentages, never zero"))
    assert body["usage"]["known"] is False
    assert body["usage"]["windows"] == []


def test_a_used_up_allowance_is_its_own_state_with_its_reset(tmp_path: Path) -> None:
    confirmed_record()
    used_up = {**ALLOWANCE, "primary": {**ALLOWANCE["primary"], "usedPercent": 100}}
    rig = codex_rig(tmp_path, proven=True, scenario={"account": SIGNED_IN, "rate_limits": used_up})

    with TestClient(rig.app) as client:
        body = reaches(client, "quota_exhausted")

    assert keys(body) == keys(example("allowance used up"))
    assert body["reason"].startswith("The ChatGPT plan's allowance is used up until ")
    assert body["usage"]["windows"][0]["remaining_percent"] == 0


def test_throttling_is_never_shown_as_a_used_up_allowance(tmp_path: Path) -> None:
    """Design §9: a turn retried on `rateLimitExceeded` is OpenAI asking Codex to slow down."""
    confirmed_record()
    rig = codex_rig(tmp_path, proven=True, scenario=SIGNED_IN_SCENARIO)
    throttled = {
        "threadId": "thread-1", "turnId": "turn-1", "willRetry": True,
        "error": {"message": "Rate limit reached", "codexErrorInfo": "rateLimitExceeded"},
    }

    with TestClient(rig.app) as client:
        reaches(client, "signed_in")
        rig.server.send("notify", method="error", params=throttled)
        time.sleep(0.5)
        body = state_of(client)

    assert body["state"] == "signed_in"


def test_a_tested_build_with_unproven_rules_pauses_with_the_fixtures_shape(tmp_path: Path) -> None:
    confirmed_record()
    rig = codex_rig(tmp_path, proven=False, scenario=SIGNED_IN_SCENARIO)

    with TestClient(rig.app) as client:
        body = eventually(
            lambda: (found := state_of(client))["usage"]["known"] and found, what="usage read"
        )

    fixture = example("paused for re-testing: the installed binary is neither tested nor accepted")
    assert keys(body) == keys(fixture)
    assert body["state"] == "untested_version"
    assert (body["runtime"]["verdict"], body["runtime"]["strict_rules"]) == ("tested", "unproven")
    assert "calibration" in body["reason"]


def test_a_paused_build_outranks_being_signed_out(tmp_path: Path) -> None:
    """The state table's order (design §3.3): a runtime row wins over an account row."""
    rig = codex_rig(tmp_path, proven=False, scenario={"account": None})

    with TestClient(rig.app) as client:
        eventually(lambda: rig.server.received("account/read"), what="the account read")
        time.sleep(0.3)
        body = state_of(client)

    assert body["state"] == "untested_version"
    assert body["account"] is None


def test_the_state_is_answered_from_memory_while_codex_answers_nothing(tmp_path: Path) -> None:
    """Design §3.3: the route never waits on Codex; the launcher allows it 1.5 s."""
    confirmed_record()
    rig = codex_rig(tmp_path, proven=True, scenario=SIGNED_IN_SCENARIO)

    with TestClient(rig.app) as client:
        reaches(client, "signed_in")
        rig.server.send("stop_answering")
        time.sleep(0.3)
        started = time.monotonic()
        answers = [client.get("/api/v1/codex") for _ in range(5)]
        took = time.monotonic() - started

    assert all(answer.status_code == 200 for answer in answers)
    assert took < 1.5


def test_the_allowance_is_polled_while_idle_paused_during_turns_and_read_after(
    tmp_path: Path,
) -> None:
    confirmed_record()
    rig = codex_rig(
        tmp_path, proven=True, scenario=SIGNED_IN_SCENARIO, codex_usage_refresh_seconds=1
    )

    def reads() -> int:
        return len(rig.server.received("account/rateLimits/read"))

    with TestClient(rig.app) as client:
        reaches(client, "signed_in")
        eventually(lambda: reads() >= 2, what="idle polling")
        rig.server.send("notify", method="turn/started", params=TURN)
        eventually(lambda: state_of(client)["runtime"]["process"]["active_turns"] == 1)
        paused_at = reads()
        update = {"rateLimits": {"primary": {**ALLOWANCE["primary"], "usedPercent": 55}}}
        rig.server.send("notify", method="account/rateLimits/updated", params=update)
        merged = eventually(
            lambda: (found := state_of(client))["usage"]["source"] == "notification" and found
        )
        time.sleep(1.6)
        during = reads()
        rig.server.send("notify", method="turn/completed", params=TURN_DONE)
        eventually(lambda: reads() == paused_at + 1, seconds=2, what="the read after the turn")

    assert during == paused_at
    windows = merged["usage"]["windows"]
    assert [window["used_percent"] for window in windows] == [55, 20]


def test_one_allowance_read_follows_the_end_of_the_last_turn(tmp_path: Path) -> None:
    """Design §3.3: with the 15-minute clock nowhere near due, a turn's end still brings a read."""
    confirmed_record()
    rig = codex_rig(tmp_path, proven=True, scenario=SIGNED_IN_SCENARIO)

    def reads() -> int:
        return len(rig.server.received("account/rateLimits/read"))

    with TestClient(rig.app) as client:
        reaches(client, "signed_in")
        rig.server.send("notify", method="turn/started", params=TURN)
        eventually(lambda: state_of(client)["runtime"]["process"]["active_turns"] == 1)
        before = reads()
        rig.server.send("notify", method="turn/completed", params=TURN_DONE)
        eventually(lambda: reads() == before + 1, seconds=2, what="the read after the turn")
        time.sleep(0.5)
        after = reads()

    assert after == before + 1


def test_a_turn_that_failed_on_the_usage_limit_reads_the_allowance_at_once(
    tmp_path: Path,
) -> None:
    confirmed_record()
    slow = dataclasses.replace(FAST, usage_after_turn_seconds=60.0)
    rig = codex_rig(tmp_path, proven=True, scenario=SIGNED_IN_SCENARIO, timings=slow)
    failed = {
        "threadId": "thread-1",
        "turn": {
            "id": "turn-1", "status": "failed", "items": [],
            "error": {
                "message": "You've hit your usage limit.", "codexErrorInfo": "usageLimitExceeded",
            },
        },
    }

    def reads() -> int:
        return len(rig.server.received("account/rateLimits/read"))

    with TestClient(rig.app) as client:
        reaches(client, "signed_in")
        rig.server.send("notify", method="turn/started", params=TURN)
        eventually(lambda: state_of(client)["runtime"]["process"]["active_turns"] == 1)
        before = reads()
        rig.server.send("notify", method="turn/completed", params=failed)
        eventually(lambda: reads() == before + 1, seconds=2, what="an immediate read")


def test_state_changes_are_published_with_their_reason(tmp_path: Path) -> None:
    confirmed_record()
    rig = codex_rig(tmp_path, proven=True, scenario=SIGNED_IN_SCENARIO)

    with TestClient(rig.app) as client:
        reaches(client, "signed_in")

    changes = rig.published("ravis.codex.state_changed")
    assert any(change["to"] == "signed_in" and change["reason_code"] for change in changes)
    assert all(set(change) == {"from", "to", "reason_code"} for change in changes)


def test_both_codex_capabilities_are_declared_with_the_relay_that_serves_them(
    tmp_path: Path,
) -> None:
    """`codex-state.json` → `capabilities`: declared since M29's third increment built the relay,
    and never a readiness check — declared even with Codex switched off."""
    rig = codex_rig(tmp_path, codex_enabled=False)

    with TestClient(rig.app) as client:
        declared = client.get("/ecosystem/capabilities").json()["capabilities"]

    by_id = {capability["id"]: capability for capability in declared}
    shown = json.loads((FIXTURES / "codex-state.json").read_text())["capabilities"]
    for expected in shown:
        assert by_id[expected["id"]]["state"] == "available"
        assert by_id[expected["id"]]["constraints"] == expected["constraints"]
