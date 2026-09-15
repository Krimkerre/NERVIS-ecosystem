"""The skills RAVIS reads itself, so that models other than Codex can use them too.

**Why** (owner decisions, 15 September 2026). Codex finds skill folders on its own
(`agent/skills.py`), but Clarvis's own engine and NERVIS chat reach their models through the OpenAI
chat-completions API, which has no notion of a skill folder. The owner decided they should use
skills too, with one switch per skill for Codex and one for **the other models** — Clarvis's own
engine and NERVIS chat together — so a skill can be on for one and off for the other. So RAVIS
reads the skill folders itself, without Codex: it is the one place that knows which skills exist,
which are on for which engine, and what they say.

**A skill** is a folder holding a `SKILL.md`: YAML front matter with `name` and `description`, then
the instructions, and perhaps other files those instructions point at.

**Where RAVIS reads them** — two folders, NERVIS's first:
- `nervis`: the NERVIS skills folder (`codex_skills_folder`). When that setting can't be used
  (`skills.folder_refusal`), none of its skills are listed and the folder's problem says why.
- `personal`: the owner's personal skills, `skills_personal_folder` (`~/.agents/skills`).
Codex's built-in skills aren't read here: they come from Codex's own `skills/list`, and exist only
for Codex.

**The reading rules**, each because a skill's text becomes instructions to a model:
- A skill is a folder at most `MOST_DEPTH` folders below its source's root holding a `SKILL.md`.
  Hidden folders are skipped, and a skill's own sub-folders aren't searched for more skills.
- **A link is followed only when its real path stays inside that source's root.** A link leading
  out to a skill is listed with that problem and never read; one leading anywhere else is left
  out. A file of a skill must really lie inside the skill's own folder, links followed.
- **A `SKILL.md`, and any file served, is at most `MOST_FILE_BYTES`**, and UTF-8 text.
- A malformed skill — no front matter, YAML that doesn't parse, no name or no description — is
  listed with its problem in plain words and never served. Nothing in a folder makes a read fail.
- Each skill is listed once, by the real path of its `SKILL.md`.

**Identifiers**: `<source>/<the skill's folder relative to its root>`, e.g. `nervis/nervis-notes`.
Stable while the folder keeps its name and place, readable in a model's instructions, and never a
full path on this Mac.

**Lined up with Codex's list by real path** (decided 15 September 2026). Codex lists a skill by the
path of its `SKILL.md` as Codex found it. A skill Codex lists and a skill RAVIS read are one skill
when the real path of Codex's path — links followed — is the real path of RAVIS's `SKILL.md`. Real
paths on both sides, so it doesn't matter whether Codex resolves links before it lists, which is
unmeasured. Codex's switches stay keyed by the path Codex lists, exactly as since 0.26.0; the other
models' are keyed by the real path RAVIS read. A skill Codex lists that RAVIS didn't read — built
in, or somewhere RAVIS doesn't look — is Codex's only.

**Served only while switched on** (`ModelSkills`). The other models' callers read the list of the
skills switched on for them, and one such skill's files; a skill switched off, unknown or malformed
is never served, whatever a caller names. Reads are logged by name and size, never by content.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import stat
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from ravis.agent.roots import inside, realpath
from ravis.agent.skills import (
    DESCRIPTION_LIMIT,
    MODELS,
    MODELS_ON_UNLESS_SWITCHED,
    SOURCES,
    Skill,
    SkillChoices,
    folder_refusal,
)
from ravis.codex import refusals
from ravis.config import Settings, codex_skills_folder, skills_personal_folder
from ravis.storage.database import Database

logger = logging.getLogger("ravis")

#: The file that makes a folder a skill.
SKILL_FILE = "SKILL.md"
#: The largest `SKILL.md`, or other file of a skill, RAVIS reads or serves. 64 KB of text is about
#: 16,000 tokens: already more than one skill should add to a chat model's instructions.
MOST_FILE_BYTES = 64 * 1024
#: How deep below its root a skill may lie, how many folders one read looks in, and how many skills
#: one folder lists — so a folder full of links, or a large tree, never makes a read slow.
MOST_DEPTH = 4
MOST_FOLDERS = 2000
MOST_SKILLS = 200
#: The longest name a skill may have, and the longest file path a caller may ask for inside one.
MOST_NAME_CHARACTERS = 100
MOST_FILE_PATH_CHARACTERS = 512


# ── What RAVIS read ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CatalogSkill:
    """One skill RAVIS read from a folder, whole or with its problem."""

    #: `<source>/<folder relative to its root>`: the identifier the other models' callers use.
    id: str
    source: str
    #: From the front matter; the folder's name when the front matter couldn't be read.
    name: str
    #: One line, at most `DESCRIPTION_LIMIT` characters; empty when it couldn't be read.
    description: str
    #: The real path of its `SKILL.md`; for a link that leads out of the root, the link's own path.
    path: str
    #: The real path of the skill's folder, the only folder its files are served from; empty for a
    #: link that leads out of the root, which is never read.
    folder: str
    #: Why the other models can't use it, in plain words; None when RAVIS read it whole.
    problem: str | None


@dataclass(frozen=True)
class Folder:
    """One source's folder as RAVIS read it just now."""

    source: str
    path: str
    #: Why its skills aren't all listed — the setting can't be used, the folder can't be read, or
    #: the read stopped at a limit; None otherwise, a folder that isn't there yet included.
    problem: str | None
    skills: tuple[CatalogSkill, ...]


