"""Checking a skill before it is installed: the Agent Skills specification's rules, and RAVIS's.

**Why** (owner decisions, 15 September 2026). NERVIS's Skills page installs skills from a GitHub
folder, a zip file, or a website's Agent Skills index, and nothing is installed before the owner has
seen a review. Every package, wherever it came from, passes through `check` here, and the review
shows what it found. The rules come from two places:

**The Agent Skills specification** (`SPECIFICATION`, read 15 September 2026). A skill is a folder
holding `SKILL.md`: YAML front matter, then Markdown.
- `name`: required, 1–64 characters, only `a-z`, `0-9` and hyphens, not starting or ending with
  a hyphen, no two hyphens together, **and the same as the folder's name**. The specification's
  text says "unicode lowercase alphanumeric" and then lists `a-z` and `0-9`; RAVIS takes the list,
  since the name is also a folder on this Mac.
- `description`: required, 1–1024 characters.
- `license`: optional text. `compatibility`: optional, 1–500 characters. `metadata`: optional, a
  mapping (values that aren't text are warned about, not refused). `allowed-tools`: optional text.
- Anything else in the front matter isn't in the specification: warned about, never refused,
  because skills written for other clients carry fields of their own.
- `SKILL.md` over 500 lines is warned about (the specification's recommendation).

**RAVIS's own rules**, because a skill's text becomes a model's instructions and its scripts may
be run by Codex:
- Caps: a zip or archive at most `MOST_ARCHIVE_BYTES`, each file at most `MOST_FILE_BYTES`, all of
  them at most `MOST_TOTAL_BYTES`, at most `MOST_FILES` files, a `SKILL.md` at most 64 KB (the most
  the other models are ever served, `skill_catalog.MOST_FILE_BYTES`) and UTF-8 text.
- **Paths stay inside the skill**: relative, `/`-separated, no empty, `.` or `..` part, no
  backslash, no control character, at most `MOST_PATH_DEPTH` folders deep (zip-slip).
- **No links and no special files**: a symbolic or hard link, a device, a pipe — refused, not
  skipped, since a skill that needs one isn't the skill the owner reviewed.
- An encrypted zip entry is refused; macOS's `__MACOSX/` folder and `.DS_Store` files, which Finder
  adds to every zip it makes, are left out and counted.
- Each file is sorted into a **kind**: `skill_md`; `script`, by extension, a `#!` first line, or an
  executable bit; `binary`, when it isn't UTF-8 text; or `text`.

A package is staged into RAVIS's own data folder (`stage`), never the skills folder, until the owner
confirms. `changes` compares an installed skill with an update, for the update's review, and says
whether the update switches the skill off (`resets_switches`).
"""

from __future__ import annotations

import difflib
import hashlib
import io
import os
import re
import stat
import tarfile
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ravis.agent.skill_catalog import MOST_FILE_BYTES as MOST_SKILL_MD_BYTES
from ravis.agent.skill_catalog import (
    MOST_FILE_PATH_CHARACTERS,
    SKILL_FILE,
    front_matter,
    unprintable,
)

#: The rules installs are checked against, as the Skills page names them.
SPECIFICATION = "https://agentskills.io/specification"
#: A zip upload, or an archive a website's index names. RAVIS takes requests of up to 10 MB
#: (`max_request_bytes`), so an upload of 8 MB fits with its headers to spare.
MOST_ARCHIVE_BYTES = 8 * 1024 * 1024
MOST_FILE_BYTES = 5 * 1024 * 1024
MOST_TOTAL_BYTES = 20 * 1024 * 1024
MOST_FILES = 300
#: Entries an archive may list, folders and left-out files included, before RAVIS reads none.
MOST_ARCHIVE_ENTRIES = 1000
MOST_PATH_DEPTH = 10
#: The specification's limits.
MOST_NAME_CHARACTERS = 64
MOST_DESCRIPTION_CHARACTERS = 1024
MOST_COMPATIBILITY_CHARACTERS = 500
LONG_SKILL_MD_LINES = 500
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SPECIFIED_FIELDS = frozenset({
    "name", "description", "license", "compatibility", "metadata", "allowed-tools",
})
#: Files that are scripts by their extension alone.
SCRIPT_EXTENSIONS = frozenset({
    ".applescript", ".bash", ".bat", ".cjs", ".cmd", ".command", ".exe", ".fish", ".go", ".jar",
    ".js", ".ksh", ".lua", ".mjs", ".php", ".pl", ".ps1", ".py", ".rb", ".rs", ".scpt", ".sh",
    ".swift", ".tcl", ".ts", ".zsh",
})
#: What Finder puts in every zip it makes, left out rather than refused.
LEFT_OUT_FOLDERS = frozenset({"__MACOSX"})
LEFT_OUT_FILES = frozenset({".DS_Store"})
#: A license file in the skill's own folder, matched without regard to case.
LICENSE_FILES = ("license", "license.txt", "license.md", "copying", "copying.txt")
#: How much of a `SKILL.md` diff the update's review shows.
MOST_DIFF_LINES = 400


