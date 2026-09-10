"""Three rooms in the workspace, and which file lands in which.

**The layout is a promise to a person, not an implementation detail.** Somebody
opens `workspace/` in a file manager and expects to know, without opening
anything, what they handed NERVIS and what NERVIS produced. Every other test
that touches these paths asserts them in passing, as a step on the way to
something else; this file asserts them as the subject, so a change that quietly
moves a file has somewhere to fail that says so.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from nervis import workspace
from nervis.app import create_app
from nervis.config import Settings


def an_api(tmp_path: Path, **overrides: object) -> TestClient:
    settings = Settings(
        database_path=str(tmp_path / "nervis.db"),
        _env_file=None,  # type: ignore[call-arg]
        **{"workspace_path": str(tmp_path / "workspace"), **overrides},  # type: ignore[arg-type]
    )
    return TestClient(create_app(settings))


def a_workspace(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        database_path=":memory:",
        _env_file=None,  # type: ignore[call-arg]
        **{"workspace_path": str(tmp_path), **overrides},  # type: ignore[arg-type]
    )


# ── The three rooms ─────────────────────────────────────────────────────────


def test_each_room_is_a_named_subdirectory_of_the_workspace(tmp_path: Path) -> None:
    """One setting still gets all three: a deployment that said where the
    workspace is does not then have to describe a layout."""
    settings = a_workspace(tmp_path)

    assert workspace.imported(settings) == tmp_path / "import"
    assert workspace.exported(settings) == tmp_path / "export"
    assert workspace.library(settings) == tmp_path / "library"
    assert workspace.editor_rooms(settings) == [str(tmp_path / "clarvis")]


def test_a_room_can_be_somewhere_else_without_moving_the_others(tmp_path: Path) -> None:
    """An import directory on another disk is a real arrangement, and saying so
    should not require restating where everything else lives."""
    elsewhere = tmp_path / "elsewhere"
    settings = a_workspace(tmp_path, workspace_import_path=str(elsewhere))

    assert workspace.imported(settings) == elsewhere
    assert workspace.exported(settings) == tmp_path / "export"


def test_a_room_is_made_when_it_is_asked_for(tmp_path: Path) -> None:
    """Created on demand, because the first upload should not fail on a
    directory nobody made."""
    settings = a_workspace(tmp_path)
    assert not (tmp_path / "import").exists()

    place = workspace.imported(settings)

    assert place is not None and place.is_dir()


def test_no_workspace_is_no_rooms_rather_than_an_invented_one() -> None:
    """`workspace_path` empty means the feature is off. Answering with a path
    anyway would be inventing a directory for a deployment that said no."""
    settings = Settings(database_path=":memory:", _env_file=None)  # type: ignore[call-arg]

    assert workspace.imported(settings) is None
    assert workspace.exported(settings) is None
    assert workspace.library(settings) is None
    assert workspace.editor_rooms(settings) == []


def test_configured_editor_roots_win_over_the_room(tmp_path: Path) -> None:
    """An operator who named the editor's roots meant those."""
    settings = a_workspace(tmp_path, code_workspace_roots="/srv/project,/srv/other")

    assert workspace.editor_rooms(settings) == ["/srv/project", "/srv/other"]


# ── Which file lands where, through the real routes ─────────────────────────


def test_an_upload_lands_in_the_import_room(tmp_path: Path) -> None:
    """A file somebody handed over, in the room for files somebody handed over."""
    with an_api(tmp_path) as client:
        answered = client.put(
            "/api/v1/workspace/files/notes.md?conversation_id=cv_ab12",
            content=b"# notes",
        )

        assert answered.status_code == 200, answered.text

    landed = list((tmp_path / "workspace" / "import").rglob("notes.md"))
    assert len(landed) == 1, landed
    assert not list((tmp_path / "workspace" / "export").rglob("notes.md"))


def test_a_saved_reply_lands_in_the_export_room(tmp_path: Path) -> None:
    """A file NERVIS produced, in the room for files NERVIS produced."""
    from typing import Any

    from test_m4_chat import _with_models, turn
    from test_m4_chat import an_api as a_chat_api

    # Built through a real turn, because a saved reply is the *reply* to
    # something: writing a row into the store directly would prove the writer
    # and skip the path a person takes to reach it.
    sent: list[dict[str, Any]] = []
    client = a_chat_api(workspace_path=str(tmp_path / "workspace"))
    _with_models(client, sent, ["qwen/qwen3-4b-2507"])
    with client:
        answered = turn(client, "why is ravis slow", system="Be someone.")
        conversation = answered.headers["x-conversation-id"]
        ran = client.post("/api/v1/commands/run", json={
            "operation": "nervis.document.write",
            "target": "answer.md",
            "conversation_id": conversation,
        })

        assert ran.status_code == 200, ran.text

    assert (tmp_path / "workspace" / "export" / "answer.md").is_file()
    assert not (tmp_path / "workspace" / "answer.md").exists()


