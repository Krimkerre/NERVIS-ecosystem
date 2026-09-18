"""M0's exit criteria, one test each (SIRVIS.md §21).

    `sirvis doctor` and `sirvis serve` work; database migrates; no runtime
    dependency needed to start; MEP conformance fixtures pass at one pinned
    protocol version and a mismatched major fails cleanly.

The runtime-independence one matters most. Stage 1 exits here and Stage 4 cannot
begin until it does, so a SIRVIS that needs LM Studio running in order to start
would block the entire ecosystem schedule on an application being open.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from typing import Any, AsyncIterator

import httpx
import uvicorn
from ecosystem_protocol import PROTOCOL_VERSION, is_supported_protocol
from fastapi.testclient import TestClient

from sirvis.app import create_app
from sirvis.cli import main
from sirvis.config import Settings
from sirvis.ecosystem import BUILD_VERSION, sirvis_surface
from sirvis.storage import current_version, prepare_database
from sirvis.storage.database import MIGRATIONS


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def test_the_service_starts_with_no_runtime_present(settings: Settings) -> None:
    """M0's exit, and the one that unblocks the schedule.

    The configured LM Studio address points nowhere reachable in a test, which
    is the point: SIRVIS must come up, migrate and answer regardless. §15.4
    requires standalone behaviour, and a laptop with nothing loaded is the
    ordinary case rather than a fault.
    """
    unreachable = settings.model_copy(update={"lmstudio_base_url": "http://127.0.0.1:9"})

    body = _client(unreachable).get("/ecosystem/health").json()

    assert body["live"] is True
    assert body["ready"] is True
    assert [check["name"] for check in body["checks"]] == ["database"]


def test_the_database_migrates_to_the_latest_version(settings: Settings) -> None:
    database = prepare_database(settings.database_path)

    assert database.version == len(MIGRATIONS)
    assert current_version(database.connection) == len(MIGRATIONS)


def test_migrating_twice_is_a_no_op() -> None:
    """Forward-only and idempotent: restarting must not re-run a migration."""
    path = ":memory:"
    first = prepare_database(path)
    again = prepare_database(first.path)

    assert again.version == first.version


def test_the_readiness_check_actually_touches_the_database() -> None:
    """`ready` is independently truthful — a live process is not a ready one.

    Driven against the check rather than through the app, and the first attempt
    is worth recording: closing `app.state.database.connection` from the test
    proved nothing, because connections are thread-local and TestClient runs the
    handler in a different thread. That is the design working correctly; the
    test was wrong about how to break it.
    """
    class Unusable:
        @property
        def connection(self) -> object:
            raise RuntimeError("database file is gone")

    failing = sirvis_surface("sirvis-1", "machine-1", database=Unusable())

    assert failing.run_checks() == [
        {"name": "database", "status": "fail", "detail": "database file is gone"}
    ]


def test_the_readiness_check_passes_against_a_real_database(settings: Settings) -> None:
    """The other half: the check has to be capable of succeeding too."""
    app = create_app(settings)

    body = TestClient(app).get("/ecosystem/health").json()

    assert body["ready"] is True
    assert body["checks"] == [{"name": "database", "status": "pass"}]


def test_identity_says_it_is_sirvis(settings: Settings) -> None:
    """The shared router must report its host, not the service it was written in."""
    body = _client(settings).get("/ecosystem/identity").json()

    assert body["service_type"] == "sirvis"
    assert body["service_id"].startswith("sirvis-")
    assert body["protocol_version"] == PROTOCOL_VERSION


def test_what_is_advertised_matches_what_is_built(settings: Settings) -> None:
    """§4.1 cuts both ways, and only one direction fails loudly.

    Advertising an operation that has not passed conformance produces a wrong
    route on somebody else's machine — the failure this list was written to
    prevent. **Under**-advertising produces nothing at all: no error, no failing
    test, just a peer that cannot integrate and no clue why. This file's own
    docstring warned that a sibling service let these go stale for four
    milestones, and then M1, M2, M3, M6, M7 and M16 all shipped here while every
    entry still said `unavailable`. It was found when RAVIS tried to negotiate
    `sirvis.evidence.query@1` and was told the surface it had been built against
    did not exist.

    So the split is pinned rather than described. A milestone that makes a
    capability real has to edit this list, which is the point: the previous
    version asserted "everything is unavailable" and went on passing for six
    milestones after that stopped being true.

    **It happened again, and this test caught it.** M15 shipped — the
    recommendation engine, §14.3's weighted score with coverage on every number
    — and `sirvis.recommendations@1` went on saying "the recommendation engine
    lands at M15" while the endpoint returned real recommendations. Found while
    wiring a diagnostics screen that renders this very list, which is a
    reasonable argument for having such a screen. Under-advertising fails
    silently by construction, so the only things that catch it are this
    assertion and somebody looking.
    """
    body = _client(settings).get("/ecosystem/capabilities").json()
    states = {c["id"]: c["state"] for c in body["capabilities"]}

    # §4.1's published table, and nothing invented beside it. Wire ids, so no
    # `@<major>`: the shorthand stays out of the `id` field.
    assert states == {
        "sirvis.inventory.read": "available",       # M1 + M2 + M3
        "sirvis.runtime.state.read": "available",   # M8
        "sirvis.runtime.control": "available",      # M8
        "sirvis.benchmarks.jobs": "available",     # M14
        "sirvis.benchmarks.results": "available",   # M6 + M7 + M16
        "sirvis.runtime_sets": "available",         # M9
        "sirvis.recommendations": "available",      # M15
        "sirvis.events": "available",               # M21
        "sirvis.catalog.read": "available",         # M11
        "sirvis.downloads": "available",            # M11
        "sirvis.model_files": "available",          # §8's Reveal and Delete (0.19.7)
    }
    # Every entry still says why, available or not: "not yet, because M15" tells
    # a peer when to look again, and a bare refusal tells it nothing.
    assert all(c["reason"] for c in body["capabilities"])


def test_every_capability_the_specification_names_is_published(
    settings: Settings,
) -> None:
    """§4.1's table is a floor, and a name is what a peer negotiates on.

    This file previously declared seven capabilities it had named itself, of
    which two happened to match the specification. A consumer written against
    SIRVIS.md would have found six of the eight missing and the evidence surface
    RAVIS reads advertised under a name that appears in no document.
    """
    body = _client(settings).get("/ecosystem/capabilities").json()
    declared = {c["id"] for c in body["capabilities"]}

    assert declared >= {
        "sirvis.inventory.read",
        "sirvis.runtime.state.read",
        "sirvis.runtime.control",
        "sirvis.benchmarks.jobs",
        "sirvis.benchmarks.results",
        "sirvis.runtime_sets",
        "sirvis.recommendations",
        "sirvis.events",
    }


def test_a_mismatched_protocol_major_fails_cleanly() -> None:
    """M0's exit names this explicitly. §4.2: majors match, minors never refuse."""
    assert is_supported_protocol(PROTOCOL_VERSION) is True
    assert is_supported_protocol("1.999.999") is True
    assert is_supported_protocol("2.0.0") is False


