"""Skills for every engine over HTTP: `/api/v1/skills` and the other models' reads (RAVIS 0.27.0).

Run against the fake Codex where Codex's half matters, and against a RAVIS whose Codex can't run at
all where it doesn't. The shapes, codes and messages are the contract fixture `skills.json`. What
RAVIS is held to:

- **one list, with a switch per engine**: NERVIS's skills on for both, personal skills off for
  both — one added later too — and Codex's built-in skills Codex's only;
- **who may call**: NERVIS's relay or an admin reads the list; only an admin switches; only
  Clarvis's or NERVIS's client credential reads skills for other models, never an admin;
- **a switch is checked**: its body, and that RAVIS lists that path for that engine just now;
- **the other models' switch is theirs alone**, and **Codex's is the switch of old**: applied to
  Codex, seen by `GET /api/v1/codex/skills`, and refused when Codex doesn't take it;
- **while Codex can't run**, the list, the other models' switch and their reads still answer;
- **a read serves only a switched-on skill, only inside its folder**, logged without its text;
- **every route carries the guard its callers need**.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.agent_rig import fixture, keys, refused, serving
from tests.codex_rig import codex_rig, eventually
from tests.test_codex_skills import codex_state, nervis_folder, real, skill_file, skills_rig, writes

from ravis.agent.skills import UNREADABLE
from ravis.api.management import skills as skills_routes
from ravis.api.management.codex import require_admin
from ravis.codex.refusals import SKILL_FILE_REFUSALS

SKILLS = "/api/v1/skills"
FOR_MODELS = "/api/v1/skills/models"
READ = "/api/v1/skills/models/read"
CODEX_SKILLS = "/api/v1/codex/skills"


def example(method: str, path: str, name: str) -> dict[str, Any]:
    """One of `skills.json`'s examples, request and response."""
    route = next(r for r in fixture("skills.json")["routes"]
                 if (r["method"], r["path"]) == (method, path))
    return next(case for case in route["examples"] if case["name"] == name)  # type: ignore[no-any-return]


def said(method: str, path: str, name: str) -> str:
    return example(method, path, name)["response"]["body"]["error"]["message"]  # type: ignore[no-any-return]


def rows(view: dict[str, Any]) -> list[tuple[Any, ...]]:
    """Each skill as (name, source, Codex has it, on for Codex, other models may, on for them)."""
    return [(entry["name"], entry["source"], entry["codex"]["available"],
             entry["codex"]["enabled"], entry["models"]["available"], entry["models"]["enabled"])
            for entry in view["skills"]]


def path_in(view: dict[str, Any], name: str) -> str:
    return next(entry["path"] for entry in view["skills"] if entry["name"] == name)  # type: ignore[no-any-return]


def personal_folder(tmp_path: Path) -> Path:
    """The owner's personal skills, where `conftest` points RAVIS and `skills_rig` points Codex."""
    return real(tmp_path) / "home" / ".agents" / "skills"


def codexless_rig(tmp_path: Path) -> Any:
    """A RAVIS whose Codex can't run at all, with one NERVIS skill and one personal skill."""
    rig = codex_rig(tmp_path, app_server=False)
    skill_file(personal_folder(tmp_path), "graphify", "Turn any input into a knowledge graph.")
    skill_file(nervis_folder(tmp_path), "nervis-notes", "How NERVIS tasks keep their notes.")
    return rig


def switched(rig: Any) -> list[dict[str, Any]]:
    return [{key: event[key] for key in ("name", "source", "engine", "enabled")}
            for event in rig.published("ravis.skill_switched")]


# ── One list, a switch per engine ────────────────────────────────────────────


