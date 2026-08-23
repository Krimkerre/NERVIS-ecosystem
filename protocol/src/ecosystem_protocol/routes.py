"""The five MEP metadata endpoints (ECOSYSTEM_RUNBOOK.md §4.1).

These are what makes a service discoverable rather than assumed. NERVIS reads
them to decide what to render; a peer reads them to decide whether it can talk
to this build at all. They answer without touching a provider, a model or the
network, which is why they stay truthful when everything downstream is broken.

One implementation, three services. Written once because a health endpoint that
means something slightly different per service is worse than no health endpoint:
the whole value is that a consumer can ask any of them the same question.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from ecosystem_protocol.capabilities import capability_snapshot
from ecosystem_protocol.surface import EcosystemSurface
from ecosystem_protocol.version import (
    COMPATIBLE_PROTOCOL_MAX,
    COMPATIBLE_PROTOCOL_MIN,
    PROTOCOL_VERSION,
)

router = APIRouter(prefix="/ecosystem", tags=["ecosystem"])

# How often the SSE stream emits a comment heartbeat. The runbook requires at
# least every 15 seconds; a proxy that sees no bytes will otherwise close what it
# reasonably concludes is a dead connection.
HEARTBEAT_SECONDS = 10.0


def surface_of(request: Request) -> EcosystemSurface:
    """The host's declaration, attached at startup.

    Fails loudly rather than defaulting. A service that mounted this router
    without supplying a surface has a wiring bug, and inventing an identity for
    it would publish a lie to every peer that asked.
    """
    surface = getattr(request.app.state, "ecosystem", None)
    if not isinstance(surface, EcosystemSurface):
        raise RuntimeError(
            "app.state.ecosystem must be an EcosystemSurface before mounting this router"
        )
    return surface


@router.get("/health")
async def read_health(request: Request) -> dict[str, Any]:
    """Liveness and readiness, reported separately and truthfully.

    The runbook is explicit that `ready` is independently truthful and that a
    successful TCP connect is not readiness. So this reports what the service has
    actually verified about itself rather than the fact that it managed to
    respond, which is self-evident from the response arriving at all.

    A service with no checks registered is ready. That is a real answer for a
    service with nothing to verify, not a default standing in for one.
    """
    checks = surface_of(request).run_checks()
    passed = all(check["status"] == "pass" for check in checks)
    return {
        "status": "healthy" if passed else "degraded",
        "live": True,
        "ready": passed,
        "checked_at": timestamp(),
        "checks": checks,
    }


@router.get("/identity")
async def read_identity(request: Request) -> dict[str, Any]:
    """Who this process is, stably enough to correlate across restarts."""
    surface = surface_of(request)
    return {
        "service_id": surface.service_id,
        "service_type": surface.service_type,
        "instance_id": surface.instance_id,
        "machine_id": surface.machine_id,
        "api_version": "1",
        "protocol_version": PROTOCOL_VERSION,
        "build_version": surface.build_version,
        "started_at": timestamp(surface.started_at),
    }


@router.get("/capabilities")
async def read_capabilities(request: Request) -> dict[str, Any]:
    """What this build can actually do right now — see `capabilities.py`."""
    surface = surface_of(request)
    return capability_snapshot(surface.revision, surface.declared)


@router.get("/version")
async def read_version(request: Request) -> dict[str, Any]:
    """Versions and the compatible protocol range.

    §4.1 requires this to answer even when `ready` is false: a peer diagnosing an
    incompatibility needs it precisely when the service is unwell, so it depends
    on nothing that can be unwell — no database, no check, no downstream.
    """
    return {
        "build_version": surface_of(request).build_version,
        "api_version": "1",
        "protocol_version": PROTOCOL_VERSION,
        "schema_versions": {"mep": PROTOCOL_VERSION},
        "compatible_protocol": {"min": COMPATIBLE_PROTOCOL_MIN, "max": COMPATIBLE_PROTOCOL_MAX},
    }


@router.get("/events")
async def stream_events(response: Response) -> StreamingResponse:
    """The canonical v1 event stream.

    A service publishes no events until it has something to say, but the stream
    exists and heartbeats from the start, because a consumer must be able to
    connect, hold the connection and reconnect before there is traffic to carry.
    Building it later would mean discovering the reconnect semantics under load.
    """
    del response  # FastAPI supplies it; the StreamingResponse below owns headers.
    return StreamingResponse(
        heartbeat_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Tells nginx and friends not to buffer, which would defeat the point
            # of a stream by delivering it in one piece at the end.
            "X-Accel-Buffering": "no",
        },
    )


async def heartbeat_stream() -> AsyncIterator[bytes]:
    """Emit the reconnect interval, then a comment every HEARTBEAT_SECONDS.

    The `retry:` line tells the client how long to wait before reconnecting, so
    the reconnect policy is the server's decision rather than each client's
    guess. Comments are ignored by EventSource but keep the connection
    observably alive.
    """
    yield b"retry: 3000\n\n"
    while True:
        yield b": heartbeat\n\n"
        await asyncio.sleep(HEARTBEAT_SECONDS)


def timestamp(moment: float | None = None) -> str:
    """An RFC 3339 UTC timestamp, which is what every MEP field expects."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(moment))
