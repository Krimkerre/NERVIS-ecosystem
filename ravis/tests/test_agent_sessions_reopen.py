"""Reopening a task's thread after blocked sites, and site asks in groups (R5).

A loaded Codex thread keeps the site list it was loaded with, so an allowed site reaches a task only
through its reopened thread (calibration runs `cal_ed672bf12c6f`, `cal_85aa0ece0f52` and
`cal_5a1d6ecc33b4`). The fake app-server behaves the same way: a relay thread reads Codex's list at
its first turn after loading, `thread/unsubscribe` unloads it (at once, after
`unload_after_seconds`, or never with `thread_never_unloads`), and `fetch <host>` goes through its
pretend proxy. What RAVIS is held to:
- **the asks one turn opens are one group**, open together; a later turn's asks wait until every ask
  of the open group is decided;
- **a turn that ends with site asks open lets go of its thread at once** (`site.reopening`), asks
  Codex until it has unloaded the thread, and resumes it with the profile and roots as after a
  restart (`site.reopened`) — and then the allowed site answers in the task's next turn;
- **after the cap** RAVIS resumes anyway and says `site.reopen_incomplete`, and the site stays
  blocked, as it would on Codex;
- **a turn asked for meanwhile answers 202 and starts only after the resume**;
- **a Stop, a switch or a settle skip the resume, never the wait**; a Stop drops the turn that
  waits for it; the next turn resumes the thread itself;
- a site allowed after the thread was resumed reopens it again, so no turn runs in a thread loaded
  before the task's last allowed site.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from tests.agent_rig import (
    Frame,
    Task,
    example,
    fixture,
    has,
    keys,
    named,
    project,
    ready_rig,
    refused,
    serving,
)
from tests.codex_rig import FAST, eventually

from ravis.agent.calibration_dependent import thread_resume_params
from ravis.codex.service import ServiceTimings

BLOCKED = ('Network access to "{}" was blocked: domain is not on the allowlist for the current '
           "sandbox mode.")
TWO_SITES = "RELAY\nfetch download.pytorch.org\nfetch huggingface.co\nsay I need both sites."
ONE_SITE = "RELAY\nfetch download.pytorch.org\nsay I need download.pytorch.org."
TURNS = "/api/v1/agent-sessions/{sid}/turns"
SESSION = "/api/v1/agent-sessions/{sid}"


def sent(rig: Any) -> list[str]:
    """Every method RAVIS sent the fake app-server, in order."""
    return [record["message"]["method"] for record in rig.server.records("received")
            if "method" in record["message"]]


def last_index(methods: list[str], method: str) -> int:
    return len(methods) - 1 - methods[::-1].index(method)


def outputs(frames: list[Frame]) -> list[str]:
    """What each completed command printed, in order."""
    return [data["item"]["output_tail"] for data in named(frames, "item.completed")
            if data["item"]["type"] == "commandExecution"]


def settled(count: int) -> Any:
    """`until` for `Task.frames`: this many turns have ended in a state needing a settle."""
    def seen(frames: list[Frame]) -> bool:
        states = [data["state"] for data in named(frames, "session.state")]
        return states.count("completed_needs_review") >= count
    return seen


def capped(seconds: float) -> ServiceTimings:
    """The rig's clocks, with Codex given `seconds` to let go of a thread."""
    session = replace(FAST.agents.session, unload_cap_seconds=seconds)
    return replace(FAST, agents=replace(FAST.agents, session=session))


def turn_after(task: Task, text: str, kind: str = "carry_on") -> list[Frame]:
    """Start a turn once the last one is settled, and read its events until it completes."""
    last = task.view()["last_event_id"]
    started = task.post("turns", {"text": text, "kind": kind}, keyed=True)
    assert started.status_code == 202, started.text
    return task.frames(after=last, until=has("turn.completed"), seconds=15)


def asks_by_host(task: Task, count: int) -> dict[str, dict[str, Any]]:
    def opened() -> dict[str, dict[str, Any]] | None:
        pending = task.view()["pending_requests"]
        return {r["payload"]["host"]: r for r in pending} if len(pending) == count else None
    return eventually(opened, 10, f"{count} site asks open")  # type: ignore[no-any-return]


def decide(task: Task, ask: dict[str, Any], word: str) -> None:
    answered = task.answer(ask["id"], {"kind": word})
    assert answered.status_code == 200, answered.text


