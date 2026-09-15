"""RAVIS's own reading of skill folders, for models that aren't Codex (`agent/skill_catalog.py`).

What RAVIS is held to, with no Codex running:

- **front matter**: a skill's name and description are read from its YAML, quoted or folded, each
  on one line, and a long description is cut; hidden folders and a skill's own sub-folders aren't
  searched, and nothing deeper than four folders is;
- **a malformed skill is listed with its problem in plain words**, and never stops the rest;
- **links are followed only inside their root**: one leading out to a skill is listed with that
  problem and its target never read, and a skill's `SKILL.md` must lie in its own folder;
- **the size cap**: a `SKILL.md` over 64 KB isn't read, and no file over it is served;
- **a file a caller names** can't leave its skill's folder or name a hidden file, and must be UTF-8
  text in a regular file;
- **a folder that isn't there lists nothing**, and a setting that can't be used says why;
- **lined up with Codex's list by real path**, Codex's own skills Codex's only;
- **the other models' switches**: NERVIS's skills on and personal ones off, one added later too,
  kept apart from Codex's; a malformed or switched-off skill is never served;
- **a read is logged by name and size**, never with what the file says;
- **RAVIS's own earlier README is brought up to date**, and the owner's is never replaced.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pytest

from ravis.agent.skill_catalog import (
    MOST_FILE_BYTES,
    ModelSkills,
    SkillFileMissingError,
    SkillFileRefusedError,
    board,
    listed,
    read_catalog,
    read_text,
)
from ravis.agent.skills import (
    CODEX,
    DESCRIPTION_LIMIT,
    EARLIER_READMES,
    MODELS,
    README,
    Skill,
    SkillChoices,
    prepare_folder,
)
from ravis.codex.refusals import CodexRefusalError
from ravis.config import Settings
from ravis.storage.database import prepare_database


def real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def folders_of(tmp_path: Path) -> tuple[Path, Path]:
    """NERVIS's skills folder and the personal one, both inside the test's own folder."""
    base = real(tmp_path)
    return (base / "coding" / "NERVIS workspace" / "clarvis" / "skills",
            base / "home" / ".agents" / "skills")


def settings_for(tmp_path: Path, **more: Any) -> Settings:
    nervis, personal = folders_of(tmp_path)
    values: dict[str, Any] = {
        "agent_allowed_roots": [str(real(tmp_path) / "coding")],
        "codex_skills_folder": str(nervis),
        "skills_personal_folder": str(personal),
        **more,
    }
    return Settings(database_path=":memory:", _env_file=None, **values)  # type: ignore[call-arg]


def skill(folder: Path, name: str, description: str = "", *, text: str | None = None) -> Path:
    """A skill on disk: a folder holding a SKILL.md, with front matter unless `text` is given."""
    path = folder / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    said = description or f"What {name} does."
    path.write_text(text if text is not None else
                    f"---\nname: {name}\ndescription: {said}\n---\n\n# {name}\n", encoding="utf-8")
    return path


# ── Reading a folder ─────────────────────────────────────────────────────────


def test_a_skill_is_read_from_its_front_matter_each_on_one_line(tmp_path: Path) -> None:
    nervis, _ = folders_of(tmp_path)
    notes = skill(nervis, "nervis-notes", text=(
        "\ufeff---\nname: \"nervis-notes\"\ndescription: >-\n  How NERVIS tasks\n"
        "  keep their notes.\nmetadata:\n  owner: nervis\n---\n\n# Notes\n"))
    skill(nervis, "wordy", "word " * 100)
    skill(nervis / "group", "deep", "One folder down.")
    skill(nervis / "a" / "b" / "c", "d", "As deep as a skill may lie.")
    skill(nervis / "a" / "b" / "c" / "x", "e", "One folder too deep.")
    skill(nervis / "nervis-notes" / "examples", "inner", "A skill inside a skill.")
    skill(nervis / ".drafts", "hidden-draft", "In a hidden folder.")
    (nervis / "README.md").write_text("# NERVIS skills\n", encoding="utf-8")

    [nervis_read, personal_read] = read_catalog(settings_for(tmp_path))

    assert (nervis_read.source, nervis_read.path, nervis_read.problem) == (
        "nervis", str(nervis), None)
    assert (personal_read.source, personal_read.problem, personal_read.skills) == (
        "personal", None, ())
    skills = {read.id: read for read in nervis_read.skills}
    assert sorted(skills) == ["nervis/a/b/c/d", "nervis/group/deep", "nervis/nervis-notes",
                              "nervis/wordy"]
    read = skills["nervis/nervis-notes"]
    assert (read.source, read.name, read.description, read.problem) == (
        "nervis", "nervis-notes", "How NERVIS tasks keep their notes.", None)
    assert (read.path, read.folder) == (str(notes), str(notes.parent))
    wordy = skills["nervis/wordy"].description
    assert len(wordy) == DESCRIPTION_LIMIT and wordy.endswith("…") and "\n" not in wordy


MALFORMED = {
    "no-front-matter": ("# Just instructions\n",
                        "its SKILL.md doesn't start with front matter (a line of ---)"),
    "never-closed": ("---\nname: never-closed\ndescription: Open.\n",
                     "its SKILL.md's front matter has no closing line of ---"),
    "bad-yaml": ("---\nname: [unclosed\n---\n", "its SKILL.md's front matter isn't valid YAML"),
    "a-list": ("---\n- name\n- description\n---\n",
               "its SKILL.md's front matter isn't a list of names and values"),
    "no-name": ("---\ndescription: Nameless.\n---\n", "its SKILL.md's front matter has no name"),
    "no-description": ("---\nname: no-description\n---\n",
                       "its SKILL.md's front matter has no description"),
    "long-name": ("---\nname: " + "n" * 101 + "\ndescription: Too long a name.\n---\n",
                  "its name is longer than 100 characters"),
}


def test_a_malformed_skill_is_listed_with_its_problem_and_never_stops_the_rest(
    tmp_path: Path,
) -> None:
    nervis, _ = folders_of(tmp_path)
    for name, (text, _) in MALFORMED.items():
        skill(nervis, name, text=text)
    (nervis / "not-text").mkdir()
    (nervis / "not-text" / "SKILL.md").write_bytes(b"---\nname: \xff\xfe\n---\n")
    (nervis / "too-large").mkdir()
    (nervis / "too-large" / "SKILL.md").write_text(
        "---\nname: too-large\ndescription: Big.\n---\n" + "x" * MOST_FILE_BYTES, encoding="utf-8")
    (nervis / "a-folder" / "SKILL.md").mkdir(parents=True)
    skill(nervis, "fine", "Still listed.")

    [nervis_read, _] = read_catalog(settings_for(tmp_path))

    problems = {read.id.removeprefix("nervis/"): read.problem for read in nervis_read.skills}
    assert problems == {
        **{name: problem for name, (_, problem) in MALFORMED.items()},
        "not-text": "its SKILL.md isn't UTF-8 text",
        "too-large": "its SKILL.md is larger than 64 KB",
        "a-folder": "RAVIS can't read its SKILL.md: it isn't a file",
        "fine": None,
    }
    names = {read.id: read.name for read in nervis_read.skills}
    # A skill whose front matter gives no name is still named, by its folder.
    assert names["nervis/no-name"] == "no-name" and names["nervis/fine"] == "fine"


def test_a_link_is_followed_only_while_it_stays_inside_its_root(tmp_path: Path) -> None:
    nervis, personal = folders_of(tmp_path)
    outside = real(tmp_path) / "elsewhere"
    graphify = skill(personal, "graphify", "Turn any input into a knowledge graph.")
    skill(outside, "stranger", text="---\nname: read-through-a-link\ndescription: Never.\n---\n")
    skill(nervis, "real-one", "A NERVIS skill.")
    # Into the personal folder, which is outside NERVIS's: a personal skill stays personal.
    (nervis / "linked-personal").symlink_to(graphify.parent)
    (nervis / "linked-outside").symlink_to(outside / "stranger")
    # Out, to a folder with no skill in it: left out altogether.
    (nervis / "linked-elsewhere").symlink_to(outside)
    # Inside the root: followed, and the skill listed once, by its own folder's name.
    (nervis / "also-real-one").symlink_to(nervis / "real-one")
    # The root itself, and a folder above it: never followed.
    (nervis / "loop").symlink_to(nervis)
    (nervis / "up").symlink_to(nervis.parent)
    sneaky = nervis / "sneaky"
    sneaky.mkdir()
    (sneaky / "SKILL.md").symlink_to(outside / "stranger" / "SKILL.md")

    [nervis_read, personal_read] = read_catalog(settings_for(tmp_path))

    here = {read.id: read for read in nervis_read.skills}
    assert sorted(here) == ["nervis/linked-outside", "nervis/linked-personal", "nervis/real-one",
                            "nervis/sneaky"]
    assert here["nervis/real-one"].path == str(nervis / "real-one" / "SKILL.md")
    for name in ("linked-personal", "linked-outside"):
        link = here[f"nervis/{name}"]
        assert link.problem == (
            f"it is a link to a folder outside {nervis}, so RAVIS doesn't read it")
        assert (link.name, link.description, link.folder) == (name, "", "")
    assert here["nervis/sneaky"].problem == (
        "its SKILL.md is a link to a file outside the skill's folder")
    assert "read-through-a-link" not in {
        read.name for folder in (nervis_read, personal_read) for read in folder.skills}
    [graphify_read] = personal_read.skills
    assert (graphify_read.id, graphify_read.path, graphify_read.problem) == (
        "personal/graphify", str(graphify), None)


def test_a_folder_not_there_lists_nothing_and_a_setting_that_cant_be_used_says_why(
    tmp_path: Path,
) -> None:
    assert [(folder.source, folder.problem, folder.skills)
            for folder in read_catalog(settings_for(tmp_path))] == [
        ("nervis", None, ()), ("personal", None, ())]
    # NERVIS's folder outside every folder Codex tasks may use: its skills aren't read at all.
    stray = real(tmp_path) / "stray" / "skills"
    skill(stray, "stray-skill", "Never listed.")
    refused = read_catalog(settings_for(tmp_path, codex_skills_folder=str(stray),
                                        skills_personal_folder="relative/skills"))
    assert [(folder.problem, folder.skills) for folder in refused] == [
        (f"the skills folder {stray} isn't inside a folder Codex tasks may use", ()),
        ("the personal skills folder 'relative/skills' isn't an absolute path", ()),
    ]
    a_file = real(tmp_path) / "a-file"
    a_file.write_text("not a folder\n", encoding="utf-8")
    [_, personal] = read_catalog(settings_for(tmp_path, skills_personal_folder=str(a_file)))
    assert (personal.problem, personal.skills) == (f"{a_file} isn't a folder", ())


# ── A skill's files ──────────────────────────────────────────────────────────


def test_a_file_is_served_only_from_inside_its_own_skill_and_only_as_text(tmp_path: Path) -> None:
    nervis, _ = folders_of(tmp_path)
    notes = skill(nervis, "nervis-notes", "How NERVIS tasks keep their notes.").parent
    other = skill(nervis, "other", "Another skill.")
    (notes / "references").mkdir()
    (notes / "references" / "guide.md").write_text("Keep notes in NOTES.md.\n", encoding="utf-8")
    (notes / ".secret").write_text("hidden\n", encoding="utf-8")
    (notes / "innocent.md").symlink_to(notes / ".secret")
    (notes / "borrowed.md").symlink_to(other)
    (notes / "exactly-the-cap.md").write_bytes(b"x" * MOST_FILE_BYTES)
    (notes / "over-the-cap.md").write_bytes(b"x" * (MOST_FILE_BYTES + 1))
    (notes / "picture.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
    os.mkfifo(notes / "pipe")

    assert read_text(notes, "references/guide.md") == (
        "Keep notes in NOTES.md.\n", notes / "references" / "guide.md")
    assert len(read_text(notes, "exactly-the-cap.md")[0]) == MOST_FILE_BYTES

    def refused(relative: str) -> str:
        with pytest.raises(SkillFileRefusedError) as caught:
            read_text(notes, relative)
        return caught.value.reason

    for outside in ("../other/SKILL.md", "/etc/hosts", "references/../SKILL.md", "", "./SKILL.md",
                    "references//guide.md", "references\\guide.md", "SKILL.md\n", "a/" * 300,
                    "borrowed.md"):
        assert refused(outside) == "outside_skill", outside
    assert refused(".secret") == "hidden"
    assert refused("innocent.md") == "hidden"
    assert refused("over-the-cap.md") == "too_large"
    assert refused("picture.png") == "not_text"
    for missing, why in (("nowhere.md", "there is no such file"),
                         ("references", "it isn't a file"), ("pipe", "it isn't a file")):
        with pytest.raises(SkillFileMissingError) as caught:
            read_text(notes, missing)
        assert caught.value.why == why, missing


# ── Lined up with Codex ──────────────────────────────────────────────────────


def test_codex_s_list_and_ravis_s_reading_line_up_by_real_path(tmp_path: Path) -> None:
    nervis, personal = folders_of(tmp_path)
    notes = skill(nervis, "nervis-notes", "How NERVIS tasks keep their notes.")
    skill(nervis, "half-written", text="---\nname: half-written\n---\n")
    graphify = skill(personal, "graphify", "Turn any input into a knowledge graph.")
    alias = real(tmp_path) / "alias"
    alias.symlink_to(personal)
    folders = read_catalog(settings_for(tmp_path))
    codex = [
        Skill(str(notes), "nervis-notes", "Codex's words for it.", "nervis", True),
        # Codex found the personal folder through a link: the same skill, by its real path.
        Skill(str(alias / "graphify" / "SKILL.md"), "graphify", "", "personal", False),
        Skill("/h/.codex/skills/.system/imagegen/SKILL.md", "imagegen", "Generate images.",
              "built_in", True),
        Skill("/etc/codex/skills/site-wide/SKILL.md", "site-wide", "For this Mac.", "personal",
              False),
        Skill("/opt/codex/skills/site-wide/SKILL.md", "site-wide", "Another.", "personal", True),
    ]
    served = {str(notes)}

    def rows(view: dict[str, Any]) -> list[tuple[Any, ...]]:
        return [(entry["id"], entry["source"], entry["codex"]["available"],
                 entry["codex"]["enabled"], entry["models"]["available"],
                 entry["models"]["enabled"]) for entry in view["skills"]]

    view = board(folders, listed(folders, codex), {"listed": True, "problem": None},
                 lambda read: read.path in served)

    assert rows(view) == [
        ("nervis/half-written", "nervis", False, False, False, False),
        ("nervis/nervis-notes", "nervis", True, True, True, True),
        ("personal/graphify", "personal", True, False, True, False),
        ("personal/site-wide", "personal", True, False, False, False),
        ("personal/site-wide~2", "personal", True, True, False, False),
        ("built_in/imagegen", "built_in", True, True, False, False),
    ]
    entries = {entry["id"]: entry for entry in view["skills"]}
    # RAVIS's own reading names the skill, and the path is the real one RAVIS read.
    assert entries["nervis/nervis-notes"]["description"] == "How NERVIS tasks keep their notes."
    assert entries["personal/graphify"]["path"] == str(graphify)
    assert entries["nervis/half-written"]["problem"] == (
        "its SKILL.md's front matter has no description")
    assert view["folders"] == {"nervis": {"path": str(nervis), "problem": None},
                               "personal": {"path": str(personal), "problem": None}}
    assert view["codex"] == {"listed": True, "problem": None}
    # While Codex can't say, RAVIS's reading is listed alone, with no Codex switch anywhere.
    alone = board(folders, listed(folders, None), {"listed": False, "problem": "not running"},
                  lambda read: read.path in served)
    assert rows(alone) == [
        ("nervis/half-written", "nervis", False, False, False, False),
        ("nervis/nervis-notes", "nervis", False, False, True, True),
        ("personal/graphify", "personal", False, False, True, False),
    ]


# ── The other models ─────────────────────────────────────────────────────────


async def test_the_other_models_get_nervis_s_skills_and_personal_ones_only_once_switched_on(
    tmp_path: Path,
) -> None:
    nervis, personal = folders_of(tmp_path)
    notes = skill(nervis, "nervis-notes", "How NERVIS tasks keep their notes.")
    skill(nervis, "half-written", text="---\nname: half-written\n---\n")
    graphify = skill(personal, "graphify", "Turn any input into a knowledge graph.")
    database = prepare_database(":memory:")
    models = ModelSkills(database, settings_for(tmp_path))

    async def switched_on() -> list[str]:
        return [read.id for read in await models.switched_on()]

    async def read_by_id(identifier: str) -> Any:
        return next(read for folder in await models.folders() for read in folder.skills
                    if read.id == identifier)

    assert await switched_on() == ["nervis/nervis-notes"]
    models.switch(await read_by_id("personal/graphify"), True)
    assert await switched_on() == ["nervis/nervis-notes", "personal/graphify"]
    # Added later: a personal skill starts off, and one in NERVIS's folder starts on.
    skill(personal, "later", "Added later.")
    skill(nervis, "nervis-later", "Added later to NERVIS's folder.")
    assert await switched_on() == ["nervis/nervis-later", "nervis/nervis-notes",
                                   "personal/graphify"]
    models.switch(await read_by_id("nervis/nervis-notes"), False)
    # A malformed skill is never served, even switched on.
    models.switch(await read_by_id("nervis/half-written"), True)
    assert await switched_on() == ["nervis/nervis-later", "personal/graphify"]
    # Kept for the other models alone: Codex's switches, by the same paths, are untouched.
    codex = SkillChoices(database, CODEX)
    assert (codex.get(str(notes)), codex.get(str(graphify))) == (None, None)
    assert SkillChoices(database, MODELS).get(str(notes)) is False
    with pytest.raises(ValueError, match="no engine called 'clarvis'"):
        SkillChoices(database, "clarvis")


async def test_a_read_serves_only_a_switched_on_skill_and_logs_names_never_text(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    nervis, personal = folders_of(tmp_path)
    notes = skill(nervis, "nervis-notes", "How NERVIS tasks keep their notes.").parent
    (notes / "guide.md").write_text("THE-GUIDE-SAYS-THIS\n", encoding="utf-8")
    (notes / "over-the-cap.md").write_bytes(b"x" * (MOST_FILE_BYTES + 1))
    skill(nervis, "half-written", text="---\nname: half-written\n---\n")
    skill(personal, "graphify", "Turn any input into a knowledge graph.")
    models = ModelSkills(prepare_database(":memory:"), settings_for(tmp_path))
    caplog.set_level(logging.INFO, logger="ravis")

    found, text = await models.read("nervis/nervis-notes", "guide.md", "clarvis")
    assert (found.id, found.name, text) == ("nervis/nervis-notes", "nervis-notes",
                                            "THE-GUIDE-SAYS-THIS\n")

    async def refusal(identifier: str, relative: str = "SKILL.md") -> tuple[Any, ...]:
        with pytest.raises(CodexRefusalError) as caught:
            await models.read(identifier, relative, "clarvis")
        return caught.value.code, caught.value.status, caught.value.details

    for unserved in ("personal/graphify", "nervis/half-written", "nervis/nowhere", "",
                     "nervis/../personal/graphify"):
        assert await refusal(unserved) == ("SKILL_NOT_FOUND", 404, {}), unserved
    assert await refusal("nervis/nervis-notes", "../half-written/SKILL.md") == (
        "SKILL_FILE_REFUSED", 422, {"reason": "outside_skill"})
    assert await refusal("nervis/nervis-notes", "over-the-cap.md") == (
        "SKILL_FILE_REFUSED", 422, {"reason": "too_large"})
    assert await refusal("nervis/nervis-notes", "missing.md") == ("SKILL_FILE_NOT_FOUND", 404, {})
    logged = [record.getMessage() for record in caplog.records]
    assert ("skills: clarvis read 'guide.md' of the skill nervis/nervis-notes (20 characters)"
            in logged)
    assert ("skills: clarvis was refused 'over-the-cap.md' of the skill nervis/nervis-notes "
            "(too_large)" in logged)
    assert not any("THE-GUIDE-SAYS-THIS" in line or "Turn any input" in line for line in logged)


# ── The folder's README ──────────────────────────────────────────────────────


def test_ravis_s_own_earlier_readme_is_brought_up_to_date_and_the_owner_s_never_is(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "skills"
    folder.mkdir()
    (folder / "README.md").write_text(EARLIER_READMES[0], encoding="utf-8")
    prepare_folder(folder)
    assert (folder / "README.md").read_text(encoding="utf-8") == README
    assert "NERVIS → Skills" in README and "RAVIS → Dashboard" not in README
    # One character the owner changed makes it theirs.
    (folder / "README.md").write_text(EARLIER_READMES[0] + "My own line.\n", encoding="utf-8")
    prepare_folder(folder)
    assert (folder / "README.md").read_text(encoding="utf-8").endswith("My own line.\n")
    # And a README that is a link is never written through, whatever it leads to.
    linked = tmp_path / "linked"
    linked.mkdir()
    (tmp_path / "earlier.md").write_text(EARLIER_READMES[0], encoding="utf-8")
    (linked / "README.md").symlink_to(tmp_path / "earlier.md")
    prepare_folder(linked)
    assert (tmp_path / "earlier.md").read_text(encoding="utf-8") == EARLIER_READMES[0]
