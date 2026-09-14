"""A whole RAVIS with its Codex service, running against the fake Codex programs (runbook §7).

The service-level tests start a real application — `create_app` and its lifespan, through
`TestClient` — whose Codex executable is the fake `codex` script, whose app-server is
`fake_codex_app_server.py`, and whose `codesign` is the fake one. Nothing in RAVIS is patched:
only the settings name the fakes, and the service's clocks are shortened so a restart backoff, a
health probe or a ten-minute sign-in expiry take a fraction of a second.

`CodexRig` holds what a test needs to drive and observe it: the app, the service, the fake
app-server's folder (to send it commands and read what RAVIS sent), the events RAVIS published,
and credentials for each kind of caller the contract names (`conventions.json`, `callers`).
"""

from __future__ import annotations

import json
import os
import socket
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from tests.codex_fakes import (
    EXPERIMENTAL_SCHEMA,
    FakeAppServer,
    FakeCodesign,
    FakeCodex,
    pin_entry,
    write_pin,
)

from ravis.agent.cleanup import CleanupTimings
from ravis.agent.session import SessionTimings
from ravis.agent.sessions import AgentTimings
from ravis.app import create_app
from ravis.codex.acceptance import HandshakeTimings
from ravis.codex.account import account_fingerprint
from ravis.codex.calibration.harness import CalibrationTimings
from ravis.codex.reprove import ReproofTimings
from ravis.codex.rpc import RpcTimings
from ravis.codex.service import CodexService, ServiceTimings
from ravis.codex.supervisor import SupervisorTimings
from ravis.config import Settings
from ravis.credentials import config_directory

FAST = ServiceTimings(
    recheck_seconds=0.3,
    housekeeping_seconds=0.05,
    account_read_seconds=2.0,
    rate_limits_read_seconds=2.0,
    model_list_seconds=2.0,
    login_seconds=1.0,
    usage_after_turn_seconds=0.2,
    consumer_stall_seconds=5.0,
    skills_seconds=2.0,
    sign_in_lifetime_seconds=600.0,
    supervisor=SupervisorTimings(
        initialize_seconds=3.0,
        health_interval_seconds=0.2,
        health_timeout_seconds=0.3,
        restart_backoff_seconds=(0.05, 0.1, 0.2, 0.3),
        failure_window_seconds=60.0,
        watch_tick_seconds=0.05,
        swap_waits=(1.0, 0.5),
        shutdown_waits=(1.0, 0.5),
        failure_waits=(0.3, 0.3),
        rpc=RpcTimings(write_seconds=0.5, hung_after_seconds=1.0,
                       overload_backoff_seconds=(0.01, 0.01, 0.01)),
    ),
    handshake=HandshakeTimings(initialize_seconds=3.0, call_seconds=3.0, exit_seconds=1.0),
    reproof=ReproofTimings(
        exec_seconds=3.0, thread_start_seconds=3.0, turn_start_seconds=3.0,
        interrupt_seconds=1.0, settle_seconds=0.5, time_cap_seconds=3.0,
    ),
    calibration=CalibrationTimings(
        request_seconds=3.0, turn_cap_seconds=8.0, interrupt_seconds=1.0, settle_seconds=1.5,
        stop_cap_seconds=2.0, processes_start_seconds=8.0, term_wait_seconds=0.2,
        confirm_seconds=2.0, heartbeat_seconds=0.2, unload_seconds=1.0,
    ),
    agents=AgentTimings(
        tick_seconds=0.05, heartbeat_seconds=0.2, stream_heartbeat_seconds=0.3,
        session=SessionTimings(
            thread_start_seconds=3.0, turn_start_seconds=3.0, steer_seconds=1.0,
            interrupt_seconds=1.0, interrupt_wait_seconds=1.0, archive_seconds=2.0,
            turns_list_seconds=1.0, file_item_wait_seconds=0.3,
            unsubscribe_seconds=1.0, loaded_list_seconds=1.0, unload_poll_seconds=0.05,
            unload_cap_seconds=5.0,
            cleanup=CleanupTimings(terminals_seconds=1.0, term_wait_seconds=0.2,
                                   confirm_seconds=1.5),
        ),
    ),
)

