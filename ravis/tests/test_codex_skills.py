"""The Codex skills a task may use: NERVIS's folder on, the owner's personal skills off.

`agent/skills.py`, run against the fake app-server's skills half (`fake_codex_skills.py`), which
answers Codex 0.154.0's three skills methods in their measured shapes on test folders only. What
RAVIS is held to:

- **the owner's defaults at every start**: the NERVIS folder made with its README and named as an
  extra root, its skills on; Codex's built-in skills on; the owner's personal skills switched off;
- **a personal skill added later starts off**, and one added to the NERVIS folder starts on, once
  Codex says its skills changed;
- **the owner's switches outlive a restart**, even of a Codex that forgot its own record of them,
  because RAVIS's database is the record;
- **anything short of Codex agreeing refuses every task** — a list Codex won't give, a switch it
  doesn't take, a skills folder setting RAVIS can't use — and `GET /api/v1/codex` says why;
- **the routes**: only NERVIS's GET relay and admin credentials read; only admin credentials switch;
  only the path of a skill Codex lists; a switch Codex doesn't take is put back;
- **no task root reaches the folder**: the folder, a folder holding it, or one inside it is refused,
  real paths compared on both sides, whether or not it exists yet;
- **where a skill comes from is decided by its real path**, and a project's own skills are Codex's.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pytest
from tests.agent_rig import fixture, project, ready_rig, refused, serving
from tests.codex_rig import eventually

from ravis.agent import roots
from ravis.agent.skills import (
    DESCRIPTION_LIMIT,
    README,
    folder_refusal,
    listed_skills,
    prepare_folder,
)
from ravis.codex import state
from ravis.codex.refusals import CodexRefusalError
from ravis.codex.routing import ActiveTurns, MessageRouter
from ravis.config import Settings

SKILLS = "/api/v1/codex/skills"


def case(method: str, name: str) -> dict[str, Any]:
    """One of `codex-admin.json`'s examples for the skills route, request and response."""
    route = next(r for r in fixture("codex-admin.json")["routes"]
                 if (r["method"], r["path"]) == (method, SKILLS))
    return next(example for example in route["examples"] if example["name"] == name)  # type: ignore[no-any-return]


def message(method: str, name: str) -> str:
    return case(method, name)["response"]["body"]["error"]["message"]  # type: ignore[no-any-return]


def skill_file(folder: Path, name: str, description: str) -> Path:
    """A skill as Codex finds one on disk: a folder holding a SKILL.md with its name."""
    path = folder / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n")
    return path


def real(tmp_path: Path) -> Path:
    return Path(os.path.realpath(tmp_path))


def nervis_folder(tmp_path: Path) -> Path:
    """The skills folder the rig names, inside the test's own coding folder (`codex_rig`)."""
    return real(tmp_path) / "coding" / "NERVIS workspace" / "clarvis" / "skills"


def skills_rig(tmp_path: Path, *, personal: bool = True, scenario: dict[str, Any] | None = None,
               **settings: Any) -> Any:
    """A ready RAVIS whose Codex has one skill of each kind: NERVIS's, personal, built in."""
    home_skills = real(tmp_path) / "home" / ".agents" / "skills"
    home_skills.mkdir(parents=True)
    if personal:
        skill_file(home_skills, "graphify", "Turn any input into a knowledge graph.")
    skill_file(tmp_path / "codex-home" / "skills" / ".system", "imagegen", "Generate images.")
    skill_file(nervis_folder(tmp_path), "nervis-notes", "How NERVIS tasks keep their notes.")
    return ready_rig(tmp_path, scenario={"skills_user_root": str(home_skills), **(scenario or {})},
                     **settings)


def shown(view: dict[str, Any]) -> list[tuple[str, str, bool]]:
    return [(skill["name"], skill["source"], skill["enabled"]) for skill in view["skills"]]


