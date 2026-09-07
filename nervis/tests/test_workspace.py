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

from nervis.documents import (
    MAX_CHARACTERS,
    MAX_UPLOAD_BYTES,
    TYPE_WORDS,
    attachment_dir,
    forget_attachments,
    list_files,
    newest_readable,
    prune_attachments,
    read_document,
    store_upload,
)
from nervis.pdf import render
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
    typo and a typo gets reported as a security boundary.

    The unreadable one is an archive rather than a picture: a picture is now a
    third kind of reading — it is looked at rather than read — and the file
    chat can do nothing at all with is one it can neither decode nor see."""
    (tmp_path / "bundle.zip").write_bytes(b"PK\x03\x04")
    (tmp_path / "folder").mkdir()

    with pytest.raises(OutsideWorkspaceError):
        read_document(tmp_path, "../elsewhere.txt")
    with pytest.raises(FileNotFoundError):
        read_document(tmp_path, "missing.txt")
    with pytest.raises(ValueError):
        read_document(tmp_path, "bundle.zip")
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


# ── Putting one there, where the name is the untrusted part ────────────────

def test_an_upload_keeps_only_the_base_name(tmp_path: Path) -> None:
    """The layer that holds when the transport does not. An HTTP client
    normalises a climbing path away before it is sent, so this is the check that
    survives a caller speaking the wire directly."""
    root = tmp_path / "workspace"
    root.mkdir()

    for hostile in ("../../escaped.txt", "/etc/passwd", "reports/2026/q3.txt"):
        stored = store_upload(root, hostile, b"payload")
        assert "/" not in stored.shown
        assert (root / stored.shown).read_bytes() == b"payload"

    assert not (tmp_path / "escaped.txt").exists()
    assert sorted(p.name for p in root.iterdir()) == ["escaped.txt", "passwd", "q3.txt"]


def test_an_upload_that_names_nothing_is_refused(tmp_path: Path) -> None:
    """`.` and `..` have a base name that is not a filename. Left alone they
    resolve to a directory, and `write_bytes` on one raises something the caller
    cannot tell from a disk failure."""
    for empty in ("", "   ", "/", ".", ".."):
        with pytest.raises(OutsideWorkspaceError):
            store_upload(tmp_path, empty, b"payload")


def test_an_upload_over_the_cap_writes_nothing(tmp_path: Path) -> None:
    """Refused before the write, not truncated at it: a half-written file under
    a name somebody recognises is worse than no file."""
    with pytest.raises(ValueError):
        store_upload(tmp_path, "big.txt", b"x" * (MAX_UPLOAD_BYTES + 1))

    assert not (tmp_path / "big.txt").exists()


def test_the_listing_is_newest_first_and_flat(tmp_path: Path) -> None:
    """Newest first because the file somebody just attached is the one they are
    about to ask about. Flat because the upload path cannot create a tree, and a
    recursive listing would describe a shape this feature never makes."""
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "old.txt").write_text("a", encoding="utf-8")
    os.utime(root / "old.txt", (1_700_000_000, 1_700_000_000))
    (root / "new.txt").write_text("b", encoding="utf-8")
    (root / "nested").mkdir()
    (root / "nested" / "hidden.txt").write_text("c", encoding="utf-8")

    listed = list_files(root)

    assert [item.name for item in listed] == ["new.txt", "old.txt"]
    assert all(item.readable for item in listed)


def test_a_workspace_that_is_not_there_lists_empty_rather_than_raising(tmp_path: Path) -> None:
    """A misconfigured path is a setting to fix, not a screen that fails to
    draw. The dashboard shows an empty workspace and the reason lives with the
    setting."""
    assert list_files(tmp_path / "nowhere") == []


# ── PDFs, which is what people actually attach ─────────────────────────────

def test_a_pdf_is_read_by_extracting_its_text(tmp_path: Path) -> None:
    """The round trip that matters: NERVIS's own writer produces a file its own
    reader answers from. A renderer and a reader that disagree about the format
    would both pass their own tests."""
    (tmp_path / "report.pdf").write_bytes(
        render("Report", "# Q3 revenue\n\nRevenue fell **12%** against Q2.").data
    )

    document = read_document(tmp_path, "report.pdf")

    assert "Q3 revenue" in document.text
    assert "Revenue fell" in document.text
    assert document.extracted is True


def test_the_reading_says_a_pdf_lost_its_layout(tmp_path: Path) -> None:
    """Extraction returns prose with the geometry gone, so a table arrives as
    loose runs of numbers. A model told nothing about that reads a mangled table
    as a tidy one and answers confidently from the wrong column."""
    (tmp_path / "report.pdf").write_bytes(render("Report", "some text").data)

    reading = read_document(tmp_path, "report.pdf").as_reading()

    assert "extracted from a PDF" in reading
    assert "layout is gone" in reading


def test_a_scanned_pdf_says_so_rather_than_reading_as_empty(tmp_path: Path) -> None:
    """The failure worth naming. A photograph of a page extracts to nothing, and
    an empty reading presented as a successful one is a model answering "the
    document does not mention that" to every question about it."""
    # **Not this renderer's output any more.** An exported page carries a
    # footer, so "render nothing" now produces a PDF with a page number on it —
    # which is text, and a fixture that quietly stopped testing what it claimed
    # would have passed forever. A genuinely blank page instead, built by the
    # same library that reads it: one valid page with no text at all, which is
    # what a photograph of a page looks like to a text extractor.
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with (tmp_path / "scan.pdf").open("wb") as handle:
        writer.write(handle)

    with pytest.raises(ValueError, match="OCR"):
        read_document(tmp_path, "scan.pdf")


