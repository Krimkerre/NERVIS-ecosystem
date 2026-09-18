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


# ── What the hook lets a gate do ────────────────────────────────────────────

REPO = Path(__file__).resolve().parents[2]


def test_the_hook_runs_the_dashboard_gates_under_the_shared_containment() -> None:
    """**The gates execute `index.html` in Node's VM, and this hook runs on every
    commit.** `tools/check_clean_clone.sh` wrapped them from the day that was
    written up; the hook ran the same gates with bare `node`, so the path taken
    most often was the unprotected one (base review, 17 September 2026, finding
    5). `tools/sandbox_check.js` proves the wrapper contains — it does not prove
    the hook uses it, which is what this asserts.
    """
    hook = (REPO / "tools" / "githooks" / "pre-commit").read_text(encoding="utf-8")
    policy = (REPO / "tools" / "node_guard.sh").read_text(encoding="utf-8")

    assert "node_guard.sh" in hook, "the hook does not source the shared containment policy"
    gate_line = next(line for line in hook.splitlines()
                     if "_check.js" in line and "${gate}" in line)
    assert '"${NODE_GUARD[@]}"' in gate_line, gate_line
    assert " node " not in gate_line, f"the gates still run bare node: {gate_line}"
    # The sandbox gate is the one exception, and stays bare on purpose: it spawns
    # the processes and makes the call the wrapper is meant to stop.
    assert 'check "dashboard sandbox" node' in hook
    # One policy, both callers: the drift this shares a file to prevent.
    clean_clone = (REPO / "tools" / "check_clean_clone.sh").read_text(encoding="utf-8")
    assert "node_guard.sh" in clean_clone
    assert "--permission" in policy and "sandbox-exec" in policy


def test_the_shared_policy_denies_writes_and_the_network_on_this_machine() -> None:
    """What the policy resolves to here, rather than what it says: read back from
    the shell that will run it."""
    profile = REPO / "tools" / "no-network.sb"
    assert profile.is_file()
    script = (f'. "{REPO}/tools/node_guard.sh"; node_guard "{profile}"; '
              'printf "%s " "${NODE_GUARD[@]}"')
    shown = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True,
    ).stdout
    assert "--permission" in shown and '--allow-fs-read=*' in shown, shown
    import platform
    if platform.system() == "Darwin":
        assert "sandbox-exec" in shown and str(profile) in shown, shown
