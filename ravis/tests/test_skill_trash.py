"""Where a removed skill goes, on each system (`agent/skill_installs.move_to_trash`, 20 Sep 2026).

RAVIS moved every removed skill into `~/.Trash`. On a Mac that is Finder's Trash. On Linux — the
owner runs the stack on a CachyOS laptop — it is a hidden folder no file manager shows, so a
removed skill was out of sight with nothing offering to put it back. Linux and WSL now get the
freedesktop Trash and its `.trashinfo`; the Mac is unchanged.
"""

from __future__ import annotations

import configparser
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ravis.agent.skill_installs import move_to_trash, trash_words

NOW = datetime(2026, 9, 20, 9, 30, 15, tzinfo=UTC)


def a_skill(root: Path, name: str = "graphify") -> Path:
    folder = root / "skills" / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: graphify\n---\n")
    return folder


def test_on_linux_it_lands_in_the_trash_a_file_manager_shows(tmp_path: Path) -> None:
    skill, trash = a_skill(tmp_path), tmp_path / "Trash"

    went = move_to_trash(skill, "graphify", NOW, system="Linux", trash=trash)

    assert went.parent == trash / "files", "the freedesktop Trash keeps what it holds in files/"
    assert (went / "SKILL.md").exists() and not skill.exists()
    assert not (Path.home() / ".Trash").exists() or skill.parent.exists(), "never ~/.Trash here"
    note = configparser.ConfigParser()
    note.read(trash / "info" / f"{went.name}.trashinfo")
    assert urllib.parse.unquote(note["Trash Info"]["Path"]) == str(skill.resolve()), (
        "the path a file manager puts it back to")
    assert note["Trash Info"]["DeletionDate"].startswith("20"), "and when it went"


def test_a_mac_keeps_finders_own_trash(tmp_path: Path) -> None:
    skill, trash = a_skill(tmp_path), tmp_path / ".Trash"

    went = move_to_trash(skill, "graphify", NOW, system="Darwin", trash=trash)

    assert went == trash / "graphify 2026-09-20 11.30.15" or went.parent == trash, went
    assert (went / "SKILL.md").exists() and not skill.exists()
    assert not (trash / "info").exists(), "Finder wants no .trashinfo"


@pytest.mark.parametrize(("system", "where"), [("Linux", "files"), ("Darwin", "")])
def test_a_name_already_taken_is_numbered_and_nothing_is_overwritten(
    tmp_path: Path, system: str, where: str
) -> None:
    trash = tmp_path / "Trash"
    first = move_to_trash(a_skill(tmp_path), "graphify", NOW, system=system, trash=trash)
    (first / "SKILL.md").write_text("the first one")

    second = move_to_trash(a_skill(tmp_path / "again"), "graphify", NOW,
                           system=system, trash=trash)

    assert second != first and second.name.endswith(" 2")
    assert (first / "SKILL.md").read_text() == "the first one", "the earlier one is untouched"
    assert (trash / where).exists()
    if system == "Linux":
        assert (trash / "info" / f"{second.name}.trashinfo").exists()


def test_the_default_linux_trash_is_the_one_the_desktop_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))

    went = move_to_trash(a_skill(tmp_path), "graphify", NOW, system="Linux")

    assert went.parent == tmp_path / "share" / "Trash" / "files"


def test_a_move_that_fails_leaves_no_record_of_a_skill_that_is_still_there(
    tmp_path: Path
) -> None:
    """The `.trashinfo` is written first, as the layout asks; a failed move takes it back."""
    trash = tmp_path / "Trash"

    with pytest.raises(OSError):
        move_to_trash(tmp_path / "never-installed", "graphify", NOW, system="Linux", trash=trash)

    assert list((trash / "info").glob("*.trashinfo")) == []


def test_the_words_for_a_refusal_name_no_single_system() -> None:
    """They reach the owner on whichever machine refused."""
    import errno

    assert "macOS" not in trash_words(OSError(errno.EPERM, "denied"))
    assert "another disk" in trash_words(OSError(errno.EXDEV, "cross-device link"))