def test_version_depends_on_nothing_that_can_be_unwell(settings: Settings) -> None:
    """§4.1 requires this to answer when `ready` is false.

    A peer diagnosing an incompatibility needs it precisely when the service is
    unhealthy, so the endpoint must not read the database, the runtime or any
    check — which is asserted here by it answering at all, and kept true by
    `read_version` touching only the surface's static fields.
    """
    body = TestClient(create_app(settings)).get("/ecosystem/version").json()

    assert body["protocol_version"] == PROTOCOL_VERSION
    # Against the package's own version rather than a literal. Pinning the
    # number here means every release edits a test to say what it just changed,
    # which checks that somebody typed the same string twice — the endpoint
    # reporting the *installed* version is the property worth holding.
    assert body["build_version"] == BUILD_VERSION
    assert body["build_version"] != "unknown", "the package should be installed under test"


def test_the_api_document_names_the_same_build(settings: Settings) -> None:
    """`/openapi.json` said 0.0.1 for the whole life of the service.

    The build version moved to one place, the package, and `/ecosystem/version`
    reads it from there — but the FastAPI constructor kept a literal of its own,
    so the API document went on describing a build that never shipped. Found by
    reading the live document during the 12 September 2026 sweep.
    """
    assert create_app(settings).openapi()["info"]["version"] == BUILD_VERSION


