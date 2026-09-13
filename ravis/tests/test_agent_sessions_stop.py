"""Stop, from a window and from the owner (design §5.3, §3.5.5; `owner-stop.json`).

What RAVIS is held to here:
- **open requests are answered before `turn/interrupt` is sent**, and a later answer gets 409
  `SESSION_STOPPING` and never reaches Codex;
- the stream says it in the contract's order: stopping, resolved, the turn interrupted, stopped;
- **only this task's processes are signalled**, and confirmed gone; a survivor leaves the task
  `leftover` and refuses the settle until it is gone;
- the owner Stop stops with `stopped_by`, is audited with no project path, replays a retry, needs
  the task's folder and turn, refuses a task that isn't running, and is rate-limited per
  application.
"""

from __future__ import annotations

import json
import os
import signal
import uuid
from pathlib import Path

from tests.agent_rig import (
    SESSIONS,
    WINDOW,
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

from ravis.agent.session import Processes

OWNER_STOP_EXAMPLES = {
    case["name"]: case["response"] for case in fixture("owner-stop.json")["examples"]
}


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_stop_answers_open_requests_before_it_interrupts_and_refuses_late_answers(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nask npm install left-pad\nwait")
        request = task.pending()
        stopping = task.post("interrupt", {"reason": "stop"})
        assert (stopping.status_code, stopping.json()) == (202, {"state": "stopping"})
        assert task.post("interrupt", {"reason": "stop"}).status_code == 202
        refused(task.answer(request["id"], {"kind": "once"}), 409, "SESSION_STOPPING")
        frames = task.frames(after=0, until=has("session.state", state="stopped"))
        refused(task.post("steer", {"text": "more"}, keyed=True), 409, "SESSION_STOPPING")
        assert task.settle("idle").json()["state"] == "idle"
    order = [(f.event, f.data.get("state") or f.data.get("by") or f.data.get("status"))
             for f in frames if f.event in ("session.state", "request.resolved", "turn.completed")]
    assert order[-4:] == [("session.state", "stopping"), ("request.resolved", "stop"),
                          ("turn.completed", "interrupted"), ("session.state", "stopped")]
    assert named(frames, "session.state")[-2]["stopped_by"] == "window"
    messages = [record["message"] for record in rig.server.records("received")]
    answered = next(i for i, m in enumerate(messages) if m.get("result") == {"decision": "cancel"})
    interrupted = next(i for i, m in enumerate(messages) if m.get("method") == "turn/interrupt")
    assert answered < interrupted
    assert all(m.get("result") != {"decision": "accept"} for m in messages)


def test_only_this_tasks_processes_are_ended_and_the_save_waits_for_survivors(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    (a_root, a_git), (b_root, b_git) = project(rig, "project-a"), project(rig, "project-b")
    spawned: list[int] = []
    try:
        with serving(rig) as relay:
            relay.ready()
            a = relay.started(a_root, a_git, text="RELAY\nspawn\nwait")
            b = relay.started(b_root, b_git, text="RELAY\nspawn\nwait", task_id=str(uuid.uuid4()))
            records = eventually(lambda: (lambda found: found if len(found) == 2 else None)(
                rig.server.records("relay_spawned")), what="both processes started")
            pids = {Path(r["root"]).name: r["pid"] for r in records}
            spawned.extend(pids.values())
            a.post("interrupt", {"reason": "stop"})
            view = a.reaches("stopped")
            assert view["processes"]["confirmed_gone"] is True and view["processes"]["attributed"]
            eventually(lambda: not alive(pids["project-a"]), what="project A's process gone")
            assert alive(pids["project-b"])
            cleanup = rig.service.agents.context.cleanup
            cleanup._kill = lambda _pid, _signal: None  # a process that won't die, for now
            b.post("interrupt", {"reason": "stop"})
            leftover = b.reaches("leftover")
            assert leftover["processes"]["leftover"]
            shown = next(e for e in fixture("event-stream.json")["events"]
                         if e["event"] == "session.state")["example_data"]
            frame = named(b.frames(after=0, until=has("session.state", state="leftover")),
                          "session.state")[-1]
            assert keys(frame["processes"]) == keys(shown["processes"])
            refused(b.post("settle-claim", {"window_id": "win-a"}), 409,
                    "PROCESSES_NOT_CONFIRMED_GONE")
            assert b.post("leftover", {"action": "stop_them"}).json() == {
                "processes_confirmed_gone": False}
            cleanup._kill = os.kill
            assert b.post("leftover", {"action": "stop_them"}).json() == {
                "processes_confirmed_gone": True}
            b.reaches("stopped")
            refused(a.post("leftover", {"action": "stop_them"}), 409, "NO_LEFTOVER")
            assert b.settle("idle").json()["state"] == "idle"
    finally:
        for pid in spawned:
            if alive(pid):
                os.kill(pid, signal.SIGKILL)


def test_a_claimed_save_still_waits_for_a_survivor_found_after_the_claim(tmp_path: Path) -> None:
    """The settle checks again for survivors, behind the claim's own check.

    A claim is only given once every process is confirmed gone, and today nothing in the API finds
    a survivor between a claim and its settle (a turn can't start until the task is settled), so
    the survivor is put on the task directly. The save must refuse it and keep the claim, then
    commit once the survivor has gone.
    """
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nwait")
        task.post("interrupt", {"reason": "stop"})
        assert task.reaches("stopped")["processes"]["confirmed_gone"] is True
        claim = task.post("settle-claim", {"window_id": WINDOW})
        assert claim.status_code == 200, claim.text
        body = {"claim_id": claim.json()["claim_id"], "commit": "9a8b7c6d",
                "checkpoint_saved": True, "next": "idle"}
        session = rig.service.agents.find(task.id)
        assert session is not None
        gone, session.processes = session.processes, Processes(1, False, ())
        refused(task.post("settle", body, key=uuid.uuid4().hex), 409,
                "PROCESSES_NOT_CONFIRMED_GONE")
        session.processes = gone
        settled = task.post("settle", body, key=uuid.uuid4().hex)
        assert settled.json()["state"] == "idle", settled.text


def owner_stop(relay: object, task: object, *, caller: str = "admin.owner_cli",
               source: str = "menu_bar", key: str | None = None, **confirm: str) -> object:
    """The owner Stop as the menu bar sends it: the folder and turn `GET /api/v1/codex` lists."""
    runs = relay.call("GET", "/api/v1/codex", caller=caller).json()["runs"]  # type: ignore[attr-defined]
    run = next(run for run in runs if run["id"] == task.id)  # type: ignore[attr-defined]
    body = {"source": source,
            "confirm": {"project": run["project"], "turn_id": run["turn_id"], **confirm}}
    return relay.call("POST", f"{SESSIONS}/{task.id}/owner-stop", caller=caller,  # type: ignore[attr-defined]
                      key=key or uuid.uuid4().hex, body=body)


def test_the_owner_stop_stops_says_who_did_and_is_audited(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    key = "4c2f8a1e-9b7d-4e3a-8f6c-2d1b0a9e8c7d"
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nask npm install left-pad\nwait")
        request = task.pending()
        turn = task.view()["codex"]["active_turn_id"]
        stopped = owner_stop(relay, task, key=key)
        assert (stopped.status_code, stopped.json()) == (202, {"state": "stopping"})
        again = relay.call("POST", f"{SESSIONS}/{task.id}/owner-stop", caller="admin.owner_cli",
                           key=key, body={"source": "menu_bar",
                                          "confirm": {"project": "add-utc-demo", "turn_id": turn}})
        assert (again.status_code, again.json()) == (202, {"state": "stopping"})
        refused(task.answer(request["id"], {"kind": "once"}), 409, "SESSION_STOPPING")
        frames = task.frames(after=0, until=has("session.state", state="stopped"))
    states = named(frames, "session.state")
    assert [(s["state"], s.get("stopped_by")) for s in states[-2:]] == [
        ("stopping", "menu_bar"), ("stopped", "menu_bar")]
    assert [(r["by"], r["decision_kind"]) for r in named(frames, "request.resolved")] == [
        ("owner_stop", "stop")]
    audits = rig.published("ravis.agent_session.owner_stopped")
    assert len(audits) == 1
    assert {k: audits[0][k] for k in ("session_id", "source", "application_id")} == {
        "session_id": task.id, "source": "menu_bar", "application_id": "owner_cli"}
    assert any(e.get("reason_code") == "owner_stop" and e["to"] == "stopping"
               for e in rig.published("ravis.agent_session.state_changed"))
    assert str(root) not in json.dumps(rig.events)


def test_the_owner_stop_needs_the_right_task_and_turn_and_something_running(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nwait")
        task.reaches("running")
        mismatch = refused(owner_stop(relay, task, project="other-project"), 409,
                           "CONFIRMATION_MISMATCH")
        assert mismatch["message"] == OWNER_STOP_EXAMPLES[
            "a stale menu: the turn moved on"]["body"]["error"]["message"]
        refused(owner_stop(relay, task, turn_id="019a1c2d-0000-7000-8000-000000000000"), 409,
                "CONFIRMATION_MISMATCH")
        unknown = relay.call("POST", f"{SESSIONS}/as_01J9ZK0000000000UNKNOWN0/owner-stop",
                             caller="admin.owner_cli", key=uuid.uuid4().hex,
                             body={"source": "menu_bar", "confirm": {}})
        refused(unknown, 404, "AGENT_SESSION_NOT_FOUND")
        dashboard = owner_stop(relay, task, caller="admin.launcher", source="dashboard")
        assert dashboard.status_code == 202
        assert has("session.state", state="stopped", stopped_by="dashboard")(
            task.frames(after=0, until=has("session.state", state="stopped")))
        refused(owner_stop(relay, task), 409, "NOTHING_RUNNING", state="stopped")


def test_the_owner_stop_is_rate_limited_per_application(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nwait")
        task.reaches("running")
        for _ in range(10):
            refused(owner_stop(relay, task, project="stale"), 409, "CONFIRMATION_MISMATCH")
        limited = refused(owner_stop(relay, task, project="stale"), 429, "RATE_LIMITED")
        assert limited["retryable"] is True
        refused(owner_stop(relay, task, caller="admin.launcher", source="dashboard",
                           project="stale"), 409, "CONFIRMATION_MISMATCH")