class PackageRefusedError(Exception):
    """A package that can't be installed: `reason` is `SKILL_REFUSED`'s detail; `why`, in words."""

    def __init__(self, reason: str, why: str) -> None:
        super().__init__(why)
        self.reason = reason
        self.why = why


@dataclass(frozen=True)
class Incoming:
    """One file of a package as it arrived, before any rule: its path, bytes and executable bit."""

    path: str
    data: bytes
    executable: bool = False


@dataclass(frozen=True)
class PackageFile:
    path: str
    size: int
    kind: str
    sha256: str
    executable: bool

    def view(self) -> dict[str, Any]:
        return {"path": self.path, "bytes": self.size, "kind": self.kind}


@dataclass(frozen=True)
class Package:
    """A skill that passed every rule, with what the review shows."""

    name: str
    description: str
    license: str | None
    #: `front_matter`, `file` (a license file in the folder) or None when neither says.
    license_from: str | None
    compatibility: str | None
    skill_md: str
    files: tuple[PackageFile, ...]
    #: `[code, message]` pairs, in the order found.
    warnings: tuple[tuple[str, str], ...]
    content_hash: str
    data: Mapping[str, bytes]


# ── Unpacking, with the path and file rules ──────────────────────────────────


def plain_path(path: str) -> str:
    """A path inside the skill, or `unsafe_path`: the zip-slip rule."""
    parts = path.split("/")
    if (not 0 < len(path) <= MOST_FILE_PATH_CHARACTERS or unprintable(path) or "\\" in path
            or any(part in ("", ".", "..") for part in parts) or len(parts) > MOST_PATH_DEPTH + 1
            or re.match(r"[A-Za-z]:", path)):
        raise PackageRefusedError("unsafe_path", f"The path {path!r} would leave the skill's "
                                                 "folder, or isn't a plain relative path.")
    return path


def left_out(path: str) -> bool:
    """Whether a path is something Finder added to a zip, which RAVIS leaves out."""
    parts = path.split("/")
    return parts[0] in LEFT_OUT_FOLDERS or parts[-1] in LEFT_OUT_FILES


def from_archive(data: bytes) -> tuple[list[Incoming], int]:
    """A zip's or a `.tar.gz`'s files, and how many Finder-added files were left out."""
    if len(data) > MOST_ARCHIVE_BYTES:
        raise PackageRefusedError("archive_too_large", "The file is larger than "
                                  f"{MOST_ARCHIVE_BYTES // (1024 * 1024)} MB, more than RAVIS "
                                  "installs from.")
    if data[:2] == b"PK":
        return _from_zip(data)
    if data[:2] == b"\x1f\x8b":
        return _from_tar(data)
    raise PackageRefusedError("not_archive", "That isn't a zip file.")


def _from_zip(data: bytes) -> tuple[list[Incoming], int]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        raise PackageRefusedError("not_archive", "That isn't a zip file RAVIS can read.") from None
    with archive:
        entries = archive.infolist()
        _entry_count(len(entries))
        files, skipped, total = [], 0, 0
        for entry in entries:
            if left_out(entry.filename.rstrip("/")):
                skipped += 1
                continue
            mode = entry.external_attr >> 16
            if entry.is_dir() or stat.S_ISDIR(mode):
                plain_path(entry.filename.rstrip("/"))
                continue
            _plain_zip_entry(entry, mode)
            body = _zip_bytes(archive, entry)
            total = _within_total(total + len(body))
            files.append(Incoming(entry.filename, body, bool(mode & 0o111)))
    return files, skipped


