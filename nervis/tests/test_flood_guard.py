"""The flood guard (NERVIS 0.31.0): one service cannot fill the event hub.

**Why these tests exist.** On 14 September 2026, from 23:32 to 23:39, a RAVIS 0.26.0
bug flipped Codex between "ready" and "switching skills" about a hundred times a
second (274 at the peak). Every flip was a real state change, so RAVIS published every
one, NERVIS stored every one, the 50,000-event retention cap filled, and the whole
history from 4 to 14 September was pushed out — nine other events were left. The
consecutive events were **not identical**: they alternated between two states.

The owner decided on 15 September that NERVIS collapses a burst of identical events
into one with a count, limits how fast any single source can fill the store so a quiet
service's history survives, and says on the dashboard when the guard kicked in. These
tests hold the guard to each clause, starting with the measured shape.

A fake monotonic clock drives the hub, so minutes of flood run in a second.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.testclient import TestClient

from nervis.app import create_app
from nervis.config import Settings
from nervis.events import SUBSCRIBER_BUFFER, Hub
from nervis.flood import GUARD_EVENT, GuardLimits, limits_from
from nervis.situation import _event_summary
from nervis.storage import prepare_database


class Clock:
    """Monotonic seconds that move only when a test moves them."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def a_guarded_hub(
    clock: Clock, *, retention_events: int = 50_000, retention_days: float = 14.0,
    **limits: Any,
) -> Hub:
    return Hub(
        prepare_database(":memory:"), retention_events=retention_events,
        retention_days=retention_days, guard=GuardLimits(**limits), clock=clock,
    )


def an_api() -> TestClient:
    """A NERVIS with the guard as the running service has it, and no peers."""
    return TestClient(create_app(Settings(
        database_path=":memory:",
        ravis_base_url="http://127.0.0.1:9", sirvis_base_url="http://127.0.0.1:9",
        clarvis_base_url="http://127.0.0.1:9", lmstudio_base_url="http://127.0.0.1:9",
        ollama_base_url="http://127.0.0.1:9",
        _env_file=None,  # type: ignore[call-arg]
    )))


# The sources as the live store recorded them.
RAVIS = {"service_type": "ravis", "service_id": "ravis-6b773addc83d", "instance_id": "",
         "machine_id": "1740cb6e173b524cb4110d52892212d6"}
SIRVIS = {"service_type": "sirvis", "service_id": "sirvis-0bb99b67716c", "instance_id": "",
          "machine_id": "5d94ae1a93544c2ba045ca191ec461b3"}
NERVIS = {"service_type": "nervis"}

# The two states the loop alternated between, as RAVIS published them.
TO_DOWN = {"from": "signed_in", "to": "runtime_down", "reason_code": "skills_changed"}
TO_UP = {"from": "runtime_down", "to": "signed_in", "reason_code": "skills"}
CODEX_STATE = "ravis.codex.state_changed"

_ids = itertools.count()


def event(source: dict[str, Any], event_type: str, data: dict[str, Any],
          **extra: Any) -> dict[str, Any]:
    """One envelope with a fresh `event_id`, as a producer would send it."""
    return {
        "event_id": f"ev-{next(_ids):09d}", "event_type": event_type, "event_version": "1.0.0",
        "occurred_at": "2026-09-14T21:32:19.654Z", "severity": "info",
        "source": dict(source), "data": dict(data),
        "privacy": {"classification": "operational", "redactions": []}, **extra,
    }


def flip(n: int) -> dict[str, Any]:
    """One flip of the measured loop: alternating states, each with its own trace."""
    return event(RAVIS, CODEX_STATE, TO_DOWN if n % 2 == 0 else TO_UP,
                 trace_id=uuid.uuid4().hex)


def rows_from(hub: Hub, service_type: str) -> int:
    row = hub._database.connection.execute(  # the store itself, not a bounded read
        "SELECT COUNT(*) AS n FROM event WHERE service_type = ? AND event_type != ?",
        (service_type, GUARD_EVENT),
    ).fetchone()
    return int(row["n"])


def guard_events(hub: Hub, phase: str) -> list[dict[str, Any]]:
    return [e for e in hub.query(event_type=GUARD_EVENT, limit=1000)
            if e["data"].get("phase") == phase]