def path_of(view: dict[str, Any], name: str) -> str:
    return next(skill["path"] for skill in view["skills"] if skill["name"] == name)  # type: ignore[no-any-return]


def writes(rig: Any, after: int = 0) -> list[tuple[str, bool]]:
    return [(record["path"], record["enabled"])
            for record in rig.server.records("skills_written")[after:]]


def codex_state(relay: Any) -> dict[str, Any]:
    return relay.call("GET", "/api/v1/codex", caller="client.nervis").json()  # type: ignore[no-any-return]


# ── At every start ───────────────────────────────────────────────────────────


def test_codex_starts_with_nervis_and_built_in_skills_on_and_personal_skills_off(
    tmp_path: Path,
) -> None:
    rig = skills_rig(tmp_path)
    folder = nervis_folder(tmp_path)
    with serving(rig) as relay:
        assert relay.ready()["state"] == "signed_in"
        view = relay.call("GET", SKILLS, caller="client.nervis").json()

    assert view["folder"] == str(folder) and view["problem"] is None
    assert shown(view) == [("nervis-notes", "nervis", True), ("graphify", "personal", False),
                           ("imagegen", "built_in", True)]
    assert view["skills"][1]["description"] == "Turn any input into a knowledge graph."
    assert set(view["skills"][0]) == {"path", "name", "description", "source", "enabled"}
    # Only the personal skill needed switching, by its path; the folder was named before the list.
    assert writes(rig) == [(path_of(view, "graphify"), False)]
    assert [record["roots"] for record in rig.server.records("skills_roots")] == [[str(folder)]]
    assert (folder / "README.md").read_text(encoding="utf-8") == README
    # The contract's example has the shape RAVIS answers with.
    example = case("GET", "every skill but a project's own")["response"]["body"]
    assert set(example) == set(view)
    assert all(set(skill) == set(view["skills"][0]) for skill in example["skills"])
    assert {skill["source"] for skill in example["skills"]} == {"nervis", "personal", "built_in"}


def test_a_skill_added_later_starts_off_when_personal_and_on_in_the_nervis_folder(
    tmp_path: Path,
) -> None:
    rig = skills_rig(tmp_path)
    home_skills = real(tmp_path) / "home" / ".agents" / "skills"
    with serving(rig) as relay:
        relay.ready()
        before = len(writes(rig))
        foundry = skill_file(home_skills, "microsoft-foundry", "Deploy to Azure AI Foundry.")
        skill_file(nervis_folder(tmp_path), "nervis-later", "A NERVIS skill added later.")
        rig.server.send("notify", method="skills/changed", params={})
        eventually(lambda: writes(rig, before) == [(str(foundry), False)], 15,
                   "the new personal skill switched off")
        eventually(lambda: codex_state(relay)["state"] == "signed_in", 15, "tasks allowed again")
        view = relay.call("GET", SKILLS, caller="admin.launcher").json()

    assert shown(view) == [
        ("nervis-later", "nervis", True), ("nervis-notes", "nervis", True),
        ("graphify", "personal", False), ("microsoft-foundry", "personal", False),
        ("imagegen", "built_in", True),
    ]


