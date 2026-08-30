"""The boundary chat reads and writes inside.

Every case here is a way out of a directory that looks unremarkable as text,
because that is the only kind that matters: a path spelled `/etc/passwd` is
refused by any implementation, and a symlink named `notes` is refused by one
that resolves before it compares.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from nervis.documents import MAX_CHARACTERS, read_document
from nervis.workspace import OutsideWorkspaceError, resolve_in_workspace


def test_a_file_inside_resolves(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("hello", encoding="utf-8")

    resolved = resolve_in_workspace(tmp_path, "notes.md")

    assert resolved.path == (tmp_path / "notes.md").resolve()
    assert resolved.shown == "notes.md"


def test_a_nested_file_resolves(tmp_path: Path) -> None:
    (tmp_path / "reports").mkdir()

    resolved = resolve_in_workspace(tmp_path, "reports/q3.txt")

    assert resolved.shown == "reports/q3.txt"


def test_a_file_that_does_not_exist_yet_still_resolves(tmp_path: Path) -> None:
    """Writing a new file is a legitimate request. Refusing an absent path would
    make this a test of existence rather than of place, and the caller could no
    longer tell "outside the workspace" from "not there yet"."""
    resolved = resolve_in_workspace(tmp_path, "summary.pdf")

    assert resolved.path.parent == tmp_path.resolve()
    assert not resolved.path.exists()


def test_a_relative_climb_is_refused(tmp_path: Path) -> None:
    with pytest.raises(OutsideWorkspaceError):
        resolve_in_workspace(tmp_path, "../outside.txt")


def test_a_deep_climb_that_lands_back_inside_is_allowed(tmp_path: Path) -> None:
    """`a/../b` leaves and returns, and the resolved form is inside. Refusing on
    the *presence* of `..` rather than on where the path lands would reject a
    legitimate one and still miss a symlink."""
    (tmp_path / "a").mkdir()

    resolved = resolve_in_workspace(tmp_path, "a/../b.txt")

    assert resolved.shown == "b.txt"


def test_an_absolute_path_outside_is_refused(tmp_path: Path) -> None:
    with pytest.raises(OutsideWorkspaceError):
        resolve_in_workspace(tmp_path, "/etc/passwd")


def test_a_sibling_directory_sharing_a_prefix_is_refused(tmp_path: Path) -> None:
    """`/tmp/nervis-evil` starts with `/tmp/nervis` as text and is a different
    directory. A string prefix test passes this; a path comparison does not."""
    root = tmp_path / "nervis"
    root.mkdir()
    (tmp_path / "nervis-evil").mkdir()

    with pytest.raises(OutsideWorkspaceError):
        resolve_in_workspace(root, "../nervis-evil/secret.txt")


def test_a_symlink_pointing_out_is_refused(tmp_path: Path) -> None:
    """The case a textual check cannot see. `notes` is an unremarkable name and
    the path contains no `..` at all; only resolving it says where it goes."""
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not yours", encoding="utf-8")
    os.symlink(outside, root / "notes")

    with pytest.raises(OutsideWorkspaceError):
        resolve_in_workspace(root, "notes/secret.txt")


def test_a_symlink_staying_inside_is_allowed(tmp_path: Path) -> None:
    """The falsifier for the test above: refusing every symlink would pass it
    while breaking a directory somebody linked for their own convenience."""
    root = tmp_path / "workspace"
    (root / "real").mkdir(parents=True)
    (root / "real" / "notes.md").write_text("hello", encoding="utf-8")
    os.symlink(root / "real", root / "shortcut")

    resolved = resolve_in_workspace(root, "shortcut/notes.md")

    assert resolved.path.read_text(encoding="utf-8") == "hello"


def test_nothing_named_is_refused_rather_than_resolving_to_the_root(tmp_path: Path) -> None:
    """An empty name resolving to the directory itself would make "read the
    file" silently mean "read the workspace"."""
    for empty in ("", "   "):
        with pytest.raises(OutsideWorkspaceError):
            resolve_in_workspace(tmp_path, empty)


def test_what_is_shown_is_relative_never_absolute(tmp_path: Path) -> None:
    """The root may contain a username, and a chat reply is read aloud and
    written to a log. The person already knows where their workspace is."""
    (tmp_path / "notes.md").write_text("hello", encoding="utf-8")

    resolved = resolve_in_workspace(tmp_path, "notes.md")

    assert str(tmp_path) not in resolved.shown


# ── Reading one, once the boundary has said where ──────────────────────────

def test_a_text_file_is_read_and_shown_by_its_relative_name(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("the quarterly numbers", encoding="utf-8")

    document = read_document(tmp_path, "notes.md")

    assert document.text == "the quarterly numbers"
    assert document.shown == "notes.md"
    assert document.truncated is False


def test_truncation_is_said_rather_than_done_quietly(tmp_path: Path) -> None:
    """A summary of the first half of a document, presented as a summary of the
    document, is a wrong answer nobody can see. The reading names the cut."""
    (tmp_path / "long.txt").write_text("x" * (MAX_CHARACTERS + 500), encoding="utf-8")

    document = read_document(tmp_path, "long.txt")

    assert document.truncated is True
    assert len(document.text) == MAX_CHARACTERS
    assert document.characters == MAX_CHARACTERS + 500
    assert "Only the first" in document.as_reading()
    assert "say so if the answer depends on the rest" in document.as_reading()


def test_three_failures_stay_distinct(tmp_path: Path) -> None:
    """Outside, absent, and not-text send a person to three different places.
    Collapsing them into "cannot read that" is how a refusal gets reported as a
    typo and a typo gets reported as a security boundary."""
    (tmp_path / "picture.png").write_bytes(b"\x89PNG")
    (tmp_path / "folder").mkdir()

    with pytest.raises(OutsideWorkspaceError):
        read_document(tmp_path, "../elsewhere.txt")
    with pytest.raises(FileNotFoundError):
        read_document(tmp_path, "missing.txt")
    with pytest.raises(ValueError):
        read_document(tmp_path, "picture.png")
    with pytest.raises(OutsideWorkspaceError):
        read_document(tmp_path, "folder")


def test_a_file_with_one_bad_byte_is_still_answerable(tmp_path: Path) -> None:
    """Failing a whole read over an encoding detail would be the converter
    problem this module exists to avoid."""
    (tmp_path / "mixed.txt").write_bytes(b"before \xff after")

    assert "before" in read_document(tmp_path, "mixed.txt").text


def test_the_reading_never_carries_the_absolute_path(tmp_path: Path) -> None:
    """It goes into a prompt, a reply that is read aloud, and a log. The person
    already knows where their own workspace is."""
    (tmp_path / "notes.md").write_text("hello", encoding="utf-8")

    assert str(tmp_path) not in read_document(tmp_path, "notes.md").as_reading()
