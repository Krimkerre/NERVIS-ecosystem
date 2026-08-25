"""M0 — the foundation, and the exit criteria it has to satisfy.

The criteria, from NERVIS.md §21:

    `nervis serve` starts; the browser opens the dashboard; `nervis doctor`
    works; the database migrates cleanly; **no external service is required**;
    NERVIS answers its own identity, health, capabilities and version, and MEP
    conformance fixtures pass at one pinned protocol version.

The one that shapes this file most is *no external service is required*. Every
test here runs with RAVIS, SIRVIS, LM Studio and Clarvis absent, because that is
the state NERVIS has to be useful in — a control plane that needs the things it
watches to be healthy cannot be used to find out why they are not.
"""

from __future__ import annotations

from typing import Any

import pytest
from ecosystem_protocol import PROTOCOL_VERSION, is_supported_protocol
from fastapi.testclient import TestClient

from nervis.app import create_app
from nervis.config import Settings, inspect_configuration
from nervis.ecosystem import DECLARED
from nervis.storage import MIGRATIONS, current_version, installation_identity, prepare_database


@pytest.fixture()
def settings(tmp_path: Any) -> Settings:
    """A configuration with nothing else running and nothing to find.

    `_env_file=None` so a developer's own `.env` cannot change what a test
    means, which is the same reason both sibling suites do it.
    """
    return Settings(
        database_path=str(tmp_path / "nervis.db"),
        _env_file=None,  # type: ignore[call-arg]
    )


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


# ── The MEP surface: NERVIS answers for itself (§3.1) ────────────────────────


def test_nervis_answers_its_own_identity(settings: Settings) -> None:
    """§3.1: a control plane demanding identity from peers must publish its own.

    "A control plane that demands an authentication reference from its peers
    while publishing nothing of its own is asking for a guarantee it does not
    offer."
    """
    body = _client(settings).get("/ecosystem/identity").json()

    assert body["service_type"] == "nervis"
    assert body["protocol_version"] == PROTOCOL_VERSION
    assert body["service_id"] and body["machine_id"]
    assert body["instance_id"] != body["machine_id"]


def test_the_machine_id_is_not_derived_from_the_machine(settings: Settings) -> None:
    """§4.1: locally generated, opaque, non-hardware-derived, resettable.

    Never a hostname, a serial number, a MAC address or anything reversible. The
    check is deliberately crude — an opaque hex string cannot contain the host's
    name — because the failure this guards against is somebody reaching for
    `socket.gethostname()` as an obvious stable identifier.
    """
    import socket

    body = _client(settings).get("/ecosystem/identity").json()

    assert socket.gethostname().lower() not in body["machine_id"].lower()
    assert body["machine_id"].isalnum()


def test_identity_survives_a_restart(settings: Settings) -> None:
    """Stability is the entire value of `machine_id` and `service_id`.

    A peer correlating two reports from this installation needs the same answer
    both times. Generating them in a constructor would give a different one per
    process — which is what `instance_id` is *for*, and why the two are separate
    fields rather than one.
    """
    first = _client(settings).get("/ecosystem/identity").json()
    second = _client(settings).get("/ecosystem/identity").json()

    assert first["machine_id"] == second["machine_id"]
    assert first["service_id"] == second["service_id"]
    # And the one that must differ, does. Two processes on one machine are
    # distinguishable, which is what tells a restart from a second instance.
    assert first["instance_id"] != second["instance_id"]


def test_version_depends_on_nothing_that_can_be_unwell(settings: Settings) -> None:
    """§4.1: `/ecosystem/version` must answer even when `ready` is false."""
    body = _client(settings).get("/ecosystem/version").json()

    assert body["protocol_version"] == PROTOCOL_VERSION
    assert body["build_version"]


def test_health_is_ready_with_no_peer_running(settings: Settings) -> None:
    """The M0 exit criterion, stated as a test.

    NERVIS reads RAVIS, SIRVIS and Clarvis. None of them is running here, and
    NERVIS is ready anyway — because a control plane that reports itself broken
    when the things it watches are broken cannot be used to find out why.
    """
    body = _client(settings).get("/ecosystem/health").json()

    assert body["status"] == "healthy"
    assert body["ready"] is True
    assert [check["name"] for check in body["checks"]] == ["database"]