def test_one_list_nervis_on_for_both_personal_off_for_both_and_built_in_codex_only(
    tmp_path: Path,
) -> None:
    rig = skills_rig(tmp_path)
    skill_file(nervis_folder(tmp_path), "half-written", "")
    with serving(rig) as relay:
        assert relay.ready()["state"] == "signed_in"
        view = relay.call("GET", SKILLS, caller="client.nervis").json()
        # Added later, and Codex told its skills changed, as it tells RAVIS when a file appears.
        later = skill_file(personal_folder(tmp_path), "later", "A personal skill added later.")
        skill_file(nervis_folder(tmp_path), "nervis-later", "A NERVIS skill added later.")
        before = len(writes(rig))
        rig.server.send("notify", method="skills/changed", params={})
        eventually(lambda: writes(rig, before) == [(str(later), False)], 15,
                   "the later personal skill switched off for Codex")
        eventually(lambda: codex_state(relay)["state"] == "signed_in", 15, "tasks allowed again")
        after = relay.call("GET", SKILLS, caller="admin.launcher").json()

    assert view["folders"] == {
        "nervis": {"path": str(nervis_folder(tmp_path)), "problem": None},
        "personal": {"path": str(personal_folder(tmp_path)), "problem": None},
    }
    assert view["codex"] == {"listed": True, "problem": None}
    assert [entry["id"] for entry in view["skills"]] == [
        "nervis/half-written", "nervis/nervis-notes", "personal/graphify", "built_in/imagegen"]
    assert rows(view) == [
        ("half-written", "nervis", True, True, False, False),
        ("nervis-notes", "nervis", True, True, True, True),
        ("graphify", "personal", True, False, True, False),
        ("imagegen", "built_in", True, True, False, False),
    ]
    assert view["skills"][0]["problem"] == "its SKILL.md's front matter has no description"
    assert rows(after) == [
        ("half-written", "nervis", True, True, False, False),
        ("nervis-later", "nervis", True, True, True, True),
        ("nervis-notes", "nervis", True, True, True, True),
        ("graphify", "personal", True, False, True, False),
        ("later", "personal", True, False, True, False),
        ("imagegen", "built_in", True, True, False, False),
    ]
    body = example("GET", SKILLS, "every skill with a switch for each engine")["response"]["body"]
    assert keys(body) == keys(view)
    assert {entry["source"] for entry in body["skills"]} == {"nervis", "personal", "built_in"}


# ── Who may call ─────────────────────────────────────────────────────────────


def test_who_may_read_the_list_switch_a_skill_and_read_skills_for_other_models(
    tmp_path: Path,
) -> None:
    rig = codexless_rig(tmp_path)
    with serving(rig) as relay:
        for reader in ("client.nervis", "admin.launcher", "admin.owner_cli"):
            assert relay.call("GET", SKILLS, caller=reader).status_code == 200, reader
        for stranger in ("anonymous", "client.clarvis", "client.other"):
            error = refused(relay.call("GET", SKILLS, caller=stranger), 403, "FORBIDDEN")
            assert error["message"] == said("GET", SKILLS, "a Clarvis credential"), stranger
        graphify = path_in(relay.call("GET", SKILLS, caller="client.nervis").json(), "graphify")
        switch = {"path": graphify, "engine": "models", "enabled": True}
        for caller in ("client.nervis", "client.clarvis", "anonymous"):
            error = refused(relay.call("POST", SKILLS, caller=caller, body=switch), 403,
                            "FORBIDDEN")
            assert error["message"] == said("POST", SKILLS, "a client credential"), caller
        for caller in ("client.clarvis", "client.nervis"):
            assert relay.call("GET", FOR_MODELS, caller=caller).status_code == 200, caller
            assert relay.call("GET", READ, caller=caller,
                              params={"skill": "nervis/nervis-notes"}).status_code == 200, caller
        for stranger in ("anonymous", "client.other", "admin.launcher", "admin.owner_cli"):
            for path, params in ((FOR_MODELS, None), (READ, {"skill": "nervis/nervis-notes"})):
                error = refused(relay.call("GET", path, caller=stranger, params=params), 403,
                                "FORBIDDEN")
                assert error["message"] == said("GET", FOR_MODELS, "an admin credential"), (
                    stranger, path)
    assert switched(rig) == []


def test_every_skills_route_carries_the_guard_its_callers_need() -> None:
    guarded = {
        (route.path, method): {dependency.dependency for dependency in route.dependencies}
        for route in skills_routes.router.routes
        for method in route.methods  # type: ignore[attr-defined]
    }
    assert guarded == {
        (SKILLS, "GET"): {skills_routes.require_board_reader},
        (SKILLS, "POST"): {require_admin},
        (FOR_MODELS, "GET"): {skills_routes.require_models_caller},
        (READ, "GET"): {skills_routes.require_models_caller},
    }


# ── A switch ─────────────────────────────────────────────────────────────────


