"""Installing, updating and removing skills in NERVIS's skills folder (RAVIS 0.28.0).

**The owner's decisions** (15 September 2026):
- **Install from a GitHub folder, a zip file, or a website's Agent Skills index**, found by link,
  by upload or in the marketplace (`skill_market.py`) — and **never without a review first**: name,
  description, license, the files with scripts flagged, and `SKILL.md`'s text.
- **An installed skill arrives switched off** for Codex and for the other models, until the owner
  switches it on. A skill copied into the folder by hand stays on, as before.
- **Update** reviews what changed first, and is never automatic. **Remove** moves the folder to the
  macOS Trash; nothing here deletes a skill.

**A preview** (`preview_*`) fetches or unpacks the skill, checks it (`skill_package.check`), and
stages it in RAVIS's own data folder (`<data>/ravis-skill-store/staging/<preview id>`), never in the
skills folder. It lives `PREVIEW_SECONDS`; its staging goes when it expires, is discarded or is
used, and at RAVIS's start. At most `MOST_PREVIEWS` wait at once. A preview whose skill RAVIS
already installed from the same place (the same repository folder, the same website, or a zip) is
an **update's** preview, with what changed.

**Confirming** (`confirm`) is one step at a time, and **a repeated confirm of the same preview
answers what the first did**, so a retry after a lost answer changes nothing.
- *Install*: refused when anything is already at `<skills folder>/<name>`. Both switches are written
  off **before** the folder appears, so nothing that notices the folder finds it on; then the staged
  folder is moved in in one step (`renamex_np` with `RENAME_EXCL`, or a copy beside it first when
  the staging folder is on another disk); then the install is recorded (`skill_install`).
- *Update*: refused when the installed folder changed since the review. **The update rule**
  (`skill_package.Changes.resets_switches`): both switches go off when `SKILL.md` changed or a
  script was added or changed, and stay as they were otherwise; the review says which. The new
  folder takes the old one's place in one step (`RENAME_SWAP`), and the earlier version goes to the
  Trash; where macOS can't swap, two renames, so the skill is missing for an instant but never
  half-written.
- *Remove*: only a skill RAVIS installed (decided 15 September 2026: a skill put in the folder by
  hand is the owner's to move in Finder, and personal and built-in skills are never touched). Its
  folder moves to this machine's Trash as `<name> <date and time>`, then its switches and its
  record go.

**The Trash, each system's own** (20 September 2026). On a Mac, a plain move into `~/.Trash`, which
Finder shows as any other item there; Finder's Put Back isn't offered, because only Finder's own
deletion records where an item came from, and asking Finder to delete needs macOS's permission to
control Finder, which a background service can't ask for without a dialog. On Linux and under WSL,
the freedesktop Trash (`$XDG_DATA_HOME/Trash`, else `~/.local/share/Trash`) with the `.trashinfo`
beside it, so the file manager lists the skill and offers to put it back — `~/.Trash` there is a
hidden folder nothing shows, which is what RAVIS wrote until this date. When the skills folder is
on another disk than the Trash, RAVIS refuses rather than copying and deleting.

After every install, update and removal, Codex is told to apply the switches again
(`CodexService.skills_moved`). Audited by the routes without any file's contents.
"""

from __future__ import annotations

import asyncio
import ctypes
import errno
import functools
import logging
import os
import platform
import secrets
import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import parse as urllib_parse

from ravis.agent import store_refusals as refusals
from ravis.agent.skill_catalog import SKILL_FILE, front_matter, read_catalog
from ravis.agent.skill_github import GitHub, Located, Repository, parse_link, skill_folders
from ravis.agent.skill_package import (
    SPECIFICATION,
    Changes,
    Incoming,
    Package,
    changes,
    check,
    folder_hash,
    from_archive,
    installed_files,
    one_skill,
    stage,
)
from ravis.agent.skill_sites import fetch_skill, read_index, site_of
from ravis.agent.skill_web import NotFoundError, Web
from ravis.agent.skills import ENGINES, SkillChoices, folder_refusal, prepare_folder
from ravis.codex.lock_file import iso
from ravis.config import Settings, codex_skills_folder, data_directory
from ravis.storage.database import Database

