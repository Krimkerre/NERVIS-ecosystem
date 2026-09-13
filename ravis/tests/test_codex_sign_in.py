"""Signing RAVIS's Codex in and out, and confirming its account (design §3.4, §4.6; §15.1.2).

The routes' shapes, codes and fixed messages are `tests/fixtures/relay-contract/codex-admin.json`.
What these tests hold them to, against the fake app-server:

- **Admin only**, for every sign-in, account and version route: anonymous and client credentials —
  NERVIS's GET relay credential and Clarvis's included — get 403 `FORBIDDEN`. This is the owner's
  credentials-screen button's path too: NERVIS's control route forwards with its admin credential.
- **The browser sign-in**: started (202), already waiting (200, the same body), completed as the
  account RAVIS then confirms, failed in Codex's own words, expired after its ten minutes,
  cancelled; the page's address only on the admin route, never in `GET /api/v1/codex`.
- **Refused plainly**: both sign-in ports held (the ChatGPT app's own Codex), Codex saying "already
  in use", Codex not answering (503, retryable), already signed in, a turn running, an untested
  build, Codex not available.
- **A RAVIS restart during a sign-in** is reported in the contract's words.
- **The account**: signed out on purpose reads `signed_out`; gone without RAVIS ending it reads
  `sign_in_expired`; a different account pauses as `account_changed` until the owner confirms the
  hint the dashboard showed; the fingerprint is the email's, else the account id's; the email itself
  is never stored or served.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.codex_rig import (
    ALLOWANCE,
    FAST,
    SIGNED_IN,
    CodexRig,
    call,
    codex_rig,
    confirmed_record,
    eventually,
    fixture_response,
    held_ports,
    reaches,
    refused_as,
    state_of,
)

SIGN_IN = "/api/v1/codex/sign-in"
BROWSER = {"method": "browser"}
SIGNED_IN_SCENARIO = {"account": SIGNED_IN, "rate_limits": ALLOWANCE}
TURN = {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "inProgress", "items": []}}
IDLE_SIGN_IN = {"state": "idle", "started_at": None, "expires_at": None, "error": None}
ADMIN_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", SIGN_IN, BROWSER),
    ("GET", SIGN_IN, None),
    ("DELETE", SIGN_IN, None),
    ("POST", "/api/v1/codex/sign-out", {}),
    ("POST", "/api/v1/codex/account/confirm", {"email_hint": "o…@example.com"}),
    ("GET", "/api/v1/codex/version-check", None),
    ("POST", "/api/v1/codex/accept-version", {"sha256": "0" * 64}),
    ("DELETE", "/api/v1/codex/accept-version/" + "0" * 64, None),
]


def admin_example(method: str, path: str, name: str) -> dict[str, Any]:
    return fixture_response("codex-admin.json", method, path, name)


def sign_in(client: TestClient, rig: CodexRig) -> Any:
    return call(client, rig, "POST", SIGN_IN, "admin.launcher", BROWSER)


def sign_in_failed(client: TestClient) -> dict[str, Any]:
    """Wait for `GET /api/v1/codex` to say the sign-in failed, and return that body."""
    return eventually(  # type: ignore[no-any-return]
        lambda: (found := state_of(client))["sign_in"]["state"] == "failed" and found,
        what="the sign-in to end as failed",
    )


# ── Who may call ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("caller", ["anonymous", "client.nervis", "client.clarvis"])
def test_every_sign_in_account_and_version_route_needs_an_admin_credential(
    tmp_path: Path, caller: str
) -> None:
    rig = codex_rig(tmp_path, codex_enabled=False)
    forbidden = admin_example("POST", SIGN_IN, "a client credential")

    with TestClient(rig.app) as client:
        answers = [
            call(client, rig, method, path, caller, body) for method, path, body in ADMIN_ROUTES
        ]

    for answer in answers:
        refused_as(answer, forbidden)


def test_a_sign_in_method_other_than_browser_has_its_own_422(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, codex_enabled=False)

    with TestClient(rig.app) as client:
        answer = call(client, rig, "POST", SIGN_IN, "admin.launcher", {"method": "device_code"})

    refused_as(answer, admin_example("POST", SIGN_IN, "a method other than browser"),
               same_message=False)


# ── The browser sign-in ─────────────────────────────────────────────────────


def test_a_sign_in_starts_waits_and_completes_as_the_account_ravis_confirms(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, proven=True, scenario={"account": None, "rate_limits": ALLOWANCE})

    with TestClient(rig.app) as client:
        reaches(client, "signed_out")
        started, again = sign_in(client, rig), sign_in(client, rig)
        public = state_of(client)
        reopened = call(client, rig, "GET", SIGN_IN, "admin.launcher").json()
        rig.server.send("login_complete", success=True, account=SIGNED_IN)
        body = eventually(
            lambda: (found := state_of(client))["state"] == "signed_in"
            and found["usage"]["known"] and found,
            what="signed in with the allowance read",
        )

    fixture = admin_example("POST", SIGN_IN, "started")
    assert (started.status_code, again.status_code) == (202, 200)
    assert again.json() == started.json() == reopened
    waiting = started.json()["sign_in"]
    assert set(waiting) == set(fixture["body"]["sign_in"])
    assert waiting["state"] == "waiting_for_browser"
    assert waiting["auth_url"].startswith("https://auth.openai.com/")
    assert waiting["callback_port"] == 1455
    lasted = _when(waiting["expires_at"]) - _when(waiting["started_at"])
    assert lasted.total_seconds() == 600
    assert public["sign_in"] == {
        "state": "waiting_for_browser", "started_at": waiting["started_at"],
        "expires_at": waiting["expires_at"], "error": None,
    }
    assert "auth.openai.com" not in json.dumps(public)
    assert body["account"]["email_hint"] == "o…@example.com"
    assert body["account"]["fingerprint_matches"] is True
    assert body["sign_in"]["state"] == "idle"
    record = rig.record()
    expected = hashlib.sha256(b"ravis-codex-account:owner@example.com").hexdigest()
    assert record["confirmed"]["fingerprint"] == expected
    assert record["sign_in_started_at"] is None
    assert "owner@example.com" not in json.dumps(record)
    assert len(rig.published("ravis.codex.sign_in_started")) == 1


def test_a_sign_in_codex_reports_as_failed_is_shown_and_can_start_again(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, proven=True, scenario={"account": None})

    with TestClient(rig.app) as client:
        reaches(client, "signed_out")
        assert sign_in(client, rig).status_code == 202
        rig.server.send("login_complete", success=False, error="Login server error: denied")
        failed = sign_in_failed(client)
        retried = sign_in(client, rig)

    assert failed["sign_in"]["error"] == "Login server error: denied"
    assert failed["state"] == "signed_out"
    assert retried.status_code == 202


def test_a_sign_in_left_past_its_ten_minutes_ends_and_codex_is_told(tmp_path: Path) -> None:
    short = dataclasses.replace(FAST, sign_in_lifetime_seconds=0.3)
    rig = codex_rig(tmp_path, proven=True, scenario={"account": None}, timings=short)

    with TestClient(rig.app) as client:
        reaches(client, "signed_out")
        assert sign_in(client, rig).status_code == 202
        ended = sign_in_failed(client)
        eventually(lambda: rig.server.received("account/login/cancel"), what="Codex told")

    assert "expired after 10 minutes" in ended["sign_in"]["error"]
    assert rig.record()["sign_in_started_at"] is None


def test_codex_giving_up_on_a_sign_in_is_reported_in_its_own_words(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, proven=True, scenario={"account": None, "login_expires_after": 0.2})

    with TestClient(rig.app) as client:
        reaches(client, "signed_out")
        assert sign_in(client, rig).status_code == 202
        ended = sign_in_failed(client)

    assert ended["sign_in"]["error"] == "Login timed out"


def test_cancelling_ends_the_sign_in_and_its_not_completed_notice_is_no_failure(
    tmp_path: Path,
) -> None:
    rig = codex_rig(tmp_path, proven=True, scenario={"account": None})

    with TestClient(rig.app) as client:
        reaches(client, "signed_out")
        assert sign_in(client, rig).status_code == 202
        cancelled = call(client, rig, "DELETE", SIGN_IN, "admin.launcher")
        eventually(lambda: rig.server.received("account/login/cancel"))
        time.sleep(0.4)
        after = state_of(client)
        nothing_waiting = call(client, rig, "DELETE", SIGN_IN, "admin.launcher")

    assert (cancelled.status_code, cancelled.json()) == (200, {"cancelled": True})
    assert after["sign_in"] == IDLE_SIGN_IN
    assert nothing_waiting.json() == {"cancelled": False}
    assert len(rig.published("ravis.codex.sign_in_cancelled")) == 2


# ── Refused plainly ─────────────────────────────────────────────────────────


def test_both_sign_in_ports_held_is_refused_before_codex_is_asked(tmp_path: Path) -> None:
    holders, ports = held_ports(2)
    rig = codex_rig(tmp_path, proven=True, scenario={"account": None}, sign_in_ports=ports)

    try:
        with TestClient(rig.app) as client:
            reaches(client, "signed_out")
            answer = sign_in(client, rig)
    finally:
        for holder in holders:
            holder.close()

    refused_as(answer, admin_example("POST", SIGN_IN, "both sign-in ports held"))
    assert rig.server.received("account/login/start") == []


def test_one_sign_in_port_held_still_signs_in_on_the_other(tmp_path: Path) -> None:
    holders, held = held_ports(1)
    free_holders, free = held_ports(1)
    free_holders[0].close()
    rig = codex_rig(
        tmp_path, proven=True, scenario={"account": None, "login_port": free[0]},
        sign_in_ports=(held[0], free[0]),
    )

    try:
        with TestClient(rig.app) as client:
            reaches(client, "signed_out")
            answer = sign_in(client, rig)
    finally:
        holders[0].close()

    assert answer.status_code == 202
    assert answer.json()["sign_in"]["callback_port"] == free[0]


def test_codex_saying_its_port_is_in_use_is_the_same_plain_refusal(tmp_path: Path) -> None:
    rig = codex_rig(
        tmp_path, proven=True,
        scenario={"account": None, "login_error": "Port 127.0.0.1:1455 is already in use"},
    )

    with TestClient(rig.app) as client:
        reaches(client, "signed_out")
        answer = sign_in(client, rig)

    refused_as(answer, admin_example("POST", SIGN_IN, "both sign-in ports held"))


def test_a_sign_in_codex_never_answers_is_a_retryable_503(tmp_path: Path) -> None:
    rig = codex_rig(
        tmp_path, proven=True,
        scenario={"account": None, "silent_methods": ["account/login/start"]},
    )

    with TestClient(rig.app) as client:
        reaches(client, "signed_out")
        answer = sign_in(client, rig)

    refused_as(answer, admin_example("POST", SIGN_IN, "account/login/start timed out after 10 s"))


def test_signing_in_while_signed_in_is_refused(tmp_path: Path) -> None:
    confirmed_record()
    rig = codex_rig(tmp_path, proven=True, scenario=SIGNED_IN_SCENARIO)

    with TestClient(rig.app) as client:
        reaches(client, "signed_in")
        answer = sign_in(client, rig)

    refused_as(answer, admin_example("POST", SIGN_IN, "already signed in"))


def test_sign_in_and_sign_out_both_wait_for_a_running_turn(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, proven=True, scenario={"account": None})
    confirmed_record()
    signed_in = codex_rig(tmp_path / "second", proven=True, scenario=SIGNED_IN_SCENARIO)

    with TestClient(rig.app) as client:
        reaches(client, "sign_in_expired")
        rig.server.send("notify", method="turn/started", params=TURN)
        eventually(lambda: state_of(client)["runtime"]["process"]["active_turns"] == 1)
        signing_in = sign_in(client, rig)
    with TestClient(signed_in.app) as client:
        reaches(client, "signed_in")
        signed_in.server.send("notify", method="turn/started", params=TURN)
        eventually(lambda: state_of(client)["runtime"]["process"]["active_turns"] == 1)
        signing_out = call(
            client, signed_in, "POST", "/api/v1/codex/sign-out", "admin.launcher", {}
        )

    refused_as(signing_in, admin_example("POST", SIGN_IN, "a Codex turn is active"))
    refused_as(
        signing_out,
        admin_example("POST", "/api/v1/codex/sign-out",
                      "a turn is active or a settle is pending; idle sessions don't block"),
    )


def test_an_untested_build_refuses_sign_in_as_untested(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, tested=False)

    with TestClient(rig.app) as client:
        reaches(client, "untested_version")
        answer = sign_in(client, rig)

    refused_as(answer, admin_example("POST", SIGN_IN, "an untested version"), same_message=False)


@pytest.mark.parametrize("setup", ["switched_off", "keeps_crashing"])
def test_codex_not_available_or_down_refuses_sign_in(tmp_path: Path, setup: str) -> None:
    if setup == "switched_off":
        rig, state = codex_rig(tmp_path, codex_enabled=False), "not_available"
    else:
        rig, state = codex_rig(tmp_path, proven=True, scenario={"exit_on_start": 1}), "runtime_down"

    with TestClient(rig.app) as client:
        reaches(client, state)
        answer = sign_in(client, rig)

    refused_as(
        answer, admin_example("POST", SIGN_IN, "not installed, not available or restarting"),
        same_message=False,
    )


def test_a_ravis_restart_during_a_sign_in_is_reported_in_the_contracts_words(
    tmp_path: Path,
) -> None:
    first = codex_rig(tmp_path / "first", proven=True, scenario={"account": None})
    with TestClient(first.app) as client:
        reaches(client, "signed_out")
        assert sign_in(client, first).status_code == 202

    second = codex_rig(tmp_path / "second", proven=True, scenario={"account": None})
    with TestClient(second.app) as client:
        reopened = call(client, second, "GET", SIGN_IN, "admin.launcher").json()
        public = reaches(client, "signed_out")["sign_in"]

    fixture = admin_example("GET", SIGN_IN, "after RAVIS restarted during a sign-in")
    assert reopened == fixture["body"]
    assert public == fixture["body"]["sign_in"]


def test_a_crash_during_a_sign_in_is_reported_and_a_new_one_can_start(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, proven=True, scenario={"account": None})

    with TestClient(rig.app) as client:
        reaches(client, "signed_out")
        assert sign_in(client, rig).status_code == 202
        rig.server.send("crash", status=1)
        lost = sign_in_failed(client)
        reaches(client, "signed_out")
        retried = sign_in(client, rig)

    assert "stopped during sign-in" in lost["sign_in"]["error"]
    assert retried.status_code == 202


# ── The account ─────────────────────────────────────────────────────────────


def test_signing_out_forgets_the_account_and_answers_the_full_state(tmp_path: Path) -> None:
    confirmed_record()
    rig = codex_rig(tmp_path, proven=True, scenario=SIGNED_IN_SCENARIO)

    with TestClient(rig.app) as client:
        reaches(client, "signed_in")
        answer = call(client, rig, "POST", "/api/v1/codex/sign-out", "admin.launcher", {})
        later = state_of(client)

    fixture = admin_example("POST", "/api/v1/codex/sign-out", "signed out: the full GET body")
    assert answer.status_code == 200
    assert answer.json()["state"] == later["state"] == "signed_out"
    assert set(answer.json()) == set(fixture["body"])
    assert rig.server.received("account/logout")
    record = rig.record()
    assert (record["confirmed"], record["signed_out_on_purpose"]) == (None, True)
    assert len(rig.published("ravis.codex.signed_out")) == 1


def test_an_account_that_goes_away_without_ravis_reads_sign_in_expired(tmp_path: Path) -> None:
    confirmed_record()
    rig = codex_rig(tmp_path, proven=True, scenario=SIGNED_IN_SCENARIO)

    with TestClient(rig.app) as client:
        reaches(client, "signed_in")
        rig.server.send("set_account", account=None)
        body = reaches(client, "sign_in_expired")

    assert body["account"] is None
    assert body["usage"]["known"] is False


def test_a_different_account_pauses_until_the_owner_confirms_its_hint(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    confirmed_record()
    rig = codex_rig(tmp_path, proven=True, scenario=SIGNED_IN_SCENARIO)
    other = {"type": "chatgpt", "email": "someone@elsewhere.org", "planType": "pro"}
    confirm = "/api/v1/codex/account/confirm"

    with TestClient(rig.app) as client:
        reaches(client, "signed_in")
        rig.server.send("set_account", account=other)
        changed = reaches(client, "account_changed")
        stale_hint = call(
            client, rig, "POST", confirm, "admin.launcher", {"email_hint": "o…@example.com"}
        )
        no_hint = call(client, rig, "POST", confirm, "admin.launcher", {})
        confirmed = call(
            client, rig, "POST", confirm, "admin.launcher", {"email_hint": "s…@elsewhere.org"}
        )

    assert changed["account"]["fingerprint_matches"] is False
    assert changed["account"]["email_hint"] == "s…@elsewhere.org"
    warnings = [record.getMessage() for record in caplog.records]
    assert any(
        "different account from the one confirmed at 2026-09-13T00:00:00Z" in message
        for message in warnings
    )
    assert not any("elsewhere.org" in message for message in warnings)
    moved = admin_example("POST", confirm, "the account moved again before the confirmation")
    refused_as(stale_hint, moved)
    refused_as(no_hint, admin_example("POST", confirm, "a body without email_hint"))
    assert (confirmed.status_code, confirmed.json()["state"]) == (200, "signed_in")
    expected = hashlib.sha256(b"ravis-codex-account:someone@elsewhere.org").hexdigest()
    assert rig.record()["confirmed"]["fingerprint"] == expected
    assert len(rig.published("ravis.codex.account_confirmed")) == 1


def test_an_account_found_at_start_that_ravis_never_confirmed_must_be_confirmed(
    tmp_path: Path,
) -> None:
    rig = codex_rig(tmp_path, proven=True, scenario=SIGNED_IN_SCENARIO)

    with TestClient(rig.app) as client:
        body = reaches(client, "account_changed")

    assert body["account"]["fingerprint_matches"] is False


def test_an_account_without_an_email_is_fingerprinted_by_its_account_id(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, proven=True, scenario={"account": None, "rate_limits": ALLOWANCE})
    no_email = {"type": "chatgpt", "email": None, "planType": "plus"}

    with TestClient(rig.app) as client:
        reaches(client, "signed_out")
        assert sign_in(client, rig).status_code == 202
        rig.server.send("login_complete", success=True, account=no_email, account_id="acct-9")
        body = reaches(client, "signed_in")

    assert body["account"]["email_hint"] is None
    assert body["account"]["fingerprint"] == "strong"
    assert rig.record()["confirmed"]["fingerprint"] == hashlib.sha256(b"acct-9").hexdigest()


def _when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