def test_the_owners_switches_outlive_a_restart_of_a_codex_that_forgot_them(tmp_path: Path) -> None:
    rig = skills_rig(tmp_path)
    with serving(rig) as relay:
        relay.ready()
        view = relay.call("GET", SKILLS, caller="client.nervis").json()
        graphify, imagegen = path_of(view, "graphify"), path_of(view, "imagegen")
        on = relay.call("POST", SKILLS, caller="admin.launcher",
                        body={"path": graphify, "enabled": True})
        assert on.status_code == 200, on.text
        assert ("graphify", "personal", True) in shown(on.json())
        off = relay.call("POST", SKILLS, caller="admin.launcher",
                         body={"path": imagegen, "enabled": False})
        assert ("imagegen", "built_in", False) in shown(off.json())
        # A fresh Codex home keeps no switches, so Codex starts with every skill on: only RAVIS's
        # database still says imagegen is off, and that graphify, a personal skill, is on.
        (tmp_path / "codex-home" / "fake-skills-config.json").unlink()
        written = len(writes(rig))
        # Every switch applies everything again, naming the folder each time, so count from here.
        named = len(rig.server.records("skills_roots"))
        rig.server.send("crash", status=3)
        eventually(lambda: len(rig.server.records("skills_roots")) == named + 1, 15,
                   "the folder named again at the new start")
        eventually(lambda: codex_state(relay)["state"] == "signed_in", 15, "Codex ready again")
        after = relay.call("GET", SKILLS, caller="client.nervis").json()
    # imagegen switched off again; graphify left on, where a lost choice would have switched it off.
    assert writes(rig, written) == [(imagegen, False)]

    assert shown(after) == [("nervis-notes", "nervis", True), ("graphify", "personal", True),
                            ("imagegen", "built_in", False)]
    # Audited with who switched it (the record adds its request id), the skill and the switch.
    switched = rig.published("ravis.codex.skill_switched")
    assert [{key: event[key] for key in ("name", "source", "enabled")} for event in switched] == [
        {"name": "graphify", "source": "personal", "enabled": True},
        {"name": "imagegen", "source": "built_in", "enabled": False},
    ]
    assert {event["application_id"] for event in switched} == {"launcher"}
    example = case("POST", "a personal skill switched on")
    assert example["request"]["caller"] == "admin.launcher"
    assert set(example["request"]["body"]) == {"path", "enabled"}


# ── Short of Codex agreeing, no task starts ──────────────────────────────────


def not_ready_saying(relay: Any, words: str) -> dict[str, Any]:
    def said() -> dict[str, Any] | None:
        body = codex_state(relay)
        return body if body["state"] == "runtime_down" and words in body["reason"] else None
    return eventually(said, 15, f"Codex not ready, saying {words!r}")  # type: ignore[no-any-return]


def test_a_skills_list_codex_wont_give_refuses_every_task_and_says_why(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="ravis")
    rig = skills_rig(tmp_path, scenario={"refused_methods": ["skills/list"]})
    root, git_dir = project(rig)
    with serving(rig) as relay:
        body = not_ready_saying(relay, "couldn't switch Codex's skills to your choices")
        assert "(codex_did_not_answer)" in body["reason"] and "no task can start" in body["reason"]
        refused(relay.create(root, git_dir), 409, "CODEX_NOT_READY", state="runtime_down",
                reason=body["reason"])
        read = refused(relay.call("GET", SKILLS, caller="client.nervis"), 503,
                       "CODEX_RUNTIME_UNAVAILABLE")
        assert read["retryable"] is True
        assert read["message"] == message("GET", "Codex isn't running")
        switch = refused(relay.call("POST", SKILLS, caller="admin.launcher",
                                    body={"path": "/x/SKILL.md", "enabled": True}), 503,
                         "CODEX_RUNTIME_UNAVAILABLE")
        assert switch["message"] == message("POST", "Codex isn't running")
    assert not rig.server.received("thread/start")
    assert writes(rig) == []
    assert any("couldn't put Codex's skills to the owner's choices (codex_did_not_answer)"
               in record.getMessage() for record in caplog.records)


def test_a_switch_codex_doesnt_take_at_start_refuses_every_task_naming_the_skill(
    tmp_path: Path,
) -> None:
    rig = skills_rig(tmp_path, scenario={"skills_write_ignored": True})
    root, git_dir = project(rig)
    with serving(rig) as relay:
        body = not_ready_saying(relay, "Codex didn't switch graphify off")
        assert "(not_written: " in body["reason"]
        refused(relay.create(root, git_dir), 409, "CODEX_NOT_READY", reason=body["reason"])
        view = relay.call("GET", SKILLS, caller="client.nervis").json()
        assert view["problem"] == body["reason"]
    assert not rig.server.received("thread/start")