class SkillFileRefusedError(Exception):
    """A file RAVIS won't serve from a skill: `reason` is `SKILL_FILE_REFUSED`'s detail."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SkillFileMissingError(Exception):
    """No file RAVIS may read at that path inside the skill; `why`, in plain words."""

    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


def read_catalog(settings: Settings) -> list[Folder]:
    """Both folders read from disk now, NERVIS's first, each skill once by its real path."""
    nervis = _read_folder("nervis", codex_skills_folder(settings), folder_refusal(settings))
    personal = _read_folder("personal", skills_personal_folder(settings),
                            personal_folder_refusal(settings))
    seen: set[str] = set()
    folders = []
    for folder in (nervis, personal):
        kept = []
        for skill in folder.skills:
            if skill.path not in seen:
                seen.add(skill.path)
                kept.append(skill)
        folders.append(replace(folder, skills=tuple(kept)))
    return folders


def personal_folder_refusal(settings: Settings) -> str | None:
    """Why the personal skills folder setting can't be used; None when it can."""
    raw = settings.skills_personal_folder
    if not raw.strip() or not os.path.isabs(os.path.expanduser(raw)):
        return f"the personal skills folder {raw!r} isn't an absolute path"
    return None


def _read_folder(source: str, root: Path, refused: str | None) -> Folder:
    if refused is not None:
        return Folder(source, str(root), refused, ())
    try:
        found, stopped = _skill_folders(root)
    except FileNotFoundError:
        # Not made yet: nothing to list, and nothing wrong. RAVIS makes NERVIS's when Codex starts.
        return Folder(source, str(root), None, ())
    except NotADirectoryError:
        return Folder(source, str(root), f"{root} isn't a folder", ())
    except OSError as failure:
        why = failure.strerror or type(failure).__name__
        return Folder(source, str(root), f"RAVIS can't read {root}: {why}", ())
    skills = tuple(_skill(source, root, reached, real) for reached, real in found)
    return Folder(source, str(root), stopped, skills)


def _skill_folders(root: Path) -> tuple[list[tuple[Path, Path | None]], str | None]:
    """Every skill folder under `root`: as reached, and its real path — None for a link leading out.

    Breadth first and by name, so the order, and which of two routes to one folder is kept, don't
    depend on the disk. The root is a real path already (`config`). Returns why it stopped early.
    """
    found: list[tuple[Path, Path | None]] = []
    visited = {root}
    waiting: deque[tuple[Path, Path, int]] = deque([(root, root, 0)])
    looked = 0
    while waiting:
        reached, real, depth = waiting.popleft()
        looked += 1
        if looked > MOST_FOLDERS or len(found) >= MOST_SKILLS:
            return found, (f"RAVIS stopped reading {root} after {MOST_SKILLS} skills or "
                           f"{MOST_FOLDERS} folders, so the rest aren't listed")
        names = _names(real, depth)
        if depth > 0 and SKILL_FILE in names:
            found.append((reached, real))
            continue
        for child, target in _children(root, reached, real, names, depth):
            if target is None:
                found.append((child, None))
            elif target not in visited:
                visited.add(target)
                waiting.append((child, target, depth + 1))
    return found, None


def _names(folder: Path, depth: int) -> list[str]:
    """What a folder holds, by name. The root must be readable; a sub-folder that isn't is empty."""
    try:
        return sorted(os.listdir(folder))
    except OSError:
        if depth == 0:
            raise
        return []