def drain(queue: asyncio.Queue[dict[str, Any]]) -> list[dict[str, Any]]:
    return [queue.get_nowait() for _ in range(queue.qsize())]


# ── The night it was built for ─────────────────────────────────────────────


def test_a_looping_producer_cannot_push_out_a_quiet_service_s_history() -> None:
    """The measured shape: alternating states at a hundred a second from one source.

    A store capped at 1,000 events holds a quiet service's 300. Two minutes of the
    loop is 12,000 events — twelve times the cap, so unguarded it would push every
    one of SIRVIS's events out, exactly as 14 September lost 4–14 September.
    Retention runs every twenty seconds, as the probe timer runs it.
    """
    clock = Clock()
    hub = a_guarded_hub(clock, retention_events=1_000)
    for n in range(300):
        hub.ingest(event(SIRVIS, "sirvis.recommendation.created", {"records": n}))
        clock.advance(30.0)
    quiet = [e["event_id"] for e in hub.query(service="sirvis", limit=1000)]

    last = 12_000
    for n in range(last):
        hub.ingest(flip(n))
        clock.advance(0.01)
        if n % 2_000 == 1_999:
            hub.enforce_retention()
    ending = event(RAVIS, CODEX_STATE, {"from": "runtime_down", "to": "ready"})
    hub.ingest(ending)
    hub.enforce_retention()

    assert [e["event_id"] for e in hub.query(service="sirvis", limit=1000)] == quiet, (
        "the quiet service's history was pushed out by another service's loop"
    )
    # A burst's worth at once, then twelve a minute: 120 + 24 for two minutes, plus
    # the final state. Unguarded this is 12,001.
    assert rows_from(hub, "ravis") <= 150

    # The loop stops. Once its kind has been quiet for five seconds, the state it
    # ended on is what the store's newest row of that kind says.
    clock.advance(6.0)
    hub.settle()
    assert hub.query(event_type=CODEX_STATE, latest=True, limit=1)[0]["data"] == ending["data"]


def test_the_guard_says_once_that_it_engaged_and_once_that_it_released() -> None:
    """One event each way, saying which service, why, when, and how much was held."""
    clock = Clock()
    hub = a_guarded_hub(clock, burst=5, per_minute=0.0)
    for n in range(40):
        hub.ingest(flip(n))
        clock.advance(0.01)

    engaged = guard_events(hub, "engaged")
    assert len(engaged) == 1
    assert engaged[0]["severity"] == "warning", "chat's 'what has gone wrong' reads warnings"
    assert engaged[0]["data"]["service_type"] == "ravis"
    assert engaged[0]["data"]["service_id"] == "ravis-6b773addc83d"
    assert engaged[0]["data"]["reason"] == "rate"
    assert "faster than NERVIS keeps them" in engaged[0]["data"]["detail"]
    active = hub.guard_report()["active"]
    assert [a["service_id"] for a in active] == ["ravis-6b773addc83d"]
    assert active[0]["held_back"] > 0

    clock.advance(6.0)
    hub.settle()
    assert guard_events(hub, "released") == [], "released before it had been calm"
    clock.advance(300.0)
    hub.settle()
    hub.settle()

    released = guard_events(hub, "released")
    assert len(released) == 1 and len(guard_events(hub, "engaged")) == 1
    data = released[0]["data"]
    # Every arrival is either its own row or held back — none is unaccounted for.
    assert data["held_back"] == 40 - rows_from(hub, "ravis")
    assert data["held_back"] == data["collapsed"] + data["thinned"]
    assert data["types"] == {CODEX_STATE: data["held_back"]}
    assert data["since"] and data["until"] and data["stopped"] is False
    assert hub.guard_report()["active"] == []


# ── Identical bursts ───────────────────────────────────────────────────────


