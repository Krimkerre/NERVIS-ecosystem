"""Which folders may hold a Codex task, and its git folder (design §3.5.1; `conventions.json`).

**The workspace root rule.** A task's root is the realpath of an existing folder **strictly inside**
one of `agent_allowed_roots` (the coding folder, by default). Refused with 422
`WORKSPACE_ROOT_NOT_ALLOWED` and a `details.reason`, checked in this order:

| reason | the folder |
|---|---|
| `not_a_folder` | doesn't exist, or isn't a folder |
| `home` | is the home folder itself |
| `private_folder` | is inside ~/.config, ~/.local/share, ~/.ssh, ~/.aws, ~/Library or ~/.codex |
| `allowed_roots_entry` | is an `agent_allowed_roots` entry itself, such as the coding folder |
| `outside_allowed_roots` | is inside none of them |
| `protected_repository` | is, holds or is inside a protected checkout: NERVIS-ecosystem, clarvis |
| `denied_path` | is, contains or lies inside a denied path such as `.run`, or the skills folder |

`allowed_roots_entry` and `protected_repository` are the contract's fixed words; the rest are
this increment's, added to `conventions.json` → `workspace_root_rule`.

**The NERVIS skills folder** (`codex_skills_folder`, owner decision of 14 September 2026) is refused
with `denied_path` too, and a sentence naming it: a skill written there is on for every Codex task.

**Owner decision (b): the ecosystem's own repositories stay refused.** The setting's documented
way to allow one — an exact entry `{"path": …, "allow_protected": true}` — also needs calibration
K5c to prove "deny beats write" inside a project, which nothing records yet. So such an entry is
recognised and still refused, with a sentence that says why; it never widens anything.

**The git folder rule, checked without running git** (review AM9): when `<root>/.git` exists,
`git_dir` must be what it validly leads to — the folder itself, or a worktree's `gitdir:` target
whose own `gitdir` file points back (`codex/lock_file.py`, `git_dir_for`). A root without `.git`
takes no `git_dir`, and its lock file lives in `<root>/.clarvis/`. Anything else is 422
`GIT_DIR_NOT_ALLOWED`.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from ravis.agent import refusals
from ravis.codex.calibration.plan import protected_repositories
from ravis.codex.lock_file import git_dir_for
from ravis.config import Settings, codex_home, codex_skills_folder

logger = logging.getLogger("ravis")

#: Folders under the home folder that hold configuration, keys or other programs' data.
PRIVATE_FOLDERS = (".config", ".local/share", ".ssh", ".aws", "Library", ".codex")
#: Paths no command's output is relayed for, and no request may grant (design §4.9).
DENIED_UNDER_HOME = (
    ".config/ravis", ".config/code-server", ".config/gh", ".local/share/clarvis",
    ".codex", ".ssh", ".aws", ".netrc",
)


@dataclass(frozen=True)
class Workspace:
    root: Path
    root_hash: str
    name: str
    git_dir: Path | None


def realpath(value: str | Path) -> Path:
    return Path(os.path.realpath(os.path.expanduser(str(value))))


def root_hash(root: Path) -> str:
    """`sha256:` and the sha256 of the root's realpath — the lock view's `workspace.root_hash`."""
    return "sha256:" + hashlib.sha256(str(root).encode("utf-8")).hexdigest()


def inside(path: Path, folder: Path) -> bool:
    """Whether `path` is `folder` or lies inside it — by path parts, never by string prefix."""
    return path == folder or folder in path.parents


def related(first: Path, second: Path) -> bool:
    """Whether either folder is, contains or lies inside the other."""
    return inside(first, second) or inside(second, first)


def denied_paths(settings: Settings) -> tuple[Path, ...]:
    """Every path a task's commands are denied and whose output is never relayed."""
    home = realpath(Path.home())
    fixed = [home / name for name in DENIED_UNDER_HOME]
    codex = codex_home(settings)
    fixed += [codex / "auth.json", codex / "sessions", codex / "archived_sessions"]
    return tuple(dict.fromkeys([*fixed, *(realpath(path) for path in settings.agent_denied_paths)]))


