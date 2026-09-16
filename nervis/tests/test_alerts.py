"""§18's other notices (`nervis/src/nervis/alerts.py`): which events and readings become notes.

Each trigger is driven with its own events or readings and a hand-held clock, and the
point of most tests is the second half — that a condition which persists, repeats or
wobbles files **one** note, because §18 says to keep notification volume low. The last
test runs the real application and shows a stored event reaching the notification centre.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis import alerts
from nervis.alerts import Alerts, Reading
from nervis.app import create_app
from nervis.config import Settings

GB = 1024**3


def watched() -> tuple[Alerts, list[dict[str, Any]]]:
    notes: list[dict[str, Any]] = []
    return Alerts(lambda **note: notes.append(note)), notes


def event(kind: str, **data: Any) -> dict[str, Any]:
    return {"event_type": kind, "data": data, "source": {"instance_id": "win-1"}}


# ── Benchmarks ──────────────────────────────────────────────────────────────


def test_a_finished_benchmark_is_noted_once_per_run() -> None:
    watch, notes = watched()
    watch.on_event(event("sirvis.benchmark.completed", run_id="run_1", model_key="qwen3-4b"), 0)
    watch.on_event(event("sirvis.benchmark.completed", run_id="run_1", model_key="qwen3-4b"), 1)
    watch.on_event(event("sirvis.benchmark.started", run_id="run_2"), 2)

    assert [n["title"] for n in notes] == ["Benchmark of qwen3-4b finished"]
    assert notes[0]["kind"] == "benchmark_finished" and notes[0]["severity"] == "info"
    assert "run_1" in notes[0]["reason"]


def test_a_failed_benchmark_says_why_and_warns() -> None:
    watch, notes = watched()
    watch.on_event(
        event(
            "sirvis.benchmark.failed",
            run_id="run_9",
            model_key="m",
            detail="LM Studio stopped answering",
        ),
        0,
    )
    assert notes[0]["title"] == "Benchmark of m failed"
    assert notes[0]["severity"] == "warning"
    assert notes[0]["body"] == "LM Studio stopped answering"


# ── Route failures ──────────────────────────────────────────────────────────


def failed(watch: Alerts, now: float) -> None:
    watch.on_event(event("ravis.request.completed", succeeded=False, cancelled=False), now)


def test_a_spike_is_several_failures_that_are_most_of_what_happened() -> None:
    watch, notes = watched()
    for second in range(4):
        failed(watch, second)
    assert notes == [], "four failures are not yet a spike"
    watch.on_event(event("ravis.route.refused"), 5)
    assert [n["kind"] for n in notes] == ["route_failures"]
    assert notes[0]["reason"].startswith("5 of the last 5 requests failed or were refused")


def test_a_spike_is_noted_once_until_a_quiet_half_hour_has_passed() -> None:
    watch, notes = watched()
    for second in range(10):
        failed(watch, second)
    assert len(notes) == 1
    for second in range(600, 610):
        failed(watch, second)
    assert len(notes) == 1, "still inside the quiet period"
    for second in range(2000, 2005):
        failed(watch, second)
    assert len(notes) == 2


def test_failures_among_many_successes_are_not_a_spike() -> None:
    watch, notes = watched()
    for second in range(20):
        watch.on_event(event("ravis.request.completed", succeeded=True, cancelled=False), second)
    for second in range(20, 26):
        failed(watch, second)
    assert notes == []


def test_old_failures_and_cancellations_do_not_count() -> None:
    watch, notes = watched()
    for second in range(4):
        failed(watch, second)
    failed(watch, 400)
    for second in range(401, 410):
        watch.on_event(event("ravis.request.completed", succeeded=False, cancelled=True), second)
    assert notes == []


# ── A Clarvis gate left waiting ─────────────────────────────────────────────


def gate(
    watch: Alerts, kind: str, now: float, activity: str = "a1", awaiting: str = "command"
) -> None:
    watch.on_event(event(f"clarvis.gate.{kind}", activity_id=activity, awaiting=awaiting), now)


def test_a_gate_waiting_past_two_minutes_is_noted_once_with_its_window() -> None:
    watch, notes = watched()
    gate(watch, "requested", 0)
    watch.on_reading(Reading(now=60, window_labels={"win-1": "Clarvis Bridge · win-1"}))
    assert notes == []
    watch.on_reading(Reading(now=121, window_labels={"win-1": "Clarvis Bridge · win-1"}))
    watch.on_reading(Reading(now=500, window_labels={"win-1": "Clarvis Bridge · win-1"}))

    assert [n["title"] for n in notes] == ["Clarvis is waiting for you in Clarvis Bridge · win-1"]
    assert notes[0]["reason"] == "a command's approval has been open for 2 minutes"
    assert "cannot answer" in notes[0]["body"]


def test_a_gate_answered_in_time_is_not_noted_and_a_new_wait_is_its_own() -> None:
    watch, notes = watched()
    gate(watch, "requested", 0)
    gate(watch, "resolved", 30)
    watch.on_reading(Reading(now=300))
    assert notes == []

    gate(watch, "requested", 400, activity="a2", awaiting="sensitive_read")
    watch.on_reading(Reading(now=600))
    assert notes[0]["reason"].startswith("an approval to read a sensitive file")
    assert "editor window win-1" in notes[0]["title"], "a window with no label is still named"


def test_an_unknown_gate_kind_reads_as_an_approval() -> None:
    watch, notes = watched()
    gate(watch, "requested", 0, awaiting="<img>")
    watch.on_reading(Reading(now=200))
    assert notes[0]["reason"].startswith("an approval has been open")
    assert "<img>" not in str(notes[0])


# ── Budget ──────────────────────────────────────────────────────────────────


def budget(band: str) -> dict[str, Any]:
    return {
        "band": band,
        "spent_estimated": 7.5,
        "limit": 10.0,
        "currency": "USD",
        "period": "monthly",
        "hard": True,
    }


def test_a_budget_is_noted_each_time_it_rises_a_band_and_never_as_it_falls() -> None:
    watch, notes = watched()
    for band in (
        "NORMAL",
        "PREFER_CHEAPER",
        "PREFER_CHEAPER",
        "STRONG_PENALTY",
        "NORMAL",
        "PREFER_CHEAPER",
        "EXHAUSTED",
        "bogus",
    ):
        watch.on_reading(Reading(now=0, budget=budget(band)))

    assert [n["title"] for n in notes] == [
        "RAVIS budget: 70% of the budget is spent; RAVIS now prefers cheaper models",
        "RAVIS budget: 90% of the budget is spent; RAVIS now strongly avoids paid models",
        "RAVIS budget: 70% of the budget is spent; RAVIS now prefers cheaper models",
        "RAVIS budget: the budget is spent",
    ]
    assert "7.5 of 10.0 USD for its monthly budget" in notes[0]["reason"]
    assert "never an invoice" in notes[0]["body"]
    assert "Paid models are blocked." in notes[-1]["body"]


def test_no_budget_configured_says_nothing() -> None:
    watch, notes = watched()
    watch.on_reading(Reading(now=0, budget=None))
    assert notes == []


# ── Memory and swap ─────────────────────────────────────────────────────────


def test_memory_pressure_is_noted_when_it_begins_and_again_only_after_it_ended() -> None:
    watch, notes = watched()
    for pressure in (False, True, True, None, True, False, True):
        watch.on_reading(Reading(now=0, memory_under_pressure=pressure, memory_detail="8 GB free"))
    assert [n["kind"] for n in notes] == ["memory_pressure", "memory_pressure"]
    assert notes[0]["body"].startswith("8 GB free ")


def test_swap_growing_fast_is_a_warning_and_a_slow_climb_is_not() -> None:
    watch, notes = watched()
    for minute in range(0, 60, 5):
        watch.on_reading(Reading(now=minute * 60, swap_used=5 * GB + minute * GB // 20))
    assert notes == [], "a quarter of a gigabyte every five minutes"

    watch.on_reading(Reading(now=3700, swap_used=9 * GB))
    assert notes == [], "the old readings have aged out of the window"
    watch.on_reading(Reading(now=3900, swap_used=12 * GB))
    assert [n["kind"] for n in notes] == ["swap_warning"]
    assert "grew by 4.2 GB within 10 minutes" in notes[0]["reason"], (
        "from the lowest reading in the window"
    )

    watch.on_reading(Reading(now=4000, swap_used=15 * GB))
    assert len(notes) == 1, "one warning an hour"


def test_readings_not_taken_are_skipped() -> None:
    watch, notes = watched()
    watch.on_reading(Reading(now=0))
    watch.on_event({"event_type": "ravis.request.completed", "data": "not a mapping"}, 0)
    watch.on_event({}, 0)
    assert notes == []


def test_every_budget_band_above_normal_has_words() -> None:
    assert set(alerts.BUDGET_WORDS) == set(alerts.BUDGET_BANDS) - {"NORMAL"}


# ── Through the application ─────────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path: Any) -> Any:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"),
        workspace_path=str(tmp_path),
        _env_file=None,
    )
    with TestClient(create_app(settings)) as made:
        yield made


def test_a_stored_event_reaches_the_notification_centre(client: TestClient) -> None:
    client.app.state.hub.ingest(
        {  # type: ignore[attr-defined]
            "event_id": "ev-bench-1",
            "event_type": "sirvis.benchmark.completed",
            "occurred_at": "2026-09-16T12:00:00Z",
            "source": {"service_type": "sirvis"},
            "data": {"run_id": "run_live", "model_key": "granite"},
        }
    )
    found: list[dict[str, Any]] = []
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not found:
        items = client.get("/api/v1/notifications").json()["items"]
        found = [n for n in items if n["kind"] == "benchmark_finished"]
        time.sleep(0.05)
    assert found and found[0]["title"] == "Benchmark of granite finished"
    assert found[0]["source"] == "sirvis"