def test_doctor_reports_a_serveable_configuration(capsys) -> None:  # type: ignore[no-untyped-def]
    """`sirvis doctor` works, contacting nothing."""
    assert main(["doctor"]) == 0

    printed = capsys.readouterr().out
    assert "configuration is serveable" in printed
    # `doctor` contacts the runtime as of §18, and an absent one is a finding
    # rather than a failure — so this asserts it reported, not that it abstained.
    assert "runtime" in printed


def test_doctor_refuses_a_credential_free_remote_bind(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Both TLS and a credential, not either — the credential-only bind is the
    trap, because it looks configured and publishes the machine in cleartext."""
    monkeypatch.setenv("SIRVIS_HOST", "0.0.0.0")  # noqa: S104 - the unsafe case under test

    assert main(["doctor"]) == 2


def test_an_in_memory_database_is_one_database_not_one_per_connection() -> None:
    """`":memory:"` gives each connection a private database, so a migration on
    one is invisible to the next. The shared-cache URI is what fixes it, and
    this is the test that fails if someone simplifies it back."""
    database = prepare_database(":memory:")
    second = sqlite3.connect(database.path, uri=True)

    applied = second.execute("SELECT COUNT(*) FROM applied_migration").fetchone()[0]
    assert applied == len(MIGRATIONS)


def test_a_fully_configured_remote_bind_is_refused() -> None:
    """SIRVIS's half of the same rule — see `ECOSYSTEM_RUNBOOK.md` §16 item 2.

    `cli.py` never passes `ssl_certfile`/`ssl_keyfile` to `uvicorn.run`, so a
    certificate and key in the settings change nothing about the wire. The bind
    is refused until remote operation is actually built.
    """
    from sirvis.config import Settings as SirvisSettings
    from sirvis.config import inspect_configuration as inspect_sirvis

    report = inspect_sirvis(
        SirvisSettings(  # type: ignore[call-arg]
            host="0.0.0.0",  # noqa: S104
            client_credential="s3cret",
            _env_file=None,
        )
    )

    assert report.is_startable() is False


@contextlib.asynccontextmanager
async def _running(app: Any) -> AsyncIterator[str]:
    """`sirvis serve`'s own call, minus the part that blocks forever.

    `test_the_service_starts_with_no_runtime_present` above proves the *app*
    comes up with nothing else running — through `TestClient`, an ASGI
    transport with no socket. Reverifying §15 found that no test in this suite,
    or any sibling's, had ever asked uvicorn to actually bind one. Port 0 so a
    run of this suite never collides with a real SIRVIS.
    """
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="critical"))
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("uvicorn never reported started — this test is broken, not SIRVIS")
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


async def test_serve_binds_a_real_port_with_no_runtime_present(settings: Settings) -> None:
    """M0's exit in full: *"sirvis serve" works* and *"no runtime dependency
    needed to start"* — proved as a real socket and a real HTTP round trip,
    the same distinction `test_the_service_starts_with_no_runtime_present`
    above cannot make on its own.
    """
    unreachable = settings.model_copy(update={"lmstudio_base_url": "http://127.0.0.1:9"})
    async with _running(create_app(unreachable)) as base_url, httpx.AsyncClient() as client:
        answered = await client.get(f"{base_url}/ecosystem/health")

    assert answered.status_code == 200
    assert answered.json()["live"] is True