def test_what_is_advertised_matches_what_is_built(settings: Settings) -> None:
    """§4.1 forbids advertising an operation that has not passed conformance.

    Almost everything is unavailable at M0 and that is correct rather than
    modest. The one exception is the dashboard, which is `degraded`: the shell
    is genuinely served, and none of its data comes from NERVIS yet.

    **Both sibling services let this assertion go stale for four milestones.**
    Under-advertising fails silently by construction — nothing breaks, the
    service just quietly cannot be integrated with — so this test and somebody
    looking are the only two things that catch it.
    """
    body = _client(settings).get("/ecosystem/capabilities").json()
    states = {capability["id"]: capability["state"] for capability in body["capabilities"]}

    assert states == {
        "nervis.registry": "unavailable",             # M2
        "nervis.dashboard": "degraded",               # shell at M0, data at M1/M2
        "nervis.event_hub": "unavailable",            # M6
        "nervis.traces": "unavailable",               # M7
        "nervis.ravis_chat": "unavailable",           # M4
        "nervis.sirvis_views": "unavailable",         # M5
        "nervis.clarvis_visibility": "unavailable",   # M8
        "nervis.diagnostics": "unavailable",          # M12, M17
        "nervis.supervision": "unavailable",          # M16, and only when owned
        "nervis.code_server_proxy": "unavailable",    # gated on M13's spike
    }
    # Every entry says why, available or not: "not yet, because M6" tells a peer
    # when to look again and a bare refusal tells it nothing.
    assert all(capability["reason"] for capability in body["capabilities"])


def test_every_capability_the_specification_names_is_published(settings: Settings) -> None:
    """§3.1 prints ten names. Inventing one advertises a contract nobody seeks.

    SIRVIS shipped seven invented names, two of which matched its own
    specification by coincidence, and it went unnoticed for eleven milestones.
    This is that mistake made unrepeatable here.
    """
    body = _client(settings).get("/ecosystem/capabilities").json()

    assert {capability["id"] for capability in body["capabilities"]} == {
        "nervis.registry",
        "nervis.dashboard",
        "nervis.event_hub",
        "nervis.traces",
        "nervis.ravis_chat",
        "nervis.sirvis_views",
        "nervis.clarvis_visibility",
        "nervis.diagnostics",
        "nervis.supervision",
        "nervis.code_server_proxy",
    }


def test_the_shorthand_never_reaches_the_wire(settings: Settings) -> None:
    """§4.1: `id` carries the identifier alone; `@<major>` is prose shorthand."""
    body = _client(settings).get("/ecosystem/capabilities").json()

    assert all("@" not in capability["id"] for capability in body["capabilities"])
    assert all("@1" in declared for declared in DECLARED)


def test_a_mismatched_protocol_major_fails_cleanly() -> None:
    """§4.2: majors match, minors never refuse."""
    assert is_supported_protocol(PROTOCOL_VERSION) is True
    assert is_supported_protocol("1.999.999") is True
    assert is_supported_protocol("2.0.0") is False


# ── Storage: the database migrates cleanly ───────────────────────────────────


def test_a_new_database_reaches_the_latest_migration(tmp_path: Any) -> None:
    database = prepare_database(str(tmp_path / "nervis.db"))

    assert database.version == max(number for number, _, _ in MIGRATIONS)


def test_migrating_twice_changes_nothing(tmp_path: Any) -> None:
    """Forward-only and idempotent: reopening is not a second upgrade."""
    path = str(tmp_path / "nervis.db")
    first = prepare_database(path).version
    second = prepare_database(path)

    assert second.version == first
    applied = second.connection.execute("SELECT COUNT(*) AS n FROM applied_migration").fetchone()
    assert applied["n"] == len(MIGRATIONS)


def test_an_unopened_database_is_at_version_zero(tmp_path: Any) -> None:
    """Zero rather than an error: a database with no bookkeeping table has
    applied nothing, which is a real answer."""
    import sqlite3

    connection = sqlite3.connect(str(tmp_path / "empty.db"))
    connection.row_factory = sqlite3.Row

    assert current_version(connection) == 0


