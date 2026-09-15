"""The Codex skills a task may use: NERVIS's own folder on, the owner's personal skills off.

**Why** (live, 14 September 2026). RAVIS starts Codex with the owner's real `HOME`, because a task's
npm, pip and git need it (`codex/supervisor.py`), so Codex also found the owner's personal skills in
`~/.agents/skills`. The first live Codex task's first command ran one of them, "graphify": it made a
`graphify-out/` folder in the project and spent allowance on something nobody asked for.

**The owner's decisions** (14 September 2026):
- **A NERVIS-only skills folder**, `<NERVIS workspace>/clarvis/skills` (the `codex_skills_folder`
  setting, which the launcher names). Its skills are **on** unless the owner switches one off.
- **The owner's personal skills** — every skill Codex finds for the user outside that folder —
  **start off**, and so does a personal skill added later, so nothing slips in unannounced. The
  owner can switch any of them on.
- **Codex's built-in skills** (`system` scope: imagegen, openai-docs, plugin-creator,
  review-agent, skill-creator, skill-installer) **start on**, and each can be switched off.
- The switches live on NERVIS's Skills page (since NERVIS 0.32.0; on its RAVIS → Dashboard Codex
  card before), and nowhere else. Since RAVIS 0.27.0 each skill also has a switch for the models
  that aren't Codex, which RAVIS reads and serves itself (`agent/skill_catalog.py`).

**Where a skill comes from is decided by its path, not by Codex's scope** (measured on Codex
0.154.0): a folder added with `skills/extraRoots/set` lists its skills as `user`, the same scope as
`~/.agents/skills`. So a `user` skill whose `SKILL.md` lies in the NERVIS folder — real paths
compared, symlinks followed, so a personal skill linked into the folder still counts as personal —
is NERVIS's, and every other one is personal; `system` is built in. `admin` (skills installed for
this whole Mac) and any scope a later Codex adds count as personal, so they start off too.
**`repo` skills belong to a task's own project**: Codex decides them, and RAVIS neither lists nor
switches them.

**RAVIS keeps the owner's choices** (migration 11; `skill_choice`, engine `codex`, since migration
13): a row only for a skill the owner switched, by the path Codex lists. Codex's `config.toml` in
RAVIS's own Codex home is where they are applied, never where they are kept.

**Applied each time Codex's process becomes ready, before any task** (`CodexService`), and again
whenever Codex says its skills changed (`skills/changed`): the folder is made, with a short
`README.md`, if it isn't there; `skills/extraRoots/set` names it, **once per Codex process**;
`skills/list` reads every skill; and `skills/config/write` switches, by path, each one whose state
isn't the owner's choice.
The list is read again until it agrees, at most `APPLY_ROUNDS` times. Until it agrees, or when
anything fails, no task starts and `GET /api/v1/codex` says why: a personal skill is never silently
on. **A task can never write into the folder**: `roots.py` refuses a task root that is, holds or
lies inside it.

**Why the folder is named once per process** (live, RAVIS 0.26.0, 14 September 2026). Measured on
Codex 0.154.0: `skills/extraRoots/set` makes Codex send `skills/changed`, while `skills/list` and
`skills/config/write` send nothing. 0.26.0 named the folder at every apply, so each apply caused
the next: RAVIS stayed "switching skills" for good, no task started, and every round published a
state change to NERVIS. Now RAVIS remembers that the running process was told, and the one
`skills/changed` answering it causes one more apply, which names nothing and settles. **That
answer doesn't hold tasks back** (`own_change`): nothing on disk changed, and while it flipped the
state to "switching skills", a task asked for just after a start was refused. It is counted before
the call is sent, since Codex may notify before it answers, and uncounted if the call fails. A
process that ends is forgotten (`process_ended`); whether Codex keeps extra roots across its own
restart is unmeasured, so a new process is told again.

**What isn't measured.** That a skill switched off is really left out of the model's instructions
in a turn (that needs a real turn, which spends allowance); and whether a change reaches a Codex
thread already loaded. A site added later didn't (calibration K3), so a change is taken to count
from a task's next start or reopen.

**The owner's routes** (`api/management/codex.py`): `GET /api/v1/codex/skills` reads the list for
NERVIS's GET relay or an admin credential; `POST /api/v1/codex/skills {path, enabled}` takes an
admin credential — NERVIS's Codex card, through its control route — and only the path of a skill
Codex listed just now. The choice is kept and everything applied again; a change Codex doesn't take
puts the choice back, applies again, and is 409 `SKILL_NOT_CHANGED`. Since RAVIS 0.27.0 they are the
older form of `/api/v1/skills` (`api/management/skills.py`), whose Codex switch is this same one.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ravis.agent.roots import denied_paths, inside, realpath, related
from ravis.codex import refusals
from ravis.codex.calibration.plan import protected_repositories
from ravis.codex.lock_file import iso
from ravis.codex.rpc import CodexRpcError, CodexUnavailableError
from ravis.codex.state import (
    SKILLS_APPLIED,
    SKILLS_CODEX_DID_NOT_ANSWER,
    SKILLS_FOLDER_NOT_MADE,
    SKILLS_FOLDER_REFUSED,
    SKILLS_NOT_LISTED,
    SKILLS_NOT_WRITTEN,
    SKILLS_STILL_DIFFERENT,
)
from ravis.config import Settings, codex_home, codex_skills_folder
from ravis.storage.database import Database

logger = logging.getLogger("ravis")

#: Where a skill comes from, in the order the card groups them (`SkillsView` → `skills[].source`).
SOURCES = ("nervis", "personal", "built_in")
#: What a skill the owner never switched is: NERVIS's and Codex's own on, the owner's personal off.
ON_UNLESS_SWITCHED = {"nervis": True, "personal": False, "built_in": True}
#: The engines a skill has a switch for (owner decision, 15 September 2026): Codex, and the other
#: models — Clarvis's own engine and NERVIS chat together (`agent/skill_catalog.py`).
CODEX, MODELS = "codex", "models"
ENGINES = (CODEX, MODELS)
#: The other models' starting switches. Codex's built-in skills aren't theirs at all.
MODELS_ON_UNLESS_SWITCHED = {"nervis": True, "personal": False}
#: How many times the list is read and put right before RAVIS says it won't agree.
APPLY_ROUNDS = 3
REQUEST_SECONDS = 10.0
#: A skill's description as the card shows it: one line, at most this many characters.
DESCRIPTION_LIMIT = 300
#: What the skills routes answer while Codex can't say which skills it has (503, retryable).
UNREADABLE = "Codex isn't running, so its skills can't be read; try again in a moment."
#: Written into the folder when RAVIS makes it, and never over a README the owner already has.
README = """# NERVIS skills