def test_a_skills_folder_setting_ravis_cant_use_refuses_every_task_and_makes_nothing(
    tmp_path: Path,
) -> None:
    elsewhere = real(tmp_path) / "elsewhere" / "skills"
    rig = skills_rig(tmp_path, codex_skills_folder=str(elsewhere))
    root, git_dir = project(rig)
    with serving(rig) as relay:
        body = not_ready_saying(relay, "RAVIS's skills folder setting can't be used")
        assert f"the skills folder {elsewhere} isn't inside a folder Codex tasks may use" in (
            body["reason"])
        refused(relay.create(root, git_dir), 409, "CODEX_NOT_READY", reason=body["reason"])
        view = relay.call("GET", SKILLS, caller="admin.launcher").json()
        assert view["problem"] == body["reason"] and view["folder"] == str(elsewhere)
        graphify = path_of(view, "graphify")
        refused(relay.call("POST", SKILLS, caller="admin.launcher",
                           body={"path": graphify, "enabled": True}), 409, "SKILL_NOT_CHANGED",
                skill="graphify", reason="folder_refused")
    assert not elsewhere.exists()
    assert rig.server.records("skills_roots") == [] and writes(rig) == []
    assert not rig.published("ravis.codex.skill_switched")


def test_tasks_wait_while_changed_skills_are_applied_again_and_until_codex_agrees(
    tmp_path: Path,
) -> None:
    """A skill file changed: until RAVIS has switched Codex's skills back to the owner's choices,
    no task starts, so none can start with a new personal skill on."""
    rig = skills_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        assert relay.ready()["state"] == "signed_in"
        rig.server.send("silence", method="skills/list")
        rig.server.send("notify", method="skills/changed", params={})
        body = not_ready_saying(relay, "switching them to your choices")
        refused(relay.create(root, git_dir), 409, "CODEX_NOT_READY", reason=body["reason"])
        # Codex never answers, so after the request's wait RAVIS says so, and tasks stay refused.
        after = not_ready_saying(relay, "(codex_did_not_answer)")
        refused(relay.create(root, git_dir), 409, "CODEX_NOT_READY", reason=after["reason"])
    assert not rig.server.received("thread/start")


def test_skills_being_applied_again_hold_tasks_back_and_say_so() -> None:
    assert state._skills_row(state.SKILLS_APPLIED, None) is None
    word, reason = state._skills_row(state.SKILLS_CHECKING, None)  # type: ignore[misc]
    assert word == "runtime_down" and "switching them to your choices" in reason
    assert state._skills_row(state.SKILLS_PENDING, None) == (
        "runtime_down", "Codex's process is starting.")
    assert state.skills_problem(state.SKILLS_CHECKING, None) is None
    assert "(still_different)" in str(state.skills_problem(state.SKILLS_STILL_DIFFERENT, None))


# ── The routes ───────────────────────────────────────────────────────────────