def test_a_switch_is_checked_and_only_a_path_listed_for_that_engine_is_switched(
    tmp_path: Path,
) -> None:
    rig = skills_rig(tmp_path)
    skill_file(nervis_folder(tmp_path), "half-written", "")
    # A link out of NERVIS's folder: RAVIS lists it with its problem, and Codex doesn't list it.
    outside = real(tmp_path) / "elsewhere"
    skill_file(outside, "stranger", "Never read.")
    (nervis_folder(tmp_path) / "linked-outside").symlink_to(outside / "stranger")
    with serving(rig) as relay:
        relay.ready()
        view = relay.call("GET", SKILLS, caller="client.nervis").json()
        graphify, imagegen, half, linked = (
            path_in(view, name) for name in ("graphify", "imagegen", "half-written",
                                             "linked-outside"))
        written = len(writes(rig))
        for body in ({}, {"path": graphify, "enabled": True},
                     {"path": graphify, "engine": "models"},
                     {"path": graphify, "engine": "clarvis", "enabled": True},
                     {"path": graphify, "engine": "models", "enabled": "yes"},
                     {"path": 7, "engine": "models", "enabled": True},
                     {"path": "", "engine": "codex", "enabled": False},
                     {"path": "/" + "a" * 4096, "engine": "models", "enabled": True}):
            error = refused(relay.call("POST", SKILLS, caller="admin.launcher", body=body), 422,
                            "INVALID_REQUEST_BODY")
            assert error["message"] == said("POST", SKILLS, "not a switch"), body
        nowhere = str(real(tmp_path) / "somewhere" / "SKILL.md")
        for path, engine in ((imagegen, "models"), (half, "models"), (linked, "models"),
                             (nowhere, "models"), (graphify + "/", "models"),
                             ("graphify", "models"), ("personal/graphify", "models"),
                             (linked, "codex"), (nowhere, "codex"),
                             ("personal/graphify", "codex")):
            error = refused(relay.call("POST", SKILLS, caller="admin.launcher",
                                       body={"path": path, "engine": engine, "enabled": True}),
                            404, "SKILL_NOT_FOUND", engine=engine)
            assert error["message"] == said(
                "POST", SKILLS, "a built-in skill for the other models"), (path, engine)
        assert rows(relay.call("GET", SKILLS, caller="client.nervis").json()) == rows(view)
    assert writes(rig, written) == []
    assert switched(rig) == []


def test_the_other_models_switch_is_theirs_alone_and_counts_from_their_next_read(
    tmp_path: Path,
) -> None:
    rig = skills_rig(tmp_path)
    with serving(rig) as relay:
        relay.ready()
        view = relay.call("GET", SKILLS, caller="client.nervis").json()
        graphify, notes = path_in(view, "graphify"), path_in(view, "nervis-notes")
        written = len(writes(rig))
        on = relay.call("POST", SKILLS, caller="admin.launcher",
                        body={"path": graphify, "engine": "models", "enabled": True})
        assert on.status_code == 200, on.text
        for_models = relay.call("GET", FOR_MODELS, caller="client.clarvis").json()
        off = relay.call("POST", SKILLS, caller="admin.launcher",
                         body={"path": notes, "engine": "models", "enabled": False})
        after = relay.call("GET", FOR_MODELS, caller="client.nervis").json()
        refusal = relay.call("GET", READ, caller="client.clarvis",
                             params={"skill": "nervis/nervis-notes"})
        # A Codex switch afterwards applies every Codex choice again, and none of the other
        # models': nervis-notes, off for them now, stays on for Codex.
        codex_on = relay.call("POST", CODEX_SKILLS, caller="admin.launcher",
                              body={"path": graphify, "enabled": True})
        assert codex_on.status_code == 200, codex_on.text
        codex_view = relay.call("GET", CODEX_SKILLS, caller="client.nervis").json()

    assert ("graphify", "personal", True, False, True, True) in rows(on.json())
    assert ("nervis-notes", "nervis", True, True, True, False) in rows(off.json())
    assert for_models == {"skills": [
        {"id": "nervis/nervis-notes", "name": "nervis-notes",
         "description": "How NERVIS tasks keep their notes."},
        {"id": "personal/graphify", "name": "graphify",
         "description": "Turn any input into a knowledge graph."},
    ]}
    assert after == {"skills": [for_models["skills"][1]]}
    error = refused(refusal, 404, "SKILL_NOT_FOUND")
    assert error["message"] == said("GET", READ, "a skill switched off, or unknown")
    # Codex's switches untouched by the other models': only the Codex switch reached Codex.
    assert writes(rig, written) == [(graphify, True)]
    assert [(entry["name"], entry["enabled"]) for entry in codex_view["skills"]] == [
        ("nervis-notes", True), ("graphify", True), ("imagegen", True)]
    assert switched(rig) == [
        {"name": "graphify", "source": "personal", "engine": "models", "enabled": True},
        {"name": "nervis-notes", "source": "nervis", "engine": "models", "enabled": False},
    ]
    assert [event["name"] for event in rig.published("ravis.codex.skill_switched")] == ["graphify"]
    listed_example = example("GET", FOR_MODELS, "the skills switched on for other models")
    assert keys(listed_example["response"]["body"]) == keys(for_models)
    switch_example = example("POST", SKILLS, "a personal skill switched on for the other models")
    assert switch_example["request"]["caller"] == "admin.launcher"
    assert keys(switch_example["response"]["body"]) == keys(on.json())


