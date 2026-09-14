"""Creating, listing and reading Codex tasks (design §3.5.1-§3.5.3; `agent-sessions.json`).

What RAVIS is held to here:
- a task is created in the contract's shape, takes the project lock and its checkout lock file,
  starts Codex's thread with RAVIS's own box — never one a client could weaken — and its first
  turn only after the answer, so a window following from `last_event_id` sees `turn.started`;
- folders outside the rule, and a git folder that isn't the project's, are refused with reasons;
- **no task starts while the strict file rules are unproven** (owner decision D2);
- a retried create replays its answer, token included, and starts nothing more;
- the live-session limit; a locked project, or one inside or around a locked folder, refused;
- a resume unarchives and resumes the task's thread;
- **a task runs at the effort it was created with, on every turn**; a model or an effort Codex
  doesn't offer is refused before anything starts, and one it hasn't listed yet waits (R5);
- the list and `GET /api/v1/codex` → `runs` show each caller only what the contract lets it see.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from tests.agent_rig import (
    SESSIONS,
    TOKEN,
    WINDOW,
    Task,
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
from tests.codex_rig import SIGNED_IN, eventually

from ravis.agent.calibration_dependent import APPROVAL_POLICY
from ravis.codex.account import account_fingerprint


def test_a_task_is_created_in_the_contracts_shape_and_takes_the_project(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        response = relay.create(root, git_dir, text="RELAY\nsay STEP: 1. Look\nwait")
        assert response.status_code == 201, response.text
        body = response.json()
        assert keys(body) == keys(example("POST", SESSIONS, "created")["body"])
        session = body["session"]
        assert TOKEN.match(body["session_token"])
        assert body["events_url"] == f"{SESSIONS}/{session['id']}/events"
        assert (session["state"], session["last_event_id"]) == ("starting", 1)
        assert session["workspace"] == {"root": str(root), "name": "add-utc-demo"}
        assert [window["id"] for window in session["attached_windows"]] == [WINDOW]
        fingerprint = account_fingerprint("chatgpt", SIGNED_IN["email"], None, "plus")[0]
        assert session["codex"]["account_fingerprint"] == fingerprint
        started = rig.server.received("thread/start")[-1]["params"]
        assert started["approvalPolicy"] == APPROVAL_POLICY["agent"]
        assert started["permissions"] == "clarvis_run"
        assert (started["cwd"], started["runtimeWorkspaceRoots"]) == (str(root), [str(root)])
        assert started["ephemeral"] is False and "sandbox" not in started
        assert "STEP: <n>" in started["developerInstructions"]
        assert "Network access to … was blocked" in started["developerInstructions"]
        task = Task(relay, session["id"], body["session_token"], body)
        assert task.frames(until=lambda seen: bool(seen))[0].event == "snapshot"
        # Following from the create's own cursor shows everything after it, the turn starting
        # included (C3's switch back follows from there).
        frames = task.frames(after=session["last_event_id"], until=has("item.completed"))
        assert has("turn.started", kind="brief")(frames)
        assert named(frames, "item.completed")[0]["item"]["text"] == "STEP: 1. Look"
        held = json.loads((git_dir / "clarvis-engine.lock").read_text())  # type: ignore[operator]
        assert held["holder"]["kind"] == "codex_session"
        assert held["holder"]["session_id"] == session["id"]
        assert held["ravis_lock_id"] == session["lock"]["id"]
        assert rig.published("ravis.agent_session.started") == [
            {"session_id": session["id"], "mode": "agent"}
        ]


def test_folders_outside_the_rule_are_refused_with_their_reason(tmp_path: Path) -> None:
    coding = Path(os.path.realpath(tmp_path)) / "coding"
    ecosystem, with_run = coding / "NERVIS-ecosystem", coding / "with-run"
    rig = ready_rig(tmp_path, agent_protected_repositories=[str(ecosystem)],
                    agent_denied_paths=[str(with_run / ".run")])
    made = (ecosystem / ".git", ecosystem / "web", with_run / ".run", tmp_path / "elsewhere")
    for folder in made:
        folder.mkdir(parents=True, exist_ok=True)
    cases = {
        Path.home(): "home",
        coding: "allowed_roots_entry",
        tmp_path / "elsewhere": "outside_allowed_roots",
        coding / "missing": "not_a_folder",
        ecosystem: "protected_repository",
        ecosystem / "web": "protected_repository",
        with_run: "denied_path",
    }
    if (Path.home() / ".config").is_dir():
        cases[Path.home() / ".config"] = "private_folder"
    with serving(rig) as relay:
        relay.ready()
        for root, reason in cases.items():
            refused(relay.create(root, None), 422, "WORKSPACE_ROOT_NOT_ALLOWED", reason=reason)
        fixed = {
            "protected_repository": example("POST", SESSIONS, "the ecosystem's own repository"),
            "allowed_roots_entry": example("POST", SESSIONS, "the coding folder itself"),
        }
        for reason, response in fixed.items():
            root = ecosystem if reason == "protected_repository" else coding
            error = refused(relay.create(root, None), 422, "WORKSPACE_ROOT_NOT_ALLOWED")
            assert error["message"] == response["body"]["error"]["message"]
        listed = relay.call("GET", SESSIONS, params={"workspace_root": str(Path.home())})
        refused(listed, 422, "WORKSPACE_ROOT_NOT_ALLOWED")
    assert not rig.server.received("thread/start")


def test_a_git_folder_that_isnt_the_projects_own_is_refused(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, _ = project(rig)
    _, other_git = project(rig, "other")
    plain, _ = project(rig, "plain", git=False)
    with serving(rig) as relay:
        relay.ready()
        wrong = refused(relay.create(root, other_git), 422, "GIT_DIR_NOT_ALLOWED")
        shown = example("POST", SESSIONS, "a git_dir that is not the root's git folder")
        assert wrong["message"] == shown["body"]["error"]["message"]
        refused(relay.create(root, None), 422, "GIT_DIR_NOT_ALLOWED")
        refused(relay.create(plain, other_git), 422, "GIT_DIR_NOT_ALLOWED")
        relay.started(plain, None)
        assert json.loads((plain / ".clarvis" / "engine.lock").read_text())["engine"] == "codex"


def test_no_task_starts_while_the_file_rules_are_unproven(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path, proven=False)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        assert relay.ready()["state"] == "untested_version"
        refused(relay.create(root, git_dir), 409, "CODEX_NOT_READY",
                state="untested_version", reason="strict_file_rules_unproven")
    assert not rig.server.received("thread/start")
    assert not (root / ".git" / "clarvis-engine.lock").exists()


def test_a_retried_create_gets_the_same_answer_and_starts_one_task(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    key = "7e8e67cf3b0ab220303d78e6022402412691cf795143211b300c3a2f1df0e687"
    with serving(rig) as relay:
        relay.ready()
        first = relay.create(root, git_dir, key=key)
        second = relay.create(root, git_dir, key=key)
        assert (first.status_code, second.status_code) == (201, 201)
        assert second.json() == first.json()
        refused(relay.create(root, git_dir, key=key, mode="auto"), 422, "IDEMPOTENCY_KEY_REUSED")
        no_key = relay.call("POST", SESSIONS, body={"workspace_root": str(root)})
        refused(no_key, 428, "IDEMPOTENCY_KEY_REQUIRED")
    assert len(rig.server.received("thread/start")) == 1


def test_the_live_session_limit_refuses_one_more(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path, agent_session_limit=1)
    first, second = project(rig, "first"), project(rig, "second")
    with serving(rig) as relay:
        relay.ready()
        relay.started(*first)
        refused(relay.create(*second), 409, "CODEX_SESSION_LIMIT")


def test_a_locked_project_or_one_around_it_is_refused_naming_the_holder(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    (root / "web").mkdir()
    with serving(rig) as relay:
        relay.ready()
        row = clarvis_lock(rig, root)
        error = refused(relay.create(root, git_dir), 409, "PROJECT_LOCKED")
        shown = example("POST", SESSIONS, "Clarvis's own engine holds the project")
        assert keys(error["details"]["lock"]) == keys(shown["body"]["error"]["details"]["lock"])
        assert error["details"]["lock"]["holder"]["kind"] == "clarvis_run"
        assert error["details"]["lock"]["verdict"] == "alive"
        refused(relay.create(root / "web", None), 409, "NESTED_PROJECT_LOCKED")
        rig.service.agents.store.delete_lock(row["id"])
        holder = relay.started(root, git_dir)
        again = refused(relay.create(root, git_dir, task_id=str(uuid.uuid4())), 409,
                        "PROJECT_LOCKED")
        # A Codex holder is named, so the window attaches to it rather than taking it over.
        assert again["details"]["lock"]["holder"]["kind"] == "codex_session"
        assert again["details"]["lock"]["holder"]["session_id"] == holder.id


def test_a_resume_unarchives_and_resumes_the_tasks_thread(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        first = relay.started(root, git_dir, text="RELAY\nsay done")
        first.reaches("completed_needs_review")
        ended = first.settle("end")
        assert ended.status_code == 200 and ended.json()["state"] == "ended", ended.text
        thread = first.created["session"]["codex"]["thread_id"]
        assert rig.server.received("thread/archive")[-1]["params"]["threadId"] == thread
        start = {"kind": "resume", "thread_id": thread, "catch_up_text": "RELAY\nsay caught up"}
        second = relay.started(root, git_dir, start=start)
        assert rig.server.received("thread/unarchive")[-1]["params"]["threadId"] == thread
        resumed = rig.server.received("thread/resume")[-1]["params"]
        assert (resumed["threadId"], resumed["runtimeWorkspaceRoots"]) == (thread, [str(root)])
        assert second.created["session"]["codex"]["thread_id"] == thread
        second.reaches("completed_needs_review")
        texts = [m["params"]["input"][0]["text"] for m in rig.server.received("turn/start")]
        assert texts[-1] == "RELAY\nsay caught up"


def test_a_task_runs_at_the_effort_it_was_created_with_on_every_turn(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path, scenario={"steer_refused": True})
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nask npm test\nsay one",
                             model="gpt-6-astra", effort="medium")
        assert (task.view()["codex"]["model"], task.view()["codex"]["effort"]) == (
            "gpt-6-astra", "medium")
        request = task.pending()
        # Codex refuses the steer, so it starts the follow-on turn: that one runs at the effort too.
        task.post("steer", {"text": "Also update the README."}, keyed=True)
        task.answer(request["id"], {"kind": "once"})
        eventually(lambda: len(rig.server.received("turn/start")) == 2, what="the follow-on turn")
        task.reaches("completed_needs_review")
        assert task.settle("idle").status_code == 200
        continued = task.post("turns", {"text": "RELAY\nsay three", "kind": "continue"},
                              keyed=True)
        assert continued.status_code == 202
        eventually(lambda: len(rig.server.received("turn/start")) == 3, what="the third turn")
        efforts = [message["params"]["effort"] for message in rig.server.received("turn/start")]
        assert efforts == ["medium", "medium", "medium"]
        assert rig.service.agents.store.session(task.id)["effort"] == "medium"  # type: ignore[index]
        plain = relay.started(*project(rig, "plain"), text="RELAY\nsay one",
                              task_id=str(uuid.uuid4()))
        assert plain.view()["codex"]["effort"] is None
        eventually(lambda: len(rig.server.received("turn/start")) == 4, what="the plain turn")
        assert "effort" not in rig.server.received("turn/start")[-1]["params"]


def test_a_model_or_effort_codex_doesnt_offer_is_refused_before_anything_starts(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        effort = refused(relay.create(root, git_dir, model="gpt-6-astra", effort="xhigh"), 422,
                         "EFFORT_NOT_OFFERED", model="gpt-6-astra", effort="xhigh",
                         efforts=["low", "medium"])
        shown = example("POST", SESSIONS, "an effort that model doesn't offer")
        assert effort["message"] == shown["body"]["error"]["message"]
        # Naming no model checks the effort against the one Codex uses by default.
        refused(relay.create(root, git_dir, effort="high"), 422, "EFFORT_NOT_OFFERED",
                model="gpt-6-astra", efforts=["low", "medium"])
        model = refused(relay.create(root, git_dir, model="gpt-4.1"), 422, "MODEL_NOT_OFFERED",
                        model="gpt-4.1", models=["gpt-6-astra", "gpt-5.6-sol"])
        shown = example("POST", SESSIONS, "a model Codex doesn't offer")
        assert model["message"] == shown["body"]["error"]["message"]
        refused(relay.create(root, git_dir, effort=7), 422, "INVALID_REQUEST_BODY")
        assert not rig.server.received("thread/start")
        assert not (root / ".git" / "clarvis-engine.lock").exists()
        task = relay.started(root, git_dir, model="gpt-5.6-sol", effort="low")
        assert task.view()["codex"]["effort"] == "low"
        assert rig.server.received("thread/start")[-1]["params"]["model"] == "gpt-5.6-sol"


def test_a_named_effort_waits_until_codex_has_listed_its_models(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path, scenario={"silent_methods": ["model/list"]})
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        error = refused(relay.create(root, git_dir, effort="low"), 503,
                        "CODEX_RUNTIME_UNAVAILABLE")
        assert error["retryable"] is True
        assert not rig.server.received("thread/start")
        relay.started(root, git_dir)


def test_the_list_and_runs_show_each_caller_what_it_may_see(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nask npm install left-pad")
        task.pending()
        response = relay.call("GET", SESSIONS, params={"workspace_root": str(root)})
        listed = response.json()
        assert keys(listed) == keys(example("GET", "/api/v1/agent-sessions?workspace_root=",
                                             "the workspace's sessions")["body"])
        item = listed["items"][0]
        assert (item["id"], item["state"], item["waiting_on_you"]) == (task.id, "waiting_on_you",
                                                                      True)
        assert item["attached_windows"] == 1 and "token" not in response.text
        states = {case["name"]: case["response"]["body"]["runs"][0]
                  for case in fixture("codex-state.json")["examples"]
                  if case["response"]["body"]["runs"]}
        runs = relay.call("GET", "/api/v1/codex", caller="client.nervis").json()["runs"]
        named_run = states["signed in, three projects busy, read by a named caller"]
        assert keys(runs[0]) == keys(named_run)
        assert (runs[0]["id"], runs[0]["project"]) == (task.id, "add-utc-demo")
        assert runs[0]["turn_id"] == task.view()["codex"]["active_turn_id"]
        anonymous = relay.call("GET", "/api/v1/codex", caller="anonymous").json()["runs"]
        assert keys(anonymous[0]) == keys(
            states["the same moment, read anonymously: no run ids or turn ids"])