def test_only_nervis_and_admin_credentials_read_and_only_admin_credentials_switch(
    tmp_path: Path,
) -> None:
    project_skills = real(tmp_path) / "project" / ".agents" / "skills"
    helper = skill_file(project_skills, "project-helper", "A project's own skill.")
    rig = skills_rig(tmp_path, scenario={"skills_repo_root": str(project_skills)})
    with serving(rig) as relay:
        relay.ready()
        for reader in ("client.nervis", "admin.launcher", "admin.owner_cli"):
            read = relay.call("GET", SKILLS, caller=reader)
            assert read.status_code == 200, reader
            assert "project-helper" not in [skill["name"] for skill in read.json()["skills"]]
        for stranger in ("anonymous", "client.clarvis", "client.other"):
            error = refused(relay.call("GET", SKILLS, caller=stranger), 403, "FORBIDDEN")
            assert error["message"] == message("GET", "a Clarvis credential"), stranger
        graphify = path_of(relay.call("GET", SKILLS, caller="client.nervis").json(), "graphify")
        written = len(writes(rig))
        switch = {"path": graphify, "enabled": True}
        for caller in ("client.nervis", "client.clarvis", "anonymous"):
            error = refused(relay.call("POST", SKILLS, caller=caller, body=switch,
                                       headers={"X-Agent-Session-Token": "ast_" + "A" * 43}),
                            403, "FORBIDDEN")
            assert error["message"] == message("POST", "a client credential"), caller
        long_path = "/" + "a" * 4096
        for body in ({}, {"path": graphify}, {"enabled": True},
                     {"path": graphify, "enabled": "yes"}, {"path": 7, "enabled": True},
                     {"path": "", "enabled": False}, {"path": long_path, "enabled": True}):
            error = refused(relay.call("POST", SKILLS, caller="admin.launcher", body=body), 422,
                            "INVALID_REQUEST_BODY")
            assert error["message"] == message("POST", "not a switch"), body
        for path in (str(helper), str(real(tmp_path) / "somewhere" / "SKILL.md"),
                     graphify + "/", graphify.replace("/SKILL.md", ""), "graphify"):
            error = refused(relay.call("POST", SKILLS, caller="admin.launcher",
                                       body={"path": path, "enabled": True}), 404,
                            "SKILL_NOT_FOUND")
            assert error["message"] == message("POST", "a path Codex doesn't list"), path
        assert ("graphify", "personal", False) in shown(
            relay.call("GET", SKILLS, caller="client.nervis").json())
    assert writes(rig, written) == []
    assert not rig.published("ravis.codex.skill_switched")


def test_a_switch_codex_doesnt_take_is_put_back_and_tasks_can_still_start(tmp_path: Path) -> None:
    # No personal skill, so the start needs no switch and applies; the owner's one then fails.
    rig = skills_rig(tmp_path, personal=False, scenario={"skills_write_ignored": True})
    with serving(rig) as relay:
        assert relay.ready()["state"] == "signed_in"
        imagegen = path_of(relay.call("GET", SKILLS, caller="client.nervis").json(), "imagegen")
        error = refused(relay.call("POST", SKILLS, caller="admin.launcher",
                                   body={"path": imagegen, "enabled": False}), 409,
                        "SKILL_NOT_CHANGED", skill="imagegen", reason="not_written")
        assert error["message"] == message("POST", "Codex didn't take it")
        # Put back: the built-in skill stays on as Codex holds it, and nothing waits on the skills.
        assert ("imagegen", "built_in", True) in shown(
            relay.call("GET", SKILLS, caller="client.nervis").json())
        assert codex_state(relay)["state"] == "signed_in"
        assert rig.service.skills.wanted(
            next(skill for skill in listed_skills(
                {"data": [{"skills": [{"name": "imagegen", "path": imagegen, "scope": "system",
                                        "enabled": True}]}]}, nervis_folder(tmp_path)) or []))
    assert writes(rig) == [(imagegen, False)]
    assert not rig.published("ravis.codex.skill_switched")


def test_skills_changed_reaches_the_service_like_the_accounts_news() -> None:
    delivered: list[str] = []
    router = MessageRouter(lambda method, _params: delivered.append(method), ActiveTurns())
    router.notification("skills/changed", {})
    router.notification("item/started", {"threadId": "t"})
    assert delivered == ["skills/changed"]


# ── No task works in or around the folder ────────────────────────────────────


def roots_settings(coding: Path, folder: Path | str, **more: Any) -> Settings:
    return Settings(  # type: ignore[call-arg]
        database_path=":memory:", _env_file=None,
        agent_allowed_roots=[str(coding)], codex_skills_folder=str(folder), **more,
    )


