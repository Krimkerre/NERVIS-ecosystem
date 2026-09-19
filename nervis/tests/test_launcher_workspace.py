"""Where the launcher puts NERVIS's workspace: beside the checkout, never inside it.

Owner decision, 13 September 2026. Tasks handed to Clarvis live in the workspace's
`clarvis/nervis-tasks/`, and Codex refuses any project that lies inside this repository
(`RAVIS_AGENT_PROTECTED_REPOSITORIES`). While the workspace was `<repo>/workspace`, every real task
would have been refused, so the whole folder moved to `NERVIS workspace` next to the repositories.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
RUN_PY = REPOSITORY / "tools" / "run.py"
ROOMS = ["clarvis", "export", "import", "library"]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("launcher_workspace_under_test", RUN_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _nervis_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, dict[str, str]]:
    """The environment `start` would launch NERVIS with, for a checkout inside `tmp_path`.

    What `_services` reaches besides the workspace is stubbed the way the LM Studio context
    test stubs it: credentials in `.run/`, the credential store's names, Ollama and code-server.
    """
    run = _load()
    root = tmp_path / "NERVIS-ecosystem"
    root.mkdir()
    monkeypatch.setattr(run, "ROOT", root)
    for minted in (
        "nervis_ravis_credential", "benchmark_token", "admin_token", "ravis_admin_credential"
    ):
        monkeypatch.setattr(run, minted, lambda: "(not a credential)")
    monkeypatch.setattr(run, "_ollama", list)
    monkeypatch.setattr(run, "_code_server", list)
    monkeypatch.delenv("NERVIS_WORKSPACE_PATH", raising=False)
    env = next(env for name, _, _, env, _ in run._services() if name == "NERVIS")
    return root, env


def test_the_workspace_sits_beside_the_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, env = _nervis_environment(monkeypatch, tmp_path)
    workspace = Path(env["NERVIS_WORKSPACE_PATH"])
    assert workspace == tmp_path / "NERVIS workspace"
    # The point of the move: nothing handed to Clarvis is inside a protected repository.
    assert not workspace.is_relative_to(root)


def test_its_rooms_exist_before_anything_looks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, env = _nervis_environment(monkeypatch, tmp_path)
    workspace = Path(env["NERVIS_WORKSPACE_PATH"])
    assert sorted(p.name for p in workspace.iterdir()) == ROOMS
    # And nothing is left where the workspace used to be.
    assert not (root / "workspace").exists()


def test_ravis_is_told_the_codex_skills_folder_inside_the_same_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """RAVIS 0.26.0 keeps the skills in the workspace's `clarvis/skills` on for Codex. It must be
    NERVIS's own workspace's folder, the launcher never makes it (RAVIS does, when Codex starts),
    and an operator's own value wins, like every other default here."""
    monkeypatch.delenv("RAVIS_CODEX_SKILLS_FOLDER", raising=False)
    _, nervis = _nervis_environment(monkeypatch, tmp_path)
    run = _load()
    monkeypatch.setattr(run, "ROOT", tmp_path / "NERVIS-ecosystem")
    for minted in (
        "nervis_ravis_credential", "benchmark_token", "admin_token", "ravis_admin_credential"
    ):
        monkeypatch.setattr(run, minted, lambda: "(not a credential)")
    monkeypatch.setattr(run, "_ollama", list)
    monkeypatch.setattr(run, "_code_server", list)
    services = {name: env for name, _, _, env, _ in run._services()}

    workspace = Path(nervis["NERVIS_WORKSPACE_PATH"])
    assert Path(services["RAVIS"]["RAVIS_CODEX_SKILLS_FOLDER"]) == workspace / "clarvis" / "skills"
    assert not (workspace / "clarvis" / "skills").exists()
    for other in ("NERVIS", "SIRVIS"):
        assert "RAVIS_CODEX_SKILLS_FOLDER" not in services[other], other

    monkeypatch.setenv("RAVIS_CODEX_SKILLS_FOLDER", "/Users/owner/elsewhere/skills")
    again = {name: env for name, _, _, env, _ in run._services()}
    assert again["RAVIS"]["RAVIS_CODEX_SKILLS_FOLDER"] == "/Users/owner/elsewhere/skills"
