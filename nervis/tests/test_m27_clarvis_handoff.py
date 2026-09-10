"""Handing a coding task to Clarvis, through the workspace (NERVIS.md M27).

**The design exists to avoid a contract, not to implement one.** `CLARVIS.md`
§6.7 forbids NERVIS invoking a tool, resolving a gate or changing a setting, and
closes with *"an agent asked to add such control must stop."* None of that is
needed: Clarvis reads a plan from disk every time, so a task authored elsewhere
arrives through the door a hand-edited one already comes through.

The test that separates this from remote control is stated in the milestone and
asserted here: **with the Bridge stopped, all of it still works**, because the
interface is a file.

The other half is provenance. A brief somebody wrote themselves and one that
arrived from another program deserve different scepticism at the moment of
approval, and only the approver can apply it — so the file says where it came
from, and Clarvis shows that before anything runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis import code_proxy, commands, handoff, workspace
from nervis.app import create_app
from nervis.config import Settings

OPEN_EDITOR = {"registered": True, "workspace_label": "", "nervis_workspace": "coding"}


@pytest.fixture()
def client(tmp_path: Any) -> Any:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"),
        workspace_path=str(tmp_path),
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        yield client


# ── Naming Clarvis is what makes it a handoff ───────────────────────────────


def test_the_editor_has_to_be_named() -> None:
    """Every other operation is matched from what the sentence asks for; this
    one from *who it asks*.

    "Fix the export bug" is a request to whoever is listening. Only "get clarvis
    to fix the export bug" says where it should go.
    """
    assert commands.propose("fix the export bug", [], clarvis=OPEN_EDITOR) is None
    offer = commands.propose("get clarvis to fix the export bug", [], clarvis=OPEN_EDITOR)
    assert offer is not None
    assert offer.operation == "nervis.clarvis.task"
    assert offer.target == "fix the export bug"


@pytest.mark.parametrize("said", [
    "get clarvis to add a retry",
    "have clarvis add a retry",
    "ask clarvis to add a retry",
    "tell clarvis to add a retry",
])
def test_the_ways_people_hand_something_over(said: str) -> None:
    offer = commands.propose(said, [], clarvis=OPEN_EDITOR)
    assert offer is not None and offer.target == "add a retry"


@pytest.mark.parametrize(("said", "task"), [
    # The sentence that was actually typed, verbatim. It produced no offer, and
    # chat then claimed the task had been written.
    ("Can you hand a small coding task to clarvis? "
     "I'd like it to make me a pomodoro timer",
     "I'd like it to make me a pomodoro timer"),
    ("hand this to clarvis: add a retry to the uploader", "add a retry to the uploader"),
    ("send a task to clarvis \u2014 add a retry", "add a retry"),
    ("can you get clarvis to add a retry?", "add a retry"),
])
def test_the_ways_people_actually_phrase_it(said: str, task: str) -> None:
    """**The object first and Clarvis after, and a polite question mark.**

    Only "get / have / ask / tell clarvis to …" used to register. The first case
    here is what the operator really typed on 10 September 2026: no offer was
    made, and chat — with no button to point at — said the task was written and
    Clarvis had it. Nothing had been written.
    """
    offer = commands.propose(said, [], clarvis=OPEN_EDITOR)
    assert offer is not None, f"no offer for {said!r}"
    assert offer.operation == "nervis.clarvis.task"
    assert offer.target == task


def test_asking_to_hand_something_over_with_nothing_to_hand_is_not_an_offer() -> None:
    """With no task after it there is nothing to write down, and an offer for an
    empty task is a button that does nothing useful. Chat asks what the task is."""
    offer = commands.propose("can you hand a coding task to clarvis?", [], clarvis=OPEN_EDITOR)
    assert offer is None


def test_a_question_about_clarvis_is_not_a_handoff() -> None:
    """The same trap M23's learning had: this operation takes the rest of the
    sentence as its content, so a question mark makes the content a question."""
    for asked in ("what did clarvis do?", "get clarvis to what?", "have clarvis it"):
        assert commands.propose(asked, [], clarvis=OPEN_EDITOR) is None


# ── The destination: a new folder per task ───────────────────────────────────


def test_the_offer_is_ready_without_a_window_already_open() -> None:
    """**The check this replaced described a layout that no longer exists.**

    It refused unless a Clarvis window was already open on the folder NERVIS
    writes into, and compared that window's workspace label. Each task now gets a
    new folder of its own, opened in Clarvis as that task's workspace — so the
    window that reads it is opened after the handoff by design, and demanding one
    beforehand would refuse every task.
    """
    editors = (
        {"registered": False},
        OPEN_EDITOR,
        {"registered": True, "workspace_label": "other", "nervis_workspace": "coding"},
    )
    for editor in editors:
        offer = commands.propose("get clarvis to fix it", [], clarvis=editor)
        assert offer is not None and offer.ready, f"refused with {editor}"
        assert "new folder of its own" in offer.detail
        assert "nervis-tasks/" in offer.detail


# ── What is written ─────────────────────────────────────────────────────────


def test_the_task_is_written_where_clarvis_reads(client: TestClient) -> None:
    answer = client.post("/api/v1/commands/run", json={
        "operation": "nervis.clarvis.task",
        "target": "add a retry to the uploader when the API returns 429",
        "conversation_id": "cv_ab12",
    })
    assert answer.status_code == 200
    assert answer.json()["file"]["name"] == handoff.TASK_FILE
    folder = answer.json()["file"]["folder"]
    assert folder.startswith("nervis-tasks/"), folder

    # Inside the editor's own room, in the task's own folder — which is what is
    # opened in Clarvis, and where Clarvis reads `clarvis-task.md` from.
    room = Path(client.app.state.settings.workspace_path) / "clarvis"  # type: ignore[attr-defined]
    waiting = handoff.waiting(room / folder)
    assert waiting is not None
    assert waiting.task == "add a retry to the uploader when the API returns 429"
    assert waiting.conversation == "cv_ab12"

    # The same folder, absolute, is what the page opens in the Code tab — so it
    # has to be exactly this folder, and one the editor session will accept.
    opened = Path(answer.json()["file"]["workspace"])
    assert opened.is_absolute()
    assert opened == (room / folder).resolve()
    settings = client.app.state.settings  # type: ignore[attr-defined]
    code_proxy.Sessions().open(str(opened), workspace.editor_rooms(settings))


def test_the_file_says_who_wrote_it(tmp_path: Path) -> None:
    """Provenance is the whole security story on the other side.

    Matched on the content rather than the filename, so a file renamed by hand
    still says what it is — and one somebody wrote themselves and happened to
    name this is not claimed as another program's work.
    """
    written = handoff.write(tmp_path, "fix the export bug")
    folder = tmp_path / written.folder
    text = (folder / handoff.TASK_FILE).read_text(encoding="utf-8")
    assert handoff.MARKER in text
    assert "read it before approving" in text

    (folder / handoff.TASK_FILE).write_text("# mine, actually", encoding="utf-8")
    assert handoff.waiting(folder) is None


def test_a_handoff_with_no_conversation_stops_at_the_timestamp(tmp_path: Path) -> None:
    """The case both sides' fixtures missed, found by parsing a real handoff.

    Without a conversation there is no comma after the stamp, and a read bounded
    by `[^,_]*` ran on into the sentence. Clarvis then showed the person a task
    that "came from NERVIS on 2026-09-01 22:21 UTC. You asked for this in
    conversation rather than in the editor" — which is the provenance line this
    whole design exists to get right.
    """
    written = handoff.write(tmp_path, "fix the export bug", today="2026-09-01 22:21 UTC")
    waiting = handoff.waiting(tmp_path / written.folder)
    assert waiting is not None
    assert waiting.asked_on == "2026-09-01 22:21 UTC"
    assert waiting.conversation == ""


def test_a_second_task_gets_its_own_folder_and_leaves_the_first_alone(tmp_path: Path) -> None:
    """**The reason for folders at all.** Clarvis keeps `plan.md` and its build
    state at the root of the folder it has open, so two tasks sharing a folder
    would share a plan — the second would open onto the first one's. This used to
    be "a second task replaces an unread one"; now neither touches the other.

    Same minute and same words on purpose, which is the one case that could
    otherwise produce the same folder name.
    """
    first = handoff.write(tmp_path, "fix the export bug", today="2026-09-10 22:15 UTC")
    second = handoff.write(tmp_path, "fix the export bug", today="2026-09-10 22:15 UTC")

    assert first.folder != second.folder, "two tasks were written into one folder"
    kept = handoff.waiting(tmp_path / first.folder)
    assert kept is not None and kept.task == "fix the export bug", "the first task was disturbed"
    assert handoff.waiting(tmp_path / second.folder) is not None


def test_an_empty_task_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="something to say"):
        handoff.write(tmp_path, "  ")


def test_writing_needs_a_workspace(tmp_path: Path) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "n.db"), workspace_path="", _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        answer = client.post("/api/v1/commands/run", json={
            "operation": "nervis.clarvis.task", "target": "fix it",
        })
        assert answer.status_code >= 400


# ── §6.7 is untouched ───────────────────────────────────────────────────────


def test_nothing_here_reaches_the_editor() -> None:
    """**The test that separates this from remote control.**

    `handoff.py` writes a file and reads one back. It holds no client, no
    address and no token, so there is no path from it to the Bridge — which is
    what makes "with the Bridge stopped, this still works" a property rather
    than a claim.
    """
    import ast

    # **Its imports, not its words.** A first version searched the source for
    # "Bridge" and failed on the docstring *explaining* that this works with the
    # Bridge stopped. What matters is what the module can reach, and a module
    # that imports no client cannot open a socket however it is edited.
    tree = ast.parse(Path(handoff.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert imported <= {"re", "dataclasses", "datetime", "pathlib", "typing", "__future__"}, (
        f"handoff.py imports {sorted(imported)}; §6.7 forbids NERVIS reaching the "
        "editor, and a module that can open a socket is one edit from doing so"
    )


def test_the_operation_is_in_the_closed_set() -> None:
    """§12: nothing is invented. A handoff is an enumerated operation like every
    other act, with a confirmation in front of it."""
    assert "nervis.clarvis.task" in commands.BY_ID
    assert commands.BY_ID["nervis.clarvis.task"].service == "nervis"


def test_each_task_folder_is_named_by_when_and_what(tmp_path: Path) -> None:
    """The folder a person opens in Clarvis, so it has to say what it is.

    Written out as literals on purpose: the date first so a listing sorts by
    when, then the task's first words so it can be told apart at a glance, and
    the task file at the folder's root — where Clarvis reads it, since the folder
    is opened as the workspace. Nothing at the top of `nervis-tasks/` itself, and
    nothing at the top of the editor room.
    """
    written = handoff.write(tmp_path, "Fix the export bug, please!", today="2026-09-10 22:15 UTC")

    assert written.folder == "nervis-tasks/2026-09-10-22-15-fix-the-export-bug-please"
    assert (tmp_path / written.folder / "clarvis-task.md").is_file()
    assert not (tmp_path / "clarvis-task.md").exists()
    assert not (tmp_path / "nervis-tasks" / "clarvis-task.md").exists()
