"""A handed-over task's folder, started as a git repository (NERVIS 0.29.3).

**Why this exists.** Codex tasks run through Clarvis save their work as commits on
a branch of their own, so the project folder has to be a git repository with at
least one commit. On 14 September 2026 the owner kept meeting "Codex needs this
folder to be a git repository" in folders that had none, and decided that the
folders NERVIS hands a task over in should start life as repositories. Clarvis
separately offers "Set up git here" for folders made any other way — which is
also what a person gets when this could not do it.

**Why it is not in `handoff.py`.** That module is held to imports that cannot
reach anything (`test_nothing_here_reaches_the_editor`), and running a program
is reaching something. This module runs exactly one program, git, with fixed
argument lists, in exactly one folder: the one `handoff.write` just created.
`git init`, `add` and `commit` touch only that folder, so nothing here reaches
the editor either, and §6.7's "with the Bridge stopped, it all still works"
holds unchanged.

**A hand-over never fails because of git.** Git missing, a refusal, a timeout, a
failed step — every one leaves the folder and brief exactly as they were before
this module existed, is logged, and comes back as a plain sentence the answer
can carry. Anything this module started (a half-made `.git`) is removed, so
Clarvis's own "Set up git here" finds an ordinary folder rather than a
repository with no commits.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nervis.handoff import TASK_FILE, TASK_FOLDER

logger = logging.getLogger("nervis.handoff_git")

#: The branch the first commit lands on. Codex branches off it.
BRANCH = "main"

#: Who the first commit is by when git has no `user.name` or `user.email` set up.
#: Clearly local on purpose — an address nobody could mistake for a real person's
#: — and passed with `-c` for that one command, so no git configuration changes.
LOCAL_NAME = "NERVIS"
LOCAL_EMAIL = "nervis@localhost"

#: Seconds each git command may take. `init` and a one-file commit take a few
#: hundredths of a second; a git that has not answered in this long is stuck
#: (a signing prompt with nobody to answer it, a hung filesystem), and the person
#: is waiting at the Hand over button.
TIMEOUT_SECONDS = 10

#: Environment variables that point git at a *different* repository than the
#: folder it runs in. Inherited from whatever started NERVIS, any of them would
#: send `git add` and `git commit` into that repository instead — so all are
#: dropped before git runs.
REDIRECTING = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
    "GIT_PREFIX", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
)

#: Runs one git command: its argument list, the folder it runs in, its environment.
Runner = Callable[[Sequence[str], Path, dict[str, str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class Started:
    """Whether the folder became a repository, and a sentence saying so."""

    repository: bool
    detail: str
    #: `yours` when git's own configured identity made the commit, `local` when
    #: NERVIS's local one did, empty when nothing was committed.
    identity: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"repository": self.repository, "detail": self.detail,
                "identity": self.identity}


class _GitStepError(Exception):
    """One git step answered with a failure."""


def _run(args: Sequence[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run one git command: an argument list (never a shell string), in `cwd`,
    with a timeout, and with no terminal for git to prompt on."""
    return subprocess.run(
        list(args), cwd=cwd, env=env, timeout=TIMEOUT_SECONDS,
        # `errors="replace"`: output that is not UTF-8 is read, not raised.
        capture_output=True, text=True, errors="replace", stdin=subprocess.DEVNULL,
        check=False,
    )


def _environment(folder: Path) -> dict[str, str]:
    """NERVIS's environment, made safe for git to run in `folder`.

    - every variable that redirects git to another repository is dropped;
    - `GIT_CEILING_DIRECTORIES` is the folder's parent, so git never walks up
      from the folder looking for an enclosing repository;
    - `GIT_TERMINAL_PROMPT=0`, so git fails rather than asking for anything.
    """
    env = {key: value for key, value in os.environ.items() if key not in REDIRECTING}
    env["GIT_CEILING_DIRECTORIES"] = str(folder.parent)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def new_folder(root: Path, folder: str) -> Path | None:
    """The folder, resolved, when git may run in it — or None when it may not.

    Git runs only in a folder `handoff.write` has just made:
    - directly under `nervis-tasks/` in `root`, once symlinks are followed —
      the same containment `write` checks, and one level more exact;
    - not itself a symlink;
    - holding the brief and nothing else, so a folder made before this change,
      or one somebody has put work in, is never touched.
    """
    base = root.expanduser().resolve(strict=False)
    place = (root / folder).expanduser()
    if place.is_symlink():
        return None
    resolved = place.resolve(strict=False)
    if resolved.parent != base / TASK_FOLDER or not resolved.is_dir():
        return None
    try:
        contents = sorted(entry.name for entry in resolved.iterdir())
    except OSError:
        return None
    return resolved if contents == [TASK_FILE] else None


