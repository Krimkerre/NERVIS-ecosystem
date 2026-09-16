"""Isolation every test file gets whether it asks for one or not.

**NERVIS writes exactly one file outside its database** — §18.2's voice
credential, at `config_directory() / "voice-credential.json"` — and that
directory is resolved from the environment. Without this fixture a test run
would read and *write* the developer's own `~/.config/nervis`, so a key entered
through the dashboard would decide whether the suite passed, and a test would
overwrite it.

That is not hypothetical: the sibling service shipped exactly this bug at its
own M10 and it went unnoticed until clicking around the dashboard started
breaking the suite. Autouse, because the protection is worthless if a new test
file has to remember to ask for it.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

_original_handle_error = logging.Handler.handleError

#: The events secret test clients present, and the services it proves (`page_control_token`).
TEST_EVENTS_SECRET = "test-events-secret-not-a-real-one"
TEST_EVENT_SERVICES = ("ravis", "sirvis", "nervis", "clarvis", "loop")

#: The port a test NERVIS believes it serves on, so its probe of itself reaches nothing. And the
#: ports the running stack listens on, which no test may connect to.
TEST_PORT = 9
LIVE_PORTS = frozenset({8721, 8731, 8790, 7071, 8080, 1234, 11434})
DEAD_ADDRESSES = tuple(
    f"NERVIS_{name}_BASE_URL"
    for name in ("RAVIS", "SIRVIS", "CLARVIS", "CODE_SERVER", "LMSTUDIO", "OLLAMA")
)


def real_default_addresses(monkeypatch: Any) -> None:
    """For a test about the defaults themselves: undo `isolated_config_home`'s dead addresses.

    Only for a test that builds `Settings` and starts no app, so nothing probes them.
    """
    for variable in (*DEAD_ADDRESSES, "NERVIS_PORT"):
        monkeypatch.delenv(variable, raising=False)


def _ignore_closed_stream_errors(self: logging.Handler, record: logging.LogRecord) -> None:
    """Swallow one specific, benign teardown race; let every other logging
    failure print exactly as it always has.

    Starlette's `TestClient` runs each request through a short-lived anyio
    "blocking portal" thread. Reproduced directly by running the full suite
    three times unmodified: 62, 62 and 64 of these threads logged something
    *after* pytest's own log-capture handler had already closed the stream
    for the test that spawned them — a nondeterministic count, appearing even
    in files that already use `with TestClient(...)`, so it is a portal
    thread teardown race against pytest's per-test capture lifecycle, not a
    missing `with` block anywhere in NERVIS's own tests. `raiseExceptions`
    exists for exactly this: per the stdlib docs, "situations which would
    cause an exception to be raised are silently ignored" so the logging
    package does not generate its own extraneous noise. Narrowed to this one
    `ValueError` so an actual new logging misconfiguration still prints.
    """
    _, exc, _ = sys.exc_info()
    if isinstance(exc, ValueError) and "closed file" in str(exc):
        return
    _original_handle_error(self, record)


@pytest.fixture(autouse=True, scope="session")
def _silence_benign_portal_teardown_races() -> Iterator[None]:
    logging.Handler.handleError = _ignore_closed_stream_errors  # type: ignore[method-assign]
    yield
    logging.Handler.handleError = _original_handle_error  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def isolated_config_home(tmp_path: Path, monkeypatch: Any) -> Iterator[Path]:
    """Point every per-user path at a directory this test owns.

    Both variables are set on every platform. `XDG_CONFIG_HOME` is what the
    POSIX branch reads and `APPDATA` is what the Windows branch reads, and
    setting only the one this machine happens to use is how a suite passes on
    macOS and writes to a real home directory in CI.
    """
    home = tmp_path / "config-home"
    home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    # `TestClient` addresses the app as `http://testserver`, so the §16 item 5
    # Host check would refuse the whole suite. Named once here rather than
    # carved into the check as a test-shaped exception.
    monkeypatch.setenv(
        "NERVIS_SERVED_HOSTS", '["127.0.0.1", "localhost", "::1", "testserver"]'
    )
    monkeypatch.setenv("APPDATA", str(home))
    # **No test reaches the running stack.** Every peer address defaults to where the real
    # service listens, so a test app's background probes hit the owner's SIRVIS, RAVIS, LM Studio,
    # Ollama, code-server and NERVIS itself — about 1,850 connections a run on 17 September 2026,
    # enough to spend RAVIS's anonymous allowance and fail a soak test's reads. Port 9 on
    # loopback has nothing behind it, so a probe fails at once. A test that wants a peer names
    # its own address, which wins over these. `no_live_service_ports` below enforces it.
    for variable in DEAD_ADDRESSES:
        monkeypatch.setenv(variable, "http://127.0.0.1:9")
    monkeypatch.setenv("NERVIS_PORT", str(TEST_PORT))
    yield home


@pytest.fixture(autouse=True)
def page_control_token(request: pytest.FixtureRequest, monkeypatch: Any) -> None:
    """Every test client sends NERVIS's control token, as the dashboard page does.

    Since NERVIS 0.34.17 every write under `/api/v1/` needs it (`nervis.api.control`), and the
    page adds it to all of them. A test of what a write does is not a test of the token, so its
    client carries the token the way the page would; `test_control_token.py`, whose subject is
    the token, sets `SENDS_NO_CONTROL_TOKEN` and gets clients without it.
    """
    no_token = getattr(request.module, "SENDS_NO_CONTROL_TOKEN", False)
    # The events half, likewise (NERVIS 0.34.18): one test secret that proves every service a
    # test publishes as, sent by default. `test_event_senders.py` and the files that test a
    # window's own token set `CHECKS_EVENT_SENDERS` and set up senders themselves.
    events = not getattr(request.module, "CHECKS_EVENT_SENDERS", False)
    if events:
        monkeypatch.setenv(
            "NERVIS_EVENT_PRODUCER_SECRETS",
            json.dumps(dict.fromkeys(TEST_EVENT_SERVICES, TEST_EVENTS_SECRET)),
        )
    if no_token and not events:
        return
    from fastapi.testclient import TestClient

    original = TestClient.__init__

    def with_token(self: TestClient, app: Any, *args: Any, **kwargs: Any) -> None:
        token = getattr(getattr(app, "state", None), "control_token", "")
        defaults: dict[str, str] = {}
        if token and not no_token:
            defaults["x-nervis-control"] = token
        if token and events:
            defaults["authorization"] = f"Bearer {TEST_EVENTS_SECRET}"
        kwargs["headers"] = {**defaults, **(kwargs.get("headers") or {})}
        original(self, app, *args, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", with_token)


_LIVE_CONNECTIONS: list[tuple[str, int]] = []


def _watch_connections(event: str, args: tuple[Any, ...]) -> None:
    """An audit hook: note every connection to a port the running stack listens on."""
    if event != "socket.connect" or len(args) < 2:
        return
    address = args[1]
    if (isinstance(address, tuple) and len(address) >= 2
            and address[0] in ("127.0.0.1", "::1", "localhost") and address[1] in LIVE_PORTS):
        _LIVE_CONNECTIONS.append((str(address[0]), int(address[1])))


@pytest.fixture(autouse=True, scope="session")
def _live_connection_watch() -> None:
    # Once per run: an audit hook cannot be removed, so it only records, and
    # `no_live_service_ports` decides per test.
    sys.addaudithook(_watch_connections)


@pytest.fixture(autouse=True)
def no_live_service_ports(_live_connection_watch: None) -> Iterator[None]:
    """Fail any test that connected to the running stack's ports (17 September 2026).

    A test's background probe reaching the owner's RAVIS, SIRVIS, LM Studio, Ollama, code-server
    or NERVIS is not isolation, whatever it asserts: it spent RAVIS's shared anonymous allowance
    during a soak test and failed three of its reads. Connections made by a test's background
    threads after it ends are caught by the next test, which is where they would show.
    """
    before = len(_LIVE_CONNECTIONS)
    yield
    reached = sorted({port for _, port in _LIVE_CONNECTIONS[before:]})
    if reached:
        pytest.fail(
            f"this test connected to the running stack's port(s) {reached}; give the app a "
            "dead address (see isolated_config_home) or a fake of its own",
            pytrace=False,
        )