def test_the_codex_switch_is_the_switch_of_old_applied_to_codex(tmp_path: Path) -> None:
    rig = skills_rig(tmp_path)
    with serving(rig) as relay:
        relay.ready()
        graphify = path_in(relay.call("GET", SKILLS, caller="client.nervis").json(), "graphify")
        written = len(writes(rig))
        on = relay.call("POST", SKILLS, caller="admin.launcher",
                        body={"path": graphify, "engine": "codex", "enabled": True})
        assert on.status_code == 200, on.text
        codex_view = relay.call("GET", CODEX_SKILLS, caller="client.nervis").json()
        for_models = relay.call("GET", FOR_MODELS, caller="client.clarvis").json()

    assert ("graphify", "personal", True, True, True, False) in rows(on.json())
    assert writes(rig, written) == [(graphify, True)]
    assert ("graphify", True) in [(entry["name"], entry["enabled"])
                                  for entry in codex_view["skills"]]
    # On for Codex, still off for the other models.
    assert [entry["id"] for entry in for_models["skills"]] == ["nervis/nervis-notes"]
    assert switched(rig) == [
        {"name": "graphify", "source": "personal", "engine": "codex", "enabled": True}]


def test_a_codex_switch_codex_doesnt_take_is_refused_and_the_skill_stays(tmp_path: Path) -> None:
    rig = skills_rig(tmp_path, personal=False, scenario={"skills_write_ignored": True})
    with serving(rig) as relay:
        assert relay.ready()["state"] == "signed_in"
        imagegen = path_in(relay.call("GET", SKILLS, caller="client.nervis").json(), "imagegen")
        error = refused(relay.call("POST", SKILLS, caller="admin.launcher",
                                   body={"path": imagegen, "engine": "codex", "enabled": False}),
                        409, "SKILL_NOT_CHANGED", skill="imagegen", reason="not_written")
        after = relay.call("GET", SKILLS, caller="client.nervis").json()
    assert error["message"] == said("POST", SKILLS, "Codex didn't take it")
    assert ("imagegen", "built_in", True, True, False, False) in rows(after)
    assert switched(rig) == []


def test_while_codex_cant_run_the_list_and_the_other_models_still_answer(tmp_path: Path) -> None:
    rig = codexless_rig(tmp_path)
    with serving(rig) as relay:
        view = relay.call("GET", SKILLS, caller="client.nervis").json()
        graphify = path_in(view, "graphify")
        on = relay.call("POST", SKILLS, caller="admin.launcher",
                        body={"path": graphify, "engine": "models", "enabled": True})
        codex = relay.call("POST", SKILLS, caller="admin.launcher",
                           body={"path": graphify, "engine": "codex", "enabled": True})
        for_models = relay.call("GET", FOR_MODELS, caller="client.clarvis").json()
        read = relay.call("GET", READ, caller="client.clarvis",
                          params={"skill": "personal/graphify"})

    assert view["codex"] == {"listed": False, "problem": UNREADABLE}
    assert rows(view) == [("nervis-notes", "nervis", False, False, True, True),
                          ("graphify", "personal", False, False, True, False)]
    assert on.status_code == 200, on.text
    assert ("graphify", "personal", False, False, True, True) in rows(on.json())
    error = refused(codex, 503, "CODEX_RUNTIME_UNAVAILABLE")
    assert error["message"] == said("POST", SKILLS, "Codex isn't running")
    assert [entry["id"] for entry in for_models["skills"]] == ["nervis/nervis-notes",
                                                               "personal/graphify"]
    assert read.status_code == 200, read.text
    assert read.json()["text"] == (personal_folder(tmp_path) / "graphify" / "SKILL.md").read_text()
    alone = example("GET", SKILLS, "Codex isn't running: RAVIS's reading alone")["response"]
    assert alone["body"]["codex"] == {"listed": False, "problem": UNREADABLE}
    assert keys(alone["body"]) == keys(view)
    assert switched(rig) == [
        {"name": "graphify", "source": "personal", "engine": "models", "enabled": True}]


