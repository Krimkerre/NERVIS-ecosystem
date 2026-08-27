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

from pathlib import Path
from typing import Any, Iterator

import pytest


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
    monkeypatch.setenv("APPDATA", str(home))
    yield home