Skills for the ecosystem's models: the Codex tasks RAVIS runs for NERVIS and Clarvis, and the other
models — Clarvis's own engine and NERVIS chat. Put each skill in a folder of its own here, with a
`SKILL.md` inside.

- Skills in this folder are **on** for Codex and for the other models, unless you switch one off.
- Your personal skills (`~/.agents/skills`) start **off** for both, and so does any you add later.
  Codex's built-in skills start **on**, and are for Codex only.
- Switch any of them on or off on the NERVIS dashboard: NERVIS → Skills.
- A change counts from a Codex task's next start or reopen, and from the other models' next
  request.

Codex tasks themselves can never be started in this folder or in a folder that holds it.
"""
#: The READMEs earlier RAVIS builds wrote. One still word for word is RAVIS's own, not the owner's,
#: so it is brought up to date; a README anyone changed by so much as a character is left alone.
EARLIER_READMES = ("""# NERVIS skills

Skills for the Codex tasks RAVIS runs for NERVIS and Clarvis. Put each skill in a folder of its
own here, with a `SKILL.md` inside.

- Skills in this folder are **on** for every Codex task, unless you switch one off.
- Your personal skills (`~/.agents/skills`) start **off** for Codex tasks, and so does any you add
  later. Codex's built-in skills start **on**.
- Switch any of them on or off on the NERVIS dashboard: RAVIS → Dashboard → Codex card → Skills.
- A change counts from a Codex task's next start or reopen.

