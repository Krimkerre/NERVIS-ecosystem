"""The project lock for Clarvis's own runs: `/api/v1/project-locks` (design §3.6, §6.2, §6.3).

Against `project-locks.json`, served under uvicorn like the relay's tests. What RAVIS is held to:
- a free project is taken in the fixture's shape with a lease; a retried create gets the same lease;
  the lock is read back by root, and a window elsewhere is refused;
- **a held project names its holder**: a Codex task to attach to, never taken over; a Clarvis
  window that may be taken over only when it is gone, unresponsive or waiting; a folder around or
  inside a locked one is `NESTED_PROJECT_LOCKED`;
- heartbeats carry the running command; a release needs processes confirmed gone;
- **takeover only for a Clarvis window, only on the exact root, with the confirmation the rules
  ask for** — and it revokes the old lease first, so the old window's next heartbeat is fenced;
- **a takeover that can't confirm the old window's command gone stays `leftover`**, and nobody
  holds the lock until a later takeover confirms it;
- **transfer both ways**: a Codex task hands the lock to a window with its token, RAVIS stops
  writing its file, and the window's own file stands; an expired token releases nothing, and the
  holder takes its lock back;
- **a window's file lock adopted over a superseded row** lets the Codex task be settled (F-A9).
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tests.agent_rig import (
    OTHER_WINDOW,
    SESSIONS,
    TASK_ID,
    WINDOW,
    example,
    has,
    keys,
    project,
    ready_rig,
    refused,
    serving,
)
from tests.codex_rig import eventually

from ravis.agent.group_kill import GroupKill, GroupKillTimings
from ravis.codex.lock_file import Holder, encode, iso, lock_content, lock_file_path
from ravis.codex.lock_rule import own_start, probe_process
from ravis.codex.process_table import snapshot

LOCKS = "/api/v1/project-locks"
DOCUMENT = "project-locks.json"
FAST_KILL = GroupKillTimings(term_wait_seconds=0.2, confirm_seconds=2.0)


def shown(method: str, path: str, name: str) -> Any:
    return example(method, path, name, DOCUMENT)


def holder(window: str, pid: int, start: str, host: str = "desktop") -> dict[str, Any]:
    return {"kind": "clarvis_run", "window_id": window, "host": host, "pid": pid,
            "pid_start": start}


def lock_body(root: Path, git_dir: Path | None, who: dict[str, Any], **extra: Any
              ) -> dict[str, Any]:
    return {"workspace_root": str(root), "clarvis_task_id": TASK_ID, "holder": who,
            "git_dir": str(git_dir) if git_dir else "", **extra}


def window_file(root: Path, git_dir: Path | None, who: dict[str, Any]) -> Path:
    """The checkout lock file a Clarvis window writes before it asks RAVIS (design §6.3)."""
    path = lock_file_path(root, git_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = lock_content(
        Holder(kind="clarvis_run", session_id=None, pid=who["pid"], pid_start=who["pid_start"],
               window_id=who["window_id"], host=who["host"]),
        task_id=TASK_ID, engine="clarvis")
    replacement = path.with_name("window.part")
    replacement.write_bytes(encode(content))
    os.replace(replacement, path)
    return path


@contextlib.contextmanager
def live_process(*, own_group: bool = False) -> Iterator[tuple[int, str]]:
    """A throwaway `sleep` of this test's own, alive: a window's pid, or its running command."""
    process = subprocess.Popen(["sleep", "60"], start_new_session=own_group)
    try:
        probe = eventually(lambda: probe_process(process.pid), what="the sleep started")
        assert probe.lstart is not None
        yield process.pid, probe.lstart
    finally:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        process.wait(10)


def dead_pid() -> int:
    process = subprocess.Popen(["true"])
    process.wait(10)
    return process.pid


def lease(relay: Any, lock_id: str, action: str, token: str | None, body: Any) -> Any:
    headers = {"X-Lock-Lease": token} if token else {}
    return relay.call("POST", f"{LOCKS}/{lock_id}/{action}", body=body, headers=headers)


def take(relay: Any, root: Path, git_dir: Path | None, who: dict[str, Any], *,
         key: str | None = None, **extra: Any) -> Any:
    window_file(root, git_dir, who)
    return relay.call("POST", LOCKS, key=key or uuid.uuid4().hex,
                      body=lock_body(root, git_dir, who, **extra))


def beat(waiting: bool = False, command: dict[str, Any] | None = None,
         state: str = "running") -> dict[str, Any]:
    return {"waiting_on_you": waiting, "state": state, "running_command": command}


def test_a_free_project_is_taken_in_the_fixtures_shape_and_read_back(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    me = holder(OTHER_WINDOW, os.getpid(), own_start() or "")
    key = uuid.uuid4().hex
    with serving(rig) as relay:
        relay.ready()
        taken = take(relay, root, git_dir, me, key=key)
        assert taken.status_code == 201, taken.text
        assert keys(taken.json()) == keys(shown("POST", LOCKS, "taken")["body"])
        body = taken.json()
        assert body["lease_token"].startswith("lk_")
        assert (body["lock"]["holder"]["kind"], body["lock"]["verdict"]) == ("clarvis_run", "alive")
        again = relay.call("POST", LOCKS, key=key, body=lock_body(root, git_dir, me))
        assert (again.status_code, again.json()) == (201, body)
        read = relay.call("GET", LOCKS, params={"workspace_root": str(root)})
        assert keys(read.json()) == keys(shown("GET", f"{LOCKS}?workspace_root=", "held")["body"])
        assert read.json()["lock"]["id"] == body["lock"]["id"]
        free = relay.call("GET", LOCKS, params={"workspace_root": str(project(rig, "free")[0])})
        assert free.json() == shown("GET", f"{LOCKS}?workspace_root=", "free")["body"]
        refused(relay.call("GET", LOCKS, caller="anonymous", params={"workspace_root": str(root)}),
                403, "AGENT_CLIENT_NOT_ALLOWED")
        refused(relay.call("GET", LOCKS, params={"workspace_root": str(tmp_path)}), 422,
                "WORKSPACE_ROOT_NOT_ALLOWED")
        runs = relay.call("GET", "/api/v1/codex", caller="client.nervis").json()["runs"]
        assert any(run["state"] == "clarvis_engine" and run["project"] == root.name for run in runs)
    assert ("running", "clarvis_run") in {(e["state"], e["holder_kind"])
                                           for e in rig.published("ravis.project_lock.changed")}


def test_a_held_project_names_its_holder_and_whether_a_takeover_may_follow(tmp_path: Path) -> None:
    protected = Path(os.path.realpath(tmp_path)) / "coding" / "clarvis"
    protected.mkdir(parents=True)
    rig = ready_rig(tmp_path, agent_protected_repositories=[str(protected)])
    root, git_dir = project(rig)
    other, other_git = project(rig, "other-project")
    me = holder(OTHER_WINDOW, os.getpid(), own_start() or "")
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir)
        codex = relay.call("POST", LOCKS, key=uuid.uuid4().hex, body=lock_body(root, git_dir, me))
        error = refused(codex, 409, "PROJECT_LOCKED", takeover_allowed=False,
                        attach_session_id=task.id)
        fixture = shown("POST", LOCKS, "a Codex session holds it: attach instead")["body"]["error"]
        assert error["message"] == fixture["message"]
        nested = root / "web"
        nested.mkdir()
        refused(relay.call("POST", LOCKS, key=uuid.uuid4().hex, body=lock_body(nested, None, me)),
                409, "NESTED_PROJECT_LOCKED")
        refused(relay.call("POST", LOCKS, key=uuid.uuid4().hex,
                           body=lock_body(protected, None, me)),
                422, "WORKSPACE_ROOT_NOT_ALLOWED", reason="protected_repository")
        refused(relay.call("POST", LOCKS, key=uuid.uuid4().hex,
                           body=lock_body(other, tmp_path / "elsewhere" / ".git", me)),
                422, "GIT_DIR_NOT_ALLOWED")
        refused(relay.call("POST", LOCKS, key=uuid.uuid4().hex,
                           body={"workspace_root": str(other)}),
                422, "INVALID_REQUEST_BODY")
        with live_process() as (pid, start):
            first = take(relay, other, other_git, holder(WINDOW, pid, start, "code-server"))
            lock_id = first.json()["lock"]["id"]
            # Alive and working: nobody is offered a takeover.
            refused(relay.call("POST", LOCKS, key=uuid.uuid4().hex,
                               body=lock_body(other, other_git, me)),
                    409, "PROJECT_LOCKED", takeover_allowed=False)
            rig.service.agents.store.update_lock(
                lock_id, heartbeat_at=iso(datetime.now(UTC) - timedelta(seconds=140)))
            rig.service.agents.locks._awake = lambda: 600.0
            stalled = refused(relay.call("POST", LOCKS, key=uuid.uuid4().hex,
                                         body=lock_body(other, other_git, me)),
                              409, "PROJECT_LOCKED", takeover_allowed=True)
        assert stalled["details"]["lock"]["verdict"] == "unresponsive"
        assert "attach_session_id" not in stalled["details"]
        assert keys(stalled) == keys(shown("POST", LOCKS, "an unresponsive Clarvis window holds it")
                                     ["body"]["error"])


def test_heartbeats_carry_the_command_and_a_release_needs_processes_gone(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    me = holder(OTHER_WINDOW, os.getpid(), own_start() or "")
    command = {"pid": 48210, "pgid": 48210, "start": "Sun Sep 13 05:41:07 2026", "comm": "npm"}
    with serving(rig) as relay:
        relay.ready()
        body = take(relay, root, git_dir, me).json()
        lock_id, token = body["lock"]["id"], body["lease_token"]
        held = lease(relay, lock_id, "heartbeat", token, beat(True, command))
        heartbeat = shown("POST", f"{LOCKS}/{{lid}}/heartbeat", "held")
        assert keys(held.json()) == keys(heartbeat["body"])
        assert held.json()["lock"]["waiting_on_you"] is True
        refused(lease(relay, lock_id, "heartbeat", token, beat(command={"pid": "x"})), 422,
                "INVALID_REQUEST_BODY")
        refused(lease(relay, lock_id, "heartbeat", None, beat()), 422, "INVALID_REQUEST_BODY")
        refused(lease(relay, lock_id, "release", token, {"processes_confirmed_gone": False}), 409,
                "PROCESSES_NOT_CONFIRMED_GONE")
        released = lease(relay, lock_id, "release", token, {"processes_confirmed_gone": True})
        assert (released.status_code, released.json()) == (200, {"lock": None})
        refused(lease(relay, lock_id, "heartbeat", token, beat()), 409, "LEASE_REVOKED",
                taken_over_by=None)
        assert relay.call("GET", LOCKS, params={"workspace_root": str(root)}).json() == {
            "lock": None}


def test_a_takeover_follows_the_rules_and_fences_the_old_window(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    codex_root, codex_git = project(rig, "codex-project")
    elsewhere, _ = project(rig, "elsewhere")
    me = {"id": WINDOW, "host": "code-server", "pid": os.getpid(), "pid_start": own_start()}
    takeover = f"{LOCKS}/{{lid}}/takeover"
    with serving(rig) as relay, live_process() as (pid, start):
        relay.ready()
        body = take(relay, root, git_dir, holder(OTHER_WINDOW, pid, start)).json()
        lock_id, old_lease, since = body["lock"]["id"], body["lease_token"], body["lock"][
            "holder"]["since"]
        path = f"{LOCKS}/{lock_id}/takeover"

        def ask(confirm: dict[str, Any] | None = None, root_: Path = root) -> Any:
            sent = {"workspace_root": str(root_), "window": me,
                    **({"confirm": confirm} if confirm else {})}
            return relay.call("POST", path, key=uuid.uuid4().hex, body=sent)

        refused(ask({"holder_window_id": OTHER_WINDOW, "holder_since": since}), 409,
                "HOLDER_ACTIVE")
        lease(relay, lock_id, "heartbeat", old_lease, beat(waiting=True))
        refused(ask(), 422, "CONFIRMATION_MISMATCH")
        refused(ask({"holder_window_id": "win-someone-else", "holder_since": since}), 422,
                "CONFIRMATION_MISMATCH")
        refused(ask({"holder_window_id": OTHER_WINDOW, "holder_since": since}, elsewhere), 422,
                "CONFIRMATION_MISMATCH")
        taken = ask({"holder_window_id": OTHER_WINDOW, "holder_since": since})
        assert taken.status_code == 200, taken.text
        assert keys(taken.json()) == keys(shown("POST", takeover,
                                                "taken over from an unresponsive window, confirmed")
                                          ["body"])
        assert (taken.json()["lock"]["holder"]["window_id"],
                taken.json()["lock"]["taken_over_from"]) == (WINDOW, OTHER_WINDOW)
        refused(lease(relay, lock_id, "heartbeat", old_lease, beat()), 409, "LEASE_REVOKED",
                taken_over_by=WINDOW)
        task = relay.started(codex_root, codex_git, task_id=str(uuid.uuid4()))
        codex_lock = task.view()["lock"]["id"]
        attach = relay.call("POST", f"{LOCKS}/{codex_lock}/takeover", key=uuid.uuid4().hex,
                            body={"workspace_root": str(codex_root), "window": me})
        refused(attach, 409, "ATTACH_INSTEAD", session_id=task.id)
    audits = rig.published("ravis.project_lock.taken_over")
    assert [(a["taken_over_from"], a["taken_over_by"]) for a in audits] == [(OTHER_WINDOW, WINDOW)]
    assert str(root) not in json.dumps(rig.events)


def test_a_gone_window_is_taken_without_confirmation_and_an_unresponsive_one_needs_it(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    (gone_root, gone_git), (slow_root, slow_git) = project(rig, "gone"), project(rig, "slow")
    me = {"id": WINDOW, "host": "code-server", "pid": os.getpid(), "pid_start": own_start()}
    with serving(rig) as relay, live_process() as (pid, start):
        relay.ready()
        rig.service.agents.locks._awake = lambda: 600.0
        gone = take(relay, gone_root, gone_git,
                    holder(OTHER_WINDOW, dead_pid(), "Sun Sep 13 05:10:02 2026")).json()
        assert gone["lock"]["verdict"] == "gone"
        taken = relay.call("POST", f"{LOCKS}/{gone['lock']['id']}/takeover", key=uuid.uuid4().hex,
                           body={"workspace_root": str(gone_root), "window": me})
        assert taken.status_code == 200, taken.text
        slow = take(relay, slow_root, slow_git, holder(OTHER_WINDOW, pid, start)).json()
        rig.service.agents.store.update_lock(
            slow["lock"]["id"], heartbeat_at=iso(datetime.now(UTC) - timedelta(seconds=140)))
        path = f"{LOCKS}/{slow['lock']['id']}/takeover"
        refused(relay.call("POST", path, key=uuid.uuid4().hex,
                           body={"workspace_root": str(slow_root), "window": me}),
                422, "CONFIRMATION_MISMATCH")
        confirm = {"holder_window_id": OTHER_WINDOW,
                   "holder_since": slow["lock"]["holder"]["since"]}
        assert relay.call("POST", path, key=uuid.uuid4().hex,
                          body={"workspace_root": str(slow_root), "window": me,
                                "confirm": confirm}).status_code == 200


def test_a_takeover_that_cant_confirm_the_old_command_gone_stays_leftover(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    me = {"id": WINDOW, "host": "code-server", "pid": os.getpid(), "pid_start": own_start()}
    windows = rig.service.agents.windows
    with serving(rig) as relay, live_process(own_group=True) as (pid, start):
        relay.ready()
        body = take(relay, root, git_dir,
                    holder(OTHER_WINDOW, dead_pid(), "Sun Sep 13 05:10:02 2026")).json()
        lock_id = body["lock"]["id"]
        command = {"pid": pid, "pgid": pid, "start": start, "comm": "sleep"}
        lease(relay, lock_id, "heartbeat", body["lease_token"], beat(command=command))
        windows.group_kill = GroupKill(FAST_KILL, kill=lambda _pid, _signal: None)
        path = f"{LOCKS}/{lock_id}/takeover"
        ask = {"workspace_root": str(root), "window": me}
        stuck = refused(relay.call("POST", path, key=uuid.uuid4().hex, body=ask), 409,
                        "PROCESSES_NOT_CONFIRMED_GONE")
        assert stuck["details"]["leftover"] == [{"pid": pid, "start": start, "comm": "sleep"}]
        fixture = shown("POST", f"{LOCKS}/{{lid}}/takeover",
                        "the old window's command couldn't be confirmed gone")["body"]["error"]
        assert (keys(stuck), stuck["message"]) == (keys(fixture), fixture["message"])
        read = relay.call("GET", LOCKS, params={"workspace_root": str(root)}).json()["lock"]
        assert (read["state"], read["holder"]["window_id"]) == ("leftover", OTHER_WINDOW)
        refused(lease(relay, lock_id, "heartbeat", body["lease_token"], beat()), 409,
                "LEASE_REVOKED", taken_over_by=WINDOW)
        windows.group_kill = GroupKill(FAST_KILL)
        taken = relay.call("POST", path, key=uuid.uuid4().hex, body=ask)
        assert taken.status_code == 200, taken.text
        assert taken.json()["lock"]["state"] == "running"
        assert all(row.pid != pid for row in snapshot() or [])


def test_a_codex_task_hands_its_lock_to_a_window_with_its_token(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    me = holder(OTHER_WINDOW, os.getpid(), own_start() or "")
    lock_file = lock_file_path(root, git_dir)
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nsay one")
        task.reaches("completed_needs_review")
        lock_id = task.view()["lock"]["id"]
        key = uuid.uuid4().hex
        path = f"{LOCKS}/{lock_id}/transfer"
        granted = relay.call("POST", path, key=key, body={"to": "clarvis_run"},
                             headers={"X-Agent-Session-Token": task.token})
        assert granted.status_code == 200, granted.text
        assert keys(granted.json()) == keys(
            shown("POST", f"{LOCKS}/{{lid}}/transfer", "Codex to Clarvis's engine, with the "
                  "session token")["body"])
        token = granted.json()["transfer_token"]
        again = relay.call("POST", path, key=key, body={"to": "clarvis_run"},
                           headers={"X-Agent-Session-Token": task.token})
        assert again.json()["transfer_token"] == token
        assert json.loads(lock_file.read_text())["state"] == "transferring"
        assert task.settle("transfer").json()["lock"]["state"] == "transferring"
        refused(relay.call("POST", LOCKS, key=uuid.uuid4().hex,
                           body=lock_body(root, git_dir, me, transfer_token="tt_wrong")),
                409, "LOCK_TRANSFER_INVALID")
        # The window replaces RAVIS's file before it asks (Clarvis `projectLock.ts`).
        lock_file.unlink()
        moved = take(relay, root, git_dir, me, transfer_token=token)
        assert moved.status_code == 201, moved.text
        assert moved.json()["lock"]["holder"]["window_id"] == OTHER_WINDOW
        assert task.view()["lock"] is None
        time.sleep(0.6)  # three of RAVIS's lock heartbeats
        assert json.loads(lock_file.read_text())["holder"]["window_id"] == OTHER_WINDOW


def test_an_expired_transfer_releases_nothing_and_the_window_takes_its_lock_back(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    me = holder(OTHER_WINDOW, os.getpid(), own_start() or "")
    store = rig.service.agents.store
    with serving(rig) as relay:
        relay.ready()
        body = take(relay, root, git_dir, me).json()
        lock_id, token = body["lock"]["id"], body["lease_token"]
        refused(lease(relay, lock_id, "transfer", token, {"to": "clarvis_run"}), 428,
                "IDEMPOTENCY_KEY_REQUIRED")
        granted = relay.call("POST", f"{LOCKS}/{lock_id}/transfer", key=uuid.uuid4().hex,
                             body={"to": "codex_session"}, headers={"X-Lock-Lease": token})
        assert granted.status_code == 200, granted.text
        expired = iso(datetime.now(UTC) - timedelta(seconds=1))
        store.update_lock(lock_id, transfer_expires_at=expired)
        create = relay.create(root, git_dir, transfer_token=granted.json()["transfer_token"])
        refused(create, 409, "LOCK_TRANSFER_INVALID")
        read = relay.call("GET", LOCKS, params={"workspace_root": str(root)}).json()["lock"]
        assert (read["id"], read["state"]) == (lock_id, "transferring")
        back = lease(relay, lock_id, "heartbeat", token, beat())
        assert back.json()["lock"]["state"] == "running"
        store.update_lock(lock_id, state="leftover")
        refused(relay.call("POST", f"{LOCKS}/{lock_id}/transfer", key=uuid.uuid4().hex,
                           body={"to": "codex_session"}, headers={"X-Lock-Lease": token}),
                409, "PROCESSES_NOT_CONFIRMED_GONE")


def test_a_window_adopting_its_file_lock_lets_a_superseded_codex_task_be_settled(
    tmp_path: Path,
) -> None:
    """F-A9: RAVIS restarted while a window held the checkout; it registers, then settles."""
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    me = holder(OTHER_WINDOW, os.getpid(), own_start() or "")
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir, text="RELAY\nsay one")
        task.reaches("completed_needs_review")
        rig.service.agents.store.update_lock(task.view()["lock"]["id"], state="superseded")
        # Only a window whose own file is in the checkout adopts: RAVIS's file names the task.
        refused(relay.call("POST", LOCKS, key=uuid.uuid4().hex,
                           body=lock_body(root, git_dir, me, adopt_file_lock=True)),
                409, "PROJECT_LOCKED")
        lock_file = window_file(root, git_dir, me)
        refused(task.post("settle-claim", {"window_id": WINDOW}), 409, "LOCK_SUPERSEDED")
        refused(relay.create(root, git_dir, task_id=str(uuid.uuid4()),
                             start={"kind": "resume", "thread_id": "t", "catch_up_text": "x"}),
                409, "LOCK_SUPERSEDED")
        adopted = relay.call("POST", LOCKS, key=uuid.uuid4().hex,
                             body=lock_body(root, git_dir, me, adopt_file_lock=True))
        assert adopted.status_code == 201, adopted.text
        assert keys(adopted.json()) == keys(shown("POST", LOCKS, "a file lock adopted after RAVIS "
                                                  "came back")["body"])
        assert task.view()["lock"] is None
        assert task.settle("idle").json()["state"] == "idle"
        assert json.loads(lock_file.read_text())["holder"]["window_id"] == OTHER_WINDOW
        assert has("session.state", state="idle")(task.frames(after=0,
                                                              until=has("session.state",
                                                                        state="idle")))
    assert SESSIONS
