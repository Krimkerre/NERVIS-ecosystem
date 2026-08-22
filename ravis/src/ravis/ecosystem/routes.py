"""The five MEP metadata endpoints (ECOSYSTEM_RUNBOOK.md §4.1).

These are what makes RAVIS discoverable rather than assumed. NERVIS reads them to
decide what to render; a peer reads them to decide whether it can talk to this
build at all. They answer without touching a provider, a model or the network,
which is why they stay truthful when everything downstream is broken.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from ravis.ecosystem.capabilities import (
    BUILD_VERSION,
    COMPATIBLE_PROTOCOL_MAX,
    COMPATIBLE_PROTOCOL_MIN,
    PROTOCOL_VERSION,
    capability_snapshot,
)

router = APIRouter(prefix="/ecosystem", tags=["ecosystem"])

# One instance identity per process lifetime (runbook §4.1). Generated at import
# rather than configured: it must differ between two processes on one machine,
# which is exactly what distinguishes a restart from a second instance.
INSTANCE_ID = uuid.uuid4().hex
STARTED_AT = time.time()

# How often the SSE stream emits a comment heartbeat. The runbook requires at
# least every 15 seconds; a proxy that sees no bytes will otherwise close what it
# reasonably concludes is a dead connection.
HEARTBEAT_SECONDS = 10.0


@router.get("/health")
async def read_health(request: Request) -> dict[str, Any]:
    """Liveness and readiness, reported separately and truthfully.

    The runbook is explicit that `ready` is independently truthful and that a
    successful TCP connect is not readiness. So this reports what RAVIS has
    actually verified about itself — that its database answered — rather than
    the fact that it managed to respond, which is self-evident from the response.
    """
    checks = [_database_check(request)]
    everything_passed = all(check["status"] == "pass" for check in checks)
    return {
        "status": "healthy" if everything_passed else "degraded",
        "live": True,
        "ready": everything_passed,
        "checked_at": _timestamp(),
        "checks": checks,
    }


def _database_check(request: Request) -> dict[str, Any]:
    """Confirm the database answers a trivial query.

    Deliberately trivial: this runs on every health poll, and a check that costs
    real work becomes the reason the service is unhealthy.
    """
    try:
        request.app.state.database.connection.execute("SELECT 1").fetchone()
    except Exception as failure:  # noqa: BLE001 — any failure here means not ready
        return {"name": "database", "status": "fail", "detail": str(failure)}
    return {"name": "database", "status": "pass"}


@router.get("/identity")
async def read_identity(request: Request) -> dict[str, Any]:
    """Who this process is, stably enough to correlate across restarts."""
    return {
        "service_id": request.app.state.service_id,
        "service_type": "ravis",
        "instance_id": INSTANCE_ID,
        "machine_id": request.app.state.machine_id,
        "api_version": "1",
        "protocol_version": PROTOCOL_VERSION,
        "build_version": BUILD_VERSION,
        "started_at": _timestamp(STARTED_AT),
    }


@router.get("/capabilities")
async def read_capabilities(request: Request) -> dict[str, Any]:
    """What this build can actually do right now — see capabilities.py."""
    return capability_snapshot(request.app.state.capability_revision)


@router.get("/version")
async def read_version() -> dict[str, Any]:
    """Versions and the compatible protocol range.

    Runbook §4.1 requires this to answer even when `ready` is false: a peer
    diagnosing an incompatibility needs it precisely when the service is unwell,
    so it depends on nothing that can be unwell.
    """
    return {
        "build_version": BUILD_VERSION,
        "api_version": "1",
        "protocol_version": PROTOCOL_VERSION,
        "schema_versions": {"mep": PROTOCOL_VERSION},
        "compatible_protocol": {"min": COMPATIBLE_PROTOCOL_MIN, "max": COMPATIBLE_PROTOCOL_MAX},
    }


@router.get("/events")
async def stream_events(response: Response) -> StreamingResponse:
    """The canonical v1 event stream.

    M0 publishes no events — RAVIS has nothing to say until it routes something —
    but the stream exists and heartbeats, because a consumer must be able to
    connect, hold the connection and reconnect before there is traffic to carry.
    Building it later would mean discovering the reconnect semantics under load.
    """
    del response  # FastAPI supplies it; the StreamingResponse below owns headers.
    return StreamingResponse(
        _heartbeat_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Tells nginx and friends not to buffer, which would defeat the point
            # of a stream by delivering it in one piece at the end.
            "X-Accel-Buffering": "no",
        },
    )


async def _heartbeat_stream() -> AsyncIterator[bytes]:
    """Emit the reconnect interval, then a comment every HEARTBEAT_SECONDS.

    The `retry:` line tells the client how long to wait before reconnecting, so
    the reconnect policy is the server's decision rather than each client's guess.
    Comments are ignored by EventSource but keep the connection observably alive.
    """
    yield b"retry: 3000\n\n"
    while True:
        yield b": heartbeat\n\n"
        await asyncio.sleep(HEARTBEAT_SECONDS)


def _timestamp(moment: float | None = None) -> str:
    """An RFC 3339 UTC timestamp, which is what every MEP field expects."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(moment))