def refusal_for(root: Path, settings: Settings) -> CodexRefusalError:
    with pytest.raises(CodexRefusalError) as refusal:
        roots.workspace_root(str(root), settings)
    return refusal.value


def test_no_task_root_is_holds_or_lies_inside_the_skills_folder_real_paths_compared(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="ravis")
    coding = real(tmp_path) / "coding"
    workspace = coding / "NERVIS workspace"
    folder = workspace / "clarvis" / "skills"
    task = workspace / "clarvis" / "nervis-tasks" / "add-utc"
    task.mkdir(parents=True)
    settings = roots_settings(coding, folder)

    def denied(root: Path, with_settings: Settings = settings) -> None:
        refusal = refusal_for(root, with_settings)
        assert (refusal.code, refusal.details) == ("WORKSPACE_ROOT_NOT_ALLOWED",
                                                   {"reason": "denied_path"}), root
        assert f"NERVIS's skills folder, {folder}," in str(refusal), root

    # Before RAVIS has made the folder, the workspace and its clarvis folder are refused already.
    assert not folder.exists()
    denied(workspace)
    denied(workspace / "clarvis")
    folder.mkdir()
    (folder / "notes").mkdir()
    denied(folder)
    denied(folder / "notes")
    assert roots.workspace_root(str(task), settings) == task
    assert roots.workspace_root(str(workspace / "clarvis" / "nervis-tasks"), settings) == (
        workspace / "clarvis" / "nervis-tasks")
    # A root reached through a link is its real path: the link to the workspace, and one into the
    # folder, are refused like the folders they lead to.
    (coding / "workspace-link").symlink_to(workspace)
    (coding / "notes-link").symlink_to(folder / "notes")
    denied(coding / "workspace-link")
    denied(coding / "notes-link")
    # And a setting that names the folder through a link means the folder it leads to.
    through_link = roots_settings(coding, coding / "workspace-link" / "clarvis" / "skills")
    denied(workspace / "clarvis", through_link)
    assert roots.workspace_root(str(task), through_link) == task
    assert any(f"NERVIS skills folder {folder}" in record.getMessage()
               for record in caplog.records)


def test_the_skills_folder_setting_must_be_a_folder_of_its_own_in_the_coding_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = real(tmp_path) / "home"
    coding = home / "coding"
    coding.mkdir(parents=True)
    # `Path.home()` reads HOME: the ChatGPT app's `~/.codex` here is the test's, never the owner's.
    monkeypatch.setenv("HOME", str(home))

    def why(folder: Path | str, roots_allowed: Path = coding, **more: Any) -> str | None:
        return folder_refusal(Settings(  # type: ignore[call-arg]
            database_path=":memory:", _env_file=None, agent_allowed_roots=[str(roots_allowed)],
            codex_skills_folder=str(folder), **{"codex_home": str(home / "ravis-codex"), **more},
        ))

    assert why(coding / "NERVIS workspace" / "clarvis" / "skills") is None
    assert why("~/coding/NERVIS workspace/clarvis/skills") is None
    for relative in ("NERVIS workspace/clarvis/skills", "", "   "):
        assert "isn't an absolute path" in str(why(relative)), relative
    assert "is the folder that holds every project" in str(why(coding))
    assert "isn't inside a folder Codex tasks may use" in str(why(real(tmp_path) / "elsewhere"))
    (coding / "out-link").symlink_to(real(tmp_path))
    assert "isn't inside a folder Codex tasks may use" in str(why(coding / "out-link" / "skills"))
    assert "the ChatGPT app's own Codex home" in str(why(home / ".codex" / "skills", home))
    assert "RAVIS's own Codex home" in str(
        why(coding / "codex" / "skills", codex_home=str(coding / "codex")))
    assert "ecosystem's own repositories" in str(why(
        coding / "NERVIS-ecosystem" / "skills",
        agent_protected_repositories=[str(coding / "NERVIS-ecosystem")]))
    assert "must never touch" in str(why(
        coding / ".run" / "skills", agent_denied_paths=[str(coding / ".run")]))


