"""Tasks NERVIS hands to Clarvis, followed to the end (NERVIS M27, CLARVIS.md §6.4).

The owner decided on 16 September 2026 that a Clarvis "task" is a handover from NERVIS.
NERVIS writes an id into the brief and into its record of the handover; Clarvis names that
id in `clarvis.task.*` as the task is planned, built, paused and finished; and
`/api/v1/handovers` joins the two across every editor window.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis import clarvis, handoff
from nervis.app import create_app
from nervis.config import Settings

ID = re.compile(r"^nt_[0-9a-f]{16}$")


@pytest.fixture()
def client(tmp_path: Any) -> Any:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"),
        workspace_path=str(tmp_path),
        _env_file=None,
    )
    with TestClient(create_app(settings)) as made:
        yield made


def hand_over(client: TestClient, name: str) -> dict[str, Any]:
    answer = client.post("/api/v1/commands/run", json={
        "operation": "nervis.clarvis.task", "target": f"build the {name} please", "name": name,
    })
    assert answer.status_code == 200, answer.text
    return dict(answer.json())


def report(client: TestClient, window: str, event_type: str, number: int, **data: Any) -> None:
    client.app.state.hub.ingest({  # type: ignore[attr-defined]
        "event_id": f"ev-{window}-{number}",
        "event_type": event_type,
        "occurred_at": f"2026-09-16T10:00:{number:02d}Z",
        "source": {"service_type": "clarvis", "instance_id": window},
        "data": data,
    })


def test_a_handover_carries_an_id_in_the_brief_and_back(tmp_path: Path) -> None:
    written = handoff.write(tmp_path, "add a retry to the uploader")
    assert ID.match(written.task_id)
    text = (tmp_path / written.folder / handoff.TASK_FILE).read_text(encoding="utf-8")
    assert f"<!-- nervis-task-id: {written.task_id} -->" in text
    assert text.startswith(handoff.MARKER), "the marker still leads the file"
    waiting = handoff.waiting(tmp_path / written.folder)
    assert waiting is not None
    assert (waiting.task_id, waiting.task) == (written.task_id, "add a retry to the uploader")
    assert handoff.write(tmp_path, "another one").task_id != written.task_id


def test_a_brief_from_before_ids_reads_with_none(tmp_path: Path) -> None:
    (tmp_path / handoff.TASK_FILE).write_text(
        f"{handoff.MARKER}\n# Task from NERVIS\n\nold task\n\n---\n", encoding="utf-8")
    waiting = handoff.waiting(tmp_path)
    assert waiting is not None and waiting.task_id == ""


def test_the_handover_is_recorded_as_clarvis_s_with_its_id_and_folder(client: TestClient) -> None:
    answer = hand_over(client, "pomodoro timer")
    task_id = answer["handoff"]["task_id"]
    assert ID.match(task_id)

    [record] = client.app.state.hub.query(  # type: ignore[attr-defined]
        event_type="nervis.command.attempted", latest=True)
    assert record["data"]["operation"] == clarvis.HANDOVER_OPERATION
    assert record["subject"]["id"] == "clarvis", "not a SIRVIS benchmark, as it was filed until now"
    assert (record["data"]["task_id"], record["data"]["folder"]) == (
        task_id, "nervis-tasks/pomodoro-timer")


def test_handovers_follow_clarvis_across_windows_to_the_end(client: TestClient) -> None:
    first = hand_over(client, "pomodoro timer")["handoff"]["task_id"]
    second = hand_over(client, "csv export")["handoff"]["task_id"]
    untouched = hand_over(client, "dark mode")["handoff"]["task_id"]

    # Picked up and planned in one window, built after a reload in another.
    report(client, "win-a", clarvis.TASK_STARTED, 1, task_id=first, stage="planning")
    report(client, "win-b", clarvis.TASK_STARTED, 2, task_id=first, stage="building")
    report(client, "win-b", clarvis.TASK_COMPLETED, 3, task_id=first, outcome="built")
    report(client, "win-b", clarvis.TASK_STARTED, 4, task_id=second, stage="planning")
    report(client, "win-b", clarvis.TASK_STARTED, 5, task_id=second, stage="building")
    report(client, "win-b", clarvis.TASK_STARTED, 6, task_id=second, stage="paused")
    unrecorded = "nt_00000000000000ff"
    report(client, "win-c", clarvis.TASK_STARTED, 7, task_id=unrecorded, stage="planning")

    items = {h["task_id"]: h for h in client.get("/api/v1/handovers").json()["items"]}

    assert items[first]["state"] == "completed" and items[first]["outcome"] == "built"
    assert items[first]["started_at"] == "2026-09-16T10:00:01Z", "the first pickup"
    assert items[first]["folder"] == "nervis-tasks/pomodoro-timer"
    assert (items[second]["state"], items[second]["stage"]) == ("running", "paused")
    assert items[second]["updated_at"] == "2026-09-16T10:00:06Z"
    assert (items[untouched]["state"], items[untouched]["stage"]) == ("waiting", "")
    assert items[unrecorded]["folder"] == "", "reported without a handover record"
    assert set(items) == {first, second, untouched, unrecorded}


def test_a_stage_clarvis_does_not_have_is_not_shown() -> None:
    found = clarvis.tasks([{"event_type": clarvis.TASK_STARTED, "occurred_at": "t",
                            "data": {"task_id": "t1", "stage": "<img>"}}])
    assert found[0]["stage"] == ""


def test_other_command_records_are_not_handovers() -> None:
    benchmark = {"operation": "sirvis.benchmark.submit", "outcome": "queued",
                 "task_id": "nt_0000000000000001"}
    refused = {"operation": clarvis.HANDOVER_OPERATION, "outcome": "refused",
               "task_id": "nt_0000000000000002"}
    audits = [{"occurred_at": "t1", "data": benchmark}, {"occurred_at": "t2", "data": refused}]
    assert clarvis.handovers(audits, []) == []
