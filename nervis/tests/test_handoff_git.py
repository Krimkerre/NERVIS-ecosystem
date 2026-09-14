"""A handed-over task's folder starts as a git repository (NERVIS 0.29.3).

**Why.** Codex tasks run through Clarvis save their work as commits on a branch of
their own, so the folder has to be a git repository with a commit in it. On 14
September 2026 the owner kept hitting "Codex needs this folder to be a git
repository", and decided the folders NERVIS hands a task over in start as one.

**What these hold it to.** The brief is the first commit, on `main`; git's own
identity is used when there is one and a clearly local one when there is not,
without any git configuration changing; git missing or failing never fails the
hand-over and leaves the folder as it always was; and git runs in the new folder
and nowhere else — not in a folder made before, not in an enclosing repository.

Every repository here is made under pytest's temporary folders, with git's
global configuration pointed at a file inside them, so nothing about the
machine's own git is read or changed.
"""

from __future__ import annotations

import ast
import logging
import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis import handoff, handoff_git
from nervis.app import create_app
from nervis.config import Settings

OWNER_NAME = "Test Owner"
OWNER_EMAIL = "owner@example.test"


# ── Git, set up inside the test's own folder ─────────────────────────────────


def _isolated_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config: str) -> Path:
    """Point git's global configuration at a file in `tmp_path`, holding `config`.

    The system file is switched off and every identity git reads from the
    environment removed, so what git knows about who is committing is exactly
    what the test wrote.
    """
    global_config = tmp_path / "gitconfig"
    global_config.write_text(config, encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                 "GIT_COMMITTER_EMAIL", "EMAIL", *handoff_git.REDIRECTING):
        monkeypatch.delenv(name, raising=False)
    return global_config


@pytest.fixture(autouse=True)
def owner_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Git with the owner's name and email set up — for every test here, so no
    test ever reads the machine's own git configuration. `no_identity` replaces
    it for the tests that ask for that."""
    return _isolated_git(monkeypatch, tmp_path,
                         f"[user]\n\tname = {OWNER_NAME}\n\temail = {OWNER_EMAIL}\n")


@pytest.fixture()
def no_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Git with no name or email set up anywhere it would look."""
    return _isolated_git(monkeypatch, tmp_path, "")


def git(folder: Path, *args: str) -> str:
    """Ask git something about `folder`, never walking above it."""
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(folder.parent)}
    done = subprocess.run(["git", *args], cwd=folder, env=env, capture_output=True,
                          text=True, timeout=10, check=True)
    return done.stdout.strip()


def a_task(room: Path, name: str = "pomodoro-timer") -> Path:
    """A task folder exactly as `handoff.write` leaves it, before git."""
    written = handoff.write(room, "make me a pomodoro timer", name=name)
    return room / written.folder


def an_enclosing_repository(tmp_path: Path) -> Path:
    """A repository with one commit, for a room to sit inside."""
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / "readme.md").write_text("mine", encoding="utf-8")
    for args in (["init", "--quiet", "--initial-branch=main"], ["add", "readme.md"],
                 ["commit", "--quiet", "-m", "mine"]):
        subprocess.run(["git", *args], cwd=outer, capture_output=True, timeout=10, check=True)
    return outer


def commits_in(repository: Path) -> int:
    done = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=repository,
                          capture_output=True, text=True, timeout=10, check=True)
    return int(done.stdout.strip())


def an_api(tmp_path: Path) -> TestClient:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"),
        workspace_path=str(tmp_path / "workspace"),
        _env_file=None,
    )
    return TestClient(create_app(settings))


HAND_OVER = {
    "operation": "nervis.clarvis.task",
    "target": "make me a pomodoro timer",
    "name": "pomodoro timer",
}


# ── A new folder is a repository ─────────────────────────────────────────────