def test_one_turns_site_asks_are_a_group_and_its_end_lets_go_of_the_thread_at_once(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path, scenario={"unload_after_seconds": 1.0})
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text=TWO_SITES)
        frames = task.frames(after=0, until=has("site.reopening"))
        asks = [data["request"] for data in named(frames, "request.opened")]
        assert [ask["payload"]["host"] for ask in asks] == [
            "download.pytorch.org", "huggingface.co"]
        group = asks[0]["group_id"]
        assert group.startswith("sg_") and asks[1]["group_id"] == group
        assert keys(asks[0]) == keys(fixture("agent-sessions.json")["request_view_examples"][4])
        order = [frame.event for frame in frames]
        assert order.index("request.opened") < order.index("turn.completed")
        assert order.index("turn.completed") < order.index("site.reopening")
        assert named(frames, "site.reopening") == [{
            "session_id": task.id, "group_id": group,
            "hosts": ["download.pytorch.org", "huggingface.co"]}]
        # Let go of at once, and nothing resumed while Codex still holds the thread.
        view = task.view()
        shown = example("GET", SESSION, "Codex's thread reopening after two blocked sites")
        assert keys(view) == keys(shown["body"])
        assert (view["codex"]["reopening"]["group_id"], view["codex"]["reopening"]["hosts"]) == (
            group, ["download.pytorch.org", "huggingface.co"])
        methods = sent(rig)
        assert methods.index("thread/unsubscribe") > methods.index("turn/start")
        assert "thread/resume" not in methods
        frames = task.frames(after=0, until=has("site.reopened"), seconds=15)
        assert named(frames, "site.reopened") == [{
            "session_id": task.id, "group_id": group,
            "hosts": ["download.pytorch.org", "huggingface.co"]}]
        # No settle came, so RAVIS resumed the thread itself, as after a restart.
        resume = eventually(lambda: rig.server.received("thread/resume"))[-1]["params"]
        thread = task.created["session"]["codex"]["thread_id"]
        assert resume == thread_resume_params(thread, root, "agent", "clarvis_run")
        assert "Network access to … was blocked" in resume["developerInstructions"]
        assert sent(rig).count("thread/loaded/list") >= 2  # asked again while Codex held it
        eventually(lambda: task.view()["codex"]["reopening"] is None, what="the reopen ended")
        assert not named(frames, "site.reopen_incomplete")


def test_an_allowed_site_answers_in_the_next_turn_once_codex_let_go_of_the_thread(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path, scenario={"unload_after_seconds": 2.0})
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text=TWO_SITES)
        first = task.frames(after=0, until=settled(1))
        assert outputs(first) == [BLOCKED.format("download.pytorch.org"),
                                  BLOCKED.format("huggingface.co")]
        asks = asks_by_host(task, 2)
        decide(task, asks["download.pytorch.org"], "allow_site")
        decide(task, asks["huggingface.co"], "keep_blocked")
        # Clarvis settles every turn: done while Codex still holds the thread, it skips the resume.
        assert task.settle("idle").json()["state"] == "idle"
        frames = task.frames(after=0, until=has("site.reopened"), seconds=15)
        assert len(named(frames, "site.reopening")) == 1  # allowing during the wait needs no other
        time.sleep(0.3)
        assert not rig.server.received("thread/resume")
        later = turn_after(task, "RELAY\nfetch download.pytorch.org\nfetch huggingface.co\n"
                                 "say done")
        assert outputs(later) == ["HTTP/2 200 from download.pytorch.org",
                                  BLOCKED.format("huggingface.co")]
        assert not named(later, "request.opened")  # a host is asked once per task
        # The turn resumed the thread first, from disk, with the profile and roots.
        methods = sent(rig)
        assert methods.index("thread/resume") < last_index(methods, "turn/start")
        thread = task.created["session"]["codex"]["thread_id"]
        assert rig.server.received("thread/resume")[-1]["params"] == thread_resume_params(
            thread, root, "agent", "clarvis_run")
        # With nothing left to ask and nothing allowed since that load, no second reopen.
        everything = task.frames(after=0, until=settled(2))
        assert len(named(everything, "site.reopening")) == 1