def test_identical_events_are_stored_once_with_a_count_and_the_last_time_seen() -> None:
    clock = Clock()
    hub = a_guarded_hub(clock)
    listening = hub.subscribe()
    same = {"from": "healthy", "to": "degraded", "detail": "breaker open"}
    for _ in range(50):
        hub.ingest(event(RAVIS, "ravis.pool.members_changed", same, trace_id="t-1"))
        clock.advance(0.5)
    hub.ingest(event(RAVIS, "ravis.pool.members_changed", {"from": "degraded", "to": "healthy"}))

    rows = hub.query(limit=1000)
    assert len(rows) == 2
    assert rows[0]["_repeats"] == 50
    assert rows[0]["_last_received_at"] >= rows[0]["_received_at"]
    assert "_repeats" not in rows[1], "an event seen once carries no count"
    assert len(drain(listening)) == 2, "a subscriber got a frame per repeat"
    assert guard_events(hub, "engaged") == [], "fifty repeats are not a flood"


def test_the_same_event_in_two_traces_or_two_windows_is_two_rows() -> None:
    """Measured on the 4 September backup: the events a sixty-second window would
    merge if trace ids were ignored were distinct chat turns, two seconds apart,
    each in its own trace. Merging them drops a span from a waterfall. Nor may two
    Clarvis windows sharing a service id be merged: §6.6 keeps them apart."""
    clock = Clock()
    hub = a_guarded_hub(clock)
    turn = {"conversation_id": "", "model": "claude-haiku-4-5-20251001", "interrupted": False}
    hub.ingest(event(NERVIS, "nervis.chat.turn_completed", turn, trace_id="trace-a"))
    clock.advance(2.2)
    hub.ingest(event(NERVIS, "nervis.chat.turn_completed", turn, trace_id="trace-b"))
    window = {"service_type": "clarvis", "service_id": "5b0f5aa7"}
    for instance in ("73fd453c", "86dd34ee"):
        hub.ingest(event({**window, "instance_id": instance}, "clarvis.lifecycle.ready",
                         {"port": 51006}))

    rows = hub.query(limit=1000)
    assert len(rows) == 4
    assert not any("_repeats" in row for row in rows)


def test_a_repeat_after_the_collapse_window_is_a_new_row() -> None:
    clock = Clock()
    hub = a_guarded_hub(clock)
    same = {"port": 51006}
    hub.ingest(event(NERVIS, "nervis.test", same))
    clock.advance(61.0)
    hub.ingest(event(NERVIS, "nervis.test", same))

    assert len(hub.query(limit=1000)) == 2


def test_a_flooding_source_s_identical_events_collapse_even_with_fresh_trace_ids() -> None:
    """Under its allowance a source's events in different traces stay apart; past it,
    identical events are one row with a count whatever trace ids they carry — the
    measured loop gave every flip a fresh one."""
    clock = Clock()
    hub = a_guarded_hub(clock, burst=5, per_minute=0.0)
    same = {"from": "signed_in", "to": "runtime_down", "reason_code": "skills_changed"}
    for _ in range(50):
        hub.ingest(event(RAVIS, CODEX_STATE, same, trace_id=uuid.uuid4().hex))
    hub.settle()
    clock.advance(1.0)
    hub.settle()

    rows = hub.query(event_type=CODEX_STATE, limit=1000)
    assert len(rows) == 5
    assert rows[-1]["_repeats"] == 46


def test_counts_taken_during_a_flood_are_written_to_their_rows() -> None:
    """While a source is guarded, repeat counts wait in memory and are written on
    settle — or at once, when a newer event replaces their row as the newest of its
    type. Either way the row ends up saying how many times it arrived."""
    clock = Clock()
    hub = a_guarded_hub(clock, burst=2, per_minute=120.0)
    hub.ingest(event(RAVIS, CODEX_STATE, TO_UP))
    hub.ingest(event(RAVIS, CODEX_STATE, TO_DOWN))
    for _ in range(5):
        hub.ingest(event(RAVIS, CODEX_STATE, TO_DOWN))
    clock.advance(0.5)  # one token back, and not yet time to settle
    hub.ingest(event(RAVIS, CODEX_STATE, TO_UP))
    for _ in range(3):
        hub.ingest(event(RAVIS, CODEX_STATE, TO_UP))
    clock.advance(1.0)
    hub.settle()

    rows = hub.query(event_type=CODEX_STATE, limit=1000)
    assert [(row["data"]["to"], row.get("_repeats", 1)) for row in rows] == [
        ("signed_in", 1), ("runtime_down", 6), ("signed_in", 4),
    ]