def test_a_new_task_folder_is_a_repository_whose_first_commit_is_the_brief(
    tmp_path: Path
) -> None:
    folder = a_task(tmp_path / "clarvis")
    global_config = tmp_path / "gitconfig"
    before = global_config.read_text(encoding="utf-8")

    started = handoff_git.start(tmp_path / "clarvis", "nervis-tasks/pomodoro-timer")

    assert started.repository, started.detail
    assert started.identity == "yours"
    assert Path(git(folder, "rev-parse", "--show-toplevel")).resolve() == folder.resolve()
    assert git(folder, "symbolic-ref", "--short", "HEAD") == "main"
    assert git(folder, "rev-list", "--count", "HEAD") == "1"
    assert git(folder, "ls-tree", "-r", "--name-only", "HEAD") == handoff.TASK_FILE
    assert git(folder, "log", "-1", "--format=%s") == (
        "Start pomodoro-timer, handed over from NERVIS"
    )
    assert git(folder, "log", "-1", "--format=%an <%ae>") == f"{OWNER_NAME} <{OWNER_EMAIL}>"
    # The working tree starts clean: the brief is committed, nothing is left over.
    assert git(folder, "status", "--porcelain") == ""
    assert global_config.read_text(encoding="utf-8") == before


def test_with_no_identity_set_up_it_commits_as_nervis_for_that_commit_only(
    no_identity: Path, tmp_path: Path
) -> None:
    """**Committed, and nothing configured.** The local identity is passed for
    the one command, so neither the global file nor the new repository's own
    configuration gains a name or an email."""
    folder = a_task(tmp_path / "clarvis")

    started = handoff_git.start(tmp_path / "clarvis", "nervis-tasks/pomodoro-timer")

    assert started.repository, started.detail
    assert started.identity == "local"
    assert "NERVIS <nervis@localhost>" in started.detail
    assert git(folder, "log", "-1", "--format=%an <%ae>") == "NERVIS <nervis@localhost>"
    assert git(folder, "log", "-1", "--format=%cn <%ce>") == "NERVIS <nervis@localhost>"
    assert no_identity.read_text(encoding="utf-8") == ""
    assert "user." not in (folder / ".git" / "config").read_text(encoding="utf-8")


def test_the_hand_over_answer_says_the_folder_is_a_repository(
    tmp_path: Path
) -> None:
    """Through the real route, which is the only thing that calls this."""
    with an_api(tmp_path) as client:
        answer = client.post("/api/v1/commands/run", json=HAND_OVER)

    assert answer.status_code == 200, answer.text
    file = answer.json()["file"]
    assert file["repository"]["repository"] is True
    assert "git repository" not in file["detail"]
    folder = tmp_path / "workspace" / "clarvis" / file["folder"]
    assert git(folder, "ls-tree", "-r", "--name-only", "HEAD") == handoff.TASK_FILE


# ── Git missing or failing never fails the hand-over ─────────────────────────


def test_with_git_missing_the_folder_is_still_written_and_the_answer_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "no-programs-here"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    with an_api(tmp_path) as client:
        answer = client.post("/api/v1/commands/run", json=HAND_OVER)

    assert answer.status_code == 200, answer.text
    file = answer.json()["file"]
    folder = tmp_path / "workspace" / "clarvis" / file["folder"]
    assert handoff.waiting(folder) is not None
    assert not (folder / ".git").exists()
    assert file["repository"]["repository"] is False
    assert "isn't a git repository yet" in file["detail"]
    assert "git isn't installed" in file["detail"]


def _fails_at(step: str, how: str) -> handoff_git.Runner:
    """Real git, except at `step`, where it fails in the way `how` names."""
    def run(args: Sequence[str], cwd: Path, env: dict[str, str]) -> Any:
        if step in args:
            if how == "answers-1":
                return subprocess.CompletedProcess(list(args), 1, "", "fatal: no")
            if how == "times-out":
                raise subprocess.TimeoutExpired(list(args), handoff_git.TIMEOUT_SECONDS)
            raise RuntimeError("something nobody expected")
        return handoff_git._run(args, cwd, env)
    return run