SIGNED_IN = {"type": "chatgpt", "email": "owner@example.com", "planType": "plus"}
#: The fixture's allowance: 38% of the 5-hour window and 20% of the week used.
ALLOWANCE = {
    "limitId": "codex",
    "primary": {"usedPercent": 38, "windowDurationMins": 300, "resetsAt": 4102444800},
    "secondary": {"usedPercent": 20, "windowDurationMins": 10080, "resetsAt": 4103049600},
    "credits": {"hasCredits": False, "unlimited": False, "balance": None},
    "individualLimit": None,
    "rateLimitReachedType": None,
    "spendControlReached": None,
}
#: A stand-in for the profile calibration will pin: the fake app-server only checks it is named.
PROFILE = {
    "name": "clarvis_run",
    "flags": ["-c", 'permissions.clarvis_run.deny=["{reproof_decoys}"]'],
}
SECRETS = {
    "admin.launcher": "launcher-admin-secret",
    "admin.owner_cli": "owner-cli-secret",
    "client.nervis": "nervis-client-secret",
    "client.clarvis": "clarvis-client-secret",
    "client.other": "other-client-secret",
}


@dataclass
class CodexRig:
    app: Any
    service: CodexService
    server: FakeAppServer
    codex: FakeCodex
    folder: Path
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def caller(self, name: str) -> dict[str, str]:
        """The `Authorization` header for a caller the contract names, or none for anonymous."""
        return {} if name == "anonymous" else {"Authorization": f"Bearer {SECRETS[name]}"}

    def supervised_starts(self) -> list[dict[str, Any]]:
        """Starts of RAVIS's own process, in its Codex home: not a version check's throwaway."""
        home = str(self.folder / "codex-home")
        return [start for start in self.server.starts() if start["env"].get("CODEX_HOME") == home]

    def published(self, event: str) -> list[dict[str, Any]]:
        return [data for name, data in self.events if name == event]

    def record(self) -> dict[str, Any]:
        """RAVIS's `codex-state.json`, as written."""
        path = config_directory() / "codex-state.json"
        return json.loads(path.read_text()) if path.exists() else {}


def confirmed_record(email: str | None = "owner@example.com", plan: str = "plus") -> None:
    """Write RAVIS's record as if the owner had already confirmed this account."""
    fingerprint, strength = account_fingerprint("chatgpt", email, None, plan)
    path = config_directory() / "codex-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "format": 1,
        "confirmed": {
            "fingerprint": fingerprint, "strength": strength, "plan": plan,
            "confirmed_at": "2026-09-13T00:00:00Z",
        },
        "signed_out_on_purpose": False, "accepted": [], "proven": {},
        "sign_in_started_at": None, "reproof": None,
    }))


def codex_rig(
    tmp_path: Path,
    *,
    scenario: dict[str, Any] | None = None,
    tested: bool = True,
    proven: bool = False,
    profile: dict[str, Any] | None = None,
    timings: ServiceTimings = FAST,
    sign_in_ports: tuple[int, ...] | None = None,
    extra_entries: tuple[dict[str, Any], ...] = (),
    app_server: bool = True,
    experimental_schema: Mapping[str, str] = EXPERIMENTAL_SCHEMA,
    agent_clock: Callable[[], float] = time.monotonic,
    **settings: Any,
) -> CodexRig:
    """A RAVIS whose Codex is the fakes. `tested=False` pins nothing, so the build is untested.

    `app_server=False` installs a `codex` that can't run an app-server at all;
    `experimental_schema` changes what the installed build's `--experimental` tree holds.
    """
    server = FakeAppServer.create(tmp_path / "app-server", **(scenario or {}))
    fake = FakeCodex.install(
        tmp_path / "bin" / "codex",
        app_server=server if app_server else None,
        experimental_schema=experimental_schema,
    )
    codesign = FakeCodesign.install(tmp_path / "tools" / "codesign")
    entries = [*([pin_entry(fake, proven=proven)] if tested else []), *extra_entries]
    pin = write_pin(tmp_path / "pin.json", *entries, file_rules_profile=profile)
    coding = Path(os.path.realpath(tmp_path)) / "coding"
    configured = Settings(
        database_path=":memory:",
        _env_file=None,  # type: ignore[call-arg]
        **{
            "codex_enabled": True,
            "codex_executable": str(fake.path),
            "codex_home": str(tmp_path / "codex-home"),
            # A coding folder of the test's own, holding the NERVIS skills folder RAVIS makes when
            # Codex starts, as the launcher names the owner's (`agent/skills.py`); a test names
            # others to try what RAVIS does with them.
            "agent_allowed_roots": [str(coding)],
            "codex_skills_folder": str(coding / "NERVIS workspace" / "clarvis" / "skills"),
            **settings,
        },
    )
    app = create_app(configured)
    rig = CodexRig(app=app, service=None, server=server, codex=fake, folder=tmp_path)  # type: ignore[arg-type]

    def recorded(event: str, *, trace_id: str, data: Any = None, **_: Any) -> None:
        assert trace_id, f"{event} was published without a trace id"
        rig.events.append((event, dict(data or {})))

    app.app.state.events.emit = recorded
    rig.service = CodexService(
        configured,
        emit=recorded,
        codesign=str(codesign.path),
        pin=pin,
        timings=timings,
        # Never the real 1455 and 1457 unless a test says so: the ChatGPT app's own Codex signs
        # in on them, and a test must not take them from it even for a moment.
        sign_in_ports=sign_in_ports or free_ports(2),
        # Calibration's K2 writes (or is refused writing) into `/tmp`; a test's stays its own.
        calibration_slash_tmp=tmp_path / "slash-tmp",
        # Clarvis's Codex tasks keep their rows in the app's own database, as in RAVIS.
        database=app.app.state.database,
        agent_clock=agent_clock,
    )
    app.app.state.codex_service = rig.service
    app.app.state.codex = rig.service.runtime
    for name, secret in SECRETS.items():
        app.app.state.credentials.store(name, secret)
    return rig