def test_a_stored_event_makes_an_older_held_one_stale() -> None:
    """Otherwise a held event would be stored after a newer one, and the store's newest
    row of that kind would be a state the service had already left."""
    clock = Clock()
    hub = a_guarded_hub(clock, burst=1, per_minute=60.0)
    hub.ingest(event(RAVIS, "ravis.pool.members_changed", {"members": 1}))
    hub.ingest(event(RAVIS, "ravis.pool.members_changed", {"members": 2}))
    clock.advance(1.0)
    hub.ingest(event(RAVIS, "ravis.pool.members_changed", {"members": 3}))
    clock.advance(6.0)
    hub.settle()

    newest = hub.query(event_type="ravis.pool.members_changed", latest=True, limit=1)[0]
    assert newest["data"] == {"members": 3}
    assert rows_from(hub, "ravis") == 2


def test_final_states_past_the_share_are_bounded_too() -> None:
    """The last event of each kind is stored even past the daily share, but only one
    burst's worth: a producer inventing a new kind per event cannot use final states
    to get round the share."""
    clock = Clock()
    hub = a_guarded_hub(clock, burst=10, per_minute=6_000.0, daily_rows=50)
    # Two tokens back per arrival, so only the share can stop this source.
    for n in range(50):
        hub.ingest(event(RAVIS, "ravis.request.completed", {"request": n}))
        clock.advance(0.02)
    for kind in range(30):
        hub.ingest(event(RAVIS, f"ravis.invented.kind_{kind}", {"kind": kind}))
        clock.advance(0.02)
    clock.advance(6.0)
    hub.settle()

    assert rows_from(hub, "ravis") == 60


def test_the_running_service_stores_a_burst_s_final_state_without_being_asked() -> None:
    """The app's own one-second tick settles the guard, so the state a burst ended on
    is stored even when nothing else arrives to trigger it."""
    import time

    client = TestClient(create_app(Settings(
        database_path=":memory:", event_source_burst=5, event_guard_quiet_seconds=0.2,
        ravis_base_url="http://127.0.0.1:9", sirvis_base_url="http://127.0.0.1:9",
        clarvis_base_url="http://127.0.0.1:9", lmstudio_base_url="http://127.0.0.1:9",
        ollama_base_url="http://127.0.0.1:9",
        _env_file=None,  # type: ignore[call-arg]
    )))
    ending = event(RAVIS, CODEX_STATE, {"from": "runtime_down", "to": "ready"})
    with client:
        client.post("/api/v1/events", json=[*(flip(n) for n in range(20)), ending])
        time.sleep(1.6)
        newest = client.get(f"/api/v1/events?event_type={CODEX_STATE}&limit=1").json()["items"]

    assert newest[0]["data"] == ending["data"]


def test_a_replayed_event_id_spends_nothing() -> None:
    """A producer resending its backlog after a reconnect stores nothing new, so it
    must not use up the allowance its real events need."""
    clock = Clock()
    hub = a_guarded_hub(clock, burst=3, per_minute=0.0)
    once = event(RAVIS, "ravis.route.selected", {"selected": "qwen3-4b"})
    for _ in range(10):
        hub.ingest(dict(once))
    hub.ingest(event(RAVIS, "ravis.route.selected", {"selected": "a"}))
    hub.ingest(event(RAVIS, "ravis.route.selected", {"selected": "b"}))

    assert rows_from(hub, "ravis") == 3
    assert guard_events(hub, "engaged") == []


# ── The share of the store ─────────────────────────────────────────────────