@pytest.mark.parametrize(("step", "how"), [
    ("init", "answers-1"),
    ("commit", "answers-1"),
    ("commit", "times-out"),
    ("add", "raises"),
])
def test_a_failing_git_leaves_the_folder_and_brief_as_they_were(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, step: str, how: str
) -> None:
    folder = a_task(tmp_path / "clarvis")
    brief = (folder / handoff.TASK_FILE).read_text(encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="nervis.handoff_git"):
        started = handoff_git.start(tmp_path / "clarvis", "nervis-tasks/pomodoro-timer",
                                    run=_fails_at(step, how))

    assert started.repository is False
    assert "isn't a git repository yet" in started.detail
    assert sorted(p.name for p in folder.iterdir()) == [handoff.TASK_FILE], (
        "a half-made repository was left behind"
    )
    assert (folder / handoff.TASK_FILE).read_text(encoding="utf-8") == brief
    assert any("could not start git" in r.getMessage() for r in caplog.records)


# ── Git runs in the new folder and nowhere else ──────────────────────────────


def test_every_git_command_runs_in_the_new_folder_with_no_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = a_task(tmp_path / "clarvis")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "somewhere-else" / ".git"))
    seen: list[tuple[list[str], Path, dict[str, str]]] = []

    def recording(args: Sequence[str], cwd: Path, env: dict[str, str]) -> Any:
        seen.append((list(args), cwd, env))
        return handoff_git._run(args, cwd, env)

    started = handoff_git.start(tmp_path / "clarvis", "nervis-tasks/pomodoro-timer",
                                run=recording)

    assert started.repository, started.detail
    assert seen
    for args, cwd, env in seen:
        assert args[0] == shutil.which("git")
        assert cwd == folder.resolve()
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_CEILING_DIRECTORIES"] == str(folder.resolve().parent)
        assert "GIT_DIR" not in env