# ── The other models' reads ──────────────────────────────────────────────────


def test_a_read_serves_only_inside_a_switched_on_skill_and_is_logged_without_its_text(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    rig = codexless_rig(tmp_path)
    notes = nervis_folder(tmp_path) / "nervis-notes"
    (notes / "references").mkdir()
    (notes / "references" / "guide.md").write_text("THE-GUIDE-SAYS-THIS\n", encoding="utf-8")
    (notes / ".secret").write_text("hidden\n", encoding="utf-8")
    (notes / "over-the-cap.md").write_bytes(b"x" * (64 * 1024 + 1))
    (notes / "picture.png").write_bytes(b"\xff\xfe\x00")
    (notes / "borrowed.md").symlink_to(personal_folder(tmp_path) / "graphify" / "SKILL.md")
    caplog.set_level(logging.INFO, logger="ravis")
    files = ("../graphify/SKILL.md", "/etc/hosts", "borrowed.md", ".secret", "over-the-cap.md",
             "picture.png", "missing.md", "references")
    strangers = ("personal/graphify", "nervis/nowhere", "", "nervis/../personal/graphify")

    with serving(rig) as relay:
        def read(file: str | None = None, skill: str = "nervis/nervis-notes",
                 caller: str = "client.clarvis") -> httpx.Response:
            params = {"skill": skill, **({} if file is None else {"file": file})}
            return relay.call("GET", READ, caller=caller, params=params)

        whole, guide = read(), read("references/guide.md", caller="client.nervis")
        answers = {file: read(file) for file in files}
        unserved = {skill: read(skill=skill) for skill in strangers}

    text = (notes / "SKILL.md").read_text(encoding="utf-8")
    assert whole.status_code == 200, whole.text
    assert whole.json() == {"skill": "nervis/nervis-notes", "name": "nervis-notes",
                            "file": "SKILL.md", "bytes": len(text.encode("utf-8")), "text": text}
    served_example = example("GET", READ, "a skill's SKILL.md")["response"]["body"]
    assert keys(whole.json()) == keys(served_example)
    assert guide.json()["text"] == "THE-GUIDE-SAYS-THIS\n"
    assert guide.json()["file"] == "references/guide.md"
    reasons = {"../graphify/SKILL.md": "outside_skill", "/etc/hosts": "outside_skill",
               "borrowed.md": "outside_skill", ".secret": "hidden",
               "over-the-cap.md": "too_large", "picture.png": "not_text"}
    for file, reason in reasons.items():
        error = refused(answers[file], 422, "SKILL_FILE_REFUSED", reason=reason)
        assert error["message"] == SKILL_FILE_REFUSALS[reason], file
    for name, reason in (("a path out of the skill", "outside_skill"), ("a hidden file", "hidden"),
                         ("a file over the cap", "too_large"),
                         ("a file that isn't text", "not_text")):
        assert said("GET", READ, name) == SKILL_FILE_REFUSALS[reason]
    for file in ("missing.md", "references"):
        error = refused(answers[file], 404, "SKILL_FILE_NOT_FOUND")
        assert error["message"] == said("GET", READ, "no such file"), file
    for skill, response in unserved.items():
        refused(response, 404, "SKILL_NOT_FOUND")
    logged = [record.getMessage() for record in caplog.records]
    assert ("skills: nervis read 'references/guide.md' of the skill nervis/nervis-notes "
            "(20 characters)" in logged)
    assert ("skills: clarvis was refused '.secret' of the skill nervis/nervis-notes (hidden)"
            in logged)
    assert not any("THE-GUIDE-SAYS-THIS" in line or "Turn any input" in line for line in logged)