def _children(root: Path, reached: Path, real: Path, names: list[str], depth: int
              ) -> list[tuple[Path, Path | None]]:
    """The sub-folders to look in: never a hidden one, and a link only while it stays in the root.

    A link leading out comes back with no real path when a skill is there, so it is listed with its
    problem; a link leading out to anything else is left out. Only `stat` goes through such a link.
    """
    if depth >= MOST_DEPTH:
        return []
    children: list[tuple[bool, Path, Path | None]] = []
    for name in names:
        path = real / name
        if name.startswith(".") or not os.path.isdir(path):
            continue
        target = realpath(path)
        if target != root and inside(target, root):
            children.append((os.path.islink(path), reached / name, target))
        elif not inside(target, root) and os.path.isfile(path / SKILL_FILE):
            children.append((True, reached / name, None))
    # Real folders before links to them, so a skill is known by its own folder's name, not an alias.
    children.sort(key=lambda child: child[0])
    return [(child, target) for _, child, target in children]


def _skill(source: str, root: Path, reached: Path, real: Path | None) -> CatalogSkill:
    relative = reached.relative_to(root).as_posix()
    identifier, fallback = f"{source}/{relative}", one_line(reached.name, MOST_NAME_CHARACTERS)

    def unread(path: Path, folder: str, problem: str) -> CatalogSkill:
        return CatalogSkill(identifier, source, fallback, "", str(path), folder, problem)

    if real is None:
        return unread(reached / SKILL_FILE, "",
                      f"it is a link to a folder outside {root}, so RAVIS doesn't read it")
    if unprintable(relative):
        return unread(real / SKILL_FILE, str(real),
                      "its folder's name holds a line break or another control character")
    try:
        text, file = read_text(real, SKILL_FILE)
    except SkillFileRefusedError as refused:
        return unread(real / SKILL_FILE, str(real), SKILL_MD_REFUSED[refused.reason])
    except SkillFileMissingError as missing:
        return unread(real / SKILL_FILE, str(real), f"RAVIS can't read its SKILL.md: {missing.why}")
    header = front_matter(text)
    if isinstance(header, str):
        return unread(file, str(real), header)
    name, description, problem = named(header)
    return CatalogSkill(identifier, source, name or fallback, description, str(file), str(real),
                        problem)


#: Why a skill's own `SKILL.md` wasn't read, by `SkillFileRefusedError.reason`.
SKILL_MD_REFUSED = {
    "outside_skill": "its SKILL.md is a link to a file outside the skill's folder",
    "hidden": "its SKILL.md is a link to a hidden file",
    "too_large": f"its SKILL.md is larger than {MOST_FILE_BYTES // 1024} KB",
    "not_text": "its SKILL.md isn't UTF-8 text",
}


# ── A skill's text ───────────────────────────────────────────────────────────


def front_matter(text: str) -> dict[str, Any] | str:
    """The YAML between a `SKILL.md`'s first two lines of `---`, or its problem in plain words."""
    lines = text.removeprefix("\ufeff").splitlines()
    if not lines or lines[0].rstrip() != "---":
        return "its SKILL.md doesn't start with front matter (a line of ---)"
    end = next((index for index, line in enumerate(lines[1:], 1) if line.rstrip() == "---"), None)
    if end is None:
        return "its SKILL.md's front matter has no closing line of ---"
    try:
        header = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError:
        return "its SKILL.md's front matter isn't valid YAML"
    if not isinstance(header, dict):
        return "its SKILL.md's front matter isn't a list of names and values"
    return header


def named(header: dict[str, Any]) -> tuple[str, str, str | None]:
    """The front matter's name and description, each on one line, and what's wrong with them."""
    name, description = header.get("name"), header.get("description")
    flat = " ".join(name.split()) if isinstance(name, str) else ""
    shown = one_line(flat, MOST_NAME_CHARACTERS)
    said = one_line(description) if isinstance(description, str) else ""
    if not shown:
        return shown, said, "its SKILL.md's front matter has no name"
    if not said:
        return shown, said, "its SKILL.md's front matter has no description"
    if len(flat) > MOST_NAME_CHARACTERS:
        return shown, said, f"its name is longer than {MOST_NAME_CHARACTERS} characters"
    return shown, said, None