def _plain_zip_entry(entry: zipfile.ZipInfo, mode: int) -> None:
    plain_path(entry.filename)
    if stat.S_ISLNK(mode):
        raise _link(entry.filename)
    if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
        raise _special(entry.filename)
    if entry.flag_bits & 0x1:
        raise PackageRefusedError("encrypted", f"{entry.filename} is encrypted, so RAVIS can't "
                                               "check it.")
    if entry.file_size > MOST_FILE_BYTES:
        raise _file_too_large(entry.filename)


def _zip_bytes(archive: zipfile.ZipFile, entry: zipfile.ZipInfo) -> bytes:
    """An entry's bytes, read no further than the cap whatever its header claims (a zip bomb)."""
    try:
        with archive.open(entry) as handle:
            body = handle.read(MOST_FILE_BYTES + 1)
    except (zipfile.BadZipFile, OSError, NotImplementedError, RuntimeError, EOFError):
        raise PackageRefusedError("not_archive", f"RAVIS couldn't read {entry.filename} from "
                                                 "the zip file.") from None
    if len(body) > MOST_FILE_BYTES:
        raise _file_too_large(entry.filename)
    return body


def _from_tar(data: bytes) -> tuple[list[Incoming], int]:
    """A `.tar.gz`'s files, by the same rules as a zip's. Only RAVIS's own refusal escapes the
    `try`: anything the archive itself breaks on is `not_archive`."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            members = archive.getmembers()
            _entry_count(len(members))
            return _tar_files(archive, members)
    except (tarfile.TarError, OSError, EOFError):
        raise PackageRefusedError("not_archive", "That isn't an archive RAVIS can read.") from None


def _tar_files(archive: tarfile.TarFile, members: list[tarfile.TarInfo]
               ) -> tuple[list[Incoming], int]:
    files, skipped, total = [], 0, 0
    for member in members:
        name = member.name.removeprefix("./").rstrip("/")
        if not name or left_out(name):
            skipped += 1 if name else 0
            continue
        body = _tar_bytes(archive, member, name)
        if body is None:
            continue
        total = _within_total(total + len(body))
        files.append(Incoming(name, body, bool(member.mode & 0o111)))
    return files, skipped


def _tar_bytes(archive: tarfile.TarFile, member: tarfile.TarInfo, name: str) -> bytes | None:
    """A regular member's bytes; None for a folder; refused for anything else."""
    plain_path(name)
    if member.isdir():
        return None
    if member.issym() or member.islnk():
        raise _link(name)
    if not member.isreg():
        raise _special(name)
    if member.size > MOST_FILE_BYTES:
        raise _file_too_large(name)
    handle = archive.extractfile(member)
    body = handle.read(MOST_FILE_BYTES + 1) if handle is not None else b""
    if len(body) > MOST_FILE_BYTES:
        raise _file_too_large(name)
    return body


def _entry_count(count: int) -> None:
    if count > MOST_ARCHIVE_ENTRIES:
        raise PackageRefusedError("too_many_files", f"The archive lists {count} entries, more "
                                                    f"than the {MOST_ARCHIVE_ENTRIES} RAVIS reads.")


def _within_total(total: int) -> int:
    if total > MOST_TOTAL_BYTES:
        raise PackageRefusedError("too_large", "The skill's files add up to more than "
                                  f"{MOST_TOTAL_BYTES // (1024 * 1024)} MB.")
    return total


def _link(path: str) -> PackageRefusedError:
    return PackageRefusedError("link", f"{path} is a link. RAVIS doesn't install links, since one "
                                       "can point outside the skill.")


def _special(path: str) -> PackageRefusedError:
    return PackageRefusedError("special_file", f"{path} isn't an ordinary file or folder.")


def _file_too_large(path: str) -> PackageRefusedError:
    return PackageRefusedError("file_too_large", f"{path} is larger than "
                               f"{MOST_FILE_BYTES // (1024 * 1024)} MB.")


