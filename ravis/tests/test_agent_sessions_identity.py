"""Who may call the relay, and the one route that is different (design §3.5.1, §3.5.5, F-A3).

- **Every agent-session route, GETs included, refuses** anonymous callers, every admin credential
  (NERVIS's `admin.launcher` and the menu bar's `admin.owner_cli` alike), NERVIS's client and any
  application but Clarvis — **even when they hold the task's valid token**, and before it is read.
- A missing, wrong or another task's token looks exactly like no task at all.
- **The route table:** every agent-session route carries `require_agent_client`; the owner Stop is
  the one that doesn't, carries `require_owner_stop_caller`, and is alone on its router — so it can
  stop and do nothing else. The routes that start Codex work carry `require_owner_cli`.
- The owner Stop takes only the owner's admin applications, and never a session token.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute
from tests.agent_rig import SESSIONS, example, fixture, project, ready_rig, refused, serving

from ravis.agent.identity import require_agent_client, require_owner_stop_caller
from ravis.agent.refusals import A_CLARVIS_CREDENTIAL, ADMIN_REFUSED
from ravis.agent.routes import owner_router
from ravis.api.management.codex import require_owner_cli
from ravis.app import create_app
from ravis.config import Settings

OWNER_STOP = f"{SESSIONS}/{{sid}}/owner-stop"


def relay_routes(sid: str) -> list[tuple[str, str]]:
    base = f"{SESSIONS}/{sid}"
    return [
        ("POST", SESSIONS), ("GET", SESSIONS), ("GET", base), ("GET", f"{base}/events"),
        ("GET", f"{base}/transcript"), ("POST", f"{base}/turns"), ("POST", f"{base}/steer"),
        ("POST", f"{base}/interrupt"), ("POST", f"{base}/requests/rq_UNKNOWN/answer"),
        ("POST", f"{base}/presence"), ("POST", f"{base}/mode"), ("POST", f"{base}/leftover"),
        ("POST", f"{base}/settle-claim"), ("POST", f"{base}/settle"),
        ("POST", f"{base}/cancel"), ("DELETE", base), ("POST", f"{base}/reissue-token"),
    ]


def test_every_relay_route_refuses_anyone_but_clarvis_even_holding_a_valid_token(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    callers = {
        "anonymous": A_CLARVIS_CREDENTIAL, "admin.launcher": ADMIN_REFUSED,
        "admin.owner_cli": ADMIN_REFUSED, "client.nervis": A_CLARVIS_CREDENTIAL,
        "client.other": A_CLARVIS_CREDENTIAL,
    }
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir)
        task.reaches("running")
        for method, path in relay_routes(task.id):
            for caller, message in callers.items():
                body = {} if method == "POST" else None
                response = relay.call(method, path, caller=caller, token=task.token,
                                      key=uuid.uuid4().hex, body=body)
                error = refused(response, 403, "AGENT_CLIENT_NOT_ALLOWED")
                assert error["message"] == message, (method, path, caller)
        # Nothing any of them sent reached the task.
        assert task.view()["state"] == "running"
        assert not rig.server.received("turn/interrupt")
    shown = example("GET", "/api/v1/agent-sessions/{sid}",
                    "an admin credential holding a valid token")
    assert shown["body"]["error"]["message"] == ADMIN_REFUSED


def test_a_missing_wrong_or_other_tasks_token_looks_like_no_task(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path)
    first, second = project(rig, "first"), project(rig, "second")
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(*first)
        other = relay.started(*second, task_id=str(uuid.uuid4()))
        wrong = "ast_FIXTURE_wrong_token_not_a_secret_WWWWWWWWWW"
        for token in (None, wrong, other.token):
            for method, path in relay_routes(task.id)[2:-1]:
                response = relay.call(method, path, token=token, key=uuid.uuid4().hex,
                                      body={} if method == "POST" else None)
                refused(response, 404, "AGENT_SESSION_NOT_FOUND")
        unknown = relay.call("GET", f"{SESSIONS}/as_01J9ZK0000000000UNKNOWN0", token=task.token)
        refused(unknown, 404, "AGENT_SESSION_NOT_FOUND")


def _served(routes: list[Any]) -> Iterator[APIRoute]:
    """Every route the application serves, including those inside included routers."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _served(inner.routes)