def test_each_command_is_an_argument_list_with_a_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What reaches `subprocess.run` itself: a list, never a shell, a timeout,
    nothing to type into, and the new folder as where it runs."""
    folder = a_task(tmp_path / "clarvis")
    real = subprocess.run
    calls: list[tuple[Any, dict[str, Any]]] = []

    def watched(args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return real(args, **kwargs)

    monkeypatch.setattr(handoff_git.subprocess, "run", watched)
    started = handoff_git.start(tmp_path / "clarvis", "nervis-tasks/pomodoro-timer")

    assert started.repository, started.detail
    assert len(calls) >= 5
    for args, kwargs in calls:
        assert isinstance(args, list)
        assert not kwargs.get("shell")
        assert kwargs["timeout"] == handoff_git.TIMEOUT_SECONDS
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["cwd"] == folder.resolve()


def _made_before(room: Path) -> str:
    """A task folder from before this change: the brief, and work beside it."""
    folder = a_task(room, "older")
    (folder / "plan.md").write_text("# plan", encoding="utf-8")
    return "nervis-tasks/older"


def _already_a_repository(room: Path) -> str:
    folder = a_task(room, "repo")
    (folder / ".git").mkdir()
    return "nervis-tasks/repo"


def _outside(room: Path) -> str:
    outside = room.parent / "elsewhere"
    outside.mkdir()
    (outside / handoff.TASK_FILE).write_text("x", encoding="utf-8")
    return "../elsewhere"


def _the_task_folder_itself(room: Path) -> str:
    a_task(room)
    return "nervis-tasks"


def _a_link_out(room: Path) -> str:
    outside = room.parent / "linked"
    outside.mkdir()
    (outside / handoff.TASK_FILE).write_text("x", encoding="utf-8")
    (room / "nervis-tasks").mkdir(parents=True)
    (room / "nervis-tasks" / "linked").symlink_to(outside)
    return "nervis-tasks/linked"


def _a_link_to_another_task(room: Path) -> str:
    """A link beside a new task folder, pointing at it. Git would run in the
    folder linked to, which is not the folder it was asked about."""
    real = a_task(room, "real")
    (real.parent / "alias").symlink_to(real)
    return "nervis-tasks/alias"


@pytest.mark.parametrize("place", [
    _made_before, _already_a_repository, _outside, _the_task_folder_itself, _a_link_out,
    _a_link_to_another_task,
])
def test_git_never_runs_anywhere_but_a_newly_made_task_folder(
    tmp_path: Path, place: Any
) -> None:
    room = tmp_path / "clarvis"
    room.mkdir()
    folder = place(room)

    ran: list[list[str]] = []

    def recording(args: Sequence[str], *_: Any) -> Any:
        # Recorded rather than raised: `start` turns every exception into "not a
        # repository yet", so a raise here would pass unnoticed.
        ran.append(list(args))
        return subprocess.CompletedProcess(list(args), 1, "", "")

    started = handoff_git.start(room, folder, run=recording)

    assert ran == [], f"git ran for {folder}: {ran}"
    assert started.repository is False
    assert not (room / "nervis-tasks" / "real" / ".git").exists()
    assert not (tmp_path / "elsewhere" / ".git").exists()
    assert not (tmp_path / "linked" / ".git").exists()
    assert not (room / "nervis-tasks" / "older" / ".git").exists()


def test_a_second_task_of_the_same_name_leaves_the_first_repository_alone(
    tmp_path: Path
) -> None:
    room = tmp_path / "clarvis"
    first = a_task(room)
    assert handoff_git.start(room, "nervis-tasks/pomodoro-timer").repository
    second = a_task(room)

    assert second.name == "pomodoro-timer-2"
    assert handoff_git.start(room, "nervis-tasks/pomodoro-timer-2").repository
    assert commits_in(first) == 1
    assert commits_in(second) == 1
    assert Path(git(second, "rev-parse", "--show-toplevel")).resolve() == second.resolve()


def test_a_repository_the_room_sits_inside_is_never_written_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace inside somebody's repository, and NERVIS started with `GIT_DIR`
    pointing at it: the new folder is still its own repository, and theirs is
    exactly as it was."""
    outer = an_enclosing_repository(tmp_path)
    folder = a_task(outer / "clarvis")
    monkeypatch.setenv("GIT_DIR", str(outer / ".git"))

    started = handoff_git.start(outer / "clarvis", "nervis-tasks/pomodoro-timer")

    monkeypatch.delenv("GIT_DIR")
    assert started.repository, started.detail
    assert commits_in(outer) == 1
    assert Path(git(folder, "rev-parse", "--show-toplevel")).resolve() == folder.resolve()


def test_a_repository_found_above_the_folder_is_refused(
    tmp_path: Path
) -> None:
    """**The root check, on its own.** Git is made to find the enclosing
    repository — its `init` skipped and its ceiling taken away — and nothing may
    be added or committed there: the folder has to be the repository's root."""
    outer = an_enclosing_repository(tmp_path)
    a_task(outer / "clarvis")

    def finds_the_outer_one(args: Sequence[str], cwd: Path, env: dict[str, str]) -> Any:
        if "init" in args:
            return subprocess.CompletedProcess(list(args), 0, "", "")
        loose = {k: v for k, v in env.items() if k != "GIT_CEILING_DIRECTORIES"}
        return handoff_git._run(args, cwd, loose)

    started = handoff_git.start(outer / "clarvis", "nervis-tasks/pomodoro-timer",
                                run=finds_the_outer_one)

    assert started.repository is False
    assert commits_in(outer) == 1
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=outer,
                            capture_output=True, text=True, timeout=10, check=True)
    assert staged.stdout.strip() == ""


# ── §6.7 still holds ─────────────────────────────────────────────────────────


def test_the_git_module_reaches_nothing_but_git() -> None:
    """Git runs as a local program with fixed arguments; this module holds no
    client, address or token, and imports nothing that could reach the editor."""
    tree = ast.parse(Path(handoff_git.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert imported <= {
        "__future__", "logging", "os", "shutil", "subprocess", "collections.abc",
        "dataclasses", "pathlib", "typing", "nervis.handoff",
    }, sorted(imported)
