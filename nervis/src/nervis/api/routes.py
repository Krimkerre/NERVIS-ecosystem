"""NERVIS's own API surface.

§14 lists eight paths. Four exist, plus M3's RAVIS reads:

- `/api/v1/health` — the convenience alias §4.1 permits beside the canonical
  `/ecosystem/health`, carrying the same data.
- `/api/v1/settings` — the key/value store M0's migration creates.
- `/api/v1/system` — M1's live telemetry for the machine NERVIS runs on.
- `/api/v1/services` — M2's registry, and the operations negotiated from it.
- `/api/v1/{peer}/{surface}` — negotiated reads of RAVIS (§8, M3) and
  SIRVIS (§9, M5).

The other four arrive with the milestones that own them. A stub returning
plausible data would be §4.1's exact prohibition one layer up, and the two
sibling services both shipped one before learning that.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ecosystem_protocol import wire_identifier
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from nervis import documents
from nervis.api.control import require_control
from nervis.errors import InvalidConfigurationError, NotFoundError
from nervis.negotiation import Operation, negotiate
from nervis.operations import OPERATIONS
from nervis.peers import ravis as ravis_peer
from nervis.peers import sirvis as sirvis_peer
from nervis.peers.reader import peer_credential
from nervis.peers.reader import read as peer_read
from nervis.telemetry import sample_system
from nervis.workspace import OutsideWorkspaceError

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


# The two peers NERVIS reads, and where each one's table lives. A dictionary
# rather than two pairs of handlers: the gating, the envelope and the 404 that
# names its alternatives are identical, and the only thing that differs is which
# table is consulted.
PEERS = {
    ravis_peer.SERVICE: ravis_peer,
    sirvis_peer.SERVICE: sirvis_peer,
}


def _peer(service: str) -> Any:
    peer = PEERS.get(service)
    if peer is None:
        raise NotFoundError(f"no peer {service!r}", known=sorted(PEERS))
    return peer


async def _read_peer_surfaces(service: str, request: Request) -> dict[str, Any]:
    """What NERVIS can read from one peer right now, without reading any of it.

    An index rather than a fan-out. Reading every surface to answer "which are
    available" would make the cheapest question on the screen the most expensive
    call on the service — and the answer is already known from the registry,
    which was probed on a timer.
    """
    peer = _peer(service)
    entry = request.app.state.registry.get(service)
    return {
        "service": entry.as_dict() if entry else None,
        "surfaces": [
            {
                "key": surface.key,
                "label": surface.label,
                "capability": surface.capability,
                "path": surface.path,
                "method": surface.method,
                **negotiate(
                    Operation(surface.key, service, surface.capability, surface.label), entry
                ).as_dict(),
            }
            for surface in peer.SURFACES
        ],
    }


async def _read_peer(service: str, surface: str, request: Request) -> dict[str, Any]:
    """One negotiated read of one peer surface (§8, §9).

    **Never a peer's database.** `reader.py` holds a base URL and an HTTP client
    and nothing else, so there is no path by which this could open `ravis.db` or
    `sirvis.db` — the guarantee is structural rather than promised.

    **Nothing is reshaped on the way through.** §9 requires views to preserve
    `MEASURED`, `ESTIMATED`, `UNKNOWN`, timestamps, staleness, method, sample
    count, units and evidence links; the surest way to preserve them is to be in
    no position to drop them. The body a peer sent is the body a caller gets.

    Both verbs, because §14.3's recommendation is a POST — its inputs are a body
    and its result is generated rather than stored. It still reads nothing and
    changes nothing.
    """
    peer = _peer(service)
    known = peer.BY_KEY.get(surface)
    if known is None:
        raise NotFoundError(
            f"no {service} surface {surface!r}",
            known=sorted(peer.BY_KEY),
        )
    parameters: dict[str, Any] = dict(request.query_params)
    if known.method != "GET":
        parameters = await _json_body(request) if await request.body() else {}
    result = await peer_read(
        request.app.state.probe_client,
        request.app.state.registry.get(service),
        known,
        service=service,
        params=parameters,
        request_id=getattr(request.state, "request_id", ""),
        trace_id=getattr(request.state, "trace_id", ""),
        credential=peer_credential(request, service),
    )
    return result.as_dict()


# **Registered per peer rather than as `/{service}`.** A wildcard segment at
# `/api/v1/{service}` matches everything under `/api/v1` — it swallowed
# `/api/v1/chat/conversations` the moment it existed, because the chat router is
# included after this one. Two literal paths cost two lines and cannot shadow a
# sibling that has not been written yet.
def _peer_routes(service: str) -> None:
    """Register one peer's two paths with the service closed over.

    A closure rather than a `service` parameter on the handler: FastAPI reads
    any argument not in the path as a *query* parameter, so a shared handler
    taking `service` answered 422 asking for it in the query string.
    """

    async def index(request: Request) -> dict[str, Any]:
        return await _read_peer_surfaces(service, request)

    async def surface(surface: str, request: Request) -> dict[str, Any]:
        return await _read_peer(service, surface, request)

    router.add_api_route(f"/{service}", index, methods=["GET"], name=f"read_{service}_surfaces")
    router.add_api_route(
        f"/{service}/{{surface}}", surface, methods=["GET", "POST"], name=f"read_{service}"
    )


for _service in PEERS:
    _peer_routes(_service)


@router.get("/workspace/files")
async def list_workspace(request: Request) -> dict[str, Any]:
    """What is in the workspace chat may read.

    Answers with an empty list and a reason rather than an error when no
    workspace is configured: the screen needs to say *"turn this on"*, and a 404
    would make an unconfigured install look broken.
    """
    root = str(getattr(request.app.state.settings, "workspace_path", "") or "").strip()
    if not root:
        return {"items": [], "workspace": "", "detail": "no workspace is configured"}

    # Scoped to the conversation, so a fresh session starts with nothing. A file
    # handed over to ask one question is not a library the person is building.
    conversation = str(request.query_params.get("conversation_id") or "")
    place = documents.attachment_dir(Path(root), conversation)
    if place is None:
        return {"items": [], "workspace": root, "detail": ""}
    return {
        "items": [vars(item) for item in documents.list_files(place)],
        "workspace": root,
        "detail": "",
    }


@router.delete("/workspace/files")
async def forget_workspace_attachments(request: Request) -> dict[str, Any]:
    """Drop everything attached to one conversation.

    Called when the conversation is deleted. Attachments expire on their own
    after a fortnight, but *delete* should mean delete now — a person who
    removed a conversation has said what they want to happen to the file they
    handed it.
    """
    root = str(getattr(request.app.state.settings, "workspace_path", "") or "").strip()
    conversation = str(request.query_params.get("conversation_id") or "")
    gone = documents.forget_attachments(Path(root), conversation) if root else 0
    return {"deleted": gone}


@router.put("/workspace/files/{name}")
async def upload_to_workspace(name: str, request: Request) -> Any:
    """Put a file the person chose into the workspace.

    **Raw body rather than multipart**, which keeps `python-multipart` out of
    the dependencies for a feature that needs one filename and some bytes. The
    browser sends the bytes and the name travels in the path.

    **The person doing this directly is why there is no confirm button.** Every
    other write on this surface is a model *proposing* and somebody agreeing;
    this is somebody acting. What it does not get is a weaker boundary: the
    filename came from a file picker, and `../../.ssh/authorized_keys` is a
    perfectly ordinary thing for a file to be called.
    """
    root = str(getattr(request.app.state.settings, "workspace_path", "") or "").strip()
    if not root:
        raise InvalidConfigurationError(
            "NERVIS has no workspace configured, so it cannot accept a file. "
            "Set NERVIS_WORKSPACE_PATH to the directory chat may read and write."
        )
    conversation = str(request.query_params.get("conversation_id") or "")
    place = documents.attachment_dir(Path(root), conversation)
    if place is None:
        raise InvalidConfigurationError(
            "an attachment belongs to a conversation, and this request named none"
        )

    payload = await request.body()
    try:
        stored = documents.store_upload(place, name, payload)
    except OutsideWorkspaceError as refusal:
        raise InvalidConfigurationError(str(refusal)) from refusal
    except ValueError as refusal:
        raise InvalidConfigurationError(str(refusal)) from refusal
    return {"file": {"name": stored.shown, "bytes": stored.written}}


@router.put("/ravis/credentials/{name}", dependencies=[Depends(require_control)])
async def set_ravis_credential(name: str, request: Request) -> Any:
    """Store a provider key in RAVIS, with NERVIS's admin credential (§15.1).

    **The dashboard cannot do this itself any more, and that is the point.**
    RAVIS used to accept a credential write from any loopback caller with no
    header at all, so the Credentials screen `PUT` straight to it. §15.1 asks
    that the power to re-point provider keys not come free with the ability to
    call the gateway, so the bypass is gone and the write needs an
    `admin.`-prefixed credential the launcher mints for NERVIS.

    NERVIS is the right holder of it for the same reason it holds SIRVIS's admin
    token: it is the one process an operator has already trusted with the
    ecosystem, and a browser form is not a place to keep an administrative
    secret.

    The upstream status travels verbatim rather than being flattened to 200 —
    "RAVIS refused it" and "NERVIS has no credential" send a reader to two
    different places.
    """
    body = await _json_body(request)
    secret = str(body.get("secret") or "")
    status, answered = await ravis_peer.write_credential(
        request.app.state.probe_client,
        request.app.state.registry.get("ravis"),
        name,
        secret,
        request.app.state.settings.ravis_admin_credential,
    )
    return JSONResponse(answered, status_code=status)


@router.delete("/ravis/credentials/{name}", dependencies=[Depends(require_control)])
async def forget_ravis_credential(name: str, request: Request) -> Any:
    """Remove a provider key from RAVIS. Same authorization as writing one:
    §15.1 is about who may change key material, and removing it changes it."""
    status, answered = await ravis_peer.forget_credential(
        request.app.state.probe_client,
        request.app.state.registry.get("ravis"),
        name,
        request.app.state.settings.ravis_admin_credential,
    )
    return JSONResponse(answered, status_code=status)


@router.put("/ravis/providers/{name}/enabled", dependencies=[Depends(require_control)])
async def set_ravis_provider_enabled(name: str, request: Request) -> Any:
    """Turn one RAVIS provider on or off, with NERVIS's admin credential.

    The four configuration proxies below exist for the reason §16 item 4 gives:
    RAVIS stopped treating a loopback bind as authorization, and the browser must
    not be handed the credential that replaces it. NERVIS already holds one and
    already proxies the credential writes this way, so this is the same hop for
    the writes that were left behind.
    """
    body = await _json_body(request)
    status, answered = await ravis_peer.configure(
        request.app.state.probe_client,
        request.app.state.registry.get("ravis"),
        "PUT", f"/api/v1/providers/{name}/enabled",
        request.app.state.settings.ravis_admin_credential,
        {"enabled": bool(body.get("enabled"))},
    )
    return JSONResponse(answered, status_code=status)


@router.put("/ravis/providers/{name}/models", dependencies=[Depends(require_control)])
async def set_ravis_model_filter(name: str, request: Request) -> Any:
    """Narrow which of a provider's models RAVIS will offer."""
    body = await _json_body(request)
    status, answered = await ravis_peer.configure(
        request.app.state.probe_client,
        request.app.state.registry.get("ravis"),
        "PUT", f"/api/v1/providers/{name}/models",
        request.app.state.settings.ravis_admin_credential,
        dict(body),
    )
    return JSONResponse(answered, status_code=status)


