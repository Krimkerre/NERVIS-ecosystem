"""Skills for every engine: the Skills page's list and switches, and the other models' reads.

The shapes, codes and messages are the contract fixture `skills.json`
(`tests/fixtures/relay-contract/`); this module is the HTTP edge of `agent/skill_catalog.py`, and,
for a Codex switch, of `agent/skills.py` through the Codex service.

**Who may call what** (owner decisions, 15 September 2026):

| route | who |
|---|---|
| `GET /api/v1/skills` | NERVIS's client credential (its GET relay) or an admin credential |
| `POST /api/v1/skills` | admin: NERVIS's Skills page, through its control route |
| `GET /api/v1/skills/models` | Clarvis's or NERVIS's client credential: the other models' callers |
| `GET /api/v1/skills/models/read` | the same |

**`/api/v1/codex/skills` is the older form** of the first two, for Codex alone (RAVIS 0.26.0), and
answers as it did, for NERVIS 0.31. A Codex switch through either is the same choice, kept by the
path Codex lists.

**The other models' routes never ask Codex anything**, so they answer while Codex is off. An admin
credential isn't one of their callers: nothing the owner runs reads skill text on a model's behalf,
and the Skills page needs the list, not the text. Audited as `ravis.skill_switched {name, source,
engine, enabled}`; the other models' reads are logged by name and size (`ModelSkills.read`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from ravis.agent.skill_catalog import SKILL_FILE, Folder, ModelSkills, board, listed
from ravis.agent.skills import CODEX, ENGINES, MODELS, Skill
from ravis.api.management import audit
from ravis.api.management.codex import MOST_PATH_CHARACTERS, _json_object, require_admin
from ravis.codex import refusals
from ravis.codex.refusals import CodexRefusalError
from ravis.codex.service import CodexService

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])

SWITCH_BODY = (
    "The body must carry path, the path of a skill as RAVIS listed it; engine, codex or models; "
    "and enabled, true or false."
)


def _service(request: Request) -> CodexService:
    return request.app.state.codex_service  # type: ignore[no-any-return]


def _models(request: Request) -> ModelSkills:
    return ModelSkills(request.app.state.database, request.app.state.settings)


def require_board_reader(request: Request) -> None:
    """Who may read the Skills page's list: NERVIS's GET relay, or an admin credential."""
    identity = request.state.identity
    if identity.may_write_configuration:
        return
    if identity.is_anonymous or identity.application_id != "nervis":
        raise refusals.skills_board_reader_required()


def require_models_caller(request: Request) -> None:
    """Who reads skills for the other models: Clarvis's or NERVIS's client credential, only."""
    identity = request.state.identity
    if identity.is_anonymous or identity.may_write_configuration:
        raise refusals.models_skills_reader_required()
    callers = {"nervis", *request.app.state.settings.agent_client_applications}
    if identity.application_id not in callers:
        raise refusals.models_skills_reader_required()


@router.get("", dependencies=[Depends(require_board_reader)])
async def read_skills(request: Request) -> dict[str, Any]:
    """Every skill, where it comes from, and its switch for Codex and for the other models."""
    models = _models(request)
    return await _board(request, models, await models.folders())


@router.post("", dependencies=[Depends(require_admin)])
async def switch_skill(request: Request) -> dict[str, Any]:
    """The Skills page switching one skill on or off for one engine; the whole list afterwards."""
    path, engine, enabled = _switch_body(await _json_object(request))
    models = _models(request)
    folders = await models.folders()
    if engine == MODELS:
        name, source = _switch_for_models(models, folders, path, enabled)
    else:
        name, source = await _switch_for_codex(request, folders, path, enabled)
    audit.record(request, "ravis.skill_switched", name=name, source=source, engine=engine,
                 enabled=enabled)
    return await _board(request, models, folders)


@router.get("/models", dependencies=[Depends(require_models_caller)])
async def skills_for_models(request: Request) -> dict[str, Any]:
    """The skills switched on for the other models: one short entry each, for their instructions."""
    skills = await _models(request).switched_on()
    return {"skills": [{"id": skill.id, "name": skill.name, "description": skill.description}
                       for skill in skills]}


@router.get("/models/read", dependencies=[Depends(require_models_caller)])
async def read_for_models(request: Request, skill: str = "", file: str = SKILL_FILE
                          ) -> dict[str, Any]:
    """One file of a skill switched on for the other models: its `SKILL.md`, or the file named."""
    found, text = await _models(request).read(skill, file, request.state.identity.application_id)
    return {"skill": found.id, "name": found.name, "file": file,
            "bytes": len(text.encode("utf-8")), "text": text}


def _switch_body(body: dict[str, Any]) -> tuple[str, str, bool]:
    """The body's `path`, `engine` and `enabled`, or 422; the path is checked against the list."""
    path, engine, enabled = body.get("path"), body.get("engine"), body.get("enabled")
    if (not isinstance(path, str) or not 0 < len(path) <= MOST_PATH_CHARACTERS
            or engine not in ENGINES or not isinstance(enabled, bool)):
        raise refusals.invalid_body(SWITCH_BODY)
    return path, engine, enabled


def _switch_for_models(models: ModelSkills, folders: list[Folder], path: str, enabled: bool
                       ) -> tuple[str, str]:
    """Only a skill RAVIS read whole just now. Kept at once: the other models' callers read the
    switches on every request, so there is nothing to apply."""
    skill = next((each for folder in folders for each in folder.skills
                  if each.path == path and each.problem is None), None)
    if skill is None:
        raise refusals.skill_not_listed_for(MODELS)
    models.switch(skill, enabled)
    return skill.name, skill.source


async def _switch_for_codex(request: Request, folders: list[Folder], path: str, enabled: bool
                            ) -> tuple[str, str]:
    """Only a skill Codex lists just now, switched exactly as `POST /api/v1/codex/skills` does:
    503 while Codex can't list, 409 `SKILL_NOT_CHANGED` when Codex doesn't take it."""
    service = _service(request)
    codex = _codex_skills(await service.skills_listed())
    entry = next((each for each in listed(folders, codex) if each.path == path), None)
    if entry is None or entry.codex is None:
        raise refusals.skill_not_listed_for(CODEX)
    await service.switch_skill(entry.codex.path, enabled)
    return entry.name, entry.source


async def _board(request: Request, models: ModelSkills, folders: list[Folder]) -> dict[str, Any]:
    """`SkillsBoard`: RAVIS's reading, lined up with Codex's list while Codex can give it."""
    codex: dict[str, Any]
    try:
        view = await _service(request).skills_listed()
    except CodexRefusalError as refused:
        if refused.status != 503:
            raise
        codex = {"listed": False, "problem": refused.message}
        return board(folders, listed(folders, None), codex, models.served)
    codex = {"listed": True, "problem": view["problem"]}
    return board(folders, listed(folders, _codex_skills(view)), codex, models.served)


def _codex_skills(view: dict[str, Any]) -> list[Skill]:
    """Codex's list as `GET /api/v1/codex/skills` gives it, back into the skills it was made of."""
    return [Skill(**skill) for skill in view["skills"]]
