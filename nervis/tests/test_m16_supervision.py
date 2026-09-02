"""Starting and stopping what NERVIS started, and nothing else (NERVIS.md §12, M16).

**The milestone's gate is a list of things that must not happen**, and so is most
of this file: *start/stop/restart, crash loop, stale PID, PID reuse, partial
start, NERVIS crash/restart and unauthorized-actor tests never affect external
instances.*

The one that would be easiest to get wrong and hardest to notice is PID reuse. A
stop that trusts a bare PID works every time you test it and kills a stranger
once, months later, on a busy machine. So identity here is the PID **and** the
executable **and** the moment the process began, and the test for it fabricates
exactly the case the real world produces rarely.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis import supervision
from nervis.app import create_app
from nervis.config import Settings
from nervis.storage.database import prepare_database

SLEEP = "/bin/sleep"


@pytest.fixture()
def database() -> Any:
    return prepare_database(":memory:")


@pytest.fixture()
def on(database: Any) -> Any:
    supervision.enable(database, True)
    supervision.configure(database, "demo", SLEEP, ["30"])
    return database


@pytest.fixture()
def client(tmp_path: Any) -> Any:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "n.db"), workspace_path=str(tmp_path),
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        yield client


# ── Off, and owned by somebody else ─────────────────────────────────────────


def test_supervision_is_off_until_somebody_turns_it_on(database: Any) -> None:
    """§12: every switch defaults to off, and no mode weakens that."""
    with pytest.raises(supervision.Refused, match="switched off"):
        supervision.may_control(database, "demo", "nervis_managed")


def test_an_external_service_is_never_controlled(on: Any) -> None:
    """The sentence the whole milestone is judged against."""
    with pytest.raises(supervision.Refused, match="external"):
        supervision.may_control(on, "demo", "external")


def test_a_user_managed_service_is_pointed_at_never_touched(on: Any) -> None:
    """§12 gives this mode a capability — *open documented instructions* — and
    takes away the one next to it. The refusal says both halves."""
    with pytest.raises(supervision.Refused, match="may not use it"):
        supervision.may_control(on, "demo", "user_managed")


def test_an_unrecognised_mode_is_refused_rather_than_defaulted(on: Any) -> None:
    with pytest.raises(supervision.Refused, match="no ownership"):
        supervision.may_control(on, "demo", "managed_somehow")


# ── Configuration permits; it does not create ───────────────────────────────


def test_owned_without_an_adapter_is_not_managed(database: Any) -> None:
    """**`nervis_managed` is earned.** A service NERVIS has no way to start is
    reported as user-managed, because claiming otherwise promises a control that
    does not exist."""
    assert supervision.mode_of(database, "demo", "nervis_managed") == "user_managed"
    supervision.configure(database, "demo", SLEEP)
    assert supervision.mode_of(database, "demo", "nervis_managed") == "nervis_managed"


def test_supervision_can_be_withdrawn(database: Any) -> None:
    """Configuring had no inverse, which is the wrong asymmetry for a surface
    whose whole design is about what NERVIS may not touch.

    Found by cleaning up after a test: the adapter could be created and not
    removed without editing the database by hand.
    """
    supervision.configure(database, "demo", SLEEP)
    assert supervision.mode_of(database, "demo", "nervis_managed") == "nervis_managed"
    supervision.configure(database, "demo", "")
    assert supervision.adapter(database, "demo").configured is False
    assert supervision.mode_of(database, "demo", "nervis_managed") == "user_managed"


def test_an_adapter_naming_something_unrunnable_is_refused(database: Any) -> None:
    """A configuration that fails at the worst moment — when somebody is
    restarting a service *because* it is already down."""
    with pytest.raises(supervision.Refused, match="not an executable"):
        supervision.configure(database, "demo", "/does/not/exist")


# ── Identity: the part that matters ─────────────────────────────────────────


def test_a_stale_pid_is_not_a_running_process(on: Any) -> None:
    """§12: *never assume a process exists from a stale PID.*"""
    record = supervision.start(on, "demo", "nervis_managed", SLEEP, ["30"])
    os.kill(record.pid, signal.SIGKILL)
    time.sleep(0.4)
    assert supervision.still_running(record) is False


def test_a_reused_pid_is_not_the_same_process(on: Any) -> None:
    """**The failure that works every time you test it and kills a stranger
    once.** Fabricated here: the same PID, the same executable, a different
    process — which is what a reused PID on a busy machine produces, and what a
    check on PID alone cannot see.
    """
    record = supervision.start(on, "demo", "nervis_managed", SLEEP, ["30"])
    imposter = subprocess.Popen([SLEEP, "30"])  # noqa: S603 - fixed argv, test
    try:
        # The record now points at the imposter's PID, with the create time of
        # the process NERVIS actually started.
        confused = supervision.Launched(
            "demo", imposter.pid, record.executable, record.created_at,
            record.started_at,
        )
        assert supervision.still_running(confused) is False
    finally:
        imposter.kill()
        supervision.stop(on, "demo", "nervis_managed")


def test_a_different_program_on_the_same_pid_is_not_it(on: Any) -> None:
    """The third case: right PID, right start time, wrong program."""
    record = supervision.start(on, "demo", "nervis_managed", SLEEP, ["30"])
    try:
        wrong = supervision.Launched(
            "demo", record.pid, "/bin/echo", record.created_at, record.started_at,
        )
        assert supervision.still_running(wrong) is False
    finally:
        supervision.stop(on, "demo", "nervis_managed")


# ── The verbs ───────────────────────────────────────────────────────────────


def test_start_then_stop(on: Any) -> None:
    record = supervision.start(on, "demo", "nervis_managed", SLEEP, ["30"])
    assert supervision.still_running(record)
    assert supervision.stop(on, "demo", "nervis_managed") == "stopped"
    assert supervision.still_running(record) is False


def test_a_second_start_does_not_make_a_second_process(on: Any) -> None:
    """§12's gate: *a restart does not create a duplicate process.* Starting is
    not idempotent-by-overwriting — an existing live record refuses."""
    supervision.start(on, "demo", "nervis_managed", SLEEP, ["30"])
    try:
        with pytest.raises(supervision.Refused, match="already running"):
            supervision.start(on, "demo", "nervis_managed", SLEEP, ["30"])
    finally:
        supervision.stop(on, "demo", "nervis_managed")


def test_restart_leaves_exactly_one_process(on: Any) -> None:
    first = supervision.start(on, "demo", "nervis_managed", SLEEP, ["30"])
    second = supervision.restart(on, "demo", "nervis_managed", SLEEP, ["30"])
    try:
        assert second.pid != first.pid
        assert supervision.still_running(first) is False
        assert supervision.still_running(second) is True
    finally:
        supervision.stop(on, "demo", "nervis_managed")


def test_stopping_something_nervis_did_not_start_is_refused(on: Any) -> None:
    """The negative the whole design turns on. A process may well be running;
    NERVIS has no record of launching it, so it will not signal it."""
    with pytest.raises(supervision.Refused, match="did not start"):
        supervision.stop(on, "demo", "nervis_managed")


def test_a_record_whose_process_vanished_stops_cleanly(on: Any) -> None:
    """Crash recovery: the service died on its own, and stopping it is not an
    error — there is simply nothing left to signal."""
    record = supervision.start(on, "demo", "nervis_managed", SLEEP, ["30"])
    os.kill(record.pid, signal.SIGKILL)
    time.sleep(0.4)
    assert supervision.stop(on, "demo", "nervis_managed") == "already gone"


# ── The circuit ─────────────────────────────────────────────────────────────


def test_repeated_failures_open_the_circuit(on: Any) -> None:
    for _ in range(supervision.FAILURE_LIMIT):
        supervision.note_failure(on, "demo", "would not start")
    with pytest.raises(supervision.Refused, match="circuit is open"):
        supervision.may_control(on, "demo", "nervis_managed")


def test_only_an_operator_clears_the_circuit(on: Any) -> None:
    """§12: open *until an operator with control authority clears it, so a
    crash-loop cannot be re-entered by retry.* A success must not clear it,
    because the retry that succeeded is the retry."""
    for _ in range(supervision.FAILURE_LIMIT):
        supervision.note_failure(on, "demo", "would not start")
    supervision._forget_failures(on, "demo")
    assert supervision.circuit(on, "demo")["opened_at"], "a success reopened the door"
    supervision.clear_circuit(on, "demo")
    supervision.may_control(on, "demo", "nervis_managed")


# ── A restart of NERVIS is not amnesia ──────────────────────────────────────


def test_what_nervis_started_survives_its_own_restart(tmp_path: Any) -> None:
    """§12 is only enforceable if what NERVIS started outlives NERVIS.

    Without the record a restarted NERVIS either adopts processes it cannot
    prove it launched, or abandons ones it did — and the first is the dangerous
    half.
    """
    path = str(tmp_path / "n.db")
    first = prepare_database(path)
    supervision.enable(first, True)
    supervision.configure(first, "demo", SLEEP, ["30"])
    record = supervision.start(first, "demo", "nervis_managed", SLEEP, ["30"])
    try:
        second = prepare_database(path)
        remembered = supervision.launched(second, "demo")
        assert remembered is not None
        assert remembered.pid == record.pid
        assert remembered.created_at == record.created_at
        assert supervision.still_running(remembered)
    finally:
        os.kill(record.pid, signal.SIGKILL)


# ── The closed surface ──────────────────────────────────────────────────────


def test_the_operation_set_is_three_verbs(client: TestClient) -> None:
    """§12: *no free-form command, script or process-selection path.*"""
    assert supervision.OPERATIONS == ("start", "stop", "restart")
    assert client.get("/api/v1/supervision").json()["operations"] == [
        "start", "stop", "restart",
    ]


def test_an_operation_outside_the_set_does_not_exist(client: TestClient) -> None:
    """404, not 422. §12 asks for *does not exist rather than failing at
    validation* — a message naming the wrong field is a map of the surface."""
    for invented in ("kill", "exec", "run", "obliterate"):
        assert client.post(f"/api/v1/supervision/ravis/{invented}").status_code == 404


def test_an_unregistered_service_does_not_exist(client: TestClient) -> None:
    assert client.post("/api/v1/supervision/nope/stop").status_code == 404


def test_the_api_refuses_an_external_service(client: TestClient) -> None:
    client.post("/api/v1/supervision/enable", json={"enabled": True})
    answer = client.post("/api/v1/supervision/ravis/stop")
    assert answer.status_code == 409
    assert "external" in answer.json()["error"]["message"]


def test_the_switch_is_off_on_a_fresh_installation(client: TestClient) -> None:
    body = client.get("/api/v1/supervision").json()
    assert body["enabled"] is False
    assert all(s["why_not"] == "supervision is switched off" for s in body["services"])


# ── The child's environment ─────────────────────────────────────────────────


def test_a_supervised_process_does_not_inherit_everything(monkeypatch: Any) -> None:
    """§12.1: an admin credential in a control plane is a control plane whose
    compromise is total. A child inheriting NERVIS's environment would hold
    every peer credential NERVIS holds."""
    monkeypatch.setenv("RAVIS_ADMIN_CREDENTIAL", "hunter2")
    handed = supervision._environment()
    assert "RAVIS_ADMIN_CREDENTIAL" not in handed
    assert set(handed) <= set(supervision.ENV_ALLOWED)


def test_the_advertisement_can_be_switched_off_again(database: Any) -> None:
    """It only ever turned the capability on.

    Revoking the last adapter left `nervis.supervision@1` advertising a control
    the machine no longer had — a conditional capability that can only move one
    way goes stale in exactly the direction that matters.
    """
    from nervis.ecosystem import advertise_supervision, nervis_surface

    surface = nervis_surface("nervis-test", "machine-test", database)
    advertise_supervision(surface, 1)
    assert surface.declared["nervis.supervision@1"].state == "available"
    advertise_supervision(surface, 0)
    assert surface.declared["nervis.supervision@1"].state == "unavailable"
    assert "nothing it owns" in surface.declared["nervis.supervision@1"].reason