def test_identity_is_generated_once_and_then_read(tmp_path: Any) -> None:
    """The storage half of the restart test above, without the HTTP layer."""
    database = prepare_database(str(tmp_path / "nervis.db"))

    assert installation_identity(database) == installation_identity(database)


# ── The dashboard: the browser opens it ──────────────────────────────────────


def test_the_dashboard_is_served(settings: Settings) -> None:
    """M0's exit says the browser opens the dashboard, so it must arrive.

    Served rather than rewritten: `index.html` was built screen by screen
    against two live services, and replacing it with a server-rendered skeleton
    to satisfy a milestone's wording would discard working software.
    """
    response = _client(settings).get("/index.html")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    # No caching. A cached copy of yesterday's dashboard reporting today's data
    # is the most confusing failure this project has produced.
    assert response.headers["cache-control"] == "no-store"


def test_the_root_redirects_to_one_address(settings: Settings) -> None:
    """One page, one URL. Two is two cache entries and one stale bookmark."""
    response = _client(settings).get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/index.html"


# ── Settings: the one thing NERVIS stores at M0 ──────────────────────────────


def test_a_setting_round_trips_with_its_type(settings: Settings) -> None:
    client = _client(settings)

    client.put("/api/v1/settings/refresh_seconds", json={"value": 5})

    assert client.get("/api/v1/settings").json()["items"]["refresh_seconds"] == 5


def test_storing_null_is_different_from_storing_nothing(settings: Settings) -> None:
    """Why the body is `{"value": …}` rather than a bare value.

    "The setting is explicitly off" and "the setting was never set" are
    different states, and a settings screen needs both. A bare body cannot
    express the difference.
    """
    client = _client(settings)

    stored = client.put("/api/v1/settings/telemetry", json={"value": None})
    missing = client.put("/api/v1/settings/telemetry", json={})

    assert stored.status_code == 200
    assert missing.status_code == 422
    assert client.get("/api/v1/settings").json()["items"] == {"telemetry": None}


def test_a_refusal_uses_the_published_envelope(settings: Settings) -> None:
    """§4.3's shape, not FastAPI's `{"detail": …}`.

    A consumer written against the runbook branches on `error.code`, and would
    not find it in the default shape.
    """
    body = _client(settings).put("/api/v1/settings/x", json={}).json()

    assert body["error"]["code"] == "INVALID_CONFIGURATION"
    assert body["error"]["request_id"]


def test_the_health_alias_agrees_with_the_canonical_answer(settings: Settings) -> None:
    """§4.2 permits the alias; two health endpoints that could disagree is worse
    than one, so it reads the same surface rather than recomputing."""
    client = _client(settings)

    canonical = client.get("/ecosystem/health").json()
    alias = client.get("/api/v1/health").json()

    assert alias["ready"] == canonical["ready"]
    assert alias["status"] == canonical["status"]
    assert alias["service_type"] == "nervis"
    assert all("@" not in capability for capability in alias["capabilities"])


# ── Configuration: it refuses to serve on an unsafe bind ─────────────────────


def test_a_loopback_configuration_is_serveable() -> None:
    report = inspect_configuration(Settings(database_path=":memory:", _env_file=None))  # type: ignore[call-arg]

    assert report.is_startable
    assert report.findings == []


def test_a_remote_bind_without_tls_or_a_credential_refuses_to_start() -> None:
    """§15: loopback-first, and a remote NERVIS requires both.

    Either alone is the trap the rule exists to close — a credential-only bind
    passes every other check and then serves a control plane in cleartext.
    """
    report = inspect_configuration(
        Settings(database_path=":memory:", host="0.0.0.0", _env_file=None)  # type: ignore[call-arg]  # noqa: S104
    )

    assert not report.is_startable
    assert {finding.setting for finding in report.findings if finding.fatal} == {
        "NERVIS_CLIENT_CREDENTIAL",
        "NERVIS_TLS_CERTIFICATE_PATH",
    }