def one_skill(files: list[Incoming]) -> tuple[str | None, list[Incoming]]:
    """An archive's skill: a `SKILL.md` at its top (no folder name), or one folder holding it.

    The folder's name comes back with the files made relative to it.
    """
    if any(file.path == SKILL_FILE for file in files):
        return None, files
    tops = {file.path.split("/", 1)[0] for file in files}
    if len(tops) == 1:
        top = next(iter(tops))
        inner = [Incoming(file.path.split("/", 1)[1], file.data, file.executable)
                 for file in files if "/" in file.path]
        if any(file.path == SKILL_FILE for file in inner):
            return top, inner
    if any(file.path.rsplit("/", 1)[-1] == SKILL_FILE for file in files):
        raise PackageRefusedError("not_one_skill", "The archive doesn't hold one skill: its "
                                  "SKILL.md must be at the top, or inside one folder at the top.")
    raise PackageRefusedError("no_skill_md", "There is no SKILL.md, so this isn't a skill.")


# ── The rules, and what the review shows ─────────────────────────────────────


def check(folder: str, incoming: Iterable[Incoming], left_out_count: int = 0) -> Package:
    """A package whose folder will be `folder`, checked against every rule above."""
    files = _files(incoming)
    text = _skill_md_text(files)
    header = front_matter(text)
    if isinstance(header, str):
        raise PackageRefusedError("front_matter", f"The skill can't be read: {header}.")
    name, description = _name(header, folder), _description(header)
    warnings = _field_warnings(header)
    listed = tuple(PackageFile(path, len(file.data), kind_of(path, file.data, file.executable),
                               hashlib.sha256(file.data).hexdigest(), file.executable)
                   for path, file in sorted(files.items()))
    warnings += _package_warnings(text, listed, left_out_count)
    licence, licence_from = _license(header, files)
    compatibility = header.get("compatibility")
    return Package(
        name=name, description=" ".join(description.split()), license=licence,
        license_from=licence_from,
        compatibility=compatibility if isinstance(compatibility, str) else None,
        skill_md=text, files=listed, warnings=warnings,
        content_hash=content_hash((file.path, file.sha256) for file in listed),
        data={path: file.data for path, file in files.items()},
    )


def front_matter_problem(header: dict[str, Any], folder: str) -> str | None:
    """What an install's review would refuse in this front matter, for a listing; else None."""
    try:
        _name(header, folder)
        _description(header)
        _field_warnings(header)
    except PackageRefusedError as refused:
        return refused.why
    return None


def _files(incoming: Iterable[Incoming]) -> dict[str, Incoming]:
    files: dict[str, Incoming] = {}
    total = 0
    for file in incoming:
        path = plain_path(file.path)
        if path in files:
            raise PackageRefusedError("duplicate_path", f"{path} is in the skill twice.")
        if len(file.data) > MOST_FILE_BYTES:
            raise _file_too_large(path)
        total = _within_total(total + len(file.data))
        files[path] = file
        if len(files) > MOST_FILES:
            raise PackageRefusedError("too_many_files", f"The skill has more than {MOST_FILES} "
                                                        "files.")
    return files


def _skill_md_text(files: dict[str, Incoming]) -> str:
    skill_md = files.get(SKILL_FILE)
    if skill_md is None:
        raise PackageRefusedError("no_skill_md", "There is no SKILL.md, so this isn't a skill.")
    if len(skill_md.data) > MOST_SKILL_MD_BYTES:
        raise PackageRefusedError("skill_md_too_large", "Its SKILL.md is larger than "
                                  f"{MOST_SKILL_MD_BYTES // 1024} KB, more than RAVIS can read.")
    try:
        return skill_md.data.decode("utf-8")
    except UnicodeDecodeError:
        raise PackageRefusedError("skill_md_not_text", "Its SKILL.md isn't UTF-8 text.") from None


def _name(header: dict[str, Any], folder: str) -> str:
    name = header.get("name")
    if not isinstance(name, str) or not name:
        raise PackageRefusedError("name_invalid", "Its SKILL.md has no name.")
    if len(name) > MOST_NAME_CHARACTERS or not NAME.fullmatch(name):
        raise PackageRefusedError("name_invalid", f"Its name {name!r} doesn't follow the "
                                  "specification: at most 64 characters, only a-z, 0-9 and "
                                  "hyphens, not starting or ending with a hyphen, and no two "
                                  "hyphens together.")
    if name != folder:
        raise PackageRefusedError("name_mismatch", f"Its name {name!r} isn't the name of its "
                                  f"folder, {folder!r}; the specification says they must match.")
    return name


