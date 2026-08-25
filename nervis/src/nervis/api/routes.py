"""NERVIS's own API surface.

§14 lists eight paths. Four exist:

- `/api/v1/health` — the convenience alias §4.1 permits beside the canonical
  `/ecosystem/health`, carrying the same data.
- `/api/v1/settings` — the key/value store M0's migration creates.
- `/api/v1/system` — M1's live telemetry for the machine NERVIS runs on.
- `/api/v1/services` — M2's registry, and the operations negotiated from it.

The other four arrive with the milestones that own them. A stub returning
plausible data would be §4.1's exact prohibition one layer up, and the two
sibling services both shipped one before learning that.
"""

from __future__ import annotations

import json
from typing import Any

from ecosystem_protocol import wire_identifier
from fastapi import APIRouter, Request

from nervis.errors import InvalidConfigurationError, NotFoundError
from nervis.negotiation import negotiate
from nervis.operations import OPERATIONS
from nervis.telemetry import sample_system

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


@router.get("/system")
async def read_system(request: Request) -> dict[str, Any]:
    """This machine's current load (§6, M1).

    Sampled when asked rather than by a background timer. M1's exit says
    sampling must not noticeably load the machine, and having no sampler is the
    only way to guarantee that — it also makes the number honest, since a value
    from a timer is whatever the timer last caught rather than the state at the
    moment somebody looked.

    **`redact=true` withholds the identifying fields** rather than dropping the
    keys, so a consumer can tell "withheld" from "this platform did not answer".
    §5.1 requires a display to be able to label sensitivity and redact; the
    query parameter is what makes that possible without a second endpoint.

    Not SIRVIS's `/api/v1/system`, which answers a different question: that one
    is an immutable snapshot of *what this machine is*, attached to benchmark
    results as provenance. This one is what it is doing, now, and the two may
    describe different machines once NERVIS watches a remote peer.
    """
    redact = request.query_params.get("redact", "").lower() in {"1", "true", "yes"}
    return sample_system().as_dict(redact=redact)


@router.get("/services")
async def read_services(request: Request) -> dict[str, Any]:
    """The registry, and what each control may do given it (§5.1, §5.2).

    Both halves in one body, because a caller that read them separately could
    render a control against one snapshot of the registry and a verdict from
    another. §5.2's *"revalidate before any mutation"* is a stronger version of
    the same concern; this is the read-side floor.

    **Every operation appears, including the unusable ones**, each with the
    reason it is not available. A control that vanishes teaches nobody anything;
    one that says "SIRVIS reports sirvis.benchmarks.jobs as unavailable" tells
    the reader both what is missing and when to look again.

    `refused` lists endpoints the SSRF guard would not let NERVIS probe. Empty
    in every ordinary installation, and worth publishing precisely because a
    non-empty one is otherwise invisible — a service that is missing because it
    was refused looks exactly like a service nobody configured.
    """
    registry = request.app.state.registry
    entries = registry.all()
    return {
        "items": [entry.as_dict() for entry in entries],
        "operations": [
            negotiate(operation, registry.get(operation.service)).as_dict()
            for operation in OPERATIONS
        ],
        "refused": [
            {"key": key, "reason": reason}
            for key, reason in request.app.state.refused_endpoints
        ],
    }


@router.get("/services/{key}")
async def read_service(key: str, request: Request) -> dict[str, Any]:
    """One entry, with the operations that depend on it.

    §5.2 requires an unsupported major to mark the dependent *feature*
    incompatible rather than the whole dashboard, and this is the shape that
    makes that checkable: asking about one service returns exactly the controls
    it owns, so a test can assert the others were untouched.
    """
    entry = request.app.state.registry.get(key)
    if entry is None:
        raise NotFoundError(f"no service {key!r} in the registry")
    return {
        **entry.as_dict(),
        "operations": [
            negotiate(operation, entry).as_dict()
            for operation in OPERATIONS
            if operation.service == key
        ],
    }