def workspace_root(raw: object, settings: Settings) -> Path:
    """The realpath of an allowed task root, or the 422 refusal naming why not."""
    if not isinstance(raw, str) or not raw.strip():
        raise refusals.workspace_root_not_allowed("not_a_folder")
    root = realpath(raw)
    if not root.is_dir():
        raise refusals.workspace_root_not_allowed("not_a_folder")
    _refuse_home_and_private(root)
    _refuse_outside_the_allowed_roots(root, settings)
    _refuse_protected(root, settings)
    _refuse_the_skills_folder(root, settings)
    if any(related(root, denied) for denied in denied_paths(settings)):
        raise refusals.workspace_root_not_allowed("denied_path")
    return root


def _refuse_the_skills_folder(root: Path, settings: Settings) -> None:
    """A task never works in, or around, the NERVIS skills folder (owner decision, 14 Sep 2026).

    A skill written there would be on for every later Codex task, and a task's root is writable to
    its commands, so a root that is the folder, holds it or lies inside it is refused — real paths
    compared on both sides, and whether or not the folder exists yet, since RAVIS makes it when
    Codex starts. The reason word is `denied_path`, but the folder isn't one of `denied_paths`:
    a command that reads a skill's SKILL.md keeps its output.
    """
    folder = codex_skills_folder(settings)
    if related(root, folder):
        logger.warning(
            "agent: refused a Codex task in %s: it is, holds or lies inside the NERVIS skills "
            "folder %s", root, folder,
        )
        raise refusals.workspace_root_not_allowed(
            "denied_path", refusals.skills_folder_refused(folder)
        )


def _refuse_home_and_private(root: Path) -> None:
    home = realpath(Path.home())
    if root == home:
        raise refusals.workspace_root_not_allowed("home")
    if any(inside(root, home / folder) for folder in PRIVATE_FOLDERS):
        raise refusals.workspace_root_not_allowed("private_folder")


def _refuse_outside_the_allowed_roots(root: Path, settings: Settings) -> None:
    entries = settings.agent_allowed_roots
    containers = [realpath(entry) for entry in entries if isinstance(entry, str)]
    if root in containers:
        raise refusals.workspace_root_not_allowed(
            "allowed_roots_entry", "Choose a project folder, not the folder that holds them all."
        )
    if not any(folder in root.parents for folder in containers):
        raise refusals.workspace_root_not_allowed("outside_allowed_roots")


def _refuse_protected(root: Path, settings: Settings) -> None:
    listed = [realpath(path) for path in settings.agent_protected_repositories]
    protected = {*listed, *(realpath(path) for path in protected_repositories())}
    if not any(related(root, repository) for repository in protected):
        return
    if root in _allowances(settings):
        # Documented, not enabled (owner decision (b)): it needs calibration K5c first.
        raise refusals.workspace_root_not_allowed(
            "protected_repository",
            "Codex doesn't work on the ecosystem's own repositories until calibration proves "
            "that its denied files stay denied inside a project.",
        )
    raise refusals.workspace_root_not_allowed(
        "protected_repository", "Codex doesn't work on the ecosystem's own repositories."
    )


def _allowances(settings: Settings) -> set[Path]:
    return {
        realpath(entry["path"])
        for entry in settings.agent_allowed_roots
        if isinstance(entry, dict) and entry.get("allow_protected") is True
        and isinstance(entry.get("path"), str)
    }


def git_dir(root: Path, raw: object) -> Path | None:
    """The validated git folder for `root`, None for a root without git, or the 422 refusal."""
    given = raw if isinstance(raw, str) and raw.strip() else None
    if not (root / ".git").exists():
        if given is not None or raw not in (None, ""):
            raise refusals.git_dir_not_allowed()
        return None
    found = git_dir_for(root)
    if found is None or given is None or realpath(given) != found:
        raise refusals.git_dir_not_allowed()
    return found


def workspace(raw_root: object, raw_git_dir: object, settings: Settings) -> Workspace:
    root = workspace_root(raw_root, settings)
    return Workspace(root, root_hash(root), root.name, git_dir(root, raw_git_dir))