def free_ports(count: int) -> tuple[int, ...]:
    """Ports nothing listens on right now, found by binding to port 0 and letting go."""
    ports = []
    for _ in range(count):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            ports.append(probe.getsockname()[1])
    return tuple(ports)


def held_ports(count: int) -> tuple[list[socket.socket], tuple[int, ...]]:
    """Ports held by a listening socket each, as another program's sign-in would hold them."""
    holders = []
    for _ in range(count):
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        holders.append(holder)
    return holders, tuple(holder.getsockname()[1] for holder in holders)


def eventually(condition: Callable[[], Any], seconds: float = 10.0, what: str = "") -> Any:
    """Poll `condition` until it returns something truthy; fail after `seconds`."""
    deadline = time.monotonic() + seconds
    while True:
        found = condition()
        if found:
            return found
        assert time.monotonic() < deadline, f"never happened: {what or condition}"
        time.sleep(0.02)


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "relay-contract"
ENVELOPE_FIELDS = {"code", "message", "retryable", "details", "request_id", "trace_id"}


def fixture_response(document: str, method: str, path: str, name: str) -> dict[str, Any]:
    """One example response from a contract fixture, by its route and its name."""
    routes = json.loads((FIXTURES / document).read_text())["routes"]
    route = next(r for r in routes if (r["method"], r["path"]) == (method, path))
    return next(case["response"] for case in route["examples"] if case["name"] == name)


def call(
    client: TestClient,
    rig: CodexRig,
    method: str,
    path: str,
    caller: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    extra = {} if body is None else {"json": body}
    return client.request(
        method, path, headers={**rig.caller(caller), **(headers or {})}, **extra
    )


def refused_as(response: Any, expected: dict[str, Any], *, same_message: bool = True) -> None:
    """The contract's refusal: its status, envelope, code and retry flag — and its fixed words."""
    assert response.status_code == expected["status"], response.text
    error, fixture = response.json()["error"], expected["body"]["error"]
    assert set(error) == ENVELOPE_FIELDS
    assert (error["code"], error["retryable"]) == (fixture["code"], fixture["retryable"])
    if same_message:
        assert error["message"] == fixture["message"]


def state_of(client: TestClient) -> dict[str, Any]:
    """`GET /api/v1/codex`, read as NERVIS reads it: a named caller.

    **Named, not anonymous** (found 13 September 2026). `eventually` polls every 20 ms, and
    RAVIS gives an anonymous caller 60 requests a minute. A test that waits through five
    scripted failed starts and their back-off crossed that in about a second, and the read
    answered 429 RATE_LIMITED. That made `test_five_failures_in_the_window_stop_the_restarts`
    fail intermittently, while the endpoint itself was answering correctly. The launcher and
    NERVIS read this with NERVIS's credential, which has the named caller's allowance, so the
    rig polls the same way. Anonymous reads are tested where they're the point.
    """
    response = client.get(
        "/api/v1/codex", headers={"Authorization": f"Bearer {SECRETS['client.nervis']}"}
    )
    assert response.status_code == 200, (response.status_code, response.text)
    return response.json()  # type: ignore[no-any-return]


def reaches(client: TestClient, state: str, seconds: float = 10.0) -> dict[str, Any]:
    """Wait for `GET /api/v1/codex` to report `state`, and return that body."""
    seen: list[str] = []

    def reached() -> dict[str, Any] | None:
        body = state_of(client)
        seen.append(body["state"])
        return body if body["state"] == state else None

    try:
        return eventually(reached, seconds)  # type: ignore[no-any-return]
    except AssertionError:
        raise AssertionError(f"never reached {state}; saw {sorted(set(seen))}") from None
