"""The launcher switches on the commit checks (`enable_commit_checks` in `tools/run.py`).

Run against real git in a throwaway repository, never this checkout: a fresh clone gets
`core.hooksPath` pointed at `tools/githooks`, a clone already pointing there is left as it is,
a hooks path somebody chose is kept, and a folder that is not a checkout is not touched.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
from tests.test_launcher_lifecycle import _load

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="needs git")


def hooks_path(root: Path) -> str:
    assert GIT
    return subprocess.run(
        [GIT, "-C", str(root), "config", "--local", "--get", "core.hooksPath"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()


@pytest.fixture()
def run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    module = _load()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    return module


def test_a_fresh_clone_is_switched_on_once(run: ModuleType, tmp_path: Path) -> None:
    assert GIT
    subprocess.run([GIT, "init", "-q", str(tmp_path)], check=True)
    assert run.enable_commit_checks() == "switched on"
    assert hooks_path(tmp_path) == "tools/githooks"
    assert run.enable_commit_checks() == "already on"


def test_a_hooks_path_somebody_chose_is_kept(run: ModuleType, tmp_path: Path) -> None:
    assert GIT
    subprocess.run([GIT, "init", "-q", str(tmp_path)], check=True)
    subprocess.run([GIT, "-C", str(tmp_path), "config", "core.hooksPath", ".husky"], check=True)
    assert run.enable_commit_checks() == "left alone"
    assert hooks_path(tmp_path) == ".husky"


def test_a_folder_that_is_not_a_checkout_is_not_touched(run: ModuleType, tmp_path: Path) -> None:
    assert run.enable_commit_checks() == "not a git checkout"
    assert not (tmp_path / ".git").exists()
    assert run.enable_commit_checks(git=None) == "not a git checkout"


def test_a_git_that_refuses_the_write_is_reported(run: ModuleType, tmp_path: Path) -> None:
    assert GIT
    subprocess.run([GIT, "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".git" / "config").chmod(0o444)
    (tmp_path / ".git").chmod(0o555)
    try:
        assert run.enable_commit_checks() == "failed"
    finally:
        (tmp_path / ".git").chmod(0o755)
        (tmp_path / ".git" / "config").chmod(0o644)
    assert hooks_path(tmp_path) == ""


def test_every_start_asks_even_with_the_environment_already_built(
    run: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`start` runs `ensure_venv` first; a clone whose environment exists still gets asked."""
    asked: list[str] = []
    monkeypatch.setattr(run, "enable_commit_checks", lambda: asked.append("asked") or "")
    (tmp_path / "python").touch()
    monkeypatch.setattr(run, "venv_bin", lambda name: tmp_path / name)
    run.ensure_venv()
    assert asked == ["asked"]