@router.put("/ravis/pools/{pool_key}/members", dependencies=[Depends(require_control)])
async def set_ravis_pool_members(pool_key: str, request: Request) -> Any:
    """Pin a pool to chosen models, or clear the pin.

    This one decides which models every client of that pool can reach, which is
    why it was named in the audit beside the credential writes.
    """
    body = await _json_body(request)
    status, answered = await ravis_peer.configure(
        request.app.state.probe_client,
        request.app.state.registry.get("ravis"),
        "PUT", f"/api/v1/pools/{pool_key}/members",
        request.app.state.settings.ravis_admin_credential,
        dict(body),
    )
    return JSONResponse(answered, status_code=status)


@router.post("/ravis/pools/curate", dependencies=[Depends(require_control)])
async def curate_ravis_pools(request: Request) -> Any:
    """Hand every pool back to its curated default — a removal, not a write."""
    status, answered = await ravis_peer.configure(
        request.app.state.probe_client,
        request.app.state.registry.get("ravis"),
        "POST", "/api/v1/pools/curate",
        request.app.state.settings.ravis_admin_credential,
    )
    return JSONResponse(answered, status_code=status)


@router.get("/ravis/routes/for/{request_id}")
async def read_decision_for(request_id: str, request: Request) -> dict[str, Any]:
    """The route decision behind one request (§7.1).

    Its own path rather than a query parameter, because it answers a different
    question — *why did this reply choose that model* — and returns one decision
    or nothing rather than a page.

    A miss is `null` and a 200, not a 404. RAVIS holds decisions in memory, so a
    restart legitimately loses them, and a chat screen asking about an older
    reply should render "no longer recorded" rather than an error.
    """
    return {
        "request_id": request_id,
        "decision": await ravis_peer.decision_for(
            request.app.state.probe_client,
            request.app.state.registry.get("ravis"),
            request_id,
        ),
    }