def start(root: Path, folder: str, *, run: Runner = _run) -> Started:
    """Make the new task folder a git repository whose first commit is the brief.

    `root` is the editor room `handoff.write` wrote into and `folder` the relative
    folder it answered with. `run` is replaceable so tests can make git fail
    without breaking it on the machine.
    """
    place = new_folder(root, folder)
    if place is None:
        logger.warning("not starting git in %s: not a newly made task folder", folder)
        return _not_yet("NERVIS only sets up git in a task folder it has just made")
    git = shutil.which("git")
    if git is None:
        logger.warning("not starting git in %s: git is not installed", folder)
        return _not_yet("git isn't installed on this computer")
    try:
        identity = _commit_brief(git, place, run)
    # **Every exception, not a list of expected ones.** A failed step, git
    # vanishing mid-way (`OSError`) and a timeout (`SubprocessError`) are the
    # expected ones; anything else is still no reason for a hand-over to fail.
    except Exception as error:
        logger.warning("could not start git in %s: %s", folder, error)
        _remove_started_repository(place)
        return _not_yet("git couldn't set it up")
    if identity == "local":
        detail = (f"a git repository on branch {BRANCH}, with the brief as its first "
                  f"commit — committed as {LOCAL_NAME} <{LOCAL_EMAIL}>, because git "
                  "has no name and email set up on this computer")
    else:
        detail = f"a git repository on branch {BRANCH}, with the brief as its first commit"
    return Started(repository=True, detail=detail, identity=identity)


def _not_yet(reason: str) -> Started:
    return Started(
        repository=False,
        detail=(f"it isn't a git repository yet ({reason}) — Clarvis will offer "
                "to set one up before Codex works in it"),
    )


def _commit_brief(git: str, place: Path, run: Runner) -> str:
    """The git steps, in order. Answers which identity made the commit.

    Every step runs in `place` and nowhere else: the folder is fixed here, and
    no step can name another.
    """
    env = _environment(place)

    def step(*args: str) -> str:
        done = run([git, *args], place, env)
        if done.returncode != 0:
            raise _GitStepError(f"git {args[0]} answered {done.returncode}: "
                             f"{(done.stderr or '').strip()[-300:]}")
        return (done.stdout or "").strip()

    step("init", "--quiet", f"--initial-branch={BRANCH}")
    # **The folder itself has to be the repository's root.** Checked before
    # anything is added or committed: a repository found anywhere else is one
    # this must not write into.
    top = step("rev-parse", "--show-toplevel")
    if Path(top).resolve(strict=False) != place:
        raise _GitStepError(f"the repository's root is {top}, not the task folder")
    identity = "yours" if _has_identity(git, place, env, run) else "local"
    override = [] if identity == "yours" else [
        "-c", f"user.name={LOCAL_NAME}", "-c", f"user.email={LOCAL_EMAIL}",
    ]
    step("add", "--", TASK_FILE)
    # `--no-verify`: a hook of the owner's is for their own repositories, and a
    # brief NERVIS wrote is not something a hook has an opinion on.
    step(*override, "commit", "--quiet", "--no-verify",
         "-m", f"Start {place.name}, handed over from NERVIS")
    return identity


def _has_identity(git: str, place: Path, env: dict[str, str], run: Runner) -> bool:
    """Whether git's own configuration names both a user and an email.

    `git config --get` answers 1 for a key that is not set, which is an answer
    rather than a failure.
    """
    for key in ("user.name", "user.email"):
        done = run([git, "config", "--get", key], place, env)
        if done.returncode != 0 or not (done.stdout or "").strip():
            return False
    return True


def _remove_started_repository(place: Path) -> None:
    """Take away a `.git` this module started, so the folder is as it was.

    Only `place/.git`, only a real directory, and `new_folder` has already
    established there was none before git ran.
    """
    started = place / ".git"
    if started.is_dir() and not started.is_symlink():
        shutil.rmtree(started, ignore_errors=True)


__all__ = ["BRANCH", "LOCAL_EMAIL", "LOCAL_NAME", "Started", "new_folder", "start"]