def test_one_source_cannot_take_more_than_its_daily_share() -> None:
    """A source under its rate but over its share is still held to the share —
    plus at most one burst of final states — and gets a new share the next day."""
    clock = Clock()
    hub = a_guarded_hub(clock, burst=10, per_minute=600.0, daily_rows=50)
    for n in range(500):
        hub.ingest(event(RAVIS, "ravis.request.completed", {"request": n}))
        clock.advance(1.0)
    clock.advance(6.0)
    hub.settle()

    assert rows_from(hub, "ravis") <= 50 + 10
    assert guard_events(hub, "engaged")[0]["data"]["reason"] == "daily_share"
    newest = hub.query(service="ravis", latest=True, limit=1)[0]
    assert newest["data"] == {"request": 499}, "the last event was dropped"

    clock.advance(86_400.0)
    tomorrow = event(RAVIS, "ravis.request.completed", {"request": "tomorrow"})
    hub.ingest(tomorrow)
    assert hub.query(service="ravis", latest=True, limit=1)[0]["event_id"] == tomorrow["event_id"]


def test_a_restart_does_not_hand_a_flooding_source_a_fresh_share() -> None:
    database = prepare_database(":memory:")
    clock = Clock()
    limits = GuardLimits(burst=1_000, per_minute=6_000.0, daily_rows=40)
    first = Hub(database, guard=limits, clock=clock)
    for n in range(40):
        first.ingest(event(RAVIS, "ravis.route.selected", {"request": n}))

    restarted = Hub(database, guard=limits, clock=clock)
    restarted.ingest(event(RAVIS, "ravis.route.selected", {"request": "after the restart"}))

    assert rows_from(restarted, "ravis") == 40
    assert guard_events(restarted, "engaged")[0]["data"]["reason"] == "daily_share"


# ── The guard's own events ─────────────────────────────────────────────────


def test_the_guard_s_own_events_spend_no_allowance_and_trigger_no_guard() -> None:
    """Ten flooding services is twenty guard events from NERVIS, with NERVIS allowed
    three at once. Were they metered, NERVIS would be guarding itself and its own
    next events would be held back."""
    clock = Clock()
    hub = a_guarded_hub(clock, burst=3, per_minute=0.0)
    for s in range(10):
        loop = {"service_type": "loop", "service_id": f"loop-{s}"}
        for n in range(6):
            hub.ingest(event(loop, "loop.tick", {"tick": n}))
    clock.advance(6.0)
    hub.settle()
    clock.advance(301.0)
    hub.settle()

    assert len(guard_events(hub, "engaged")) == 10
    assert len(guard_events(hub, "released")) == 10
    for n in range(3):
        hub.emit("nervis.test", data={"n": n})
    assert len(hub.query(event_type="nervis.test", limit=10)) == 3
    assert all(a["service_type"] != "nervis" for a in hub.guard_report()["active"])


def test_an_event_posted_under_the_guard_s_name_is_guarded_like_any_other() -> None:
    """Exempt by code path, not by name: a producer calling its events
    `nervis.events.flood_guarded` does not get past the guard."""
    clock = Clock()
    hub = a_guarded_hub(clock, burst=2, per_minute=0.0)
    for n in range(8):
        hub.ingest(event(NERVIS, GUARD_EVENT, {"phase": "forged", "n": n}))

    assert len(guard_events(hub, "forged")) == 2
    assert len(guard_events(hub, "engaged")) == 1


# ── Producers ──────────────────────────────────────────────────────────────


def test_a_flooding_producer_still_gets_202_and_every_event_is_accepted() -> None:
    """The publisher retries a refusal, so a refusal would make a loop loop harder."""
    with an_api() as client:
        batch = [flip(n) for n in range(300)]
        answered = client.post("/api/v1/events", json=batch)
        assert answered.status_code == 202
        assert answered.json() == {"accepted": 300, "rejected": []}
        for n in range(300, 340):
            one = client.post("/api/v1/events", json=flip(n))
            assert one.status_code == 202 and one.json() == {"accepted": 1, "rejected": []}

        read = client.get("/api/v1/events?limit=1000").json()

    ravis = [e for e in read["items"] if e["event_type"] == CODEX_STATE]
    assert len(ravis) <= 125, "the guard in the running service let the flood through"
    guard = read["guard"]
    assert guard["on"] is True
    assert [a["service_id"] for a in guard["active"]] == ["ravis-6b773addc83d"]
    assert guard["active"][0]["held_back"] > 0
    assert [e["data"]["phase"] for e in guard["recent"]] == ["engaged"]


