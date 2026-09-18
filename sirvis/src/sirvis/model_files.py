"""Where an installed model's files are, and Reveal and Delete on them (§8, 19 September 2026).

§8's installed view lists Reveal and Delete among its actions, and the owner kept both when the
unbuilt parts of the specification were struck. LM Studio's CLI can list and load but not
remove a model (`lms` has no `rm`), so SIRVIS finds the files itself and acts on them.

**Where the files are is LM Studio's answer, not a guess from the name.** `lms ls --json` gives
each model's `path`, relative to the models folder. Two layouts were found on the owner's Mac:
- a **downloaded** model: `path` is a file (`owner/repo/file.gguf`) or a folder (`owner/repo`
  for MLX) under the models folder (`~/.lmstudio/models`);
- a **catalogue** model: `path` names an 84 KB description under `~/.lmstudio/hub/models`
  (`manifest.json`, a README, a thumbnail), and the weights — 6.7 GB for `google/gemma-4-12b-qat`
  — are a separate folder under the models folder, which `lms ls --variants --json` names after
  the `@` of the build's `indexedModelIdentifier`. Deleting the description alone would leave the
  weights on disk and gone from every list. So both go.
LM Studio spells those weight folders with other capitals than the disk has
(`gemma-4-12B-it-QAT-GGUF` for `gemma-4-12b-it-qat-gguf`), harmless on a Mac and not on Linux, so
a name that doesn't match exactly is looked up ignoring case.

**What Delete moves, and what it keeps.** A downloaded file takes its folder with it when no other
installed model lives there (a GGUF's `mmproj` companion sits beside it); otherwise just the file.
Anything another installed model also needs is kept and named. Every path must resolve inside the
models or hub folder and not be either folder itself, and a link is refused rather than followed.

**Delete is the Trash, never an erase** — the owner's standing rule. On a Mac the files move to
`~/.Trash`; on Linux (and WSL) to the freedesktop Trash, through `gio trash` where it's installed
and by the specification's own layout where it isn't, so a file manager can restore them. A move
that would cross disks is refused rather than turned into a copy of gigabytes.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
import urllib.parse
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


class ModelFilesError(Exception):
    """Reveal or Delete can't be done, with a reason a person can act on."""


@dataclass(frozen=True)
class ModelFiles:
    """One installed model's files: what Delete would move, what it keeps, what Reveal shows."""

    runtime_key: str
    targets: tuple[Path, ...]
    kept: tuple[tuple[Path, str], ...]
    reveal: Path | None
    size_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime_key": self.runtime_key,
            "targets": [str(path) for path in self.targets],
            "kept": [{"path": str(path), "because": why} for path, why in self.kept],
            "reveal": str(self.reveal) if self.reveal else None,
            "size_bytes": self.size_bytes,
        }


def locate(
    runtime_key: str,
    plain: Iterable[Any],
    variants: Iterable[Any],
    models_root: Path,
    hub_root: Path,
) -> ModelFiles:
    """Where `runtime_key`'s files are, from LM Studio's two listings. Raises if it can't tell."""
    plain_rows = [row for row in plain if isinstance(row, Mapping)]
    variant_rows = [row for row in variants if isinstance(row, Mapping)]
    everyone = {
        str(row.get("modelKey")): _own_paths(row, variant_rows, models_root, hub_root)
        for row in plain_rows
        if row.get("modelKey")
    }
    if runtime_key not in everyone:
        raise ModelFilesError(f"LM Studio doesn't list {runtime_key!r} as installed")
    mine = everyone[runtime_key]
    if not mine:
        raise ModelFilesError(
            f"{runtime_key} isn't in LM Studio's models folder — it may come with LM Studio "
            "itself, which removes it only by uninstalling"
        )
    # What each other model needs: its file where it has one, else its folder or description.
    others = [file or home for key, paths in everyone.items() if key != runtime_key
              for home, file in paths]
    targets: list[Path] = []
    kept: list[tuple[Path, str]] = []
    for home, file in mine:
        # A download's folder goes with it unless another model lives there too; then just
        # the file, unless even that is shared.
        for candidate in (home, file) if file else (home,):
            sharer = next((other for other in others if _overlaps(candidate, other)), None)
            if sharer is None:
                targets.append(candidate)
                break
        else:
            kept.append((file or home, f"another installed model uses {sharer}"))
    for path in targets:
        _inside(path, (models_root, hub_root))
    weights = [path for path in targets if not _under(path, hub_root)]
    reveal = weights[0] if weights else (targets[0] if targets else None)
    return ModelFiles(runtime_key, tuple(targets), tuple(kept), reveal,
                      sum(_size(path) for path in targets))


def _own_paths(row: Mapping[str, Any], variants: list[Mapping[str, Any]], models_root: Path,
               hub_root: Path) -> list[tuple[Path, Path | None]]:
    """The files one listed model owns, as (where, the file itself when `where` is its folder):
    its hub description and weights, or its download."""
    key, relative = str(row.get("modelKey")), str(row.get("path") or "")
    description = hub_root / relative if relative else None
    if description is not None and (description / "manifest.json").is_file():
        builds = [
            str(variant.get("indexedModelIdentifier") or "").split("@", 1)[1]
            for variant in _builds(variants)
            if str(variant.get("indexedModelIdentifier") or "").startswith(key + "@")
        ]
        weights = [_download_home(found, models_root) for found in
                   (_find(models_root, build) for build in builds) if found is not None]
        return [(description, None), *dict.fromkeys(weights)]
    found = _find(models_root, relative) if relative else None
    return [_download_home(found, models_root)] if found is not None else []