def one_line(text: str, limit: int = DESCRIPTION_LIMIT) -> str:
    """Text on one line, at most `limit` characters, ending in "…" when it was cut."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit - 1].rstrip() + "…"


def unprintable(text: str) -> bool:
    """Whether text holds a line break or another control character."""
    return any(ord(character) < 32 or ord(character) == 127 for character in text)


def read_text(folder: Path, relative: str) -> tuple[str, Path]:
    """A skill's file as text, with its real path: a regular file inside the skill's real `folder`,
    at most `MOST_FILE_BYTES`, UTF-8. `SkillFileRefusedError` or `SkillFileMissingError` otherwise.
    """
    if not _plainly_inside(relative):
        raise SkillFileRefusedError("outside_skill")
    target = realpath(folder / relative)
    if target == folder or not inside(target, folder):
        raise SkillFileRefusedError("outside_skill")
    # Hidden as asked for, or as a link inside the folder leads: `.env`, `.git/config` and the like.
    if any(part.startswith(".") for part in (*relative.split("/"),
                                             *target.relative_to(folder).parts)):
        raise SkillFileRefusedError("hidden")
    data = _bytes_of(target)
    try:
        return data.decode("utf-8"), target
    except UnicodeDecodeError:
        raise SkillFileRefusedError("not_text") from None


def _plainly_inside(relative: str) -> bool:
    """Whether a caller's path could only name something inside the folder, before links count:
    relative, `/`-separated, with no empty, `.` or `..` part, and no control character."""
    parts = relative.split("/")
    return (0 < len(relative) <= MOST_FILE_PATH_CHARACTERS and not unprintable(relative)
            and "\\" not in relative and all(part not in ("", ".", "..") for part in parts))


def _bytes_of(target: Path) -> bytes:
    """A file's bytes, opened without following a link — `realpath` already did, so a link here is
    a path that changed under RAVIS — and without waiting on something that isn't a file."""
    try:
        descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise SkillFileMissingError("there is no such file") from None
    except OSError as failure:
        if failure.errno == errno.ELOOP:
            raise SkillFileRefusedError("outside_skill") from None
        raise SkillFileMissingError("RAVIS may not read it") from None
    try:
        facts = os.fstat(descriptor)
        if not stat.S_ISREG(facts.st_mode):
            raise SkillFileMissingError("it isn't a file")
        data = _at_most(descriptor, MOST_FILE_BYTES + 1)
    finally:
        os.close(descriptor)
    if facts.st_size > MOST_FILE_BYTES or len(data) > MOST_FILE_BYTES:
        raise SkillFileRefusedError("too_large")
    return data


