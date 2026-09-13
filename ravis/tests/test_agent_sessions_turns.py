"""Turns, requests, steering, the lock, settle and ending a task (design §3.5.2-§3.5.4, §5.4, §5.5).

What RAVIS is held to here:
- an approval is offered with the decisions RAVIS allows, answered once, forwarded as Codex's
  own answer, and another window's answer is refused; decisions RAVIS doesn't offer are refused,
  and no grant for the whole session — or any network — is ever sent;
- a secret question and an MCP elicitation are answered by policy, never offered;
- a file change whose item never says what it writes is offered only to decline (calibration K12);
- **a turn Codex ends by itself has its open request answered by RAVIS** (calibration K7);
- steering reaches the running turn, or is queued and starts the follow-on turn — unless the task
  lost the project lock, when it starts nothing and comes back `not_delivered`;
- **turns, and a steer with no turn to steer, need the project lock** (F-A1), taken atomically on
  a free root or with a transfer token;
- the step cap ends a turn while no window is attached; output that may hold a secret is hidden;
- one window claims the settle, a retried settle replays the settled view; ending keeps the thread.
"""

from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.agent_rig import (
    OTHER_WINDOW,
    SESSIONS,
    clarvis_lock,
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
from tests.codex_rig import eventually
from tests.fake_codex_relay import LOOKS_LIKE_A_KEY

from ravis.agent.redact import HIDDEN
from ravis.agent.roots import root_hash
from ravis.agent.tokens import token_sha256
from ravis.codex.lock_file import Holder, encode, iso, lock_content
from ravis.codex.lock_rule import own_start


def answers(rig: object) -> list[object]:
    return [record["answer"] for record in rig.server.records("relay_answer")]  # type: ignore[attr-defined]


def test_an_approval_is_offered_answered_once_and_forwarded_as_codexs_answer(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nask npm install left-pad\nsay done")
        request = task.pending()
        assert keys(request) == keys(fixture("agent-sessions.json")["request_view_examples"][0])
        assert request["payload"]["command"] == "npm install left-pad"
        assert (request["payload"]["cwd"], request["allowed_decisions"]) == (
            ".", ["once", "skip", "stop"])
        assert task.view()["state"] == "waiting_on_you"
        answered = task.answer(request["id"], {"kind": "once"})
        assert (answered.status_code, answered.json()) == (
            200, {"resolved": True, "decision_kind": "once"})
        assert task.answer(request["id"], {"kind": "once"}).json() == answered.json()
        other = task.answer(request["id"], {"kind": "once"}, window=OTHER_WINDOW)
        refused(other, 409, "REQUEST_ALREADY_RESOLVED", by="window")
        assert eventually(lambda: answers(rig)) == [{"decision": "accept"}]
        task.reaches("completed_needs_review")
        refused(task.answer("rq_01J9ZK0000000000000UNKNOWN", {"kind": "once"}), 404,
                "REQUEST_NOT_FOUND")


def test_decisions_ravis_doesnt_offer_are_refused_and_nothing_wider_is_granted(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    outside = Path(os.path.realpath(tmp_path)) / "outside.txt"
    secret_file = Path.home() / ".ssh" / "config"
    script = "\n".join(("RELAY", "ask-outside touch x", f"edit {outside}", f"grant {secret_file}",
                        "question q1", "edit src/app.ts", "say done"))
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text=script)
        outside_command = task.pending()
        assert outside_command["allowed_decisions"] == ["skip", "stop"]
        refused(task.answer(outside_command["id"], {"kind": "once"}), 422, "DECISION_NOT_ALLOWED",
                allowed_decisions=["skip", "stop"])
        assert task.answer(outside_command["id"], {"kind": "skip"}, key="k1").status_code == 200
        change = eventually(lambda: [r for r in task.view()["pending_requests"]
                                     if r["kind"] == "fileChange"], what="the file change")[0]
        assert change["payload"]["outside_workspace"] == [str(outside)]
        assert change["allowed_decisions"] == ["skip", "stop"]
        task.answer(change["id"], {"kind": "skip"})
        grant = eventually(lambda: [r for r in task.view()["pending_requests"]
                                    if r["kind"] == "permissions"], what="the grant")[0]
        assert grant["payload"]["denied"] and grant["allowed_decisions"] == ["skip", "stop"]
        task.answer(grant["id"], {"kind": "skip"})
        question = eventually(lambda: [r for r in task.view()["pending_requests"]
                                       if r["kind"] == "question"], what="the question")[0]
        assert keys(question) == keys(fixture("agent-sessions.json")["request_view_examples"][3])
        assert (question["payload"]["questions"][0]["options"], question["allowed_decisions"]) == (
            ["Yes", "No"], ["answer", "stop"])
        refused(task.answer(question["id"], {"kind": "once"}), 422, "DECISION_NOT_ALLOWED")
        task.answer(question["id"], {"kind": "answer", "answers": {"q1": "Yes"}}, key="k2")
        inside = eventually(lambda: [r for r in task.view()["pending_requests"]
                                     if r["kind"] == "fileChange"], what="the inside change")[0]
        assert inside["payload"]["files"] == [
            {"path": "src/app.ts", "change": "update", "added": 2, "removed": 1}]
        assert inside["allowed_decisions"] == ["once", "skip", "stop"]
        task.answer(inside["id"], {"kind": "once"}, key="k3")
        task.reaches("completed_needs_review")
    assert answers(rig) == [
        {"decision": "decline"}, {"decision": "decline"}, {"permissions": {}},
        {"answers": {"q1": {"answers": ["Yes"]}}}, {"decision": "accept"},
    ]
    sent = json.dumps(rig.server.records("received"))
    assert "acceptForSession" not in sent and '"scope": "session"' not in sent


def test_a_network_approval_is_offered_only_to_decline(tmp_path: Path) -> None:
    """Approvals never open the network on Codex 0.154.0; the owner's sites do (fake only)."""
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nask-network example.com\nsay done")
        request = task.pending()
        assert request["payload"]["network"] == {"host": "example.com", "protocol": "https"}
        assert request["allowed_decisions"] == ["skip", "stop"]
        refused(task.answer(request["id"], {"kind": "once"}), 422, "DECISION_NOT_ALLOWED")
        assert task.answer(request["id"], {"kind": "skip"}, key="k-skip").status_code == 200
        task.reaches("completed_needs_review")
    assert answers(rig) == [{"decision": "decline"}]


BLOCKED = ('Network access to "{}" was blocked: domain is not on the allowlist for the current '
           "sandbox mode.")
ANSWER_PATH = "/api/v1/agent-sessions/{sid}/requests/{rid}/answer"


def test_a_blocked_site_is_asked_of_the_owner_one_at_a_time_and_added_while_codex_runs(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    script = "\n".join((
        "RELAY", f"run pip download https://pypi.org/simple => {BLOCKED.format('pypi.org')}",
        f"run curl https://api.github.com => {BLOCKED.format('api.github.com')}",
        f"run curl http://example.com => {BLOCKED.format('example.com')}",
        f"run pip download https://pypi.org/other => {BLOCKED.format('pypi.org')}", "say done",
    ))
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text=script)
        frames = task.frames(after=0, until=has("session.state", state="completed_needs_review"))
        assert [(f["host"], f["protocol"]) for f in named(frames, "site.blocked")] == [
            ("pypi.org", "https"), ("api.github.com", "https"), ("example.com", "http")]
        [site] = task.view()["pending_requests"]
        assert keys(site) == keys(fixture("agent-sessions.json")["request_view_examples"][4])
        assert (site["kind"], site["payload"], site["allowed_decisions"]) == (
            "site", {"host": "pypi.org", "protocol": "https"}, ["allow_site", "keep_blocked"])
        refused(task.answer(site["id"], {"kind": "once"}, key="k-once"), 422,
                "DECISION_NOT_ALLOWED")
        allowed = task.answer(site["id"], {"kind": "allow_site"}, key="k-allow")
        assert (allowed.status_code, allowed.json()) == (
            200, {"resolved": True, "decision_kind": "allow_site"})
        retried = task.answer(site["id"], {"kind": "allow_site"}, key="k-allow")
        assert retried.json() == allowed.json()
        assert [record["params"] for record in rig.server.records("config_written")] == [{
            "edits": [{"keyPath": "permissions.clarvis_run.network.domains",
                       "mergeStrategy": "upsert", "value": {"pypi.org": "allow"}}],
            "reloadUserConfig": True,
        }]
        [github] = task.view()["pending_requests"]
        assert github["payload"]["host"] == "api.github.com"
        assert task.answer(github["id"], {"kind": "keep_blocked"}, key="k-keep").status_code == 200
        [left] = task.view()["pending_requests"]
        # A site ask never holds the task up: the work saves, and ending the task resolves it.
        assert task.settle("idle").status_code == 200
        assert relay.call("DELETE", f"{SESSIONS}/{task.id}", token=task.token).status_code == 200
        frames = task.frames(after=0, until=has("session.ended"))
    assert named(frames, "site.allowed") == [
        {"session_id": task.id, "request_id": site["id"], "host": "pypi.org"}]
    last = named(frames, "request.resolved")[-1]
    assert (last["request_id"], last["by"]) == (left["id"], "turn_ended")
    decided = rig.published("ravis.agent_session.site_decided")
    assert [(d["host"], d["decision"]) for d in decided] == [
        ("pypi.org", "allow_site"), ("api.github.com", "keep_blocked")]
    assert all(d["decided_at"] for d in decided) and str(root) not in json.dumps(decided)


def test_a_site_codex_didnt_add_stays_blocked_and_the_owner_is_told(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path, scenario={"batch_write_status": "okOverridden"})
    root, git_dir = project(rig)
    script = f"RELAY\nrun curl https://pypi.org => {BLOCKED.format('pypi.org')}\nsay done"
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text=script)
        site = task.pending()
        shown = example("POST", ANSWER_PATH, "allow a site Codex didn't add")
        error = refused(task.answer(site["id"], {"kind": "allow_site"}), 409, "SITE_NOT_ADDED",
                        host="pypi.org", reason="overridden")
        assert error["message"] == shown["body"]["error"]["message"]
        assert [r["id"] for r in task.view()["pending_requests"]] == [site["id"]]


def test_a_secret_question_and_an_elicitation_are_answered_by_policy_never_offered(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nsecret s1\nelicit\nsay done")
        frames = task.frames(after=0, until=has("session.state", state="completed_needs_review"))
    assert [f["by"] for f in named(frames, "request.resolved")] == [
        "policy_secret", "policy_elicitation"]
    assert not named(frames, "request.opened")
    assert answers(rig) == [{"answers": {}}, {"action": "decline"}]


def test_a_file_change_that_never_says_what_it_writes_is_offered_only_to_decline(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nedit-unseen src/app.ts\nsay done")
        change = task.pending()
        assert (change["payload"]["files"], change["allowed_decisions"]) == ([], ["skip", "stop"])
        task.answer(change["id"], {"kind": "skip"})
        task.reaches("completed_needs_review")


def test_a_turn_codex_ends_by_itself_has_its_open_request_answered_by_ravis(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nask npm test\nsay never")
        request = task.pending()
        # As the real Codex did in calibration K7: the turn ends, and nothing resolves the request.
        rig.server.send("interrupt_turns")
        frames = task.frames(after=0, until=has("session.state", state="completed_needs_review"))
        assert named(frames, "request.resolved") == [{
            "session_id": task.id, "request_id": request["id"], "by": "turn_ended",
            "decision_kind": "stop",
        }]
        left = eventually(lambda: rig.server.records("relay_left_open"))[0]["request_id"]

        def replies() -> list[object]:
            return [r["message"] for r in rig.server.records("received")
                    if r["message"].get("id") == left]
        assert eventually(replies) == [{"id": left, "result": {"decision": "cancel"}}]
        refused(task.answer(request["id"], {"kind": "once"}), 409, "REQUEST_ALREADY_RESOLVED",
                by="turn_ended")


def test_two_windows_answering_at_once_reach_codex_once(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nask npm test\nwait")
        request = task.pending()
        with ThreadPoolExecutor(2) as pool:
            statuses = sorted(pool.map(
                lambda window: task.answer(request["id"], {"kind": "once"}, window=window
                                           ).status_code, ["win-a", "win-b"]))
        assert statuses == [200, 409]
        assert eventually(lambda: answers(rig)) == [{"decision": "accept"}]


def test_steering_reaches_the_running_turn_with_the_text_as_typed(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    text = "Use datetime.timezone.utc, not pytz."
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nwait")
        turn = task.reaches("running")["codex"]["active_turn_id"]
        steered = task.post("steer", {"text": text, "expected_turn_id": turn}, keyed=True)
        assert (steered.status_code, steered.json()) == (202, {"delivered": "steered"})
        assert rig.server.received("turn/steer")[-1]["params"]["expectedTurnId"] == turn
        assert has("feedback", text=text, how="steered")(task.frames(after=0,
                                                                     until=has("feedback")))
        refused(task.post("steer", {"text": "  "}, keyed=True), 422, "EMPTY_STEER")


def test_a_steer_codex_refuses_is_queued_and_starts_the_follow_on_turn(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path, scenario={"steer_refused": True})
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nask npm test\nsay done")
        request = task.pending()
        queued = task.post("steer", {"text": "Also update the README."}, keyed=True)
        assert (queued.status_code, queued.json()) == (202, {"delivered": "queued"})
        assert task.view()["queued_feedback"] == 1
        task.answer(request["id"], {"kind": "once"})
        frames = task.frames(after=0, until=has("turn.started", kind="continue"))
        assert has("feedback", text="Also update the README.", how="delivered_in_turn")(frames)
        eventually(lambda: len(rig.server.received("turn/start")) == 2)
        assert rig.server.received("turn/start")[-1]["params"]["input"][0]["text"] == (
            "Also update the README.")


def test_a_queued_steer_whose_task_lost_the_lock_starts_nothing(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path, scenario={"steer_refused": True})
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nask npm test\nsay done")
        request = task.pending()
        task.post("steer", {"text": "Also update the README."}, keyed=True)
        # What R4's transfer does: the lock is now a Clarvis-engine run's.
        rig.service.agents.store.update_lock(
            task.view()["lock"]["id"], holder_kind="clarvis_run", holder_session_id=None,
            holder_window_id=OTHER_WINDOW, holder_host="desktop")
        task.answer(request["id"], {"kind": "once"})
        frames = task.frames(after=0, until=has("feedback", how="not_delivered"))
        completed_at = next(i for i, f in enumerate(frames) if f.event == "turn.completed")
        undelivered = next(i for i, f in enumerate(frames) if f.data.get("how") == "not_delivered")
        assert completed_at < undelivered
        task.reaches("completed_needs_review")
        assert len(rig.server.received("turn/start")) == 1


def test_turns_and_a_steer_with_no_turn_need_the_project_lock(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    lock_file = root / ".git" / "clarvis-engine.lock"
    turn = {"text": "RELAY\nsay two", "kind": "continue"}
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nsay one")
        task.reaches("completed_needs_review")
        refused(task.post("turns", turn, keyed=True), 409, "SETTLE_FIRST")
        settled = task.settle("idle")
        assert keys(settled.json()) == keys(example("POST", "/api/v1/agent-sessions/{sid}/settle",
                                                    "settled")["body"])
        assert (settled.json()["state"], settled.json()["lock"]) == ("idle", None)
        assert not lock_file.exists()
        row = clarvis_lock(rig, root)
        error = refused(task.post("turns", turn, keyed=True), 409, "PROJECT_LOCKED")
        assert error["details"]["lock"]["holder"]["kind"] == "clarvis_run"
        refused(task.post("steer", {"text": "hello"}, keyed=True), 409, "PROJECT_LOCKED")
        assert task.view()["queued_feedback"] == 0
        rig.service.agents.store.delete_lock(row["id"])
        started = task.post("turns", turn, keyed=True)
        assert (started.status_code, started.json()) == (202, {"turn": {"state": "starting"}})
        assert task.view()["lock"]["state"] == "running"
        assert json.loads(lock_file.read_text())["holder"]["session_id"] == task.id
        task.reaches("completed_needs_review")


def test_a_transfer_token_hands_the_lock_back_to_the_task_atomically(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    lock_file = root / ".git" / "clarvis-engine.lock"
    store = rig.service.agents.store
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nsay one")
        task.reaches("completed_needs_review")
        settled = task.settle("transfer").json()
        assert settled["state"] == "idle" and settled["lock"]["state"] == "running"
        token = "FIXTURE-transfer-token-1"
        later = iso(datetime.now(UTC) + timedelta(minutes=15))
        # What R4's transfer and a Clarvis window leave: the window's lock, a token to hand it back.
        store.update_lock(
            settled["lock"]["id"], holder_kind="clarvis_run", holder_session_id=None,
            holder_window_id=OTHER_WINDOW, holder_host="desktop", holder_pid=os.getpid(),
            holder_pid_start=own_start(), state="transferring",
            transfer_token_sha256=token_sha256(token), transfer_expires_at=later)
        window = Holder(kind="clarvis_run", session_id=None, pid=os.getpid(),
                        pid_start=own_start() or "", window_id=OTHER_WINDOW, host="desktop")
        replacement = lock_file.with_name("window.part")
        replacement.write_bytes(encode(lock_content(window, task_id="t", engine="clarvis")))
        os.replace(replacement, lock_file)
        catch_up = {"text": "RELAY\nsay back", "kind": "catch_up"}
        refused(task.post("turns", {**catch_up, "lock": {"transfer_token": "wrong"}}, keyed=True),
                409, "LOCK_TRANSFER_INVALID")
        taken = task.post("turns", {**catch_up, "lock": {"transfer_token": token}}, keyed=True)
        assert taken.status_code == 202, taken.text
        row = store.lock_for_root(root_hash(root))
        assert (row["holder_session_id"], row["state"], row["transfer_token_sha256"]) == (
            task.id, "running", None)
        assert json.loads(lock_file.read_text())["holder"]["session_id"] == task.id
        assert has("turn.started", kind="catch_up")(task.frames(after=0,
                                                                until=has("turn.started",
                                                                          kind="catch_up")))
        task.reaches("completed_needs_review")
        settled = task.settle("transfer").json()
        store.update_lock(settled["lock"]["id"], holder_kind="clarvis_run",
                          holder_session_id=None, transfer_token_sha256=token_sha256(token),
                          transfer_expires_at=iso(datetime.now(UTC) - timedelta(seconds=1)))
        refused(task.post("turns", {**catch_up, "lock": {"transfer_token": token}}, keyed=True),
                409, "LOCK_TRANSFER_INVALID")


def test_the_step_cap_ends_a_turn_while_no_window_is_attached(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nsteps 10\nwait", max_steps=3)
        assert task.presence(False).status_code == 204
        frames = task.frames(after=0, until=has("session.state", state="completed_needs_review"))
    completed = named(frames, "turn.completed")[-1]
    events = fixture("event-stream.json")["events"]
    shown = next(e for e in events if e["event"] == "turn.completed")
    assert keys(completed) == keys(shown["example_data"])
    assert (completed["status"], completed["error"]) == (
        "interrupted", {"kind": "step_cap", "message": "The step cap of 3 was reached."})
    assert completed["processes_confirmed_gone"] is True
    assert rig.server.received("turn/interrupt")


def test_output_that_may_hold_a_secret_is_hidden_live_in_the_ledger_and_the_transcript(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    script = "\n".join(("RELAY", "run cat settings.txt => KEY", "run cat ~/.ssh/config => Host a",
                        "run echo hi => hi", "say done"))
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text=script)
        frames = task.frames(after=0, until=has("session.state", state="completed_needs_review"))
        transcript = relay.call("GET", f"{SESSIONS}/{task.id}/transcript", token=task.token,
                                params={"limit": "50"})
    assert [f["text"] for f in named(frames, "command.output")] == [HIDDEN, HIDDEN, "hi"]
    tails = [f["item"]["output_tail"] for f in named(frames, "item.completed")
             if f["item"]["type"] == "commandExecution"]
    assert tails == [HIDDEN, HIDDEN, "hi"]
    assert transcript.status_code == 200
    assert transcript.json()["turns"][0]["items"][1]["output_tail"] == HIDDEN
    assert LOOKS_LIKE_A_KEY not in json.dumps([f.data for f in frames]) + transcript.text


def test_a_transcript_codex_doesnt_answer_is_unavailable(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path, scenario={"silent_methods": ["thread/turns/list"]})
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir)
        response = relay.call("GET", f"{SESSIONS}/{task.id}/transcript", token=task.token)
        assert refused(response, 503, "CODEX_RUNTIME_UNAVAILABLE")["retryable"] is True


def test_a_failed_turn_says_why_in_the_owners_words_and_still_needs_a_settle(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nfail usageLimitExceeded")
        frames = task.frames(after=0, until=has("session.state", state="completed_needs_review"))
    completed = named(frames, "turn.completed")[-1]
    assert (completed["status"], completed["error"]["kind"]) == ("failed", "quota_exhausted")
    assert completed["error"]["message"].startswith("Codex has used this ChatGPT plan's allowance")


def test_one_window_claims_the_settle_and_a_retry_replays_the_settled_view(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    settle_path = "/api/v1/agent-sessions/{sid}/settle-claim"
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nsay done")
        task.reaches("completed_needs_review")
        claim = task.post("settle-claim", {"window_id": "win-a"})
        assert keys(claim.json()) == keys(example("POST", settle_path, "claimed")["body"])
        refused(task.post("settle-claim", {"window_id": "win-b"}), 409, "SETTLE_CLAIMED",
                window="win-a")
        assert task.post("settle-claim", {"window_id": "win-a"}).json() == claim.json()
        body = {"claim_id": claim.json()["claim_id"], "commit": "9a8b7c6d",
                "checkpoint_saved": True, "next": "idle"}
        refused(task.post("settle", {**body, "claim_id": "sc_wrong"}, keyed=True), 409,
                "CLAIM_INVALID")
        settled = task.post("settle", body, key="settle-key-1")
        assert settled.status_code == 200 and settled.json()["settle"]["needed"] is False
        assert task.post("settle", body, key="settle-key-1").json() == settled.json()
        refused(task.post("settle", body, keyed=True), 409, "CLAIM_INVALID")
        refused(task.post("settle-claim", {"window_id": "win-a"}), 409, "NOTHING_TO_SETTLE")
        assert has("session.state", state="idle")(task.frames(after=0,
                                                              until=has("session.state",
                                                                        state="idle")))


def test_ending_a_task_needs_its_work_saved_first_and_keeps_the_thread(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    first, second = project(rig, "first"), project(rig, "second")
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(*first, text="RELAY\nask npm test\nsay done")
        request = task.pending()
        base = f"{SESSIONS}/{task.id}"
        refused(relay.call("DELETE", base, token=task.token), 409, "TURN_ACTIVE")
        task.answer(request["id"], {"kind": "once"})
        task.reaches("completed_needs_review")
        refused(relay.call("DELETE", base, token=task.token), 409, "SETTLE_FIRST")
        task.settle("idle")
        ended = relay.call("DELETE", base, token=task.token)
        assert (ended.status_code, ended.json()) == (200, {"state": "ended"})
        thread = task.created["session"]["codex"]["thread_id"]
        assert rig.server.received("thread/archive")[-1]["params"]["threadId"] == thread
        cancelled = relay.started(*second, text="RELAY\nwait", task_id=str(uuid.uuid4()))
        cancelled.reaches("running")
        answer = cancelled.post("cancel")
        assert (answer.status_code, answer.json()) == (202, {"state": "stopping",
                                                            "end_after_settle": True})
        cancelled.reaches("stopped")
        assert cancelled.settle("idle").json()["state"] == "ended"


def test_a_mode_change_applies_from_the_next_turn(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nsay one")
        changed = task.post("mode", {"mode": "unattended"})
        assert (changed.status_code, changed.json()) == (
            200, {"mode": "unattended", "applies_from": "next_turn"})
        refused(task.post("mode", {"mode": "yolo"}), 422, "INVALID_REQUEST_BODY")
        assert task.view()["mode"] == "unattended"


def test_a_crash_leaves_the_running_turn_uncertain(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nwait")
        task.reaches("running")
        rig.server.send("crash")
        frames = task.frames(after=0, until=has("session.state", state="uncertain"))
        completed = named(frames, "turn.completed")[-1]
        assert (completed["status"], completed["error"]["kind"]) == ("failed", "runtime_crashed")
        assert task.post("settle-claim", {"window_id": "win-a"}).status_code == 200
