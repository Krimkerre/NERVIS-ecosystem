"""A ready RAVIS for the agent-session relay's tests, served on a real loopback port (runbook §7).

**Why a real port.** A task's event stream is an endless response, and `TestClient` only hands a
response back once the application has finished writing it. So these tests run the whole
application under uvicorn on a free port and speak HTTP to it with httpx, the way Clarvis does:
the same routes, admission layer and streaming. Everything underneath is `codex_rig`'s — the fake
Codex programs, the shortened clocks, and a credential for each caller the contract names.

**Ready** means what a real task needs: a tested build with its file rules proven and a profile
pinned, a signed-in account the owner confirmed, and a coding folder to put projects in. A test
that needs Codex *not* ready says so (`proven=False`).

A task's first text starting `RELAY` is acted out line by line by the fake app-server
(`tests/fake_codex_relay.py`).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from tests.codex_rig import (
    ALLOWANCE,
    ENVELOPE_FIELDS,
    PROFILE,
    SIGNED_IN,
    CodexRig,
    codex_rig,
    confirmed_record,
    eventually,
)

from ravis.agent.roots import root_hash
from ravis.agent.tokens import new_id
from ravis.codex.lock_file import iso
from ravis.codex.lock_rule import own_start

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "relay-contract"
TASK_ID = "8b1c2d3e-4f50-4a61-9b72-83c4d5e6f708"
WINDOW = "win-code-server-7f3a2b"
OTHER_WINDOW = "win-desktop-1c9e4d"
TOKEN = re.compile(r"^ast_[A-Za-z0-9_-]{43}$")
SESSIONS = "/api/v1/agent-sessions"


class FakeClock:
    """A monotonic clock a test moves by hand: the unanswered policy's 30 minutes in a moment."""

    def __init__(self) -> None:
        # Whole seconds, so whole-second steps add up exactly: a fractional start made 178 × 40 + 80
        # land a hair under 7200 on some runs, and a test at the policy's exact limit flaked.
        self.now = float(int(time.monotonic()))

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def fixture(document: str) -> Any:
    return json.loads((FIXTURES / document).read_text())


def example(method: str, path: str, name: str, document: str = "agent-sessions.json") -> Any:
    """One example's response from a fixture's routes, by its name."""
    routes = fixture(document)["routes"]
    route = next(r for r in routes if (r["method"], r["path"]) == (method, path))
    return next(case["response"] for case in route["examples"] if case["name"] == name)