def _builds(variants: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Every build in the variants listing, whether nested in a group or listed alone."""
    found: list[Mapping[str, Any]] = []
    for row in variants:
        nested = row.get("variants")
        if isinstance(nested, list) and nested and isinstance(nested[0], Mapping):
            found.extend(item for item in nested if isinstance(item, Mapping))
        else:
            found.append(row)
    return found


def _download_home(found: Path, models_root: Path) -> tuple[Path, Path | None]:
    """A downloaded file's repository folder (`owner/repo`), which holds its companions,
    with the file itself as the fallback; a folder or a top-level file stands alone."""
    if found.is_file() and found.parent != models_root and found.parent.parent != models_root:
        return found.parent, found
    return found, None


def _find(root: Path, relative: str) -> Path | None:
    """`root/relative` as the disk spells it — an exact name first, else one matching ignoring
    case — or None. Walked name by name even on a Mac, whose disk would accept LM Studio's
    capitals as they are and hand back a path spelled unlike anything on it."""
    here = root
    for part in Path(relative).parts:
        try:
            names = {child.name: child for child in here.iterdir()}
        except OSError:
            return None
        match = names.get(part) or next(
            (child for name, child in names.items() if name.lower() == part.lower()), None
        )
        if match is None:
            return None
        here = match
    return here


def _overlaps(one: Path, other: Path) -> bool:
    return one == other or _under(one, other) or _under(other, one)


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


def _inside(path: Path, roots: Iterable[Path]) -> None:
    """Refuse a path that is a link, is a root, or resolves outside the roots."""
    if path.is_symlink():
        raise ModelFilesError(f"{path} is a link; SIRVIS moves only real files and folders")
    resolved = path.resolve()
    if not any(_under(resolved, root.resolve()) for root in roots):
        raise ModelFilesError(f"{path} is not inside LM Studio's models folders")


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file()
               and not child.is_symlink())


# ── The Trash ───────────────────────────────────────────────────────────────


def to_trash(path: Path, *, system: str | None = None, trash: Path | None = None,
             gio: str | None = None) -> str:
    """Move `path` to this user's Trash and say where it went. Never erases.

    `trash` and `gio` stand in for this user's Trash folder and `gio` in tests; left out, the
    real ones are used — `gio` wherever it is installed on Linux, since it also knows the Trash
    of other disks, and the freedesktop layout by hand where it isn't.
    """
    system = system or platform.system()
    if system == "Darwin":
        return str(_move(path, _free_name(trash or Path.home() / ".Trash", path.name)))
    gio = shutil.which("gio") if gio is None else gio
    if gio:
        finished = subprocess.run([gio, "trash", str(path)], capture_output=True, text=True,
                                  timeout=60, check=False)
        if finished.returncode == 0:
            return "the Trash"
    return str(_freedesktop_trash(path, trash))


def _freedesktop_trash(path: Path, trash: Path | None) -> Path:
    """The freedesktop.org Trash's own layout: the file in `files/`, a `.trashinfo` in `info/`."""
    trash = trash or Path(os.environ.get("XDG_DATA_HOME")
                          or Path.home() / ".local" / "share") / "Trash"
    (trash / "files").mkdir(parents=True, exist_ok=True, mode=0o700)
    (trash / "info").mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = _free_name(trash / "files", path.name)
    info = trash / "info" / f"{destination.name}.trashinfo"
    info.write_text(
        "[Trash Info]\n"
        f"Path={urllib.parse.quote(str(path.resolve()))}\n"
        f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n"
    )
    try:
        return _move(path, destination)
    except ModelFilesError:
        info.unlink(missing_ok=True)
        raise


def _move(path: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(path, destination)
    except OSError as failure:
        if failure.errno == 18:  # EXDEV: another disk; a copy of gigabytes is not a move
            raise ModelFilesError(
                f"{path} is on another disk than the Trash, so it wasn't moved; "
                "delete it from the file manager"
            ) from None
        raise ModelFilesError(
            f"{path} couldn't be moved to the Trash: {failure.strerror}"
        ) from None
    return destination


def _free_name(folder: Path, name: str) -> Path:
    candidate = folder / name
    if not candidate.exists():
        return candidate
    return folder / f"{name} {time.strftime('%Y-%m-%d %H.%M.%S')}"


# ── Reveal ──────────────────────────────────────────────────────────────────


def reveal(path: Path, *, system: str | None = None) -> str:
    """Show `path` in this machine's file manager, and say how. Raises when nothing can."""
    system = system or platform.system()
    if system == "Darwin":
        return _opened(["open", "-R", str(path)], "Finder")
    if _in_wsl():
        windows = subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True,
                                 timeout=10, check=False).stdout.strip()
        if windows:
            # Explorer answers 1 even when it opened, so its exit status says nothing.
            subprocess.run(["explorer.exe", f"/select,{windows}"], capture_output=True,
                           timeout=10, check=False)
            return "Windows Explorer"
    if shutil.which("gdbus"):
        shown = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
             "--object-path", "/org/freedesktop/FileManager1",
             "--method", "org.freedesktop.FileManager1.ShowItems",
             f"['{path.as_uri()}']", ""],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if shown.returncode == 0:
            return "the file manager"
    if shutil.which("xdg-open"):
        folder = path if path.is_dir() else path.parent
        return _opened(["xdg-open", str(folder)], "the file manager")
    raise ModelFilesError("no file manager could be asked to show it on this machine")


def _opened(command: list[str], name: str) -> str:
    finished = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    if finished.returncode != 0:
        raise ModelFilesError(f"{name} couldn't show it: {finished.stderr.strip()[:200]}")
    return name


def _in_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower()
    except OSError:
        return False