Codex tasks themselves can never be started in this folder or in a folder that holds it.
""",)

Request = Callable[..., Awaitable[Any]]
#: Where the owner's choices stand for Codex, and a clause saying more: `state.SKILLS_*`.
SkillsOutcome = tuple[str, str | None]


# ── The folder ───────────────────────────────────────────────────────────────


def folder_refusal(settings: Settings) -> str | None:
    """Why the skills folder setting can't be used, in words `GET /api/v1/codex` shows; else None.

    It must be absolute once `~` is expanded; strictly inside one of `agent_allowed_roots`, and not
    one of them itself; and neither be nor hold `~/.codex`, RAVIS's own Codex home, one of the
    ecosystem's own repositories, or a path Codex tasks must never touch. Real paths are compared,
    whether or not the folder exists yet.
    """
    raw = settings.codex_skills_folder
    if not raw.strip() or not os.path.isabs(os.path.expanduser(raw)):
        return f"the skills folder {raw!r} isn't an absolute path"
    folder = codex_skills_folder(settings)
    containers = [realpath(entry) for entry in settings.agent_allowed_roots
                  if isinstance(entry, str)]
    if folder in containers:
        return f"the skills folder {folder} is the folder that holds every project"
    if not any(container in folder.parents for container in containers):
        return f"the skills folder {folder} isn't inside a folder Codex tasks may use"
    others = (
        (realpath(Path.home() / ".codex"), "the ChatGPT app's own Codex home, ~/.codex"),
        (realpath(codex_home(settings)), "RAVIS's own Codex home"),
    )
    for other, what in others:
        if related(folder, other):
            return f"the skills folder {folder} is or holds {what}"
    repositories = {*(realpath(path) for path in settings.agent_protected_repositories),
                    *(realpath(path) for path in protected_repositories())}
    if any(related(folder, repository) for repository in repositories):
        return f"the skills folder {folder} is or holds one of the ecosystem's own repositories"
    if any(related(folder, denied) for denied in denied_paths(settings)):
        return f"the skills folder {folder} is or holds a path Codex tasks must never touch"
    return None


def prepare_folder(folder: Path) -> None:
    """Make the folder, and its `README.md` unless something is already there; `OSError` if not.

    `lexists`, so a dangling link named README.md is left alone rather than written through. An
    earlier build's README, still word for word, is replaced by this build's.
    """
    folder.mkdir(parents=True, exist_ok=True)
    readme = folder / "README.md"
    if not os.path.lexists(readme) or _earlier_readme(readme):
        readme.write_text(README, encoding="utf-8")


def _earlier_readme(readme: Path) -> bool:
    """Whether the README is a plain file RAVIS wrote before, unchanged since."""
    try:
        return (not readme.is_symlink() and readme.is_file()
                and readme.read_text(encoding="utf-8") in EARLIER_READMES)
    except (OSError, UnicodeDecodeError):
        return False


# ── What Codex lists ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Skill:
    """One skill Codex lists, as the card shows it (`SkillsView` → `skills[]`)."""

    path: str
    name: str
    description: str
    source: str
    enabled: bool

    def view(self) -> dict[str, Any]:
        return {"path": self.path, "name": self.name, "description": self.description,
                "source": self.source, "enabled": self.enabled}


def source_of(scope: object, path: str, folder: Path) -> str | None:
    """`nervis`, `personal` or `built_in` by the rule above; None for a project's own skill."""
    if scope == "repo":
        return None
    if scope == "system":
        return "built_in"
    if scope == "user" and inside(realpath(path), folder):
        return "nervis"
    return "personal"


def listed_skills(result: object, folder: Path) -> list[Skill] | None:
    """Every skill in a `skills/list` answer but a project's own, each once and grouped by source.

    None when the answer, or any skill RAVIS would have to switch, isn't in the shape RAVIS reads:
    a skill it can't read is a skill it can't switch off.
    """
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, list):
        return None
    found: dict[str, Skill] = {}
    for entry in data:
        skills = entry.get("skills") if isinstance(entry, dict) else None
        if not isinstance(skills, list):
            return None
        errors = entry.get("errors")
        if isinstance(errors, list) and errors:
            logger.warning("codex: Codex couldn't read %d of its skill files", len(errors))
        for raw in skills:
            if isinstance(raw, dict) and raw.get("scope") == "repo":
                continue
            skill = _skill(raw, folder)
            if skill is None:
                return None
            found.setdefault(skill.path, skill)
    return sorted(found.values(), key=_grouped)


def _grouped(skill: Skill) -> tuple[int, str, str]:
    """The card's order: NERVIS's, then personal, then built in; by name within each."""
    return SOURCES.index(skill.source), skill.name.casefold(), skill.path


def _skill(raw: object, folder: Path) -> Skill | None:
    if not isinstance(raw, dict):
        return None
    path, name, enabled = raw.get("path"), raw.get("name"), raw.get("enabled")
    if not (isinstance(path, str) and os.path.isabs(path) and isinstance(name, str)
            and isinstance(enabled, bool)):
        return None
    source = source_of(raw.get("scope"), path, folder)
    if source is None:
        return None
    return Skill(path=path, name=name, description=_description(raw), source=source,
                 enabled=enabled)