# ── What Codex lists ─────────────────────────────────────────────────────────


def raw_skill(name: str, path: Path | str, scope: str, **more: Any) -> dict[str, Any]:
    return {"name": name, "path": str(path), "scope": scope, "enabled": True,
            "description": f"{name} does things.", **more}


def test_where_a_skill_comes_from_is_decided_by_its_real_path(tmp_path: Path) -> None:
    folder = real(tmp_path) / "skills"
    folder.mkdir()
    personal = real(tmp_path) / "home-skills" / "graphify"
    personal.mkdir(parents=True)
    (folder / "graphify").symlink_to(personal)
    long_text = "word " * 200
    listed = listed_skills({"data": [
        {"cwd": str(folder), "errors": [{"path": "/x", "message": "bad"}], "skills": [
            raw_skill("notes", folder / "notes" / "SKILL.md", "user"),
            raw_skill("graphify-linked", folder / "graphify" / "SKILL.md", "user"),
            raw_skill("site-wide", "/etc/codex/skills/site-wide/SKILL.md", "admin"),
            raw_skill("future", "/opt/skills/future/SKILL.md", "team",
                      description="  Spread\n over   lines. "),
            raw_skill("imagegen", "/h/.codex/skills/.system/imagegen/SKILL.md", "system",
                      shortDescription="From SKILL.md", interface={"shortDescription": "Images"}),
            raw_skill("openai-docs", "/h/.codex/skills/.system/openai-docs/SKILL.md", "system",
                      description=long_text),
            raw_skill("project-helper", "/p/.agents/skills/project-helper/SKILL.md", "repo"),
            {"scope": "repo", "path": "not even a path"},
        ]},
        {"cwd": "/other", "errors": [], "skills": [
            raw_skill("notes", folder / "notes" / "SKILL.md", "user")]},
    ]}, folder)
    assert listed is not None
    assert [(skill.name, skill.source) for skill in listed] == [
        ("notes", "nervis"), ("future", "personal"), ("graphify-linked", "personal"),
        ("site-wide", "personal"), ("imagegen", "built_in"), ("openai-docs", "built_in")]
    descriptions = {skill.name: skill.description for skill in listed}
    assert descriptions["imagegen"] == "Images"
    assert descriptions["future"] == "Spread over lines."
    assert len(descriptions["openai-docs"]) == DESCRIPTION_LIMIT
    assert descriptions["openai-docs"].endswith("…")


@pytest.mark.parametrize("answer", [
    None, {}, {"data": "x"}, {"data": [{"cwd": "/x"}]},
    {"data": [{"skills": [{"name": "graphify", "path": "/h/graphify/SKILL.md", "scope": "user"}]}]},
    {"data": [{"skills": [{"name": "graphify", "path": "graphify/SKILL.md", "scope": "user",
                           "enabled": True}]}]},
    {"data": [{"skills": ["graphify"]}]},
])
def test_a_list_ravis_cant_read_whole_is_not_a_list(answer: object, tmp_path: Path) -> None:
    """A skill RAVIS can't read is one it can't switch off, so the whole answer counts as unread."""
    assert listed_skills(answer, tmp_path) is None


def test_the_folder_is_made_with_a_readme_that_never_replaces_the_owners(tmp_path: Path) -> None:
    folder = tmp_path / "workspace" / "clarvis" / "skills"
    prepare_folder(folder)
    assert (folder / "README.md").read_text(encoding="utf-8") == README
    (folder / "README.md").write_text("mine\n")
    prepare_folder(folder)
    assert (folder / "README.md").read_text() == "mine\n"
    other = tmp_path / "other"
    other.mkdir()
    (other / "README.md").symlink_to(tmp_path / "nowhere.md")
    prepare_folder(other)
    assert not (tmp_path / "nowhere.md").exists()