def test_what_the_guard_still_holds_is_stored_when_nervis_stops() -> None:
    client = an_api()
    ending = event(RAVIS, CODEX_STATE, {"from": "runtime_down", "to": "ready"})
    with client:
        client.post("/api/v1/events", json=[*(flip(n) for n in range(200)), ending])
        hub = client.app.state.hub  # type: ignore[attr-defined]

    assert hub.query(event_type=CODEX_STATE, latest=True, limit=1)[0]["data"] == ending["data"]
    released = guard_events(hub, "released")
    assert len(released) == 1 and released[0]["data"]["stopped"] is True


def test_the_running_service_uses_the_numbers_config_documents() -> None:
    """120 at once, 12 a minute, 5% of the 50,000 cap a day, 60 s to collapse."""
    assert limits_from(Settings(_env_file=None)) == GuardLimits(  # type: ignore[call-arg]
        burst=120, per_minute=12.0, daily_rows=2_500,
        collapse_seconds=60.0, quiet_seconds=5.0, release_seconds=300.0,
    )


# ── Live subscribers ───────────────────────────────────────────────────────


def test_live_subscribers_get_what_is_stored_and_nothing_that_was_held_back() -> None:
    clock = Clock()
    hub = a_guarded_hub(clock, burst=20, per_minute=0.0)
    listening = hub.subscribe()
    for n in range(200):
        hub.ingest(flip(n))
        clock.advance(0.01)
    clock.advance(6.0)
    hub.settle()

    frames = drain(listening)
    assert [f["_sequence"] for f in frames] == [e["_sequence"] for e in hub.query(limit=1000)]
    assert len(frames) < 30


def test_a_flood_does_not_cut_an_open_stream_loose() -> None:
    """Through the real SSE endpoint. Unguarded, 1,000 flips overrun the subscriber's
    256-frame buffer and the stream ends on a gap frame; guarded, it carries the
    rows that were stored and stays open."""
    from nervis.api.events import stream

    async def exercise() -> tuple[list[bytes], int]:
        clock = Clock()
        hub = a_guarded_hub(clock)

        class _Request:
            app = type("_App", (), {"state": type("_S", (), {"hub": hub})()})()
            headers: dict[str, str] = {}
            query_params: dict[str, str] = {}

        frames = (await stream(_Request())).body_iterator  # type: ignore[arg-type]
        while b"ecosystem.stream.live" not in await frames.__anext__():  # type: ignore[attr-defined]
            pass
        before = hub.latest_sequence()
        for n in range(1_000):
            hub.ingest(flip(n))
            clock.advance(0.01)
        waiting = sum(queue.qsize() for queue in hub._subscribers)
        seen = [await asyncio.wait_for(frames.__anext__(), timeout=2)  # type: ignore[attr-defined]
                for _ in range(waiting)]
        return seen, hub.latest_sequence() - before

    seen, stored = asyncio.run(exercise())

    assert stored < SUBSCRIBER_BUFFER
    assert not any(b"ecosystem.stream.gap" in frame for frame in seen)
    assert len(seen) == stored


# ── Retention and chat ─────────────────────────────────────────────────────


def test_retention_by_age_still_works_with_the_guard_on() -> None:
    clock = Clock()
    hub = a_guarded_hub(clock, retention_days=7.0)
    for _ in range(3):
        hub.ingest(event(RAVIS, "ravis.credential.set", {"provider": "openai"}))
    hub.ingest(event(SIRVIS, "sirvis.benchmark.completed", {"run": 1}))

    assert hub.enforce_retention() == 0
    later = datetime.now(timezone.utc) + timedelta(days=8)
    assert hub.enforce_retention(now=later) == 2
    assert hub.query(limit=1000) == []


def test_chat_counts_a_collapsed_row_as_every_time_it_arrived() -> None:
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = _event_summary([
        {"event_type": "ravis.pool.members_changed", "severity": "warning",
         "occurred_at": stamp, "_repeats": 400},
        {"event_type": "nervis.test", "severity": "info", "occurred_at": stamp},
    ], now)

    assert lines[0] == "recent events (last 15 minutes): 401 — 400 warning, 1 info"
    assert "  ravis.pool.members_changed ×400" in lines