def test_a_thread_codex_never_lets_go_is_resumed_after_the_cap_and_the_site_stays_blocked(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path, timings=capped(1.5),
                    scenario={"calibration_faults": ["thread_never_unloads"]})
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        began = time.monotonic()
        task = relay.started(root, git_dir, text=ONE_SITE)
        task.frames(after=0, until=has("site.reopening"))
        ask = asks_by_host(task, 1)["download.pytorch.org"]
        decide(task, ask, "allow_site")
        frames = task.frames(after=0, until=has("site.reopen_incomplete"), seconds=15)
        assert time.monotonic() - began >= 1.5
        assert named(frames, "site.reopen_incomplete") == [{
            "session_id": task.id, "group_id": ask["group_id"], "hosts": ["download.pytorch.org"]}]
        assert not named(frames, "site.reopened")
        eventually(lambda: rig.server.received("thread/resume"), what="resumed anyway")
        assert sent(rig).count("thread/loaded/list") >= 10
        task.reaches("completed_needs_review")
        assert task.settle("idle").status_code == 200
        later = turn_after(task, "RELAY\nfetch download.pytorch.org\nsay done")
        # As on Codex: the thread was never unloaded, so it still has the list it loaded with.
        assert outputs(later) == [BLOCKED.format("download.pytorch.org")]


def test_a_turn_asked_for_while_codex_holds_the_thread_starts_only_after_the_resume(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path, scenario={"unload_after_seconds": 2.5})
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text=ONE_SITE)
        task.frames(after=0, until=has("site.reopening"))
        ask = asks_by_host(task, 1)["download.pytorch.org"]
        task.reaches("completed_needs_review")
        decide(task, ask, "allow_site")
        assert task.settle("idle").status_code == 200
        turn = {"text": "The owner allowed download.pytorch.org. Carry on.", "kind": "carry_on"}
        accepted = task.post("turns", turn, keyed=True)
        shown = example("POST", TURNS, "accepted while Codex's thread is reopening: it starts "
                                       "after the resume")
        assert (accepted.status_code, accepted.json()) == (202, shown["body"])
        view = task.view()
        assert view["state"] == "starting" and view["codex"]["reopening"] is not None
        refused(task.post("turns", turn, keyed=True), 409, "TURN_ACTIVE")
        steered = task.post("steer", {"text": "Use the CPU wheel."}, keyed=True)
        assert (steered.status_code, steered.json()) == (202, {"delivered": "queued"})
        assert len(rig.server.received("turn/start")) == 1  # nothing starts while Codex holds it
        frames = task.frames(after=0, until=has("turn.started", kind="carry_on"), seconds=15)
        order = [frame.event for frame in frames]
        assert order.index("site.reopened") < last_index(order, "turn.started")
        methods = sent(rig)
        assert methods.index("thread/resume") < last_index(methods, "turn/start")
        assert has("feedback", text="Use the CPU wheel.", how="delivered_in_turn")(frames)
        text = rig.server.received("turn/start")[-1]["params"]["input"][0]["text"]
        assert text == "Use the CPU wheel.\n\nThe owner allowed download.pytorch.org. Carry on."
        task.reaches("completed_needs_review")


def test_a_stop_while_the_thread_reopens_drops_the_waiting_turn_and_skips_the_resume(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path, scenario={"unload_after_seconds": 3.0})
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text=ONE_SITE)
        task.frames(after=0, until=has("site.reopening"))
        task.reaches("completed_needs_review")
        assert task.settle("idle").status_code == 200
        waiting = task.post("turns", {"text": "RELAY\nsay carried on", "kind": "carry_on"},
                            keyed=True)
        assert waiting.status_code == 202
        stopping = task.post("interrupt", {"reason": "stop"})
        assert (stopping.status_code, stopping.json()) == (202, {"state": "stopping"})
        stopped = task.reaches("stopped")
        assert stopped["codex"]["reopening"] is not None  # the wait goes on
        frames = task.frames(after=0, until=has("site.reopened"), seconds=15)
        assert named(frames, "turn.completed")[-1]["status"] == "interrupted"
        time.sleep(0.3)
        assert len(rig.server.received("turn/start")) == 1
        assert not rig.server.received("thread/resume")
        # The next turn resumes the thread itself.
        assert task.settle("idle").status_code == 200
        later = turn_after(task, "RELAY\nsay carried on")
        assert has("item.completed")(later)
        methods = sent(rig)
        assert methods.index("thread/resume") < last_index(methods, "turn/start")


def test_a_switch_while_the_thread_reopens_skips_the_resume_too(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path, scenario={"unload_after_seconds": 2.0})
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text=ONE_SITE)
        task.frames(after=0, until=has("site.reopening"))
        task.reaches("completed_needs_review")
        switching = task.post("interrupt", {"reason": "switch"})
        assert (switching.status_code, switching.json()["state"]) == (
            202, "completed_needs_review")
        settled_view = task.settle("transfer").json()
        assert (settled_view["state"], settled_view["lock"]["state"]) == ("idle", "running")
        task.frames(after=0, until=has("site.reopened"), seconds=15)
        time.sleep(0.3)
        assert not rig.server.received("thread/resume")
        # Ending the task still archives the thread Codex let go of.
        ended = relay.call("DELETE", f"/api/v1/agent-sessions/{task.id}", token=task.token)
        assert ended.status_code == 200
        thread = task.created["session"]["codex"]["thread_id"]
        assert rig.server.received("thread/archive")[-1]["params"]["threadId"] == thread