def test_a_file_that_is_not_really_a_pdf_is_refused_not_raised_through(
    tmp_path: Path,
) -> None:
    """A malformed PDF makes the parser raise from several layers down. Any one
    of them escaping turns a bad attachment into a 500."""
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.4 this is not a pdf at all")

    with pytest.raises(ValueError):
        read_document(tmp_path, "broken.pdf")


def test_a_pdf_counts_as_readable_in_the_listing(tmp_path: Path) -> None:
    """The screen's ○ marks what chat cannot read. Leaving it on PDFs after
    teaching chat to read them tells people not to try — and the same is now
    true of a picture, which a model with vision answers about directly."""
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "report.pdf").write_bytes(render("Report", "text").data)
    (root / "photo.png").write_bytes(b"\x89PNG")
    (root / "bundle.zip").write_bytes(b"PK\x03\x04")

    marked = {item.name: item.readable for item in list_files(root)}

    assert marked == {"report.pdf": True, "photo.png": True, "bundle.zip": False}


# ── Attachments belong to a conversation, not to the machine ───────────────

def test_two_conversations_get_two_directories(tmp_path: Path) -> None:
    """The whole of what "attachments do not persist between sessions" means: a
    new conversation is a new directory, and a new directory is empty."""
    first = attachment_dir(tmp_path, "cv_aaaa")
    second = attachment_dir(tmp_path, "cv_bbbb")
    assert first is not None and second is not None
    store_upload(first, "notes.md", b"mine")

    assert first != second
    assert [item.name for item in list_files(first)] == ["notes.md"]
    assert list_files(second) == []


def test_a_conversation_id_that_could_be_a_path_gets_no_directory(tmp_path: Path) -> None:
    """NERVIS mints these, and it reads them back off a request body. A value
    that becomes a path component is a path component whoever wrote it."""
    for hostile in ("", "   ", ".", "..", "../escape", "a/b", "/etc"):
        assert attachment_dir(tmp_path, hostile) is None

    assert not (tmp_path.parent / "escape").exists()


def test_forgetting_a_conversation_removes_its_files_and_only_its_files(
    tmp_path: Path,
) -> None:
    """Delete should mean delete now. It should also mean *this* conversation —
    a cleanup that reaches into the next one is worse than none."""
    mine = attachment_dir(tmp_path, "cv_aaaa")
    theirs = attachment_dir(tmp_path, "cv_bbbb")
    assert mine is not None and theirs is not None
    store_upload(mine, "a.md", b"x")
    store_upload(mine, "b.md", b"y")
    store_upload(theirs, "c.md", b"z")

    assert forget_attachments(tmp_path, "cv_aaaa") == 2

    assert not mine.exists()
    assert [item.name for item in list_files(theirs)] == ["c.md"]


def test_orphaned_attachments_expire_and_recent_ones_do_not(tmp_path: Path) -> None:
    """A browser that cleared its local history leaves directories nothing points
    at. Without a sweep the disk grows forever; with too eager a sweep, a
    conversation somebody comes back to has lost its file."""
    old = attachment_dir(tmp_path, "cv_old")
    recent = attachment_dir(tmp_path, "cv_new")
    assert old is not None and recent is not None
    store_upload(old, "stale.md", b"x")
    store_upload(recent, "fresh.md", b"y")
    ancient = 1_700_000_000.0
    os.utime(old / "stale.md", (ancient, ancient))
    os.utime(old, (ancient, ancient))

    assert prune_attachments(tmp_path, now=ancient + 20 * 86_400) == 1

    assert not old.exists()
    assert [item.name for item in list_files(recent)] == ["fresh.md"]


def test_the_newest_readable_file_is_what_this_pdf_means(tmp_path: Path) -> None:
    """A person says "read this pdf" and never types the filename — that is what
    the attach gesture was for. The directory's own timestamps answer it, and a
    type word narrows it so "the pdf" does not open a newer `.md`."""
    place = attachment_dir(tmp_path, "cv_aaaa")
    assert place is not None
    store_upload(place, "report.pdf", render("Report", "text").data)
    store_upload(place, "later.md", b"newer")
    store_upload(place, "bundle.zip", b"PK\x03\x04")
    os.utime(place / "later.md", (2_000_000_000, 2_000_000_000))

    assert newest_readable(place) == "later.md"
    assert newest_readable(place, TYPE_WORDS["pdf"]) == "report.pdf"
    assert newest_readable(place, TYPE_WORDS["csv"]) is None


def test_nothing_readable_is_none_rather_than_a_file_chat_cannot_open(
    tmp_path: Path,
) -> None:
    """The falsifier. Returning the newest file regardless would hand "read this"
    a file nothing can open and produce a refusal about one the person never
    named. An archive, since a picture is readable now."""
    place = attachment_dir(tmp_path, "cv_aaaa")
    assert place is not None
    store_upload(place, "bundle.zip", b"PK\x03\x04")

    assert newest_readable(place) is None


def test_a_type_word_narrows_but_does_not_veto(tmp_path: Path) -> None:
    """"The pdf" should not open a newer `.md` when a PDF is attached. When none
    is, the person calling their attachment a pdf is being loose rather than
    wrong — and giving up tells them nothing is attached about a file they are
    looking at."""
    place = attachment_dir(tmp_path, "cv_aaaa")
    assert place is not None
    store_upload(place, "notes.md", b"only file here")

    assert newest_readable(place, TYPE_WORDS["pdf"]) is None
    assert newest_readable(place) == "notes.md"
