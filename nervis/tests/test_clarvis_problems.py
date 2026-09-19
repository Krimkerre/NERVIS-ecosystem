"""A Clarvis window's problems by checker (`clarvis.diagnostics.summary@1`, Clarvis 0.17.20).

NERVIS reads each window's `/v1/diagnostics` beside its status for Diagnostics → Clarvis. Only
counts arrive, and they are shaped again here: a checker name that could be a path or a sentence,
and a count that is not a count, never reach the page.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from fastapi.testclient import TestClient
from tests.test_m8b_status import api, register  # noqa: F401 - `api` is a fixture

from nervis.bridges import MAX_SOURCES, interpret_problems

ZERO = {"errors": 0, "warnings": 0, "information": 0, "hints": 0}


def a_bridge(routes: dict[str, tuple[int, Any]]) -> HTTPServer:
    """A fake Bridge on a real loopback port that answers each path as `routes` says."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
            status, body = routes.get(self.path, (404, {"error": "no route"}))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode("utf-8"))

        def log_message(self, *_: Any) -> None:
            """Silent."""

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_only_a_short_name_with_whole_counts_survives() -> None:
    kept = interpret_problems({"by_source": {
        "ts": {**ZERO, "errors": 2},
        "/Users/someone/plan.ts": {**ZERO, "errors": 1},
        "eslint": {**ZERO, "warnings": -1},
        "pylint": {**ZERO, "hints": True},
        "ruff": "three",
    }})
    assert kept == {"ts": {**ZERO, "errors": 2}}
    assert interpret_problems({"by_source": [1, 2]}) == {}


def test_no_more_checkers_than_clarvis_sends() -> None:
    many = {f"c{n}": ZERO for n in range(MAX_SOURCES + 10)}
    assert len(interpret_problems({"by_source": many})) == MAX_SOURCES


def test_the_window_diagnostics_carry_its_problems_by_checker(api: TestClient) -> None:  # noqa: F811
    bridge = a_bridge({
        "/v1/status": (200, {"state": "idle"}),
        "/v1/diagnostics": (200, {"errors": 2, "by_source": {"ts": {**ZERO, "errors": 2}},
                                  "read_at": "2026-09-19T08:00:00Z"}),
    })
    try:
        instance_id, _ = register(api, bridge.server_address[1])
        body = api.get(f"/api/v1/registry/instances/clarvis/{instance_id}/diagnostics").json()
        assert body["problems"] == {"reachable": True, "detail": "",
                                    "by_source": {"ts": {**ZERO, "errors": 2}}}
    finally:
        bridge.shutdown()


def test_an_older_clarvis_is_said_to_publish_none(api: TestClient) -> None:  # noqa: F811
    bridge = a_bridge({"/v1/status": (200, {"state": "idle"})})
    try:
        instance_id, _ = register(api, bridge.server_address[1])
        body = api.get(f"/api/v1/registry/instances/clarvis/{instance_id}/diagnostics").json()
        assert body["problems"]["detail"] == "this Clarvis does not publish a problems summary"
        assert body["status"]["state"] == "idle", "the rest of the window still reads"
    finally:
        bridge.shutdown()