def test_a_credential_alone_is_still_refused() -> None:
    """The half-configured case, which is the one that actually happens."""
    report = inspect_configuration(
        Settings(  # type: ignore[call-arg]
            database_path=":memory:", host="0.0.0.0", client_credential="s3cret", _env_file=None  # noqa: S104
        )
    )

    assert not report.is_startable


# ── M1: this machine's live telemetry (§6) ───────────────────────────────────


def test_the_system_sample_describes_load_not_hardware(settings: Settings) -> None:
    """The distinction that keeps this from duplicating SIRVIS.

    SIRVIS's `/api/v1/system` is an immutable snapshot of *what this machine
    is*, attached to benchmark results as provenance. This one is *what it is
    doing*, sampled now — and once NERVIS watches a remote peer the two describe
    different machines.
    """
    body = _client(settings).get("/api/v1/system").json()

    assert body["sampled_at"] > 0
    assert body["memory_total_bytes"] > body["memory_available_bytes"] > 0
    assert body["disk_free_bytes"] > 0
    assert body["cpu_count"] >= 1


def test_two_samples_are_two_readings(settings: Settings) -> None:
    """Sampled on demand, so asking twice asks twice.

    A cached sample would make the dashboard show whatever a timer last caught
    rather than the state at the moment somebody looked.
    """
    client = _client(settings)

    first = client.get("/api/v1/system").json()
    second = client.get("/api/v1/system").json()

    assert second["sampled_at"] >= first["sampled_at"]


def test_the_identifying_fields_are_labelled_and_redactable(settings: Settings) -> None:
    """§5.1: a display must be able to label sensitivity and redact.

    Redaction blanks the field rather than dropping the key, so "withheld" stays
    distinguishable from "this platform did not answer" — which is precisely
    the distinction §5.1 asks a display to be able to make.
    """
    client = _client(settings)

    plain = client.get("/api/v1/system").json()
    hidden = client.get("/api/v1/system?redact=true").json()

    assert plain["sensitive_fields"] == ["hostname"]
    assert "hostname" in hidden
    assert hidden["hostname"] is None


def test_a_process_row_carries_no_command_line(settings: Settings) -> None:
    """§15 forbids publishing a raw workspace path.

    The argument list of an editor or a benchmark runner is exactly where one
    shows up, so the row carries the executable name and never the command line.
    """
    body = _client(settings).get("/api/v1/system").json()

    assert body["processes"], "no process was readable, which makes this test vacuous"
    for process in body["processes"]:
        assert set(process) == {"pid", "command", "cpu_percent", "memory_bytes"}
        assert "/" not in process["command"]


def test_processes_are_ordered_by_the_resource_that_runs_out(settings: Settings) -> None:
    """Memory, not CPU.

    This ecosystem's failure mode is a multi-gigabyte model resident when
    something else needs the room; CPU on a machine running an inference server
    is either idle or pinned.
    """
    body = _client(settings).get("/api/v1/system").json()
    sizes = [process["memory_bytes"] for process in body["processes"]]

    assert sizes == sorted(sizes, reverse=True)


def test_an_unreadable_reading_is_absent_rather_than_zero(settings: Settings) -> None:
    """A zero for "we could not read swap" is a number somebody will believe.

    Checked against thermal state, which only macOS reports: on every other
    platform the field must be `None` rather than a comfortable-looking
    `nominal`. SIRVIS's first probe collapsed the two and reported a happy
    machine while it lost 48% of its throughput to heat.
    """
    import platform

    body = _client(settings).get("/api/v1/system").json()

    if platform.system() != "Darwin":
        assert body["thermal_state"] is None
    else:
        assert body["thermal_state"] in {None, "nominal", "fair", "serious", "critical"}


def test_the_os_description_is_specific_enough_for_a_bug_report(settings: Settings) -> None:
    """"macOS" alone is not an OS description.

    The column reads `OS` and the value underneath has to distinguish the three
    platforms this runs on rather than assuming one of them.
    """
    body = _client(settings).get("/api/v1/system").json()

    assert body["os_description"].strip()
    assert body["os_description"] != "Darwin"