def _description(raw: dict[str, Any]) -> str:
    """The short description where Codex has one (interface's, then SKILL.md's), else the full."""
    interface = raw.get("interface")
    candidates = (
        interface.get("shortDescription") if isinstance(interface, dict) else None,
        raw.get("shortDescription"),
        raw.get("description"),
    )
    for value in candidates:
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            if len(text) <= DESCRIPTION_LIMIT:
                return text
            return text[:DESCRIPTION_LIMIT - 1].rstrip() + "…"
    return ""


# ── The owner's choices ──────────────────────────────────────────────────────


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SkillChoices:
    """The skills the owner switched on or off for one engine, by path (`skill_choice`).

    `codex`: by the path Codex lists a skill at, as since migration 11 (carried over by 13).
    `models`: by the real path of the `SKILL.md` RAVIS read (`agent/skill_catalog.py`). Bound to
    one engine when made, so no caller can read or write another engine's switch by mistake.
    """

    def __init__(self, database: Database, engine: str,
                 now: Callable[[], datetime] = _utc_now) -> None:
        if engine not in ENGINES:
            raise ValueError(f"skills have no engine called {engine!r}")
        self._database = database
        self._engine = engine
        self._now = now

    def get(self, path: str) -> bool | None:
        """The owner's switch for this skill, or None when they never switched it."""
        row = self._database.connection.execute(
            "SELECT enabled FROM skill_choice WHERE path = ? AND engine = ?", (path, self._engine)
        ).fetchone()
        return None if row is None else bool(row["enabled"])

    def choose(self, path: str, enabled: bool) -> None:
        self._database.connection.execute(
            "INSERT INTO skill_choice (path, engine, enabled, changed_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (path, engine) DO UPDATE SET enabled = excluded.enabled, "
            "changed_at = excluded.changed_at",
            (path, self._engine, int(enabled), iso(self._now())),
        )

    def forget(self, path: str) -> None:
        self._database.connection.execute(
            "DELETE FROM skill_choice WHERE path = ? AND engine = ?", (path, self._engine)
        )


# ── Codex's skills, put to the owner's choices ───────────────────────────────


@dataclass(frozen=True)
class Switched:
    """What became of the owner's switch: Codex's state afterwards, and why Codex didn't take it."""

    skill: Skill
    #: Where the choices stand for Codex now: what `GET /api/v1/codex` reads from.
    outcome: SkillsOutcome
    #: None when Codex took the switch; else the word `SKILL_NOT_CHANGED` gives.
    refused: str | None
    #: The list as Codex holds it afterwards; empty when it couldn't be read.
    skills: list[Skill]


