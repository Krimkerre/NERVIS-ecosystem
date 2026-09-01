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

from nervis import commands, handoff
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


def test_a_question_about_clarvis_is_not_a_handoff() -> None:
    """The same trap M23's learning had: this operation takes the rest of the
    sentence as its content, so a question mark makes the content a question."""
    for asked in ("what did clarvis do?", "get clarvis to what?", "have clarvis it"):
        assert commands.propose(asked, [], clarvis=OPEN_EDITOR) is None


# ── The destination, checked as far as it can be ────────────────────────────


def test_no_editor_means_nothing_would_read_it() -> None:
    offer = commands.propose("get clarvis to fix it", [], clarvis={"registered": False})
    assert offer is not None
    assert not offer.ready
    assert "no Clarvis window has registered" in offer.detail


def test_a_label_that_disagrees_is_refused() -> None:
    """The case a label exists to catch: a task landing in the wrong project."""
    offer = commands.propose("get clarvis to fix it", [], clarvis={
        "registered": True, "workspace_label": "other", "nervis_workspace": "coding",
    })
    assert offer is not None and not offer.ready
    assert "not looking" in offer.detail


def test_a_label_that_agrees_says_where_it_lands() -> None:
    offer = commands.propose("get clarvis to fix it", [], clarvis={
        "registered": True, "workspace_label": "coding", "nervis_workspace": "coding",
    })
    assert offer is not None and offer.ready
    assert "coding" in offer.detail


def test_without_a_label_the_offer_says_it_cannot_tell() -> None:
    """**The clause this milestone had to correct.**

    It read *a mismatch is refused at proposal time*, which is unachievable:
    `CLARVIS.md` §6.1 keeps the raw workspace path and name private by default
    and salts `workspace_id`, so NERVIS cannot compare directories. Saying so is
    the honest answer; claiming alignment would be a guarantee nothing checked.
    """
    offer = commands.propose("get clarvis to fix it", [], clarvis=OPEN_EDITOR)
    assert offer is not None
    assert offer.ready, "not knowing is not a reason to refuse"
    assert "cannot confirm" in offer.detail
    assert "private by default" in offer.detail


# ── What is written ─────────────────────────────────────────────────────────


def test_the_task_is_written_where_clarvis_reads(client: TestClient) -> None:
    answer = client.post("/api/v1/commands/run", json={
        "operation": "nervis.clarvis.task",
        "target": "add a retry to the uploader when the API returns 429",
        "conversation_id": "cv_ab12",
    })
    assert answer.status_code == 200
    assert answer.json()["file"]["name"] == handoff.TASK_FILE

    root = Path(client.app.state.settings.workspace_path)  # type: ignore[attr-defined]
    waiting = handoff.waiting(root)
    assert waiting is not None
    assert waiting.task == "add a retry to the uploader when the API returns 429"
    assert waiting.conversation == "cv_ab12"


def test_the_file_says_who_wrote_it(tmp_path: Path) -> None:
    """Provenance is the whole security story on the other side.

    Matched on the content rather than the filename, so a file renamed by hand
    still says what it is — and one somebody wrote themselves and happened to
    name this is not claimed as another program's work.
    """
    handoff.write(tmp_path, "fix the export bug")
    text = (tmp_path / handoff.TASK_FILE).read_text(encoding="utf-8")
    assert handoff.MARKER in text
    assert "read it before approving" in text

    (tmp_path / handoff.TASK_FILE).write_text("# mine, actually", encoding="utf-8")
    assert handoff.waiting(tmp_path) is None


def test_a_handoff_with_no_conversation_stops_at_the_timestamp(tmp_path: Path) -> None:
    """The case both sides' fixtures missed, found by parsing a real handoff.

    Without a conversation there is no comma after the stamp, and a read bounded
    by `[^,_]*` ran on into the sentence. Clarvis then showed the person a task
    that "came from NERVIS on 2026-09-01 22:21 UTC. You asked for this in
    conversation rather than in the editor" — which is the provenance line this
    whole design exists to get right.
    """
    handoff.write(tmp_path, "fix the export bug", today="2026-09-01 22:21 UTC")
    waiting = handoff.waiting(tmp_path)
    assert waiting is not None
    assert waiting.asked_on == "2026-09-01 22:21 UTC"
    assert waiting.conversation == ""


def test_a_second_task_replaces_an_unread_one(tmp_path: Path) -> None:
    """A queue of tasks nobody read is a queue, and this is a handoff."""
    handoff.write(tmp_path, "first thing")
    handoff.write(tmp_path, "second thing")
    waiting = handoff.waiting(tmp_path)
    assert waiting is not None and waiting.task == "second thing"


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