def _at_most(descriptor: int, count: int) -> bytes:
    """Up to `count` bytes, however short each read the system gives."""
    chunks: list[bytes] = []
    total = 0
    while total < count:
        chunk = os.read(descriptor, count - total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


# ── The other models' switches, and what they may read ─────────────────────


class ModelSkills:
    """The skills the other models may use — Clarvis's own engine and NERVIS chat — and the owner's
    switch for each (`skill_choice`, engine `models`). Nothing here needs Codex."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self._choices = SkillChoices(database, MODELS)
        self._settings = settings

    async def folders(self) -> list[Folder]:
        """Both folders read from disk now, so a skill added a moment ago is listed at once."""
        return await asyncio.to_thread(read_catalog, self._settings)

    def wanted(self, skill: CatalogSkill) -> bool:
        """On or off as the owner chose; NERVIS's on and the personal ones off until switched."""
        chosen = self._choices.get(skill.path)
        return MODELS_ON_UNLESS_SWITCHED[skill.source] if chosen is None else chosen

    def served(self, skill: CatalogSkill) -> bool:
        """Whether the other models may use it now: read whole, and switched on."""
        return skill.problem is None and self.wanted(skill)

    def switch(self, skill: CatalogSkill, enabled: bool) -> None:
        self._choices.choose(skill.path, enabled)

    async def switched_on(self) -> list[CatalogSkill]:
        """Every skill the other models may use now, NERVIS's first and by name within each."""
        folders = await self.folders()
        on = [skill for folder in folders for skill in folder.skills if self.served(skill)]
        return sorted(on, key=_catalog_order)

    async def read(self, identifier: str, relative: str, caller: str) -> tuple[CatalogSkill, str]:
        """One file of a skill switched on for the other models, as text; refused otherwise.

        404 `SKILL_NOT_FOUND` for a skill unknown, switched off or unreadable alike, so a caller
        learns nothing about the skills it may not read; 422 `SKILL_FILE_REFUSED` or 404
        `SKILL_FILE_NOT_FOUND` for the file. Logged with names and sizes, never with the text.
        """
        skill = next((on for on in await self.switched_on() if on.id == identifier), None)
        if skill is None:
            logger.info("skills: %s asked for a skill that isn't switched on for other models",
                        caller)
            raise refusals.skill_not_served()
        try:
            text, _ = await asyncio.to_thread(read_text, Path(skill.folder), relative)
        except SkillFileRefusedError as refused:
            logger.info("skills: %s was refused %r of the skill %s (%s)", caller, relative,
                        skill.id, refused.reason)
            raise refusals.skill_file_refused(refused.reason) from None
        except SkillFileMissingError:
            logger.info("skills: %s asked for %r of the skill %s, which has no such file", caller,
                        relative, skill.id)
            raise refusals.skill_file_not_found() from None
        logger.info("skills: %s read %r of the skill %s (%d characters)", caller, relative,
                    skill.id, len(text))
        return skill, text


def _catalog_order(skill: CatalogSkill) -> tuple[int, str, str]:
    return SOURCES.index(skill.source), skill.name.casefold(), skill.path


# ── The Skills page: RAVIS's reading and Codex's list, lined up ──────────────


@dataclass(frozen=True)
class Listed:
    """One skill on the Skills page: as RAVIS read it, as Codex lists it, or both."""

    id: str
    source: str
    name: str
    description: str
    path: str
    problem: str | None
    catalog: CatalogSkill | None
    codex: Skill | None


def listed(folders: list[Folder], codex: list[Skill] | None) -> list[Listed]:
    """Every skill RAVIS read and every skill Codex lists, each once, lined up by real path.

    `codex` is None while Codex can't say which skills it has: RAVIS's reading is listed alone.
    """
    matched, codex_only = _lined_up(folders, codex or [])
    entries = [_read_by_ravis(skill, matched.get(skill.path))
               for folder in folders for skill in folder.skills]
    taken = {entry.id for entry in entries}
    entries += [_codex_only(skill, taken) for skill in codex_only]
    return sorted(entries, key=lambda entry: (SOURCES.index(entry.source),
                                              entry.name.casefold(), entry.path))


def _lined_up(folders: list[Folder], codex: list[Skill]) -> tuple[dict[str, Skill], list[Skill]]:
    """Codex's skills by the real path of a `SKILL.md` RAVIS read, and the ones RAVIS didn't read.

    Only a skill RAVIS reached inside its root takes part: a link leading out keeps its own path,
    which no real path can equal, and is never matched.
    """
    readable = {skill.path for folder in folders for skill in folder.skills if skill.folder}
    matched: dict[str, Skill] = {}
    codex_only: list[Skill] = []
    for skill in codex:
        real = str(realpath(skill.path))
        if real in readable and real not in matched:
            matched[real] = skill
        else:
            codex_only.append(skill)
    return matched, codex_only


def _read_by_ravis(skill: CatalogSkill, codex: Skill | None) -> Listed:
    description = skill.description or (codex.description if codex is not None else "")
    return Listed(skill.id, skill.source, skill.name, description, skill.path, skill.problem,
                  skill, codex)


def _codex_only(skill: Skill, taken: set[str]) -> Listed:
    """A skill only Codex lists: its identifier made from its folder's name, unique on the page."""
    base = f"{skill.source}/{Path(skill.path).parent.name}"
    identifier, count = base, 1
    while identifier in taken:
        count += 1
        identifier = f"{base}~{count}"
    taken.add(identifier)
    return Listed(identifier, skill.source, skill.name, skill.description, skill.path, None, None,
                  skill)


def board(folders: list[Folder], entries: list[Listed], codex: dict[str, Any],
          served: Callable[[CatalogSkill], bool]) -> dict[str, Any]:
    """`SkillsBoard` (`skills.json`): the folders, where Codex stands, and each skill's switches."""
    return {
        "folders": {folder.source: {"path": folder.path, "problem": folder.problem}
                    for folder in folders},
        "codex": codex,
        "skills": [_entry(entry, served) for entry in entries],
    }


def _entry(entry: Listed, served: Callable[[CatalogSkill], bool]) -> dict[str, Any]:
    usable = entry.catalog if entry.catalog is not None and entry.catalog.problem is None else None
    return {
        "id": entry.id,
        "name": entry.name,
        "description": entry.description,
        "source": entry.source,
        "path": entry.path,
        "problem": entry.problem,
        # Codex's own answer while it lists the skill; a skill Codex doesn't list has no switch.
        "codex": {"available": entry.codex is not None,
                  "enabled": entry.codex is not None and entry.codex.enabled},
        # Whether the other models are served it now: read whole, and switched on.
        "models": {"available": usable is not None,
                   "enabled": usable is not None and served(usable)},
    }