class CodexSkills:
    """Codex's skills, and the owner's choice for each: listed, switched, and put right."""

    def __init__(
        self, request: Request, choices: SkillChoices, settings: Settings,
        *, seconds: float = REQUEST_SECONDS,
    ) -> None:
        self._request = request
        self._choices = choices
        self._settings = settings
        self._seconds = seconds
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        #: The folder the running Codex process was named, and which process that is:
        #: `process_ended` moves the count, so an answer after its process ended isn't remembered.
        self._told: str | None = None
        self._process = 0
        #: How many `skills/changed` RAVIS's own naming of the folder has yet to cause: one each.
        self._own_changes = 0

    def folder(self) -> Path:
        return codex_skills_folder(self._settings)

    def process_ended(self) -> None:
        """The Codex process ended: the next one hasn't been told the folder."""
        self._told = None
        self._own_changes = 0
        self._process += 1

    def own_change(self) -> bool:
        """Whether a `skills/changed` is the one RAVIS's own naming caused; each is taken once."""
        if self._own_changes <= 0:
            return False
        self._own_changes -= 1
        return True

    def wanted(self, skill: Skill) -> bool:
        """On or off, as the owner chose, or as its source starts when they never switched it."""
        chosen = self._choices.get(skill.path)
        return ON_UNLESS_SWITCHED[skill.source] if chosen is None else chosen

    def _one_at_a_time(self) -> asyncio.Lock:
        """One read-then-write at a time, so an apply never interleaves with a switch or a read.

        Made for the event loop that runs, since a test may run two (as `SiteAllowlist` does).
        """
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock

    async def apply(self) -> SkillsOutcome:
        """Put every skill Codex has to the owner's choice: `(SKILLS_APPLIED, None)`, or why not."""
        refused = folder_refusal(self._settings)
        if refused is not None:
            return SKILLS_FOLDER_REFUSED, refused
        async with self._one_at_a_time():
            outcome, _ = await self._apply()
        return outcome

    async def listed(self) -> list[Skill]:
        """`GET /api/v1/codex/skills`: every skill as Codex holds it now; 503 while it can't say."""
        async with self._one_at_a_time():
            return await self._readable_list()

    async def switch(self, path: str, enabled: bool) -> Switched:
        """`POST /api/v1/codex/skills`: the owner's switch, for a skill Codex lists just now.

        404 `SKILL_NOT_FOUND` for any other path, and nothing is kept. Otherwise the choice is kept
        and every skill applied again. When Codex doesn't take it, the previous choice is put back
        and applied again, so RAVIS's record stays what Codex was last told.
        """
        async with self._one_at_a_time():
            skill = next((listed for listed in await self._readable_list()
                          if listed.path == path), None)
            if skill is None:
                raise refusals.skill_not_found()
            refused = folder_refusal(self._settings)
            if refused is not None:
                return Switched(skill, (SKILLS_FOLDER_REFUSED, refused), SKILLS_FOLDER_REFUSED, [])
            before = self._choices.get(skill.path)
            self._choices.choose(skill.path, enabled)
            outcome, skills = await self._apply()
            if outcome[0] == SKILLS_APPLIED:
                return Switched(skill, outcome, None, skills)
            logger.error("codex: Codex didn't take the owner's switch of the skill %s (%s)",
                         skill.name, outcome[0])
            if before is None:
                self._choices.forget(skill.path)
            else:
                self._choices.choose(skill.path, before)
            settled, skills = await self._apply()
            return Switched(skill, settled, outcome[0], skills)

    async def _readable_list(self) -> list[Skill]:
        try:
            skills = await self._list()
        except (CodexRpcError, CodexUnavailableError):
            raise refusals.runtime_unavailable(UNREADABLE) from None
        if skills is None:
            raise refusals.runtime_unavailable(UNREADABLE)
        return skills

    async def _list(self) -> list[Skill] | None:
        """Every skill Codex finds, read from disk again; the folder names the working folder, so
        no project's own skills come with them."""
        folder = self.folder()
        result = await self._request("skills/list", {"cwds": [str(folder)], "forceReload": True},
                                     timeout=self._seconds)
        return listed_skills(result, folder)

    async def _name_folder(self, folder: Path) -> None:
        """`skills/extraRoots/set`, once per Codex process: naming it makes Codex send
        `skills/changed`, so naming it at every apply would make every apply cause the next."""
        if self._told == str(folder):
            return
        process = self._process
        self._own_changes += 1  # before sending: Codex may notify before it answers
        try:
            await self._request("skills/extraRoots/set", {"extraRoots": [str(folder)]},
                                timeout=self._seconds)
        except (CodexRpcError, CodexUnavailableError):
            self._own_changes = max(0, self._own_changes - 1)
            raise
        if process == self._process:
            self._told = str(folder)

    async def _apply(self) -> tuple[SkillsOutcome, list[Skill]]:
        """The folder made and named, then list and switch until the list agrees (lock held)."""
        folder = self.folder()
        try:
            prepare_folder(folder)
        except OSError as failure:
            why = failure.strerror or type(failure).__name__
            return (SKILLS_FOLDER_NOT_MADE, f"the skills folder {folder} can't be made: {why}"), []
        try:
            await self._name_folder(folder)
            for _ in range(APPLY_ROUNDS):
                skills = await self._list()
                if skills is None:
                    return (SKILLS_NOT_LISTED, None), []
                differing = [skill for skill in skills if skill.enabled != self.wanted(skill)]
                if not differing:
                    return (SKILLS_APPLIED, None), skills
                for skill in differing:
                    wanted = self.wanted(skill)
                    answer = await self._request(
                        "skills/config/write", {"path": skill.path, "enabled": wanted},
                        timeout=self._seconds,
                    )
                    taken = answer.get("effectiveEnabled") if isinstance(answer, dict) else None
                    if taken is not wanted:
                        word = "on" if wanted else "off"
                        return (SKILLS_NOT_WRITTEN, f"Codex didn't switch {skill.name} {word}"), []
        except (CodexRpcError, CodexUnavailableError):
            return (SKILLS_CODEX_DID_NOT_ANSWER, None), []
        return (SKILLS_STILL_DIFFERENT, None), []
