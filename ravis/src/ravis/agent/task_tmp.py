"""A Codex task's temp folder: there while Codex can use it, removed whenever nothing runs.

**Why this exists.** Every task's commands get `<root>/.clarvis/tmp/<sid>` as their `TMPDIR`
(design §4.9, calibration K2b), and RAVIS made that folder when a task started and never removed
it: on 15 September 2026 the live test's project `live-test-a` still held `.clarvis/tmp/as_…/tmp…`
after its task had ended. RAVIS 0.26.2 removed it once the task was over for good — ended, or its
creation failed — but Clarvis never ends a task: it settles every finished one to `idle`, kept for
follow-ups, so the folders stayed. Since 0.26.3 (owner decision, 15 September 2026) the folder is
removed whenever the task has nothing running (`AgentSessions._task_resting`) and made again before
Codex takes the task's next step: every `turn/start` and every `thread/resume`
(`AgentSession._ensure_tmp`). When RAVIS made them and nothing else is in them, `.clarvis/tmp` and
`.clarvis` go too, and are made again with it.

**Making it** walks down the same way removing does: `.clarvis`, then `tmp`, then the task's folder,
each made and opened relative to the folder before it and refusing a symbolic link, so a link in
the project can't make RAVIS create a folder somewhere else. `ready` then says whether the folder is
really there; a step Codex would take without it is refused instead.

**Which folder.** A resumed task runs in a thread started by an earlier task, and `thread/resume`
carries no `TMPDIR`, so its commands keep the folder named after the task that started the thread.
That is why the folder's name is recorded per thread (`agent_thread.tmp_session_id`, migration 12)
and a resume makes the folder again: removing it at the first task's end must not leave a later
resume's commands without a temp folder.

**Only inside the project, and only that folder.** The walk opens `.clarvis`, then `tmp`, each
relative to the folder before it and refusing a symbolic link (`O_NOFOLLOW`), and removes the task's
folder through that open folder with `shutil.rmtree`, which never follows a link inside what it
removes. So a link anywhere below the project stops the walk instead of leading it out, and a link
that *is* the task's folder is removed as a link, its target untouched. The project root must still
be its own real path. Nothing is removed but `<root>/.clarvis/tmp/<that folder>`, and then `tmp` and
`.clarvis` with `rmdir`, which refuses a folder with anything in it — so Clarvis's checkout lock
(`.clarvis/engine.lock`) and task checkpoint (`.clarvis/task-checkpoint.json`) in a project without
git are never touched.

**Never while another task needs it.** The caller says which temp folders the project's other
unfinished tasks use and whether anything else holds the project; a folder in use is kept, and the
parents are kept while the project is busy.

**Never an error for the task.** A failed removal is logged and reported to the caller, which tries
again later: at the task's next rest, or RAVIS's next start. A folder that can't be made is logged
too, and the caller refuses the step that needed it.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import re
import shutil
import stat
from pathlib import Path

logger = logging.getLogger("ravis")

#: The two parents, in the order they nest. Must match `calibration_dependent.TMP_FOLDER`.
CLARVIS = ".clarvis"
TMP = "tmp"

#: A folder name RAVIS could have given: a task id (`as_` and URL-safe characters). Anything else —
#: `..`, a path with a separator, an empty string — is refused before any file is opened.
FOLDER_NAME = re.compile(r"[A-Za-z0-9_-]{1,128}")


def made_list(made: str) -> set[str]:
    """The parents a record says RAVIS made: `""`, `"tmp"` or `".clarvis,tmp"`."""
    return {name for name in made.split(",") if name in (CLARVIS, TMP)}


def merge(first: str, second: str) -> str:
    """Two records of made parents as one, in nesting order."""
    names = made_list(first) | made_list(second)
    return ",".join(name for name in (CLARVIS, TMP) if name in names)


def clarvis_exists(root: Path) -> bool:
    """Whether the project has a `.clarvis` of any kind — asked before RAVIS writes into it."""
    return os.path.lexists(root / CLARVIS)


def make(root: Path, folder: str, *, clarvis_existed: bool | None = None) -> str:
    """Make `<root>/.clarvis/tmp/<folder>`, and say which of its parents RAVIS made.

    `clarvis_existed` is whether `.clarvis` was there before the task took its project lock: in a
    project without git RAVIS's own lock file goes in `.clarvis` a moment before this, and that
    `.clarvis` is RAVIS's to remove too.

    Each level is made with `mkdir` relative to the one before and then opened without following a
    link, so nothing is ever made through a link. The parents get the default mode, as before; the
    task's own folder `0700`, so only this user reads what its commands leave there.

    Never raises: a failure is logged, and `ready` tells the caller the folder isn't there.
    """
    if not FOLDER_NAME.fullmatch(folder):
        logger.warning("agent: refused to make a task temp folder named %r", folder)
        return ""
    if os.path.realpath(root) != str(root):
        logger.warning("agent: %s is no longer the project's real path, so no task temp folder was "
                       "made in it", root)
        return ""
    created: list[str] = []
    reached: list[str] = []
    try:
        folder_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as failure:
        logger.warning("agent: couldn't open %s to make a task temp folder: %s", root, failure)
        return ""
    try:
        for name, mode in ((CLARVIS, 0o777), (TMP, 0o777), (folder, 0o700)):
            try:
                os.mkdir(name, mode, dir_fd=folder_fd)
                created.append(name)
            except FileExistsError:
                pass  # already there: opened below, which refuses a link or a file in its place
            inner = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder_fd)
            os.close(folder_fd)
            folder_fd = inner
            reached.append(name)
    except OSError as failure:
        logger.warning("agent: couldn't make a task temp folder in %s: %s", root, failure)
    finally:
        os.close(folder_fd)
    # A `.clarvis` RAVIS's own lock file made a moment before is RAVIS's to remove, as is one made
    # here.
    if clarvis_existed is False and CLARVIS in reached:
        created.append(CLARVIS)
    return merge("", ",".join(created))


def ready(root: Path, folder: str) -> bool:
    """Whether `<root>/.clarvis/tmp/<folder>` is a real folder, reached without following a link.

    Asked right after `make`, before a step of Codex's that would hand commands this `TMPDIR`.
    """
    if not FOLDER_NAME.fullmatch(folder) or os.path.realpath(root) != str(root):
        return False
    opened: list[int] = []
    try:
        opened.append(os.open(root, os.O_RDONLY | os.O_DIRECTORY))
        for name in (CLARVIS, TMP, folder):
            opened.append(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                  dir_fd=opened[-1]))
    except OSError:
        return False
    finally:
        for descriptor in opened:
            os.close(descriptor)
    return True


def remove(
    root: Path, folder: str, *, made: str, folders_in_use: frozenset[str], project_busy: bool
) -> bool:
    """Remove a finished task's temp folder, then the parents RAVIS made if they are empty.

    `folders_in_use` are the temp folders the project's other unfinished tasks use; `project_busy`
    is whether any other task or a lock holds the project. Returns whether nothing is left owed:
    False only when something failed and is worth trying again later.
    """
    if not FOLDER_NAME.fullmatch(folder):
        logger.warning("agent: refused to remove a task temp folder named %r", folder)
        return True
    if os.path.realpath(root) != str(root):
        logger.warning("agent: %s is no longer the project's real path, so its task temp folder "
                       "was left alone", root)
        return True
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    except FileNotFoundError:
        return True  # the project has gone, and its temp folder with it
    except OSError as failure:
        logger.warning("agent: couldn't open %s to remove a task temp folder: %s", root, failure)
        return False
    try:
        return _remove_below(root_fd, folder, made_list(made), folders_in_use, project_busy)
    except OSError as failure:
        logger.warning("agent: couldn't remove the task temp folder %s in %s: %s", folder, root,
                       failure)
        return False
    finally:
        os.close(root_fd)


def _remove_below(
    root_fd: int, folder: str, made: set[str], in_use: frozenset[str], busy: bool
) -> bool:
    clarvis_fd = _open_folder(root_fd, CLARVIS)
    if clarvis_fd is None:
        return True
    try:
        done = _remove_in_clarvis(clarvis_fd, folder, made, in_use, busy)
    finally:
        os.close(clarvis_fd)
    if done and not busy and CLARVIS in made:
        _remove_if_empty(root_fd, CLARVIS)
    return done


def _remove_in_clarvis(
    clarvis_fd: int, folder: str, made: set[str], in_use: frozenset[str], busy: bool
) -> bool:
    tmp_fd = _open_folder(clarvis_fd, TMP)
    if tmp_fd is None:
        return True
    try:
        if folder not in in_use:
            _remove_entry(tmp_fd, folder)
    finally:
        os.close(tmp_fd)
    if not busy and TMP in made:
        _remove_if_empty(clarvis_fd, TMP)
    return True


def _open_folder(parent_fd: int, name: str) -> int | None:
    """A real folder inside the parent, opened; None when absent or not a real folder."""
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except OSError as failure:
        if failure.errno not in (errno.ELOOP, errno.ENOTDIR):
            raise
        logger.warning("agent: a project's %s is a link or a file, not a folder, so no task temp "
                       "folder was removed through it", name)
        return None


def _remove_entry(tmp_fd: int, name: str) -> None:
    """The task's own folder, whatever is in it; a link or file in its place, as itself."""
    try:
        status = os.stat(name, dir_fd=tmp_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(status.st_mode):
        shutil.rmtree(name, dir_fd=tmp_fd)
    else:
        os.unlink(name, dir_fd=tmp_fd)


def _remove_if_empty(parent_fd: int, name: str) -> None:
    """`rmdir`, which refuses anything but an empty real folder — a lock file keeps it."""
    with contextlib.suppress(OSError):
        os.rmdir(name, dir_fd=parent_fd)
