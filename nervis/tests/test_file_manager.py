"""The workspace file manager: what it moves, and what it refuses to.

**The refusals are the subject.** Listing a directory and renaming a file are
the easy half and would pass whatever the boundary did; every test that matters
here is about a path that must not work — out of the workspace, into a
conversation's attachments, or over one of the rooms the rest of NERVIS resolves
files by name from.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from nervis.api.files import TRASH, TRASH_SECONDS, prune_trash
from nervis.app import create_app
from nervis.config import Settings

# This file sends the control token itself where a test needs it (`conftest.page_control_token`).
SENDS_NO_CONTROL_TOKEN = True


def an_api(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_path=str(tmp_path / "nervis.db"),
        workspace_path=str(tmp_path / "workspace"),
        _env_file=None,  # type: ignore[call-arg]
    )
    return TestClient(create_app(settings))


def control(client: TestClient) -> dict[str, str]:
    return {"x-nervis-control": client.app.state.control_token}  # type: ignore[attr-defined]


def a_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    for room in ("import", "library", "export", "clarvis"):
        (root / room).mkdir(parents=True)
    (root / "library" / "notes.md").write_text("kept", encoding="utf-8")
    (root / ".attachments" / "cv_ab12").mkdir(parents=True)
    (root / ".attachments" / "cv_ab12" / "handed-over.pdf").write_bytes(b"%PDF-1.4")
    return root


# ── Browsing ────────────────────────────────────────────────────────────────


def test_the_top_of_the_workspace_lists_its_rooms(tmp_path: Path) -> None:
    a_workspace(tmp_path)
    with an_api(tmp_path) as client:
        body = client.get("/api/v1/workspace/entries").json()

    # In the order they mean something — what arrives, what is kept, what is
    # produced, what the editor opens — rather than alphabetically, which would
    # lead with the room a person visits least.
    assert [row["name"] for row in body["items"]] == ["import", "library", "export", "clarvis"]
    assert all(row["kind"] == "folder" for row in body["items"])
    assert body["rooms"] == ["import", "library", "export", "clarvis"]


def test_a_conversation_s_attachments_are_not_the_manager_s_business(
    tmp_path: Path,
) -> None:
    """**Listed nowhere and writable nowhere.** An attachment moved out from
    under its conversation is a file that still exists and a reading that has
    silently changed — the worst shape a bug can take here, because nothing
    reports it."""
    a_workspace(tmp_path)
    with an_api(tmp_path) as client:
        top = client.get("/api/v1/workspace/entries").json()
        reached = client.get("/api/v1/workspace/entries?path=.attachments")

    assert ".attachments" not in [row["name"] for row in top["items"]]
    assert reached.status_code == 409, reached.text


def test_a_climb_out_of_the_workspace_is_refused(tmp_path: Path) -> None:
    """The wall `resolve_in_workspace` is, reached through this surface."""
    a_workspace(tmp_path)
    (tmp_path / "secret.txt").write_text("not yours", encoding="utf-8")
    with an_api(tmp_path) as client:
        listed = client.get("/api/v1/workspace/entries?path=../")
        fetched = client.get("/api/v1/workspace/download/../secret.txt")

    assert listed.status_code == 409, listed.text
    assert fetched.status_code in {404, 409}, fetched.text


def test_folders_come_first_and_files_newest_first(tmp_path: Path) -> None:
    """What somebody browsing is looking for: somewhere to go, or the thing
    they just put there."""
    root = a_workspace(tmp_path)
    (root / "library" / "old.md").write_text("older", encoding="utf-8")
    import os
    os.utime(root / "library" / "old.md", (1000, 1000))
    (root / "library" / "sub").mkdir()

    with an_api(tmp_path) as client:
        rows = client.get("/api/v1/workspace/entries?path=library").json()["items"]

    # Folders by name, files newest first: a folder's own timestamp moves
    # whenever anything inside it does, so ranking folders by time is noise.
    assert rows[0]["name"] == "sub"
    assert [row["name"] for row in rows[1:]] == ["notes.md", "old.md"]


# ── Changing things ─────────────────────────────────────────────────────────


def test_a_file_can_be_put_into_a_room(tmp_path: Path) -> None:
    a_workspace(tmp_path)
    with an_api(tmp_path) as client:
        put = client.put(
            "/api/v1/workspace/entries/library/report.md",
            content=b"# report",
            headers=control(client),
        )

        assert put.status_code == 200, put.text

    assert (tmp_path / "workspace" / "library" / "report.md").read_bytes() == b"# report"


def test_putting_a_file_needs_the_page_s_own_token(tmp_path: Path) -> None:
    """A write is a mutation, and every mutation on this service carries it."""
    a_workspace(tmp_path)
    with an_api(tmp_path) as client:
        refused = client.put("/api/v1/workspace/entries/library/x.md", content=b"x")

    assert refused.status_code == 403
    assert not (tmp_path / "workspace" / "library" / "x.md").exists()


def test_a_file_may_not_be_dropped_beside_the_rooms(tmp_path: Path) -> None:
    """The root stays clean, which is the whole reason the rooms exist."""
    a_workspace(tmp_path)
    with an_api(tmp_path) as client:
        refused = client.put(
            "/api/v1/workspace/entries/loose.md", content=b"x", headers=control(client)
        )

    assert refused.status_code == 409, refused.text
    assert not (tmp_path / "workspace" / "loose.md").exists()


def test_a_room_cannot_be_renamed_out_from_under_the_rest_of_nervis(
    tmp_path: Path,
) -> None:
    """**The one refusal that protects something invisible from here.** Chat
    writes to `export` by name and the editor opens `clarvis` by name; renaming
    a room in a file manager would leave both writing into directories nothing
    reads, with no error anywhere."""
    a_workspace(tmp_path)
    with an_api(tmp_path) as client:
        renamed = client.post("/api/v1/workspace/move",
                              json={"from": "export", "to": "outbox"},
                              headers=control(client))
        deleted = client.request("DELETE", "/api/v1/workspace/entries/clarvis",
                                 headers=control(client))

    assert renamed.status_code == 409, renamed.text
    assert deleted.status_code == 409, deleted.text
    assert (tmp_path / "workspace" / "export").is_dir()
    assert (tmp_path / "workspace" / "clarvis").is_dir()


def test_a_file_moves_between_rooms(tmp_path: Path) -> None:
    """The thing the tab exists for: moving a file without leaving the tab."""
    a_workspace(tmp_path)
    with an_api(tmp_path) as client:
        moved = client.post("/api/v1/workspace/move",
                            json={"from": "library/notes.md", "to": "export/notes.md"},
                            headers=control(client))

        assert moved.status_code == 200, moved.text

    assert (tmp_path / "workspace" / "export" / "notes.md").is_file()
    assert not (tmp_path / "workspace" / "library" / "notes.md").exists()


def test_a_move_onto_an_existing_name_is_refused_rather_than_silent(
    tmp_path: Path,
) -> None:
    """Overwriting is the one outcome a person cannot undo from this screen."""
    root = a_workspace(tmp_path)
    (root / "export" / "notes.md").write_text("already here", encoding="utf-8")
    with an_api(tmp_path) as client:
        refused = client.post("/api/v1/workspace/move",
                              json={"from": "library/notes.md", "to": "export/notes.md"},
                              headers=control(client))

    assert refused.status_code == 409, refused.text
    assert (root / "export" / "notes.md").read_text() == "already here"


def test_a_folder_cannot_be_moved_inside_itself(tmp_path: Path) -> None:
    """`shutil.move` would start and leave both ends broken."""
    root = a_workspace(tmp_path)
    (root / "library" / "papers").mkdir()
    with an_api(tmp_path) as client:
        refused = client.post("/api/v1/workspace/move",
                              json={"from": "library/papers", "to": "library/papers/inner"},
                              headers=control(client))

    assert refused.status_code == 409, refused.text
    assert (root / "library" / "papers").is_dir()


def test_a_new_folder_goes_inside_a_room(tmp_path: Path) -> None:
    a_workspace(tmp_path)
    with an_api(tmp_path) as client:
        made = client.post("/api/v1/workspace/folders",
                           json={"path": "library/papers"}, headers=control(client))
        beside = client.post("/api/v1/workspace/folders",
                             json={"path": "scratch"}, headers=control(client))

    assert made.status_code == 201, made.text
    assert (tmp_path / "workspace" / "library" / "papers").is_dir()
    assert beside.status_code == 409, beside.text


# ── Delete, and being wrong about it ────────────────────────────────────────


def test_delete_moves_to_the_trash_rather_than_unlinking(tmp_path: Path) -> None:
    """**A person clicking delete in a browser should be able to be wrong.**"""
    root = a_workspace(tmp_path)
    with an_api(tmp_path) as client:
        gone = client.request("DELETE", "/api/v1/workspace/entries/library/notes.md",
                              headers=control(client))

        assert gone.status_code == 200, gone.text
        waiting = client.get("/api/v1/workspace/trash").json()["items"]

    assert not (root / "library" / "notes.md").exists()
    assert [item["name"] for item in waiting] == ["notes.md"]
    assert list((root / TRASH).iterdir()), "nothing is actually in the trash"


def test_a_trashed_file_can_be_put_back(tmp_path: Path) -> None:
    root = a_workspace(tmp_path)
    with an_api(tmp_path) as client:
        client.request("DELETE", "/api/v1/workspace/entries/library/notes.md",
                       headers=control(client))
        waiting = client.get("/api/v1/workspace/trash").json()["items"][0]
        back = client.post("/api/v1/workspace/trash/restore",
                           json={"path": waiting["path"], "to": "library/notes.md"},
                           headers=control(client))

        assert back.status_code == 200, back.text

    assert (root / "library" / "notes.md").read_text() == "kept"


def test_the_trash_is_swept_after_a_fortnight_and_not_before(tmp_path: Path) -> None:
    """The same window attachments get, and the same reason: long enough to
    undo a mistake, not long enough to become an archive nobody chose."""
    root = a_workspace(tmp_path)
    with an_api(tmp_path) as client:
        client.request("DELETE", "/api/v1/workspace/entries/library/notes.md",
                       headers=control(client))

    import time as clock
    assert prune_trash(root, now=clock.time()) == 0
    assert prune_trash(root, now=clock.time() + TRASH_SECONDS + 1) == 1
    assert list((root / TRASH).iterdir()) == []


def test_a_name_the_trash_did_not_write_is_left_alone(tmp_path: Path) -> None:
    """Deleting something because its name is unfamiliar is the opposite of
    what a trash directory is for."""
    root = a_workspace(tmp_path)
    (root / TRASH).mkdir()
    (root / TRASH / "somebody-elses-file.txt").write_text("?", encoding="utf-8")

    import time as clock
    assert prune_trash(root, now=clock.time() + TRASH_SECONDS * 10) == 0
    assert (root / TRASH / "somebody-elses-file.txt").is_file()


# ── Reading a file back ─────────────────────────────────────────────────────


def test_a_file_can_be_downloaded_from_its_room(tmp_path: Path) -> None:
    a_workspace(tmp_path)
    with an_api(tmp_path) as client:
        got = client.get("/api/v1/workspace/download/library/notes.md")

    assert got.status_code == 200
    assert got.content == b"kept"
    assert "notes.md" in got.headers["content-disposition"]


def test_an_unconfigured_workspace_is_a_state_to_render(tmp_path: Path) -> None:
    """"Turn this on" is a sentence the screen has to be able to say. A 404
    would make an install that was never asked to read files look broken."""
    settings = Settings(database_path=str(tmp_path / "n.db"), _env_file=None)  # type: ignore[call-arg]
    with TestClient(create_app(settings)) as client:
        body = client.get("/api/v1/workspace/entries").json()

    assert body["items"] == []
    assert "NERVIS_WORKSPACE_PATH" in body["detail"]


# ── Opening a file rather than saving it ────────────────────────────────────


def test_a_pdf_opens_in_the_browser_when_asked(tmp_path: Path) -> None:
    """`inline` is the difference between a PDF landing in Downloads and a PDF
    opening — which is the whole point of not leaving the dashboard."""
    root = a_workspace(tmp_path)
    (root / "library" / "paper.pdf").write_bytes(b"%PDF-1.4 ...")
    with an_api(tmp_path) as client:
        shown = client.get("/api/v1/workspace/download/library/paper.pdf?inline=1")
        saved = client.get("/api/v1/workspace/download/library/paper.pdf")

    assert shown.headers["content-type"] == "application/pdf"
    assert shown.headers["content-disposition"].startswith("inline")
    assert saved.headers["content-disposition"].startswith("attachment")


def test_html_and_svg_are_never_shown_inline(tmp_path: Path) -> None:
    """**The exclusion that matters.** Anything served inline runs in NERVIS's
    own origin, and both of these can carry script — a file somebody dropped
    into a room must not execute next to the page's control token. Asking for
    inline gets the download instead, because the file is still what was asked
    for and a refusal over a presentation preference would be a boundary
    pretending to be one."""
    root = a_workspace(tmp_path)
    (root / "library" / "page.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    (root / "library" / "art.svg").write_text("<svg onload='alert(1)'/>", encoding="utf-8")
    with an_api(tmp_path) as client:
        page = client.get("/api/v1/workspace/download/library/page.html?inline=1")
        art = client.get("/api/v1/workspace/download/library/art.svg?inline=1")

    for answered in (page, art):
        assert answered.headers["content-type"] == "application/octet-stream"
        assert answered.headers["content-disposition"].startswith("attachment")
        assert answered.headers["x-content-type-options"] == "nosniff"


def test_text_is_shown_as_text_whatever_its_suffix_claims(tmp_path: Path) -> None:
    """A `.json` opened in a tab is something to read, not something to run."""
    root = a_workspace(tmp_path)
    (root / "library" / "data.json").write_text('{"a":1}', encoding="utf-8")
    with an_api(tmp_path) as client:
        shown = client.get("/api/v1/workspace/download/library/data.json?inline=1")

    assert shown.headers["content-type"].startswith("text/plain")


# ── Somewhere else NERVIS was given ─────────────────────────────────────────


def an_api_with_a_place(tmp_path: Path, where: Path) -> TestClient:
    settings = Settings(
        database_path=str(tmp_path / "nervis.db"),
        workspace_path=str(tmp_path / "workspace"),
        file_places=f"nas={where}",
        _env_file=None,  # type: ignore[call-arg]
    )
    return TestClient(create_app(settings))


def test_a_configured_place_is_listed_beside_the_workspace(tmp_path: Path) -> None:
    """The reason the tab reaches more than one root: a share to put things on."""
    a_workspace(tmp_path)
    nas = tmp_path / "nas"
    (nas / "documents").mkdir(parents=True)
    with an_api_with_a_place(tmp_path, nas) as client:
        here = client.get("/api/v1/workspace/entries").json()
        there = client.get("/api/v1/workspace/entries?place=nas").json()

    assert here["places"] == ["nas", "workspace"]
    assert [row["name"] for row in there["items"]] == ["documents"]
    # A share is somebody else's directory: NERVIS has no rooms to impose on it.
    assert there["rooms"] == []


def test_a_place_that_is_not_there_is_left_out_rather_than_created(
    tmp_path: Path,
) -> None:
    """**A NAS that is asleep is unreachable, not empty.** Creating the
    directory to have somewhere to write would put files on the local disk
    under a name that says they are on the share."""
    a_workspace(tmp_path)
    absent = tmp_path / "never-mounted"
    with an_api_with_a_place(tmp_path, absent) as client:
        listed = client.get("/api/v1/workspace/entries").json()
        reached = client.get("/api/v1/workspace/entries?place=nas")

    assert listed["places"] == ["workspace"]
    assert reached.status_code == 404, reached.text
    assert not absent.exists()


def test_a_request_may_choose_a_place_but_never_where_one_is(tmp_path: Path) -> None:
    """The containment argument, with more than one root: a request picks from
    the places an operator configured and cannot name a directory."""
    a_workspace(tmp_path)
    nas = tmp_path / "nas"
    nas.mkdir()
    (tmp_path / "elsewhere").mkdir()
    with an_api_with_a_place(tmp_path, nas) as client:
        invented = client.get(f"/api/v1/workspace/entries?place={tmp_path}/elsewhere")
        climbed = client.get("/api/v1/workspace/entries?place=nas&path=../elsewhere")

    assert invented.status_code == 404, invented.text
    assert climbed.status_code == 409, climbed.text


def test_a_file_copies_from_a_room_to_the_share(tmp_path: Path) -> None:
    """What the tab is for now: getting a document onto the NAS without
    leaving it — and leaving the original where it was."""
    root = a_workspace(tmp_path)
    nas = tmp_path / "nas"
    nas.mkdir()
    with an_api_with_a_place(tmp_path, nas) as client:
        copied = client.post("/api/v1/workspace/move", json={
            "from": "library/notes.md", "from_place": "workspace",
            "to": "notes.md", "to_place": "nas", "copy": True,
        }, headers=control(client))

        assert copied.status_code == 200, copied.text

    assert (nas / "notes.md").read_text() == "kept"
    assert (root / "library" / "notes.md").is_file(), "a copy must leave the original"


def test_a_file_moves_from_the_share_back_into_a_room(tmp_path: Path) -> None:
    """And the other direction, which is a move rather than a copy: the file
    was asked to be *in* the room."""
    root = a_workspace(tmp_path)
    nas = tmp_path / "nas"
    nas.mkdir()
    (nas / "from-the-nas.md").write_text("came back", encoding="utf-8")
    with an_api_with_a_place(tmp_path, nas) as client:
        moved = client.post("/api/v1/workspace/move", json={
            "from": "from-the-nas.md", "from_place": "nas",
            "to": "library/from-the-nas.md", "to_place": "workspace",
        }, headers=control(client))

        assert moved.status_code == 200, moved.text

    assert (root / "library" / "from-the-nas.md").read_text() == "came back"
    assert not (nas / "from-the-nas.md").exists()


def test_the_share_s_own_top_level_may_be_written_to(tmp_path: Path) -> None:
    """The workspace's rooms are structural; a share's top level is somebody
    else's directory, and refusing to make a folder there would be NERVIS
    imposing its layout on a NAS it was merely given access to."""
    a_workspace(tmp_path)
    nas = tmp_path / "nas"
    nas.mkdir()
    with an_api_with_a_place(tmp_path, nas) as client:
        made = client.post("/api/v1/workspace/folders",
                           json={"path": "archive", "place": "nas"},
                           headers=control(client))
        beside = client.post("/api/v1/workspace/folders",
                             json={"path": "archive"}, headers=control(client))

    assert made.status_code == 201, made.text
    assert (nas / "archive").is_dir()
    assert beside.status_code == 409, "the workspace's own top level stays structural"


# ── The trash's own boundary ────────────────────────────────────────────────


def test_a_symlinked_trash_cannot_carry_a_delete_out_of_the_workspace(tmp_path: Path) -> None:
    """**The file is checked; the destination was not.** `mkdir(exist_ok=True)`
    on a symlink succeeds silently, so a `.trash` planted as a link sent every
    delete wherever it pointed — and the fortnightly prune then permanently
    removed whatever was there (base review, 17 September 2026, finding 3)."""
    root = a_workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / TRASH).symlink_to(outside, target_is_directory=True)

    with an_api(tmp_path) as client:
        answer = client.request("DELETE", "/api/v1/workspace/entries/library/notes.md",
                                headers=control(client))

    assert answer.status_code == 409, answer.text
    assert "symbolic link" in answer.text
    assert (root / "library" / "notes.md").is_file(), "the file was moved out of the workspace"
    assert list(outside.iterdir()) == []


def test_a_symlinked_trash_is_never_pruned_through(tmp_path: Path) -> None:
    """The sweep runs on a timer with nobody watching, so it checks the same
    thing the delete does rather than trusting what it finds."""
    root = a_workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "1000-not-ours.txt").write_text("private", encoding="utf-8")
    (root / TRASH).symlink_to(outside, target_is_directory=True)

    assert prune_trash(root, now=1000 + TRASH_SECONDS * 10) == 0
    assert (outside / "1000-not-ours.txt").is_file()


def test_two_files_of_one_name_deleted_in_the_same_second_are_both_recoverable(
    tmp_path: Path,
) -> None:
    """The trashed name carried whole seconds and nothing else, and `move`
    replaces: deleting `import/same.txt` and `library/same.txt` in one second
    left one of them (base review, finding 11)."""
    root = a_workspace(tmp_path)
    (root / "import" / "same.txt").write_text("FIRST", encoding="utf-8")
    (root / "library" / "same.txt").write_text("SECOND", encoding="utf-8")

    with an_api(tmp_path) as client:
        for room in ("import", "library"):
            answer = client.request("DELETE", f"/api/v1/workspace/entries/{room}/same.txt",
                                    headers=control(client))
            assert answer.status_code == 200, answer.text

    kept = sorted(entry.read_text(encoding="utf-8") for entry in (root / TRASH).iterdir())
    assert kept == ["FIRST", "SECOND"], "one delete overwrote the other's only copy"


def test_a_copied_folder_never_turns_a_link_out_into_a_readable_file(tmp_path: Path) -> None:
    """**Both ends were checked; nothing looked inside.** `copytree` follows
    symbolic links by default, so a link at any depth in the copied folder was
    dereferenced and an outside file's bytes landed in the workspace as an
    ordinary file — which the file manager then lists and serves (base review,
    17 September 2026, finding 7)."""
    root = a_workspace(tmp_path)
    secret = tmp_path / "outside" / "secret.txt"
    secret.parent.mkdir()
    secret.write_text("OUTSIDE_SECRET", encoding="utf-8")
    (root / "library" / "bundle").mkdir()
    (root / "library" / "bundle" / "ours.txt").write_text("ours", encoding="utf-8")
    (root / "library" / "bundle" / "leak.txt").symlink_to(secret)

    with an_api(tmp_path) as client:
        answer = client.post("/api/v1/workspace/move", json={
            "from": "library/bundle", "to": "export/bundle", "copy": True,
        }, headers=control(client))

        assert answer.status_code == 200, answer.text
        # The ordinary file came across; the link came across *as a link*, so the
        # outside bytes are not sitting in the workspace as a file.
        assert (root / "export" / "bundle" / "ours.txt").read_text() == "ours"
        landed = root / "export" / "bundle" / "leak.txt"
        assert landed.is_symlink(), "the link became a plain file holding the outside bytes"
        # And nothing inside the workspace will serve what it points at.
        served = client.get("/api/v1/workspace/download/export/bundle/leak.txt",
                            headers=control(client))
        assert served.status_code == 409, served.text
        assert b"OUTSIDE_SECRET" not in served.content