def _dependencies(route: APIRoute) -> set[object]:
    return {dependency.dependency for dependency in route.dependencies}


def test_the_route_table_keeps_the_owner_stop_apart_and_stop_only() -> None:
    settings = Settings(database_path=":memory:", codex_calibration=True,
                        _env_file=None)  # type: ignore[call-arg]
    api = create_app(settings).app  # type: ignore[attr-defined]
    routes = list(_served(api.routes))
    relay = [r for r in routes if r.path.startswith(("/api/v1/agent-sessions",
                                                     "/api/v1/project-locks"))]
    owner = [r for r in relay if require_owner_stop_caller in _dependencies(r)]
    assert [(sorted(r.methods), r.path) for r in owner] == [(["POST"], OWNER_STOP)]
    for route in relay:
        guarded = require_agent_client in _dependencies(route)
        assert guarded is (route not in owner), route.path
    # Alone on its router: nothing that answers, approves, steers, starts or reads rides along.
    assert [(sorted(r.methods), r.path) for r in owner_router.routes] == [  # type: ignore[attr-defined]
        (["POST"], OWNER_STOP)
    ]
    listed = fixture("agent-sessions.json")["routes"] + fixture("project-locks.json")["routes"]
    contract = {(r["method"], r["path"].split("?")[0]) for r in listed}
    served = {(method, route.path) for route in relay for method in route.methods}
    stream = fixture("event-stream.json")["route"]
    assert served == contract | {(stream["method"], stream["path"]), ("POST", OWNER_STOP)}
    starting_work = {("POST", "/api/v1/codex/reprove"), ("POST", "/api/v1/codex/calibration/runs"),
                     ("GET", "/api/v1/codex/calibration/runs"),
                     ("GET", "/api/v1/codex/calibration/runs/{run_id}")}
    for method, path in starting_work:
        route = next(r for r in routes if r.path == path and method in r.methods)
        assert require_owner_cli in _dependencies(route), path


def test_the_owner_stop_takes_only_the_owners_admin_applications_and_no_token(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)
    rig.app.app.state.credentials.store("admin.other", "admin-other-secret")
    with serving(rig) as relay:
        relay.ready()
        task = relay.started(root, git_dir)
        turn = task.reaches("running")["codex"]["active_turn_id"]
        path = f"{SESSIONS}/{task.id}/owner-stop"
        body = {"source": "menu_bar", "confirm": {"project": "add-utc-demo", "turn_id": turn}}
        shown = next(case for case in fixture("owner-stop.json")["examples"]
                     if case["name"] == "anonymous")["response"]["body"]["error"]["message"]
        for caller in ("client.clarvis", "client.nervis", "anonymous"):
            error = refused(relay.call("POST", path, caller=caller, key=uuid.uuid4().hex,
                                       body=body), 403, "OWNER_STOP_NOT_ALLOWED")
            assert error["message"] == shown
        other_admin = relay.call("POST", path, caller="anonymous", key=uuid.uuid4().hex, body=body,
                                 headers={"Authorization": "Bearer admin-other-secret"})
        refused(other_admin, 403, "OWNER_STOP_NOT_ALLOWED")
        with_token = relay.call("POST", path, caller="admin.owner_cli", token=task.token,
                                key=uuid.uuid4().hex, body=body)
        refused(with_token, 400, "TOKEN_NOT_ACCEPTED")
        refused(relay.call("POST", path, caller="admin.owner_cli", body=body), 428,
                "IDEMPOTENCY_KEY_REQUIRED")
        assert task.view()["state"] == "running"
        assert not rig.server.received("turn/interrupt")