def test_a_later_turns_asks_wait_until_the_open_group_is_decided(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text=ONE_SITE)
        first = asks_by_host(task, 1)["download.pytorch.org"]
        task.frames(after=0, until=has("site.reopened"))
        eventually(lambda: task.view()["codex"]["reopening"] is None, what="the reopen ended")
        task.reaches("completed_needs_review")
        assert task.settle("idle").status_code == 200  # the ask outlives the turn
        later = turn_after(task, "RELAY\nfetch huggingface.co\nsay I need that too.", "continue")
        assert [data["host"] for data in named(later, "site.blocked")] == ["huggingface.co"]
        assert not named(later, "request.opened")
        assert [r["id"] for r in task.view()["pending_requests"]] == [first["id"]]
        decide(task, first, "keep_blocked")
        second = asks_by_host(task, 1)["huggingface.co"]
        assert second["group_id"] != first["group_id"]


def test_a_site_allowed_after_the_thread_was_resumed_reopens_it_again(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text=ONE_SITE)
        ask = asks_by_host(task, 1)["download.pytorch.org"]
        task.frames(after=0, until=has("site.reopened"))
        eventually(lambda: len(rig.server.received("thread/resume")) == 1, what="the resume")
        eventually(lambda: task.view()["codex"]["reopening"] is None, what="the reopen ended")
        task.reaches("completed_needs_review")
        # The owner decides only now, in a thread Codex loaded before the site was allowed.
        decide(task, ask, "allow_site")
        frames = task.frames(after=0, until=lambda seen: len(named(seen, "site.reopened")) == 2)
        assert [data["hosts"] for data in named(frames, "site.reopening")] == [
            ["download.pytorch.org"], ["download.pytorch.org"]]
        eventually(lambda: len(rig.server.received("thread/resume")) == 2, what="resumed again")
        eventually(lambda: task.view()["codex"]["reopening"] is None, what="the reopen ended")
        assert task.settle("idle").status_code == 200
        later = turn_after(task, "RELAY\nfetch download.pytorch.org\nsay done")
        assert outputs(later) == ["HTTP/2 200 from download.pytorch.org"]


def test_every_site_one_command_was_blocked_from_is_asked_in_the_same_group(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    # One command, two of the proxy's blocked lines in its output.
    both = f"{BLOCKED.format('download.pytorch.org')} {BLOCKED.format('huggingface.co')}"
    with serving(rig) as relay:
        relay.ready()
        script = f"RELAY\nrun pip install torch => {both}\nsay done"
        task = relay.started(root, git_dir, text=script)
        asks = asks_by_host(task, 2)
        assert asks["download.pytorch.org"]["group_id"] == asks["huggingface.co"]["group_id"]
        frames = task.frames(after=0, until=has("site.reopening"))
        assert [data["host"] for data in named(frames, "site.blocked")] == [
            "download.pytorch.org", "huggingface.co"]
        assert named(frames, "site.reopening")[0]["hosts"] == [
            "download.pytorch.org", "huggingface.co"]


def test_runs_say_a_task_is_reconnecting_while_its_thread_reopens(tmp_path: Path) -> None:
    """R5b: `GET /api/v1/codex` → `runs` carries the same `reopening` a window's view shows."""
    rig = ready_rig(tmp_path, scenario={"unload_after_seconds": 2.0})
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()

        def run() -> dict[str, Any]:
            [only] = relay.call("GET", "/api/v1/codex", caller="client.nervis").json()["runs"]
            return only  # type: ignore[no-any-return]

        task = relay.started(root, git_dir, text=ONE_SITE)
        task.frames(after=0, until=has("site.reopening"))
        reopening = run()["reopening"]
        assert reopening == task.view()["codex"]["reopening"]
        assert reopening["hosts"] == ["download.pytorch.org"]
        assert reopening["group_id"].startswith("sg_") and reopening["since"]
        task.frames(after=0, until=has("site.reopened"), seconds=15)
        eventually(lambda: run()["reopening"] is None, what="the run no longer reconnecting")
