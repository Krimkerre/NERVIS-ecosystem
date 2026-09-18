"""code-server's password as the launcher writes it: whatever the owner chose, whole.

Since 19 September 2026 the owner chooses it in the installer, so it can hold characters a
generated one never did. The launcher wrote it into YAML unquoted, where `#` starts a comment:
`abc#de f!1` would have been read as `abc`. A real code-server, given the quoted form, accepted
the whole password and refused `abc` (checked by hand the same day).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

RUN_PY = Path(__file__).resolve().parents[2] / "tools" / "run.py"


@pytest.fixture
def launcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("launcher_password_under_test", RUN_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "RUN", tmp_path / ".run")
    return module


@pytest.mark.parametrize("chosen", ["abc#de f!1", "&anchor: 'x' \"y\" \\\\z", "plain-password"])
def test_the_chosen_password_reaches_code_server_whole(launcher: ModuleType, tmp_path: Path,
                                                        chosen: str) -> None:
    (tmp_path / ".run").mkdir()
    (tmp_path / ".run" / "code-server.password").write_text(chosen + "\n")

    written = launcher._written_config().read_text()

    [line] = [line for line in written.splitlines() if line.startswith("password: ")]
    # A JSON string is a YAML double-quoted scalar, so this is what code-server reads.
    assert json.loads(line.removeprefix("password: ")) == chosen