PREVIEW_SECONDS = 15 * 60
MOST_PREVIEWS = 5
#: `renamex_np`'s flags (macOS `<stdio.h>`).
RENAME_SWAP = 0x2
RENAME_EXCL = 0x4


# ── Where a skill came from ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Origin:
    """Where an installed or previewed skill comes from; what Update fetches from again."""

    kind: str
    repository: str | None = None
    folder: str | None = None
    ref: str | None = None
    commit: str | None = None
    site: str | None = None
    index_url: str | None = None
    digest: str | None = None
    via: str | None = None

    def view(self) -> dict[str, Any]:
        if self.kind == "github":
            tail = f"/{self.folder}" if self.folder else ""
            return {"origin": "github", "repository": self.repository, "folder": self.folder,
                    "ref": self.ref, "commit": self.commit, "via": self.via,
                    "url": f"https://github.com/{self.repository}/tree/{self.commit}{tail}"}
        if self.kind == "website":
            return {"origin": "website", "site": self.site, "index": self.index_url,
                    "digest": self.digest, "via": self.via}
        return {"origin": "zip"}

    def same_place(self, other: Origin) -> bool:
        """The same home: one repository folder, one website, or a zip file either way."""
        if self.kind != other.kind:
            return False
        if self.kind == "github":
            return ((self.repository or "").lower() == (other.repository or "").lower()
                    and (self.folder or "") == (other.folder or ""))
        return self.kind == "zip" or self.site == other.site


@dataclass(frozen=True)
class Install:
    """One row of `skill_install`."""

    name: str
    origin: Origin
    content_hash: str
    installed_at: str
    updated_at: str | None


@dataclass
class Preview:
    """A skill checked and staged, waiting for the owner to confirm it."""

    id: str
    package: Package
    origin: Origin
    staged: Path
    expires_at: datetime
    #: The installed skill this updates; None for an install.
    update_of: str | None
    #: The installed folder's hash when the review was made (updates only).
    base_hash: str | None
    changes: Changes | None
    warnings: tuple[tuple[str, str], ...]
    #: What confirming it answered, once it has been confirmed.
    result: dict[str, Any] | None = None

    def view(self) -> dict[str, Any]:
        package = self.package
        return {
            "preview_id": self.id,
            "expires_at": iso(self.expires_at),
            "kind": "update" if self.update_of else "install",
            "name": package.name,
            "description": package.description,
            "license": {"text": package.license, "from": package.license_from},
            "compatibility": package.compatibility,
            "source": self.origin.view(),
            "files": [file.view() for file in package.files],
            "skill_md": package.skill_md,
            "warnings": [{"code": code, "message": message} for code, message in self.warnings],
            "checked_against": SPECIFICATION,
            "arrives_switched_off": self.update_of is None,
            "update": self._update_view(),
        }

    def _update_view(self) -> dict[str, Any] | None:
        if self.update_of is None or self.changes is None:
            return None
        reset = self.changes.resets_switches
        return {"changes": self.changes.view(), "switches": "reset" if reset else "kept",
                "why": switch_rule_words(self.changes)}

    def audit(self) -> dict[str, Any]:
        """What the audit says about it: where from and how much, never what a file says."""
        origin, package = self.origin, self.package
        return {"name": package.name, "origin": origin.kind, "repository": origin.repository,
                "folder": origin.folder, "ref": origin.ref, "commit": origin.commit,
                "site": origin.site, "digest": origin.digest, "via": origin.via,
                "content_hash": package.content_hash, "files": len(package.files),
                "scripts": sum(file.kind == "script" for file in package.files)}


