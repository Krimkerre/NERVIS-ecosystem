#!/usr/bin/env python3
"""One suite, three services, their real routes (§16 item 10).

**Why this is a repository tool and not three test files.** Each service already
tests its own error helper, and all three helpers passed while the services
emitted three different shapes on the wire — because a helper test proves the
helper, and what §4.5 governs is what a *route* returns. The gap those tests
could not see:

    RAVIS   /api/v1/providers/…    {"error": {"message", "type"}}
    SIRVIS  /api/v1/benchmark-jobs {"error": {"code", "message", "details", …}}
    NERVIS  /ecosystem/events      {"detail": {"code", "message", …}}

Three services, three dialects, one specification. So the suite is one file that
imports all three applications, drives them through `TestClient`, and reads what
comes back off the wire — the only place the contract is actually observable.

It lives in `tools/` beside the other gates rather than in a package's tests
because it belongs to none of them: a conformance check owned by one service is
a check that service can quietly relax.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
for package in ("nervis", "ravis", "sirvis", "protocol"):
    sys.path.insert(0, str(ROOT / package / "src"))

os.environ.setdefault("NERVIS_SERVED_HOSTS", '["127.0.0.1","localhost","::1","testserver"]')
os.environ.setdefault("RAVIS_ALLOWED_HOSTS", '["127.0.0.1","localhost","::1","testserver"]')
os.environ.setdefault("SIRVIS_ALLOWED_HOSTS", '["127.0.0.1","localhost","::1","testserver"]')

from fastapi.testclient import TestClient

#: Every field §4.5 names. `retryable` is in the specification and was missing
#: from two of the three envelopes — a client cannot tell "wait and try again"
#: from "this will never work" without it, which is the field's whole purpose.
REQUIRED = ("code", "message", "retryable", "details", "request_id", "trace_id")

failures: list[str] = []


def check(service: str, where: str, body: Any, status: int) -> None:
    """Assert one response carries the canonical envelope."""
    if not isinstance(body, dict) or "error" not in body:
        failures.append(
            f"{service} {where}: {status} carried no `error` object — got "
            f"{sorted(body) if isinstance(body, dict) else type(body).__name__}"
        )
        return
    envelope = body["error"]
    missing = [field for field in REQUIRED if field not in envelope]
    if missing:
        failures.append(f"{service} {where}: {status} envelope omits {', '.join(missing)}")
        return
    if not isinstance(envelope["retryable"], bool):
        failures.append(f"{service} {where}: `retryable` is not a boolean")


def nervis_client() -> Any:
    from nervis.app import create_app
    from nervis.config import Settings

    settings = Settings(  # type: ignore[call-arg]
        database_path=":memory:", workspace_path=str(ROOT), _env_file=None
    )
    return TestClient(create_app(settings))


def ravis_client() -> Any:
    from ravis.app import create_app
    from ravis.config import Settings

    return TestClient(create_app(Settings(database_path=":memory:", _env_file=None)))  # type: ignore[call-arg]


def sirvis_client() -> Any:
    from sirvis.app import create_app
    from sirvis.config import Settings

    return TestClient(create_app(Settings(database_path=":memory:", _env_file=None)))  # type: ignore[call-arg]


def main() -> int:
    # **More than one refusal each, and deliberately different kinds.** A gate
    # that probes one route per service passes as soon as that route is fixed,
    # and says nothing about the next one. These are the shapes a client meets:
    # a service's own refusal, a framework 404 for a path that does not exist,
    # and a media-type refusal raised before any handler runs.
    with ravis_client() as client:
        for where, answered in (
            ("PUT /api/v1/providers/{name}/enabled",
             client.put("/api/v1/providers/openai/enabled", json={"enabled": True})),
            ("GET /api/v1/nothing-here", client.get("/api/v1/nothing-here")),
            ("PUT /api/v1/pools/chat/members (form content type)",
             client.put("/api/v1/pools/chat/members", content="{}",
                        headers={"Content-Type": "text/plain"})),
        ):
            check("RAVIS", where, answered.json(), answered.status_code)

    with sirvis_client() as client:
        for where, answered in (
            ("POST /api/v1/benchmark-jobs", client.post("/api/v1/benchmark-jobs", json={})),
            ("GET /api/v1/nothing-here", client.get("/api/v1/nothing-here")),
        ):
            check("SIRVIS", where, answered.json(), answered.status_code)

    with nervis_client() as client:
        for where, answered in (
            ("POST /api/v1/instances", client.post("/api/v1/instances", json={})),
            ("GET /api/v1/nothing-here", client.get("/api/v1/nothing-here")),
        ):
            check("NERVIS", where, answered.json(), answered.status_code)

    if failures:
        print(f"{len(failures)} conformance failure(s) against §4.5's error envelope:\n")
        for failure in failures:
            print(f"  • {failure}")
        print(
            "\nThe envelope is one contract. A service answering in its own dialect\n"
            "is a client having to learn three, and a field nobody publishes is a\n"
            "field nobody can act on."
        )
        return 1
    print("3 services answer §4.5's error envelope on their own routes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
