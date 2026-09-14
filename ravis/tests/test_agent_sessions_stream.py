"""The event stream, attachment, the unanswered-request policy and token reissue (design §3.5.4).

What RAVIS is held to here:
- a first connection starts with `retry: 3000` and the snapshot, whose id is its `last_event_id`;
  a reconnect replays what came after its cursor, by `Last-Event-ID` or `?after=` alike;
- a cursor older than the kept events is refused with 409 before any byte is streamed;
- deltas are kept apart; a resume says where some were dropped; ids keep rising after a restart;
  a stream too slow to keep up is ended rather than waited for;
- a window counts as attached while its panel's presence is fresh, and stops counting 45 s later;
- **the unanswered-request policy** declines and pauses after 30 minutes with no window attached
  and 2 hours with one — keeping the thread;
- a token is reissued only once no window has been attached for 60 s, for the task's own root.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from tests.agent_rig import (
    OTHER_WINDOW,
    SESSIONS,
    TOKEN,
    WINDOW,
    FakeClock,
    fixture,
    has,
    keys,
    named,
    project,
    ready_rig,
    refused,
    serving,
)
from tests.codex_rig import eventually

from ravis.agent import events as agent_events
from ravis.agent.events import EventLog, Replay, Subscriber

EVENT_EXAMPLES = {event["event"]: event["example_data"]
                  for event in fixture("event-stream.json")["events"]}


def test_a_first_connection_starts_with_the_snapshot_and_a_reconnect_replays_its_cursor(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        script = "RELAY\nplan Add the flag\nsay STEP: 1. Look\nwait"
        task = relay.started(root, git_dir, text=script)
        headers = {**rig.caller("client.clarvis"), "X-Agent-Session-Token": task.token}
        with relay.http.stream("GET", f"{SESSIONS}/{task.id}/events", headers=headers) as raw:
            assert next(raw.iter_lines()) == "retry: 3000"
        snapshot = task.frames(until=lambda seen: bool(seen))[0]
        assert snapshot.event == "snapshot" and snapshot.id == snapshot.data["last_event_id"]
        # No request is open here, where the example shows one: compare all but that list.
        assert keys({**snapshot.data, "pending_requests": []}) == keys(
            {**EVENT_EXAMPLES["snapshot"], "pending_requests": []})
        by_query = task.frames(after=1, until=has("item.completed"))
        by_header = task.frames(last_event_id=1, until=has("item.completed"))
    ids = [frame.id for frame in by_query]
    assert ids == [frame.id for frame in by_header][:len(ids)]
    assert all(isinstance(i, int) and i > 1 for i in ids) and ids == sorted(ids)
    items = fixture("event-stream.json")["normalised_items"]
    for frame in by_query:
        if frame.event in ("turn.started", "agent.delta", "plan.updated"):
            assert keys(frame.data) == keys(EVENT_EXAMPLES[frame.event]), frame.event
        if frame.event == "item.completed":
            assert keys({**frame.data, "item": None}) == keys(
                {**EVENT_EXAMPLES["item.completed"], "item": None})
            assert keys(frame.data["item"]) == keys(items[frame.data["item"]["type"]])


def test_a_cursor_older_than_the_kept_events_is_refused_before_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_events, "OTHERS_KEPT", 4)
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nsteps 6\nwait")
        eventually(lambda: task.view()["last_event_id"] >= 15, what="the steps' events")
        response = relay.call("GET", f"{SESSIONS}/{task.id}/events", token=task.token,
                              params={"after": "1"})
        error = refused(response, 409, "EVENT_CURSOR_EXPIRED")
        assert error["details"]["oldest_event_id"] > 2
        # The recovery the contract gives: the snapshot, then from its last event.
        last = task.view()["last_event_id"]
        task.frames(after=last, until=lambda _seen: True)


def test_deltas_are_kept_apart_and_a_resume_says_where_some_were_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_events, "DELTAS_KEPT", 2)
    reserved: list[int] = []
    log = EventLog("as_test", after=0, reserve=reserved.append)
    log.append("turn.started", {"turn_id": "t1"})
    for index in range(5):
        log.append("agent.delta", {"turn_id": "t1", "item_id": "m1", "text": str(index)})
    log.append("item.completed", {"turn_id": "t1", "item": {}})
    replay = log.replay(1)
    assert isinstance(replay, Replay)
    assert ([e.id for e in replay.events], replay.deltas_skipped) == ([5, 6, 7], True)
    later = log.replay(6)
    assert isinstance(later, Replay) and ([e.id for e in later.events], later.deltas_skipped) == (
        [7], False)
    assert reserved == [1001]
    # After a restart the ids start above every one handed out, and old cursors are expired.
    restarted = EventLog("as_test", after=reserved[-1], reserve=reserved.append)
    assert restarted.append("session.state", {"state": "uncertain"}).id == 1002
    assert restarted.replay(7) == 1002


async def test_a_stream_too_slow_to_keep_up_is_ended_not_waited_for() -> None:
    log = EventLog("as_test", after=0, reserve=lambda _through: None)
    subscriber: Subscriber = log.subscribe()
    subscriber._limit = 400  # a tiny buffer: one frame fits, the second overflows it
    log.append("warning", {"message": "x" * 150})
    log.append("warning", {"message": "y" * 150})
    assert subscriber.closed
    assert [event_id for event_id, _ in await subscriber.take(0.01)] == [1]


def test_a_window_counts_as_attached_while_its_panel_pings(tmp_path: Path) -> None:
    clock = FakeClock()
    rig = ready_rig(tmp_path, clock=clock)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir)
        assert [w["id"] for w in task.view()["attached_windows"]] == [WINDOW]
        assert task.presence(True, window=OTHER_WINDOW).status_code == 204
        assert {w["id"] for w in task.view()["attached_windows"]} == {WINDOW, OTHER_WINDOW}
        clock.advance(30)
        task.presence(True, window=OTHER_WINDOW)
        clock.advance(20)
        eventually(lambda: [w["id"] for w in task.view()["attached_windows"]] == [OTHER_WINDOW],
                   what="the quiet window detached")
        task.presence(False, window=OTHER_WINDOW)
        assert task.view()["attached_windows"] == []
        frames = task.frames(after=0, until=has("attached", windows=[]))
        assert keys(named(frames, "attached")[0]) == keys(EVENT_EXAMPLES["attached"])
        listed = relay.call("GET", SESSIONS, params={"workspace_root": str(root)}).json()
        assert listed["items"][0]["attached_windows"] == 0
        refused(task.post("presence", {"window_id": 3}), 422, "INVALID_REQUEST_BODY")


def test_the_unanswered_policy_pauses_after_30_minutes_detached_keeping_the_thread(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    rig = ready_rig(tmp_path, clock=clock)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nask npm install left-pad\nsay never")
        request = task.pending()
        task.presence(False)
        clock.advance(29 * 60)
        time.sleep(0.4)
        assert task.view()["state"] == "waiting_on_you"
        clock.advance(61)
        frames = task.frames(after=0, until=has("session.state", state="paused_unanswered"))
        assert named(frames, "request.resolved")[-1]["by"] == "policy_timeout"
        view = task.view()
        assert view["codex"]["thread_id"] == task.created["session"]["codex"]["thread_id"]
        assert not rig.server.received("thread/archive")
        assert task.post("settle-claim", {"window_id": WINDOW}).status_code == 200
    replies = [r["message"] for r in rig.server.records("received")
               if isinstance(r["message"].get("result"), dict)
               and "decision" in r["message"]["result"]]
    assert [reply["result"] for reply in replies] == [{"decision": "cancel"}]
    assert request["id"]


def test_the_unanswered_policy_waits_2_hours_while_a_window_is_attached(tmp_path: Path) -> None:
    clock = FakeClock()
    rig = ready_rig(tmp_path, clock=clock)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nask npm test\nsay never")
        task.pending()
        for _ in range(int(7200 / 40) - 2):
            clock.advance(40)
            task.presence(True)
        time.sleep(0.4)
        assert task.view()["state"] == "waiting_on_you"
        clock.advance(80)
        task.presence(True)
        task.reaches("paused_unanswered")


def test_a_token_is_reissued_only_for_its_own_root_once_no_window_is_attached(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    rig = ready_rig(tmp_path, clock=clock)
    root, git_dir = project(rig)
    other, _ = project(rig, "add-utc-demo-copy")
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir)
        path = f"{SESSIONS}/{task.id}/reissue-token"
        body = {"workspace_root": str(root)}
        refused(relay.call("POST", path, key="reissue-1", body=body), 409, "WINDOW_ATTACHED")
        task.presence(False)
        clock.advance(61)
        reissued = relay.call("POST", path, key="reissue-1", body=body)
        assert reissued.status_code == 200, reissued.text
        token = reissued.json()["session_token"]
        assert TOKEN.match(token) and token != task.token
        assert relay.call("POST", path, key="reissue-1", body=body).json() == reissued.json()
        refused(relay.call("GET", f"{SESSIONS}/{task.id}", token=task.token), 404,
                "AGENT_SESSION_NOT_FOUND")
        assert relay.call("GET", f"{SESSIONS}/{task.id}", token=token).status_code == 200
        refused(relay.call("POST", path, key="reissue-2", body={"workspace_root": str(other)}),
                404, "AGENT_SESSION_NOT_FOUND")
    assert [event["session_id"] for event in rig.published("ravis.agent_session.token_reissued")
            ] == [task.id]