def switch_rule_words(found: Changes) -> str:
    """The update rule, as the review says it."""
    if not found.resets_switches:
        return "Its switches stay as they are: SKILL.md and its scripts didn't change."
    what = [*(["SKILL.md changed"] if found.skill_md_changed else []),
            *([f"{len(found.scripts_changed)} script"
               f"{'' if len(found.scripts_changed) == 1 else 's'} added or changed"]
              if found.scripts_changed else [])]
    return (f"Both switches go off, because {' and '.join(what)}; switch it on again once you "
            "have read the changes.")


# ── The service ──────────────────────────────────────────────────────────────


class SkillInstalls:
    """Previews, installs, updates and removals, one confirm at a time."""

    def __init__(self, database: Database, settings: Settings, web: Web,
                 on_change: Callable[[], None] = lambda: None,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._database = database
        self._settings = settings
        self.web = web
        self.github = GitHub(web)
        self._on_change = on_change
        self.now = now
        self._previews: dict[str, Preview] = {}
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        #: RAVIS's own staging folder; what an earlier run left there is its own, and goes.
        self.staging = data_directory() / "ravis-skill-store" / "staging"
        discard_tree(self.staging)

    # Previews

    async def preview_github(self, *, url: str | None = None,
                             repository: Repository | None = None, folder: str = "",
                             ref: str | None = None, skill: str | None = None,
                             via: str | None = None) -> Preview:
        """A GitHub folder's skill: by a link, by repository, folder and ref, or by a skill's name
        in a repository (a skills.sh result)."""
        located = await self._locate(url, repository, folder, ref, skill)
        files = await self.github.download(located)
        name = located.folder.rsplit("/", 1)[-1] if located.folder else located.repository.name
        origin = Origin("github", repository=located.repository.full, folder=located.folder,
                        ref=located.ref, commit=located.commit, via=via)
        return await self._previewed(name, files, 0, origin)

    async def preview_zip(self, data: bytes) -> Preview:
        files, left = from_archive(data)
        top, inner = one_skill(files)
        return await self._previewed(top or declared_name(inner), inner, left, Origin("zip"))

    async def preview_website(self, site_url: str, name: str, via: str | None) -> Preview:
        site = site_of(site_url)
        if site is None:
            raise NotFoundError(f"{site_url} isn't a website address RAVIS reads")
        index = await read_index(self.web, site)
        entry = next((entry for entry in index.entries if entry.name == name), None)
        if entry is None:
            raise NotFoundError(f"{site.host}'s index has no skill named {name}")
        files, left = await fetch_skill(self.web, site, entry)
        origin = Origin("website", site=site.url, index_url=index.index_url, digest=entry.digest,
                        via=via)
        return await self._previewed(name, files, left, origin)

    async def preview_update(self, name: str) -> Preview | dict[str, Any]:
        """An installed skill's update, fetched from where it came from; or, when nothing
        changed there, the answer that it is up to date."""
        record = self.record(name)
        if record is None:
            raise refusals.not_installed(name)
        origin = record.origin
        if origin.kind == "zip":
            raise refusals.not_updatable(name)
        if origin.kind == "github":
            preview = await self._github_update(record)
        else:
            preview = await self._website_update(record)
        if preview is None or (preview.changes is not None and not preview.changes.any):
            if preview is not None:
                self.discard(preview.id)
            return await asyncio.to_thread(self._up_to_date, record)
        return preview

    async def _github_update(self, record: Install) -> Preview | None:
        origin = record.origin
        repository = Repository.parse(origin.repository)
        if repository is None:
            raise refusals.not_updatable(record.name)
        commit = await self.github.commit(repository, origin.ref)
        if commit == origin.commit:
            return None
        located = Located(repository, origin.ref, origin.folder or "", commit)
        files = await self.github.download(located)
        return await self._previewed(record.name, files, 0, replace(origin, commit=commit),
                                     update_of=record.name)

    async def _website_update(self, record: Install) -> Preview | None:
        origin = record.origin
        site = site_of(origin.site)
        if site is None:
            raise refusals.not_updatable(record.name)
        index = await read_index(self.web, site)
        entry = next((entry for entry in index.entries if entry.name == record.name), None)
        if entry is None:
            raise NotFoundError(f"{site.host}'s index no longer lists {record.name}")
        if entry.digest is not None and entry.digest == origin.digest:
            return None
        files, left = await fetch_skill(self.web, site, entry)
        updated = replace(origin, index_url=index.index_url, digest=entry.digest)
        return await self._previewed(record.name, files, left, updated, update_of=record.name)

    def _up_to_date(self, record: Install) -> dict[str, Any]:
        view = self.install_view(record)
        return {"name": record.name, "up_to_date": True, "source": view["source"],
                "changed_on_this_mac": view["changed_on_this_mac"]}

    async def _locate(self, url: str | None, repository: Repository | None, folder: str,
                      ref: str | None, skill: str | None) -> Located:
        if url is not None:
            link = parse_link(url)
            if link is None:
                raise refusals.not_a_github_link()
            return await self.github.locate(link.repository, link.splits())
        assert repository is not None
        if skill is not None:
            return await self._find_skill(repository, skill)
        return Located(repository, ref, folder, await self.github.commit(repository, ref))

    async def _find_skill(self, repository: Repository, skill: str) -> Located:
        """The folder holding a `SKILL.md` whose folder is named `skill`: the shallowest.

        Matched whatever the letter case (RAVIS 0.28.1): skills.sh names its results in lower case
        (`composiohq/awesome-claude-skills`), and GitHub answers a repository's name in any case,
        so only the folder's name needed it."""
        commit = await self.github.commit(repository, None)
        entries, _ = await self.github.tree(repository, commit)
        folders, _ = skill_folders(entries, [""], 10_000)
        wanted = skill.lower()
        named = sorted((found for found, _ in folders
                        if (found.rsplit("/", 1)[-1] if found else repository.name).lower()
                        == wanted),
                       key=lambda found: (found.count("/"), found))
        if not named:
            raise NotFoundError(f"{repository.full} has no folder named {skill} holding a SKILL.md")
        return Located(repository, None, named[0], commit)

    async def _previewed(self, folder_name: str, files: list[Incoming], left_out: int,
                         origin: Origin, update_of: str | None = None) -> Preview:
        package = await asyncio.to_thread(check, folder_name, files, left_out)
        record = self.record(package.name)
        if update_of is None and record is not None and record.origin.same_place(origin):
            update_of = package.name
        target = codex_skills_folder(self._settings) / package.name
        base_hash, found = None, None
        if update_of is not None:
            installed = await asyncio.to_thread(installed_files, target)
            base_hash, found = folder_hash(installed), changes(installed, package)
        warnings = package.warnings + await asyncio.to_thread(self._clashes, package, update_of)
        self._sweep()
        preview_id = f"sp_{secrets.token_urlsafe(18)}"
        staged = await asyncio.to_thread(stage, package, self.staging / preview_id)
        preview = Preview(preview_id, package, origin, staged,
                          self.now() + timedelta(seconds=PREVIEW_SECONDS), update_of, base_hash,
                          found, warnings)
        self._keep(preview)
        return preview

    def _clashes(self, package: Package, update_of: str | None) -> tuple[tuple[str, str], ...]:
        """What else is called the same: something already in the folder, or a personal skill."""
        warnings: list[tuple[str, str]] = []
        name = package.name
        if update_of is None and os.path.lexists(codex_skills_folder(self._settings) / name):
            how = ("installed by RAVIS from somewhere else" if self.record(name) is not None
                   else "put there by hand")
            warnings.append(("clashes_with_installed", f"A skill named {name} is already in "
                             f"NERVIS's skills folder ({how}), so this one can't be installed "
                             "beside it."))
        personal = next((folder for folder in read_catalog(self._settings)
                         if folder.source == "personal"), None)
        if personal is not None and any(skill.name == name or Path(skill.folder).name == name
                                         for skill in personal.skills):
            warnings.append(("clashes_with_personal", f"One of your personal skills is also "
                             f"named {name}, so Codex and the other models could see two skills "
                             "with that name."))
        return tuple(warnings)

    def _keep(self, preview: Preview) -> None:
        waiting = sorted((kept for kept in self._previews.values() if kept.result is None),
                         key=lambda kept: kept.expires_at)
        for oldest in waiting[:max(0, len(waiting) - MOST_PREVIEWS + 1)]:
            self.discard(oldest.id)
        self._previews[preview.id] = preview
        asyncio.get_running_loop().call_later(PREVIEW_SECONDS + 1, self._sweep)

    def _sweep(self) -> None:
        now = self.now()
        for preview in [kept for kept in self._previews.values() if kept.expires_at <= now]:
            self.discard(preview.id)

    def discard(self, preview_id: str) -> bool:
        """Forget a preview and remove its staging; whether there was one.

        **An id nobody minted deletes nothing.** This removed `staging/<id>`
        whichever id arrived, so one climbing through a real preview's folder —
        `sp_real/../../somewhere` — deleted a directory RAVIS can write, and
        answered `False` as if it had done nothing (base review, 17 September
        2026, finding 4). The staged folder is now taken from the preview
        record, which holds the path `stage()` actually returned, so an unknown
        id has nothing to act on.
        """
        preview = self._previews.pop(preview_id, None)
        if preview is None:
            return False
        # `preview.id`, not the caller's string: the same characters in the case
        # that matters, and minted here, so nothing a caller wrote decides which
        # directory goes. The folder is the preview's own staging root, which
        # holds the staged skill inside it.
        discard_tree(self.staging / preview.id)
        return True

    # Confirming, and removing

    def _one_at_a_time(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock

    async def confirm(self, preview_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Install or update from a preview: the answer, and the audit's facts (None when this is
        a repeat of a confirm already done, which changes nothing)."""
        async with self._one_at_a_time():
            self._sweep()
            preview = self._previews.get(preview_id)
            if preview is None:
                raise refusals.preview_not_found()
            if preview.result is not None:
                return preview.result, None
            work = self._update if preview.update_of else self._install
            result = await asyncio.to_thread(work, preview)
            preview.result = result
            discard_tree(self.staging / preview.id)
        self._on_change()
        facts = preview.audit()
        if preview.update_of:
            facts["switches_reset"] = result["switches_reset"]
        return result, facts

    def _install(self, preview: Preview) -> dict[str, Any]:
        folder = self._usable_folder()
        name, origin = preview.package.name, preview.origin
        target = folder / name
        if os.path.lexists(target):
            raise refusals.already_installed(name, self.record(name) is not None)
        skill_md = str(target / SKILL_FILE)
        before = self._switch_off(skill_md)
        try:
            move_into(preview.staged, target, folder)
        except OSError as failure:
            self._restore(skill_md, before)
            if failure.errno == errno.EEXIST:
                raise refusals.already_installed(name, self.record(name) is not None) from None
            raise refusals.not_moved(f"RAVIS couldn't move {name} into NERVIS's skills folder: "
                                     f"{failure.strerror or type(failure).__name__}") from None
        self._database.connection.execute(
            "INSERT OR REPLACE INTO skill_install (name, origin, repository, folder, ref, "
            "commit_sha, site, index_url, digest, via, content_hash, installed_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (name, origin.kind, origin.repository, origin.folder, origin.ref, origin.commit,
             origin.site, origin.index_url, origin.digest, origin.via,
             preview.package.content_hash, iso(self.now())),
        )
        record = self.record(name)
        assert record is not None
        return {"installed": self.install_view(record), "switches": {"codex": False,
                                                                      "models": False}}

    def _update(self, preview: Preview) -> dict[str, Any]:
        folder = self._usable_folder()
        name = preview.package.name
        target = folder / name
        if self.record(name) is None or target.is_symlink() or not target.is_dir():
            raise refusals.not_installed(name)
        if folder_hash(installed_files(target)) != preview.base_hash:
            raise refusals.changed_since_review(name)
        reset = preview.changes is not None and preview.changes.resets_switches
        skill_md = str(target / SKILL_FILE)
        before = self._switch_off(skill_md) if reset else None
        try:
            earlier = swap_in(preview.staged, target, folder)
        except OSError as failure:
            if before is not None:
                self._restore(skill_md, before)
            raise refusals.not_moved(f"RAVIS couldn't put the update of {name} in place: "
                                     f"{failure.strerror or type(failure).__name__}") from None
        kept = self._earlier_version(earlier, name)
        origin = preview.origin
        self._database.connection.execute(
            "UPDATE skill_install SET ref = ?, commit_sha = ?, index_url = ?, digest = ?, "
            "content_hash = ?, updated_at = ? WHERE name = ?",
            (origin.ref, origin.commit, origin.index_url, origin.digest,
             preview.package.content_hash, iso(self.now()), name),
        )
        record = self.record(name)
        assert record is not None
        return {"updated": self.install_view(record), "switches_reset": reset,
                "earlier_version": kept}

    def _earlier_version(self, earlier: Path, name: str) -> dict[str, Any]:
        """The replaced version, to the Trash; where it stays, and why, when it can't go."""
        try:
            trashed = move_to_trash(earlier, f"{name} (before update)", self.now())
        except OSError as failure:
            return {"trash": None, "kept_at": str(earlier), "problem": trash_words(failure)}
        return {"trash": str(trashed), "kept_at": None, "problem": None}

    async def remove(self, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        async with self._one_at_a_time():
            result = await asyncio.to_thread(self._remove, name)
        self._on_change()
        return result, {"name": name, "trashed": result["trash"] is not None}

    def _remove(self, name: str) -> dict[str, Any]:
        if self.record(name) is None:
            raise refusals.not_installed(name)
        target = codex_skills_folder(self._settings) / name
        trashed: Path | None = None
        if os.path.lexists(target):
            if target.is_symlink() or not target.is_dir():
                raise refusals.not_moved(f"The skill {name} in NERVIS's skills folder isn't a "
                                         "folder any more, so RAVIS leaves it alone")
            try:
                trashed = move_to_trash(target, name, self.now())
            except OSError as failure:
                raise refusals.not_moved(f"The skill {name} wasn't removed: "
                                         f"{trash_words(failure)}") from None
        for engine in ENGINES:
            SkillChoices(self._database, engine).forget(str(target / SKILL_FILE))
        self._database.connection.execute("DELETE FROM skill_install WHERE name = ?", (name,))
        return {"removed": name, "trash": str(trashed) if trashed is not None else None}

    def _usable_folder(self) -> Path:
        refused = folder_refusal(self._settings)
        if refused is not None:
            raise refusals.folder_unusable(refused)
        folder = codex_skills_folder(self._settings)
        try:
            prepare_folder(folder)
        except OSError as failure:
            why = failure.strerror or type(failure).__name__
            raise refusals.folder_unusable(f"{folder} can't be made: {why}") from None
        return folder

    def _switch_off(self, path: str) -> dict[str, bool | None]:
        """Both engines' switches off for a skill's `SKILL.md`; what they were before."""
        before: dict[str, bool | None] = {}
        for engine in ENGINES:
            choices = SkillChoices(self._database, engine)
            before[engine] = choices.get(path)
            choices.choose(path, False)
        return before

    def _restore(self, path: str, before: dict[str, bool | None]) -> None:
        for engine, was in before.items():
            choices = SkillChoices(self._database, engine)
            if was is None:
                choices.forget(path)
            else:
                choices.choose(path, was)

    # What RAVIS installed

    def records(self) -> list[Install]:
        rows = self._database.connection.execute(
            "SELECT * FROM skill_install ORDER BY name").fetchall()
        return [_install_of(row) for row in rows]

    def record(self, name: str) -> Install | None:
        row = self._database.connection.execute(
            "SELECT * FROM skill_install WHERE name = ?", (name,)).fetchone()
        return None if row is None else _install_of(row)

    def install_view(self, record: Install) -> dict[str, Any]:
        target = codex_skills_folder(self._settings) / record.name
        present = target.is_dir() and not target.is_symlink()
        changed = False
        if present:
            try:
                changed = folder_hash(installed_files(target)) != record.content_hash
            except (OSError, ValueError):
                changed = True
        return {"name": record.name, "path": str(target / SKILL_FILE),
                "source": record.origin.view(), "installed_at": record.installed_at,
                "updated_at": record.updated_at, "content_hash": record.content_hash,
                "present": present, "changed_on_this_mac": changed}

    async def installs(self) -> dict[str, Any]:
        def read() -> dict[str, Any]:
            return {"folder": str(codex_skills_folder(self._settings)),
                    "installs": [self.install_view(record) for record in self.records()]}
        return await asyncio.to_thread(read)


def _install_of(row: Any) -> Install:
    origin = Origin(row["origin"], row["repository"], row["folder"], row["ref"],
                    row["commit_sha"], row["site"], row["index_url"], row["digest"], row["via"])
    return Install(row["name"], origin, row["content_hash"], row["installed_at"],
                   row["updated_at"])


def declared_name(files: list[Incoming]) -> str:
    """The name a zip's `SKILL.md` gives itself, for a zip with no folder at its top; `check`
    refuses anything wrong with it."""
    skill_md = next((file for file in files if file.path == SKILL_FILE), None)
    header = front_matter(skill_md.data.decode("utf-8", "replace")) if skill_md else "none"
    name = header.get("name") if isinstance(header, dict) else None
    return name if isinstance(name, str) and name else "skill"


# ── Moving folders ───────────────────────────────────────────────────────────


@functools.cache
def _renamex() -> Any:
    """macOS's `renamex_np`, or None where the system has none."""
    try:
        function = ctypes.CDLL(None, use_errno=True).renamex_np
    except (OSError, AttributeError):
        return None
    function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    return function


def rename(source: Path, target: Path, flags: int) -> None:
    """One rename with `renamex_np`'s flags where macOS has it; else an ordinary rename, after
    checking nothing is there (`RENAME_EXCL`). `OSError` with the system's errno otherwise."""
    function = _renamex()
    if function is None:
        if flags & RENAME_SWAP:
            raise OSError(errno.ENOTSUP, "this system can't swap two folders")
        if os.path.lexists(target):
            raise OSError(errno.EEXIST, os.strerror(errno.EEXIST), str(target))
        os.rename(source, target)
        return
    if function(os.fsencode(source), os.fsencode(target), flags) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))


def move_into(staged: Path, target: Path, folder: Path) -> None:
    """The staged folder at `target` in one step; copied beside it first when the staging folder
    is on another disk, so the folder still appears whole."""
    try:
        rename(staged, target, RENAME_EXCL)
        return
    except OSError as failure:
        if failure.errno != errno.EXDEV:
            raise
    beside = folder / f".ravis-installing-{secrets.token_hex(6)}"
    shutil.copytree(staged, beside, symlinks=True)
    try:
        rename(beside, target, RENAME_EXCL)
    except OSError:
        discard_tree(beside)
        raise


def swap_in(staged: Path, target: Path, folder: Path) -> Path:
    """The staged folder in `target`'s place; where the earlier version is now (hidden, beside)."""
    beside = folder / f".ravis-update-{secrets.token_hex(6)}"
    move_into(staged, beside, folder)
    try:
        if _renamex() is not None:
            rename(beside, target, RENAME_SWAP)
            return beside
        aside = folder / f".ravis-replaced-{secrets.token_hex(6)}"
        os.rename(target, aside)
        try:
            os.rename(beside, target)
        except OSError:
            os.rename(aside, target)
            raise
        return aside
    except OSError:
        if beside.exists() and not os.path.samefile(beside, target):
            discard_tree(beside)
        raise


def move_to_trash(path: Path, label: str, now: datetime, *, system: str | None = None,
                  trash: Path | None = None) -> Path:
    """`path` moved to this user's Trash as `<label> <date and time>`, numbered when that name is
    taken. Never overwrites, and never erases.

    **Each system's own Trash** (20 September 2026). On a Mac, `~/.Trash`, which Finder lists.
    On Linux and under WSL, the freedesktop Trash — `$XDG_DATA_HOME/Trash`, else
    `~/.local/share/Trash` — with the `.trashinfo` file beside it that makes a file manager list
    the skill and offer to put it back. Until this date RAVIS wrote `~/.Trash` on every system:
    on Linux that is a hidden folder no file manager shows, so a removed skill was gone from the
    owner's view with no way to restore it, which is what the owner's CachyOS laptop would have
    done. `system` and `trash` stand in for this machine and its Trash in tests.

    SIRVIS moves a deleted model the same way (`sirvis/src/sirvis/model_files.py`, `to_trash`) and
    keeps its own copy of these few lines: the package both services share is the wire protocol
    (`ECOSYSTEM_RUNBOOK.md` §4), and a Trash is not part of it.
    """
    system = system or platform.system()
    stamp = now.astimezone().strftime("%Y-%m-%d %H.%M.%S")
    if system == "Darwin":
        folder = Path.home() / ".Trash" if trash is None else trash
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        return _first_free(path, folder, label, stamp, information=None)
    root = trash if trash is not None else Path(
        os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "Trash"
    (root / "files").mkdir(mode=0o700, parents=True, exist_ok=True)
    (root / "info").mkdir(mode=0o700, parents=True, exist_ok=True)
    return _first_free(path, root / "files", label, stamp, information=root / "info")


def _first_free(path: Path, folder: Path, label: str, stamp: str, *,
                information: Path | None) -> Path:
    """The first name in `folder` nothing holds, with its `.trashinfo` where the system wants one.

    The information file is written first and with `O_EXCL`, as the freedesktop layout asks, so
    two removals in the same second cannot land on one name; a move that then fails takes its
    information file with it rather than leaving a record of something that is still installed.
    """
    for count in range(1, 100):
        name = f"{label} {stamp}" if count == 1 else f"{label} {stamp} {count}"
        note = information / f"{name}.trashinfo" if information is not None else None
        if note is not None and not _wrote_information(note, path):
            continue
        try:
            rename(path, folder / name, RENAME_EXCL)
        except FileExistsError:
            if note is not None:
                note.unlink(missing_ok=True)
            continue
        except OSError:
            if note is not None:
                note.unlink(missing_ok=True)
            raise
        return folder / name
    raise OSError(errno.EEXIST, "every name RAVIS tried in the Trash was taken")


def _wrote_information(note: Path, path: Path) -> bool:
    """The `.trashinfo` a file manager reads to offer Put Back, or False when that name is taken."""
    try:
        handle = os.open(note, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(handle, "w", encoding="utf-8") as writing:
        writing.write("[Trash Info]\n"
                      f"Path={urllib_parse.quote(str(path.resolve()))}\n"
                      f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n")
    return True


def trash_words(failure: OSError) -> str:
    if failure.errno == errno.EXDEV:
        return ("NERVIS's skills folder is on another disk than your Trash, so RAVIS can't move "
                "it there")
    if failure.errno in (errno.EPERM, errno.EACCES):
        return "this machine didn't let RAVIS move it to the Trash"
    return f"RAVIS couldn't move it to the Trash ({failure.strerror or type(failure).__name__})"


def discard_tree(path: Path) -> None:
    """Remove RAVIS's own staging or a copy it made; never a skill the owner has.

    **Proven to be RAVIS's own, not assumed.** The docstring above was the whole
    guarantee, and the caller supplied whatever path it had built from a caller's
    id. A tree outside the staging root is refused rather than removed.
    """
    root = (data_directory() / "ravis-skill-store").resolve(strict=False)
    landed = Path(path).expanduser().resolve(strict=False)
    if landed != root and not landed.is_relative_to(root):
        logging.getLogger(__name__).warning(
            "skills: refused to remove %s — it is not RAVIS's own staging", landed)
        return
    shutil.rmtree(landed, ignore_errors=True)