def _description(header: dict[str, Any]) -> str:
    description = header.get("description")
    if not isinstance(description, str) or not description.strip():
        raise PackageRefusedError("description_invalid", "Its SKILL.md has no description.")
    if len(description) > MOST_DESCRIPTION_CHARACTERS:
        raise PackageRefusedError("description_invalid", "Its description is longer than "
                                  f"{MOST_DESCRIPTION_CHARACTERS} characters.")
    return description


def _field_warnings(header: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """The optional fields: refused when the specification's rule is broken, warned otherwise."""
    for field in ("license", "allowed-tools"):
        if field in header and header[field] is not None and not isinstance(header[field], str):
            raise PackageRefusedError("field_invalid", f"Its {field} must be text.")
    compatibility = header.get("compatibility")
    if "compatibility" in header and (not isinstance(compatibility, str) or not compatibility
                                      or len(compatibility) > MOST_COMPATIBILITY_CHARACTERS):
        raise PackageRefusedError("field_invalid", "Its compatibility must be text of 1 to "
                                  f"{MOST_COMPATIBILITY_CHARACTERS} characters.")
    metadata = header.get("metadata")
    if "metadata" in header and not isinstance(metadata, dict):
        raise PackageRefusedError("field_invalid", "Its metadata must be a list of names and "
                                                   "values.")
    warnings: list[tuple[str, str]] = []
    if isinstance(metadata, dict) and not all(isinstance(key, str) and isinstance(value, str)
                                              for key, value in metadata.items()):
        warnings.append(("metadata_not_text", "Some of its metadata values aren't text, which "
                                              "the specification asks for."))
    unknown = sorted(str(field) for field in header if field not in SPECIFIED_FIELDS)
    if unknown:
        warnings.append(("unknown_fields", "Its SKILL.md has fields the specification doesn't "
                                           f"name: {', '.join(unknown)}."))
    return tuple(warnings)


def _package_warnings(text: str, files: tuple[PackageFile, ...], left_out_count: int
                      ) -> tuple[tuple[str, str], ...]:
    warnings: list[tuple[str, str]] = []
    scripts = [file.path for file in files if file.kind == "script"]
    if scripts:
        warnings.append(("has_scripts", f"It has {len(scripts)} script"
                         f"{'' if len(scripts) == 1 else 's'} Codex could run: "
                         f"{', '.join(scripts[:5])}{'…' if len(scripts) > 5 else ''}."))
    if len(text.splitlines()) > LONG_SKILL_MD_LINES:
        warnings.append(("long_skill_md", f"Its SKILL.md is longer than {LONG_SKILL_MD_LINES} "
                                          "lines, which the specification advises against."))
    if left_out_count:
        warnings.append(("left_out", f"{left_out_count} file{'' if left_out_count == 1 else 's'} "
                                     "macOS adds to zip files (__MACOSX, .DS_Store) left out."))
    return tuple(warnings)


def _license(header: dict[str, Any], files: dict[str, Incoming]) -> tuple[str | None, str | None]:
    """The front matter's license; else a license file's first line; else nothing said."""
    said = header.get("license")
    if isinstance(said, str) and said.strip():
        return " ".join(said.split())[:300], "front_matter"
    for path, file in sorted(files.items()):
        if "/" not in path and path.lower() in LICENSE_FILES:
            first = next((line.strip() for line in file.data.decode("utf-8", "replace").splitlines()
                          if line.strip()), "")
            return f"{path}: {first[:200]}" if first else path, "file"
    return None, None


def kind_of(path: str, data: bytes, executable: bool) -> str:
    """`skill_md`, `script`, `binary` or `text`, by the rules in the module's docstring."""
    if path == SKILL_FILE:
        return "skill_md"
    if Path(path).suffix.lower() in SCRIPT_EXTENSIONS or data.startswith(b"#!") or executable:
        return "script"
    if b"\x00" in data[:8192]:
        return "binary"
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    return "text"


def content_hash(files: Iterable[tuple[str, str]]) -> str:
    """One hash for a whole skill: every file's path and its own sha256, in path order."""
    digest = hashlib.sha256()
    for path, sha256 in sorted(files):
        digest.update(f"{path}\0{sha256}\n".encode())
    return f"sha256:{digest.hexdigest()}"


# ── Staging, and what an installed skill holds ───────────────────────────────


def stage(package: Package, folder: Path) -> Path:
    """Write the package into `folder/<name>` (made private to this user), and return that."""
    target = folder / package.name
    target.mkdir(mode=0o700, parents=True)
    for file in package.files:
        path = target / file.path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(package.data[file.path])
        os.chmod(path, 0o755 if file.executable else 0o644)
    for inner in (target, *(p for p in target.rglob("*") if p.is_dir())):
        os.chmod(inner, 0o755)
    return target


def installed_files(folder: Path) -> dict[str, bytes]:
    """An installed skill's ordinary files by relative path, read no further than the caps.

    A link or special file RAVIS finds there (it installs none) counts as a file whose bytes are
    `LINK_MARKER`, so the folder's hash says it changed.
    """
    found: dict[str, bytes] = {}
    total = 0
    for root, folders, names in os.walk(folder, followlinks=False):
        folders.sort()
        for name in sorted(names) + [inner for inner in folders if os.path.islink(
                os.path.join(root, inner))]:
            path = Path(root) / name
            relative = path.relative_to(folder).as_posix()
            if path.is_symlink() or not path.is_file():
                found[relative] = LINK_MARKER
                continue
            with open(path, "rb") as handle:
                data = handle.read(MOST_FILE_BYTES + 1)
            total += len(data)
            if len(data) > MOST_FILE_BYTES or total > MOST_TOTAL_BYTES * 2 or len(found) > 5000:
                raise PackageRefusedError("too_large", "The installed skill's folder has grown "
                                          "larger than RAVIS compares.")
            found[relative] = data
    return found


LINK_MARKER = b"\0a link or special file\0"


def folder_hash(files: Mapping[str, bytes]) -> str:
    return content_hash((path, hashlib.sha256(data).hexdigest()) for path, data in files.items())


@dataclass(frozen=True)
class Changes:
    """What an update changes, for its review."""

    added: tuple[PackageFile, ...]
    removed: tuple[str, ...]
    changed: tuple[PackageFile, ...]
    skill_md_diff: str
    skill_md_changed: bool
    scripts_changed: tuple[str, ...]

    @property
    def any(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    @property
    def resets_switches(self) -> bool:
        """**The update rule** (decided 15 September 2026): both switches go off when `SKILL.md`
        changed, or a script was added or changed — what a model follows or Codex may run is then
        not what the owner switched on. Changes to other files, and removed files, keep them."""
        return self.skill_md_changed or bool(self.scripts_changed)

    def view(self) -> dict[str, Any]:
        return {
            "added": [file.view() for file in self.added],
            "removed": list(self.removed),
            "changed": [file.view() for file in self.changed],
            "skill_md_diff": self.skill_md_diff,
        }


def changes(installed: Mapping[str, bytes], package: Package) -> Changes:
    """The difference between an installed skill's files and an update's."""
    new = {file.path: file for file in package.files}
    added = tuple(new[path] for path in sorted(new.keys() - installed.keys()))
    removed = tuple(sorted(installed.keys() - new.keys()))
    changed = tuple(new[path] for path in sorted(new.keys() & installed.keys())
                    if hashlib.sha256(installed[path]).hexdigest() != new[path].sha256)
    before = installed.get(SKILL_FILE, b"").decode("utf-8", "replace")
    return Changes(
        added=added, removed=removed, changed=changed,
        skill_md_diff=_diff(before, package.skill_md),
        skill_md_changed=before != package.skill_md,
        scripts_changed=tuple(file.path for file in (*added, *changed) if file.kind == "script"),
    )


def _diff(before: str, after: str) -> str:
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                      "installed SKILL.md", "update's SKILL.md", lineterm=""))
    if len(lines) > MOST_DIFF_LINES:
        rest = len(lines) - MOST_DIFF_LINES
        lines = [*lines[:MOST_DIFF_LINES], f"… {rest} more line{'' if rest == 1 else 's'}"]
    return "\n".join(lines)