def test_a_task_for_clarvis_lands_where_the_editor_opens(tmp_path: Path) -> None:
    """**The one room a wrong answer makes silent.** A task written where the
    editor never looks is not an error anywhere — it is a file nobody reads and
    a person waiting at an editor that shows nothing."""
    with an_api(tmp_path) as client:
        ran = client.post("/api/v1/commands/run", json={
            "operation": "nervis.clarvis.task",
            "target": "add a retry to the uploader",
            "conversation_id": "cv_ab12",
        })

        assert ran.status_code == 200, ran.text

    folder = ran.json()["file"]["folder"]
    assert folder.startswith("nervis-tasks/"), folder
    assert (tmp_path / "workspace" / "clarvis" / folder / "clarvis-task.md").is_file()


def test_a_file_on_the_shelf_is_found_by_name(tmp_path: Path) -> None:
    """The room that exists because the workspace root stopped being one.

    A file somebody keeps and asks for by name is not an attachment — it
    belongs to no conversation and nothing should sweep it after a fortnight —
    and it is not something NERVIS produced either.
    """
    from typing import Any

    from test_m4_chat import _with_models, turn
    from test_m4_chat import an_api as a_chat_api

    shelf = tmp_path / "workspace" / "library"
    shelf.mkdir(parents=True)
    (shelf / "notes.md").write_text("Revenue fell in Q3.", encoding="utf-8")
    sent: list[dict[str, Any]] = []
    client = a_chat_api(workspace_path=str(tmp_path / "workspace"))
    _with_models(client, sent, ["qwen/qwen3-4b-2507"])

    with client:
        turn(client, 'summarise "notes.md" for me', system="Be someone.")

    prompt = " ".join(
        str(message.get("content", ""))
        for body in sent
        for message in body.get("messages", [])
    )
    assert "Revenue fell in Q3." in prompt


def test_a_stray_file_in_the_workspace_itself_is_not_searched(tmp_path: Path) -> None:
    """**The root is not a room, and that is the point of having rooms.**

    A layout with one place a stray file can sit and still work is a layout
    that is only advice — the loose file is where everything ends up, which is
    the state the rooms were made to leave behind.
    """
    from typing import Any

    from test_m4_chat import _with_models, turn
    from test_m4_chat import an_api as a_chat_api

    root = tmp_path / "workspace"
    root.mkdir()
    (root / "stray.md").write_text("Revenue fell in Q3.", encoding="utf-8")
    sent: list[dict[str, Any]] = []
    client = a_chat_api(workspace_path=str(root))
    _with_models(client, sent, ["qwen/qwen3-4b-2507"])

    with client:
        turn(client, 'summarise "stray.md" for me', system="Be someone.")

    prompt = " ".join(
        str(message.get("content", ""))
        for body in sent
        for message in body.get("messages", [])
    )
    assert "Revenue fell in Q3." not in prompt


# ── A workspace on a network share ──────────────────────────────────────────


def test_a_room_under_an_unmounted_share_is_absent_rather_than_empty() -> None:
    """**The expensive silent failure this prevents.**

    A workspace on a NAS lives under `/Volumes/<name>`, and that directory
    exists whether or not the share is mounted. Creating a room inside an
    unmounted one writes to the *local* disk at the mountpoint — after which
    macOS cannot mount the share there and brings it up as `<name>-1`. Two
    workspaces, one shadowing the other, files accumulating in the wrong one,
    and nothing reporting anything.

    So it reads as "no workspace", which every caller already has a sentence
    for, rather than as an empty one it should start filling.
    """
    settings = Settings(
        database_path=":memory:",
        workspace_path="/Volumes/nervis-not-mounted-in-this-test",
        _env_file=None,  # type: ignore[call-arg]
    )

    assert workspace.imported(settings) is None
    assert workspace.exported(settings) is None
    assert not Path("/Volumes/nervis-not-mounted-in-this-test").exists(), (
        "the check created the very directory it exists to avoid creating"
    )


def test_an_ordinary_local_workspace_is_not_mistaken_for_a_share(
    tmp_path: Path,
) -> None:
    """`os.path.ismount` is False for every ordinary directory, so a check
    written a shade too broadly would turn every local workspace into no
    workspace at all."""
    settings = a_workspace(tmp_path)

    assert workspace.imported(settings) == tmp_path / "import"
