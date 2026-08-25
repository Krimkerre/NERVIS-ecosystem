"""NERVIS's own API surface.

§14 lists eight paths. M0 ships the two that can be answered without any peer
and without any data NERVIS has not yet collected:

- `/api/v1/health` — the convenience alias §4.1 permits beside the canonical
  `/ecosystem/health`, carrying the same data.
- `/api/v1/settings` — the key/value store M0's migration creates.

The other six arrive with the milestones that own them. A stub returning
plausible data would be §4.1's exact prohibition one layer up, and the two
sibling services both shipped one before learning that.
"""

from __future__ import annotations

import json
from typing import Any

from ecosystem_protocol import wire_identifier
from fastapi import APIRouter, Request

from nervis.errors import InvalidConfigurationError

router = APIRouter(prefix="/api/v1", tags=["nervis"])


@router.get("/health")
async def read_health(request: Request) -> dict[str, Any]:
    """§4.2's convenience alias: the same readiness as `/ecosystem/health`,
    plus identity and a capability summary.

    `/ecosystem/*` stays canonical for negotiation — a peer deciding whether it
    can talk to this build reads that one. This exists so a person or a script
    can ask one question and get the whole picture, and it must never disagree
    with the canonical answer, which is why it reads the same surface rather
    than recomputing anything.
    """
    surface = request.app.state.ecosystem
    checks = surface.run_checks()
    ready = all(check["status"] == "pass" for check in checks)
    return {
        "status": "healthy" if ready else "degraded",
        "live": True,
        "ready": ready,
        "checks": checks,
        "service_id": surface.service_id,
        "service_type": surface.service_type,
        # Wire ids, like every other published capability list. §4.1 keeps the
        # `@<major>` shorthand out of anything a consumer reads.
        "capabilities": {
            wire_identifier(capability_id): capability.state
            for capability_id, capability in sorted(surface.declared.items())
        },
    }


@router.get("/settings")
async def read_settings(request: Request) -> dict[str, Any]:
    """Every stored setting.

    Values are stored as JSON text and returned decoded, so a caller reads a
    number as a number. A value that fails to decode is returned as the raw
    string rather than dropped: losing a setting silently is worse than
    returning one whose type is surprising.
    """
    rows = request.app.state.database.connection.execute(
        "SELECT key, value FROM setting ORDER BY key"
    )
    return {"items": {row["key"]: _decoded(row["value"]) for row in rows}}


@router.put("/settings/{key}")
async def write_setting(key: str, request: Request) -> dict[str, Any]:
    """Store one setting.

    The body is `{"value": …}` rather than a bare value, so that storing the
    JSON `null` and storing nothing are different requests. A bare body cannot
    express that difference, and "the setting is explicitly off" is exactly the
    case a settings screen needs.
    """
    body = await _json_body(request)
    if "value" not in body:
        raise InvalidConfigurationError("body must be an object with a 'value' field")
    with request.app.state.database.connection as connection:
        connection.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(body["value"])),
        )
    return {"key": key, "value": body["value"]}


def _decoded(value: str) -> Any:
    try:
        return json.loads(value)
    except ValueError:
        return value


async def _json_body(request: Request) -> dict[str, Any]:
    """The request body as an object, refusing anything else."""
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError as failure:
        raise InvalidConfigurationError(f"body is not valid JSON: {failure}") from failure
    if not isinstance(body, dict):
        raise InvalidConfigurationError("body must be a JSON object")
    return body
