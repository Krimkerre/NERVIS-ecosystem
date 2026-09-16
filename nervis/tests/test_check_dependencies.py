"""The dependency check (`tools/check_dependencies.py`) against recorded tool answers.

The real `pip-audit` and `npm audit` are replaced by their recorded JSON, so these run
offline. The point is the verdict: a hole in anything the stack runs or Clarvis ships
fails, one only in Clarvis's build tools is listed and does not, and a tool that gives no
report is "could not check" — never a clean pass.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "check_dependencies.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_dependencies", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PIP_HOLE = {
    "dependencies": [
        {
            "name": "setuptools",
            "version": "79.0.1",
            "vulns": [{"id": "PYSEC-1", "fix_versions": ["83.0.0"]}] * 2,
        },
        {"name": "fastapi", "version": "1.0", "vulns": []},
    ]
}
NPM_CLEAN: dict[str, Any] = {"vulnerabilities": {}}
NPM_TOOL_HOLE = {
    "vulnerabilities": {
        "esbuild": {
            "severity": "moderate",
            "range": "<=0.24.2",
            "fixAvailable": {"name": "esbuild", "version": "0.28.2", "isSemVerMajor": True},
        },
    }
}


def fake(pip: Any, npm: Any, npm_shipped: Any = NPM_CLEAN) -> Any:
    """A `subprocess.run` that answers with recorded reports, by command."""

    def run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        shipped = "--omit=dev" in command
        body = (npm_shipped if shipped else npm) if "npm" in command[0] else pip
        text = body if isinstance(body, str) else json.dumps(body)
        return subprocess.CompletedProcess(command, 1, stdout=text, stderr="boom")

    return run


@pytest.fixture()
def check(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = load()
    monkeypatch.setattr(module.shutil, "which", lambda _: "/bin/pip-audit")
    return module


def test_a_python_hole_fails_and_is_listed_once(
    check: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(check.subprocess, "run", fake(PIP_HOLE, NPM_CLEAN))
    assert check.main() == 1
    out = capsys.readouterr().out
    assert out.count("setuptools 79.0.1: PYSEC-1 (fixed in 83.0.0)") == 1
    assert "fastapi" not in out


def test_a_hole_only_in_clarvis_build_tools_is_listed_but_passes(
    check: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        check,
        "npm_findings",
        lambda folder, *extra: (
            [] if extra or folder == check.ROOT / "nervis" else ["esbuild (moderate)"]
        ),
    )
    monkeypatch.setattr(check.subprocess, "run", fake({"dependencies": []}, NPM_CLEAN))
    assert check.main() == 0
    assert "! Clarvis's build and test tools (npm): 1 known hole(s)" in capsys.readouterr().out


def test_a_hole_clarvis_ships_fails_and_is_not_listed_twice(
    check: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        check.subprocess,
        "run",
        fake({"dependencies": []}, NPM_TOOL_HOLE, npm_shipped=NPM_TOOL_HOLE),
    )
    assert check.main() == 1
    out = capsys.readouterr().out
    assert "✗ What Clarvis ships (npm): 1 known hole(s)" in out
    assert "✓ Clarvis's build and test tools (npm): 0 known hole(s)" in out


def test_npm_findings_read_the_fix(check: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check.subprocess, "run", fake({}, NPM_TOOL_HOLE))
    assert check.npm_findings(Path(".")) == [
        "esbuild (moderate, <=0.24.2; fix: esbuild 0.28.2, a major change)"
    ]


@pytest.mark.parametrize("answer", ["not json", {"error": {"code": "ENOLOCK"}}])
def test_no_report_is_could_not_check_never_clean(
    check: ModuleType, monkeypatch: pytest.MonkeyPatch, answer: Any
) -> None:
    monkeypatch.setattr(check.subprocess, "run", fake("not json", answer, npm_shipped=answer))
    assert check.main() == 2


def test_no_pip_audit_is_could_not_check(
    check: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check, "PIP_AUDIT", Path("/nonexistent/pip-audit"))
    monkeypatch.setattr(check.shutil, "which", lambda _: None)
    monkeypatch.setattr(check.subprocess, "run", fake({"dependencies": []}, NPM_CLEAN))
    assert check.main() == 2