def keys(value: Any) -> Any:
    """A body's shape: every key however deep, each list read from its first item."""
    if isinstance(value, dict):
        return {key: keys(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [keys(value[0])] if value else []
    return None


def refused(response: httpx.Response, status: int, code: str, **details: Any) -> dict[str, Any]:
    """The MEP envelope with this status and code, and at least these details."""
    assert response.status_code == status, (response.status_code, response.text)
    error = response.json()["error"]
    assert set(error) == ENVELOPE_FIELDS
    assert error["code"] == code, error
    for name, value in details.items():
        assert error["details"].get(name) == value, error
    return error  # type: ignore[no-any-return]


def ready_rig(
    tmp_path: Path,
    *,
    scenario: dict[str, Any] | None = None,
    clock: Callable[[], float] | None = None,
    proven: bool = True,
    **settings: Any,
) -> CodexRig:
    confirmed_record()
    coding = Path(os.path.realpath(tmp_path)) / "coding"
    coding.mkdir(exist_ok=True)
    return codex_rig(
        tmp_path,
        scenario={"account": SIGNED_IN, "rate_limits": ALLOWANCE, **(scenario or {})},
        proven=proven,
        profile=PROFILE,
        agent_clock=clock or time.monotonic,
        agent_allowed_roots=[str(coding)],
        # Tests poll; a named caller's usual 600 a minute is RAVIS's to test elsewhere.
        rate_limit_per_minute=100_000,
        **settings,
    )


def project(rig: CodexRig, name: str = "add-utc-demo", *, git: bool = True
            ) -> tuple[Path, Path | None]:
    """A project folder in the rig's coding folder: with a `.git` folder, or without git."""
    root = Path(os.path.realpath(rig.folder)) / "coding" / name
    root.mkdir(parents=True, exist_ok=True)
    if not git:
        return root, None
    (root / ".git").mkdir(exist_ok=True)
    return root, root / ".git"


def create_body(root: Path, git_dir: Path | None, *, text: str = "RELAY\nwait",
                window: str = WINDOW, start: dict[str, Any] | None = None,
                transfer_token: str | None = None, max_steps: int = 25, mode: str = "agent",
                task_id: str = TASK_ID) -> dict[str, Any]:
    return {
        "workspace_root": str(root), "clarvis_task_id": task_id,
        "window": {"id": window, "host": "code-server"}, "mode": mode, "model": "",
        "branch": {"name": "clarvis/add-utc", "head_commit": "3f9c2e1d8b7a6f5e4d3c2b1a0f9e8d7c"},
        "git_dir": str(git_dir) if git_dir else "",
        "start": start or {"kind": "brief", "text": text},
        "lock": {"transfer_token": transfer_token}, "limits": {"max_steps": max_steps},
    }


def clarvis_lock(rig: CodexRig, root: Path, **changes: Any) -> dict[str, Any]:
    """A Clarvis-engine run's lock row, as M29's fourth increment's routes will write one."""
    stamp = iso(__import__("datetime").datetime.now(__import__("datetime").UTC))
    row = {
        "id": new_id("pl_"), "workspace_root": str(root), "root_hash": root_hash(root),
        "holder_kind": "clarvis_run", "holder_session_id": None, "holder_window_id": OTHER_WINDOW,
        "holder_host": "desktop", "holder_pid": os.getpid(), "holder_pid_start": own_start(),
        "state": "running", "waiting_on_you": 0, "heartbeat_at": stamp, "acquired_at": stamp,
        **changes,
    }
    rig.service.agents.store.insert_lock(row)
    return row


@dataclass(frozen=True)
class Frame:
    id: int | None
    event: str
    data: dict[str, Any]


def parse_frames(lines: Iterator[str]) -> Iterator[Frame | None]:
    """SSE frames as they arrive; None for each heartbeat comment."""
    current: dict[str, str] = {}
    for line in lines:
        if not line:
            if "data" in current:
                event_id = int(current["id"]) if "id" in current else None
                yield Frame(event_id, current.get("event", "message"), json.loads(current["data"]))
            current = {}
        elif line.startswith(":"):
            yield None
        else:
            name, _, value = line.partition(":")
            current[name] = value[1:] if value.startswith(" ") else value


def named(frames: list[Frame], event: str) -> list[dict[str, Any]]:
    return [frame.data for frame in frames if frame.event == event]


@dataclass
class Relay:
    """An HTTP client on a served RAVIS, speaking as a Clarvis window unless told otherwise."""

    rig: CodexRig
    http: httpx.Client

    def call(self, method: str, path: str, *, caller: str = "client.clarvis",
             token: str | None = None, key: str | None = None, body: Any = None,
             headers: dict[str, str] | None = None, params: dict[str, str] | None = None
             ) -> httpx.Response:
        sent = {**self.rig.caller(caller), **(headers or {})}
        if token is not None:
            sent["X-Agent-Session-Token"] = token
        if key is not None:
            sent["Idempotency-Key"] = key
        request = self.http.build_request(method, path, headers=sent, json=body, params=params)
        response = self.http.send(request, stream=True)
        if "text/event-stream" in response.headers.get("content-type", ""):
            # An event stream never ends and its heartbeats keep the read timeout from firing, so
            # reading its body would hang the test for good: a call answers with the status and
            # headers alone (`Task.frames` reads streams). A test expecting a refusal fails at once.
            response.close()
            return response
        try:
            response.read()
        finally:
            response.close()
        return response

    def ready(self) -> dict[str, Any]:
        """Wait until Codex reports what its tests expect: signed in, or paused, but settled."""
        def settled() -> dict[str, Any] | None:
            body = self.call("GET", "/api/v1/codex", caller="client.nervis").json()
            return body if body["state"] not in ("checking", "runtime_down") else None
        return eventually(settled, 15, "Codex's state settled")  # type: ignore[no-any-return]

    def create(self, root: Path, git_dir: Path | None, *, key: str | None = None,
               caller: str = "client.clarvis", **changes: Any) -> httpx.Response:
        return self.call("POST", SESSIONS, caller=caller, key=key or uuid.uuid4().hex,
                         body=create_body(root, git_dir, **changes))

    def started(self, root: Path, git_dir: Path | None, **changes: Any) -> Task:
        response = self.create(root, git_dir, **changes)
        assert response.status_code == 201, response.text
        body = response.json()
        return Task(self, body["session"]["id"], body["session_token"], body)


@dataclass
class Task:
    relay: Relay
    id: str
    token: str
    created: dict[str, Any]

    def post(self, action: str, body: Any = None, *, keyed: bool = False, key: str | None = None,
             caller: str = "client.clarvis", token: str | None = None) -> httpx.Response:
        return self.relay.call(
            "POST", f"{SESSIONS}/{self.id}/{action}", caller=caller,
            token=self.token if token is None else token,
            key=key or (uuid.uuid4().hex if keyed else None), body={} if body is None else body)

    def view(self) -> dict[str, Any]:
        response = self.relay.call("GET", f"{SESSIONS}/{self.id}", token=self.token)
        assert response.status_code == 200, response.text
        return response.json()  # type: ignore[no-any-return]

    def reaches(self, state: str, seconds: float = 10.0) -> dict[str, Any]:
        seen: list[str] = []

        def reached() -> dict[str, Any] | None:
            view = self.view()
            seen.append(view["state"])
            return view if view["state"] == state else None
        try:
            return eventually(reached, seconds)  # type: ignore[no-any-return]
        except AssertionError:
            raise AssertionError(f"never reached {state}; saw {sorted(set(seen))}") from None

    def pending(self, seconds: float = 10.0) -> dict[str, Any]:
        """The first open request, once Codex has asked one."""
        def asked() -> dict[str, Any] | None:
            requests = self.view()["pending_requests"]
            return requests[0] if requests else None
        return eventually(asked, seconds, "a request opened")  # type: ignore[no-any-return]

    def answer(self, request_id: str, decision: dict[str, Any], *, window: str = WINDOW,
               key: str | None = None) -> httpx.Response:
        return self.post(f"requests/{request_id}/answer", {"decision": decision},
                         key=key or f"{request_id}:{window}")

    def presence(self, connected: bool, *, window: str = WINDOW) -> httpx.Response:
        return self.post("presence", {"window_id": window, "host": "code-server",
                                       "panel_connected": connected})

    def settle(self, next_step: str = "idle", *, window: str = WINDOW,
               key: str | None = None) -> httpx.Response:
        claim = self.post("settle-claim", {"window_id": window})
        assert claim.status_code == 200, claim.text
        return self.post("settle", {"claim_id": claim.json()["claim_id"], "commit": "9a8b7c6d",
                                    "checkpoint_saved": True, "next": next_step},
                         key=key or uuid.uuid4().hex)

    def frames(self, *, until: Callable[[list[Frame]], bool], after: int | None = None,
               last_event_id: int | None = None, seconds: float = 10.0) -> list[Frame]:
        """Read this task's stream until `until` holds for the frames seen so far."""
        headers = {**self.relay.rig.caller("client.clarvis"), "X-Agent-Session-Token": self.token}
        if last_event_id is not None:
            headers["Last-Event-ID"] = str(last_event_id)
        params = {"window_id": WINDOW, "host": "code-server"}
        if after is not None:
            params["after"] = str(after)
        seen: list[Frame] = []
        deadline = time.monotonic() + seconds
        with self.relay.http.stream("GET", f"{SESSIONS}/{self.id}/events", headers=headers,
                                    params=params, timeout=httpx.Timeout(seconds, read=3.0)
                                    ) as response:
            assert response.status_code == 200, response.read()
            assert "text/event-stream" in response.headers["content-type"]
            for frame in parse_frames(response.iter_lines()):
                if frame is not None:
                    seen.append(frame)
                if until(seen):
                    return seen
                if time.monotonic() > deadline:
                    break
        raise AssertionError(f"the stream never showed it; saw {[f.event for f in seen]}")


def has(event: str, **fields: Any) -> Callable[[list[Frame]], bool]:
    """`until` for `Task.frames`: a frame of this event carrying these fields."""
    def found(frames: list[Frame]) -> bool:
        return any(frame.event == event and all(frame.data.get(k) == v for k, v in fields.items())
                   for frame in frames)
    return found


@contextlib.contextmanager
def serving(rig: CodexRig) -> Iterator[Relay]:
    """The rig's whole RAVIS under uvicorn on a free loopback port, lifespan and all."""
    config = uvicorn.Config(rig.app, host="127.0.0.1", port=0, log_level="critical",
                            lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    eventually(lambda: server.started, 20, "uvicorn started")
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15.0) as http:
            yield Relay(rig, http)
    finally:
        server.should_exit = True
        thread.join(30)
