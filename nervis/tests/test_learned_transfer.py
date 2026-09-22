"""Notes crossing the link — the third thing that does, after settings and conversations.

`nervis/knowledge/learned.md` is git-ignored: the repository is public, and what somebody told
NERVIS to remember is theirs (owner's decision, 23 September 2026). That decision costs
something, and this is what pays it — *"but make it so we can share it to the ThinkPad through
the link"*. Without it, a fact taught to one computer would be known only there.

The rules held here: what a note says is its identity, so the same fact taught twice is one
note; the date and the sentence that prompted it travel with it; only what was ticked crosses;
and nothing here is ever changed or removed by a pull.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis import learned
from nervis.api import learned as api
from nervis.app import create_app
from nervis.config import Settings


@pytest.fixture()
def here(tmp_path: Path, monkeypatch: Any) -> Path:
    """A knowledge directory this test owns, so nothing reaches the real notes."""
    root = tmp_path / "knowledge"
    root.mkdir()
    monkeypatch.setattr(learned, "KNOWLEDGE_ROOT", root, raising=False)
    monkeypatch.setattr(learned, "path", lambda root=None: root_path(root, tmp_path))
    return tmp_path


def root_path(root: Any, tmp_path: Path) -> Path:
    return (Path(root) if root else tmp_path / "knowledge") / learned.LEARNED


@pytest.fixture()
def client(tmp_path: Any) -> Any:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"), workspace_path=str(tmp_path), _env_file=None,
    )
    with TestClient(create_app(settings)) as made:
        yield made


def theirs(*notes: tuple[str, str]) -> dict[str, Any]:
    """What the other computer's `GET /api/v1/learned` answers with."""
    return {"items": [{"heading": heading, "body": body, "learned_on": "2026-09-01",
                       "prompted_by": f"remember that {body}"}
                      for heading, body in notes], "count": len(notes)}


def test_what_the_other_computer_knows_and_this_one_does_not(here: Path) -> None:
    learned.remember("The GPU box", "has an RX 6800", "remember that…", root=here / "knowledge")

    changes = learned.differences(
        theirs(("The GPU box", "has an RX 6800"), ("The ThinkPad", "runs CachyOS"))["items"],
        learned.notes(here / "knowledge"),
    )

    assert [one["heading"] for one in changes] == ["The ThinkPad"]
    assert changes[0]["state"] == "new"


def test_the_same_fact_taught_on_both_computers_is_one_note(here: Path) -> None:
    """Matched on what was said, not on when — the dates and the prompting sentences differ."""
    learned.remember("The GPU box", "has an  RX 6800 ", "one sentence",
                     root=here / "knowledge", today="2026-08-01")

    changes = learned.differences(theirs(("The GPU box", "has an RX 6800"))["items"],
                                  learned.notes(here / "knowledge"))

    assert changes == [], "same heading, same words: the same note"


def test_a_second_note_under_a_known_heading_is_offered_as_one(here: Path) -> None:
    learned.remember("The GPU box", "has an RX 6800", "…", root=here / "knowledge")

    changes = learned.differences(theirs(("The GPU box", "lives in the cupboard"))["items"],
                                  learned.notes(here / "knowledge"))

    assert [one["state"] for one in changes] == ["another"], (
        "the file is append-only on purpose — two notes under one heading are two notes"
    )


def test_a_note_that_crosses_keeps_its_date_and_what_prompted_it(here: Path) -> None:
    """A fact learned on Sunday is a fact from Sunday wherever it is read."""
    written = learned.take([{"heading": "The ThinkPad", "body": "runs CachyOS",
                             "learned_on": "2026-09-01", "prompted_by": "remember that it runs"}],
                           root=here / "knowledge")

    assert [note.learned_on for note in written] == ["2026-09-01"]
    assert written[0].prompted_by == "remember that it runs"
    (kept,) = learned.notes(here / "knowledge")
    assert kept.learned_on == "2026-09-01", "and that is what the file says too"


def test_a_note_with_nothing_in_it_is_skipped_rather_than_written(here: Path) -> None:
    written = learned.take([{"heading": "", "body": "no heading"},
                            {"heading": "fine", "body": "kept"}], root=here / "knowledge")

    assert [note.heading for note in written] == ["fine"]


def test_nothing_ticked_brings_nothing(client: Any) -> None:
    answer = client.post("/api/v1/learned/peer", json={"ids": []}).json()

    assert answer["ok"] is False and "nothing was ticked" in answer["detail"]


def test_only_the_ticked_notes_are_written(client: Any, monkeypatch: Any) -> None:
    """The body names which; the other computer supplies what."""
    monkeypatch.setattr(api, "peer_json", lambda *_, **__: (
        theirs(("The GPU box", "has an RX 6800"), ("The ThinkPad", "runs CachyOS")), ""))
    written: list[dict[str, Any]] = []
    monkeypatch.setattr(api.learned, "take", lambda chosen, **_: (written.extend(chosen), [])[1])

    wanted = learned.note_id("The ThinkPad", "runs CachyOS")
    client.post("/api/v1/learned/peer", json={"ids": [wanted]})

    assert [one["heading"] for one in written] == ["The ThinkPad"]


def test_a_sleeping_computer_is_an_answer_not_an_error(client: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(api, "peer_json", lambda *_, **__: (None, "it did not answer"))

    response = client.get("/api/v1/learned/peer")

    assert response.status_code == 200
    assert response.json()["reachable"] is False


def test_the_preview_counts_what_is_already_here(client: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(api, "peer_json", lambda *_, **__: (
        theirs(("A", "one"), ("B", "two")), ""))
    monkeypatch.setattr(api.learned, "notes", lambda *_, **__: [])

    answer = client.get("/api/v1/learned/peer").json()

    assert answer["reachable"] is True
    assert len(answer["changes"]) == 2 and answer["same"] == 0
