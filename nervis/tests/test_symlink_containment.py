"""A symlink where NERVIS is about to write, which used to be followed silently.

From the external audit. `resolve_in_workspace` has always resolved before
comparing, so a path a *caller* supplies cannot leave the tree. The paths NERVIS
builds for itself were never checked: a fixed filename, a directory entry's own
name, an id that had already passed a character test. None of those says
anything about what is *at* the path, and `write_text`, `write_bytes` and
`mkdir` all follow a symlink without a word.

It needs no hostile user to be reachable. The workspace takes files from a NAS
mount and an import directory, and a symlink arriving that way is an ordinary
thing for a backup or sync tool to have made.

A link pointing at another place *inside* the workspace is deliberately allowed:
it resolves to somewhere still contained, and writing through it is what the
person who made it asked for.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nervis import documents, handoff
from nervis.workspace import OutsideWorkspaceError, still_inside

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need a privilege this test cannot assume"
)


def test_a_link_out_of_the_tree_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (root / "escape").symlink_to(tmp_path / "elsewhere")

    with pytest.raises(OutsideWorkspaceError):
        still_inside(root, root / "escape" / "stolen.txt")


def test_a_link_inside_the_tree_is_allowed(tmp_path: Path) -> None:
    """The half that keeps this a containment check rather than a ban on links.
    Somewhere still inside the workspace is somewhere chat may write.

    **Both shapes, because only one of them was covered.** A probe that made the
    guard refuse every symlink outright failed nothing — the original case put
    the link on a *parent* directory, so the final component was an ordinary
    name and `is_symlink()` on it was false. The link-as-final-component case is
    the one a blanket ban would break, and it is the one that was missing.
    """
    root = tmp_path / "workspace"
    (root / "real").mkdir(parents=True)
    (root / "real" / "note.txt").write_text("hello", encoding="utf-8")

    # A linked directory on the way to the file.
    (root / "shortcut").symlink_to(root / "real")
    assert still_inside(root, root / "shortcut" / "note.txt")

    # And the file itself being the link.
    (root / "alias.txt").symlink_to(root / "real" / "note.txt")
    assert still_inside(root, root / "alias.txt")


def test_the_handover_file_is_not_written_through_a_link(tmp_path: Path) -> None:
    """`handoff.py` writes one fixed filename. The name cannot traverse; what is
    at it can.

    The check is spelled out inside that module rather than imported, because
    §6.7 keeps it free of every `nervis` import — see the note there.
    """
    root = tmp_path / "handover"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("untouched", encoding="utf-8")
    (root / handoff.TASK_FILE).symlink_to(outside)

    with pytest.raises(ValueError, match="outside"):
        handoff.write(root, "please look at the failing test", conversation="c1")

    assert outside.read_text(encoding="utf-8") == "untouched", (
        "the handover was written straight through the link"
    )


def test_a_conversation_s_attachments_cannot_be_moved_out(tmp_path: Path) -> None:
    """**The widest of the three.** `mkdir(exist_ok=True)` on a symlinked
    directory succeeds without a word, and every attachment written or read
    afterwards goes wherever it points — one link relocates a whole
    conversation's files."""
    root = tmp_path / "workspace"
    (root / documents.ATTACHMENTS).mkdir(parents=True)
    (tmp_path / "exfil").mkdir()
    (root / documents.ATTACHMENTS / "c1").symlink_to(tmp_path / "exfil")

    with pytest.raises(OutsideWorkspaceError):
        documents.attachment_dir(root, "c1")


def test_an_ordinary_attachment_directory_still_works(tmp_path: Path) -> None:
    """The guard on the guard: refusing everything would pass every test above."""
    root = tmp_path / "workspace"
    root.mkdir()

    place = documents.attachment_dir(root, "c1")

    assert place is not None and place.is_dir()
    assert place.name == "c1"
