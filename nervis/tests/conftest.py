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

import logging
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

_original_handle_error = logging.Handler.handleError


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
    yield home


@pytest.fixture(autouse=True)
def page_control_token(request: pytest.FixtureRequest, monkeypatch: Any) -> None:
    """Every test client sends NERVIS's control token, as the dashboard page does.

    Since NERVIS 0.34.17 every write under `/api/v1/` needs it (`nervis.api.control`), and the
    page adds it to all of them. A test of what a write does is not a test of the token, so its
    client carries the token the way the page would; `test_control_token.py`, whose subject is
    the token, sets `SENDS_NO_CONTROL_TOKEN` and gets clients without it.
    """
    if getattr(request.module, "SENDS_NO_CONTROL_TOKEN", False):
        return
    from fastapi.testclient import TestClient

    original = TestClient.__init__

    def with_token(self: TestClient, app: Any, *args: Any, **kwargs: Any) -> None:
        token = getattr(getattr(app, "state", None), "control_token", "")
        if token:
            kwargs["headers"] = {"x-nervis-control": token, **(kwargs.get("headers") or {})}
        original(self, app, *args, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", with_token)
