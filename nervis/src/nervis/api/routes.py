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

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from ecosystem_protocol import wire_identifier
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from nervis import documents, workspace
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


@router.put("/settings/{key}", dependencies=[Depends(require_control)])
async def write_setting(key: str, request: Request) -> dict[str, Any]:
    """Store one setting.

    The body is `{"value": …}` rather than a bare value, so that storing the
    JSON `null` and storing nothing are different requests. A bare body cannot
    express that difference, and "the setting is explicitly off" is exactly the
    case a settings screen needs.

    **The page's own token, like every other mutation here.** This route had
    none, which was survivable while a setting only described NERVIS to itself
    — and stopped being so when `files.share` arrived: that value is read by
    the *launcher*, before anything starts, and mounted. A write nobody had to
    prove they came from this page would be a way to hand the next start an
    address of somebody else's choosing.
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
    # In a worker thread: the sample scans every process and runs `osascript`,
    # and taken here in the event loop it made every other read wait behind it.
    sample = await asyncio.to_thread(sample_system)
    return sample.as_dict(redact=redact)


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
    place_root = workspace.imported(request.app.state.settings)
    if place_root is None:
        return {"items": [], "workspace": "", "detail": "no workspace is configured"}
    root = str(place_root)

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
    place_root = workspace.imported(request.app.state.settings)
    conversation = str(request.query_params.get("conversation_id") or "")
    gone = documents.forget_attachments(place_root, conversation) if place_root else 0
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
    place_root = workspace.imported(request.app.state.settings)
    if place_root is None:
        raise InvalidConfigurationError(
            "NERVIS has no workspace configured, so it cannot accept a file. "
            "Set NERVIS_WORKSPACE_PATH to the directory chat may read and write."
        )
    root = str(place_root)
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
    # **`readable` is answered here rather than guessed at the screen.** The
    # dashboard kept its own suffix list "in step with `documents.py` by hand",
    # and the hand slipped the day pictures became readable: a `.png` uploaded
    # and answered about correctly still carried the label *not readable as
    # text* on its own card. One list, on the side that owns the reader.
    return {
        "file": {
            "name": stored.shown,
            "bytes": stored.written,
            "readable": documents.readable_name(stored.shown),
        }
    }


_RELAY_ROOT = "/api/v1/relay/ravis/"


@router.get("/relay/ravis/{path:path}")
async def relay_ravis_read(path: str, request: Request) -> Response:
    """The dashboard's reads of RAVIS, carried with NERVIS's credential.

    See `ravis_peer.relay_read` for why. Read-only by construction: GET is the only
    verb registered, and only RAVIS's management and ecosystem paths are forwarded.

    **The raw path is what travels.** The path parameter arrives percent-decoded, so a
    pool key the page encoded would reach RAVIS as extra path segments; the check runs
    on the decoded form, which is the one a `..` would climb with.
    """
    if not ravis_peer.relayable(path):
        raise NotFoundError(
            f"NERVIS does not relay the RAVIS path {path!r}",
            known=list(ravis_peer.RELAYED_PREFIXES),
        )
    raw = bytes(request.scope.get("raw_path") or b"").decode("latin-1")
    forwarded = raw[len(_RELAY_ROOT):] if raw.startswith(_RELAY_ROOT) else path
    status, body, media_type, failure = await ravis_peer.relay_read(
        request.app.state.probe_client,
        request.app.state.registry.get("ravis"),
        forwarded,
        request.url.query,
        peer_credential(request, "ravis"),
    )
    headers = {"x-nervis-relay": failure} if failure else None
    return Response(body, status_code=status, media_type=media_type, headers=headers)


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


@router.post(
    "/ravis/health/suppressions/{model:path}/lift", dependencies=[Depends(require_control)]
)
async def lift_ravis_suppression(model: str, request: Request) -> Any:
    """End a RAVIS tool-refusal suppression early, with NERVIS's admin credential.

    The same hop as the writes above, for their reason: lifting decides which model
    every client's tool requests may reach, so RAVIS asks for an admin credential, and
    the browser must not hold one. RAVIS answers with the suppressions still active,
    which the Diagnostics screen redraws from, so the body travels back untouched.

    `{model:path}` because model ids carry slashes, and Starlette decodes the path
    before matching — `suppression_lift_path` re-encodes what needs it and refuses an
    id that would climb out of the lift path.
    """
    path = ravis_peer.suppression_lift_path(model)
    if path is None:
        return JSONResponse(
            {"message": f"{model!r} is not a model id NERVIS will forward to RAVIS"},
            status_code=400,
        )
    status, answered = await ravis_peer.configure(
        request.app.state.probe_client,
        request.app.state.registry.get("ravis"),
        "POST", path,
        request.app.state.settings.ravis_admin_credential,
    )
    return JSONResponse(answered, status_code=status)


# ── Codex's ChatGPT sign-in, from RAVIS → Credentials ──────────────────────────
#
# The owner asked on 13 September 2026 for a way to sign RAVIS's Codex in to the ChatGPT
# plan from the dashboard, beside the provider keys. RAVIS serves the sign-in on admin
# routes (RAVIS 0.23.9); these are the same hop every other RAVIS write on the dashboard
# takes: the page's control token is checked, then NERVIS presents its RAVIS admin
# credential, which the browser never holds (design record `design/codex-engine/design.md`
# §3.8). Nothing here starts Codex work: no route in NERVIS reaches RAVIS's re-test.
#
# **The sign-in page's address is a live way into the sign-in until it ends**, so RAVIS
# gives it only on these admin routes and never on the public `GET /api/v1/codex`. NERVIS
# keeps it that way: the answer passes straight back to the page, nothing logs or stores
# it, and every answer says `no-store` so the browser does not keep a copy either.

#: Longer than RAVIS's own waits on Codex — ten seconds to start or cancel a sign-in, and up
#: to twenty-five to re-read an account being confirmed — so RAVIS's answer arrives, rather
#: than NERVIS giving up first and saying only that RAVIS did not answer.
CODEX_CONTROL_TIMEOUT_SECONDS = 35.0
#: RAVIS's sign-in route: start with POST, read with GET, cancel with DELETE.
_CODEX_SIGN_IN = "/api/v1/codex/sign-in"


async def _codex_control(
    request: Request,
    method: str,
    path: str,
    credential: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = CODEX_CONTROL_TIMEOUT_SECONDS,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Forward one Codex control call to RAVIS and hand back exactly what RAVIS said.

    The credential is a parameter rather than read here, so each route below names
    `ravis_admin_credential` itself — which is how `test_control_token`'s route-table
    gate recognises a route that spends it and checks that route is gated.

    RAVIS's status and body travel verbatim — 409 with the ports held, 422 for a body
    RAVIS will not take, 409 when Codex is not available — because each asks the page
    to say something different, and a flattened 200 or 500 would lose which.

    `timeout` is longer only for the version report, and `headers` carries only a task
    Stop's `Idempotency-Key`; `ravis_peer.configure` refuses any that names the
    authorization header.
    """
    status, answered = await ravis_peer.configure(
        request.app.state.probe_client,
        request.app.state.registry.get("ravis"),
        method, path, credential, body,
        timeout=timeout, headers=headers,
    )
    return JSONResponse(answered, status_code=status, headers={"cache-control": "no-store"})


@router.post("/ravis/codex/sign-in", dependencies=[Depends(require_control)])
async def start_codex_sign_in(request: Request) -> Any:
    """Start the browser sign-in: RAVIS answers 202 with the page's address, or 200 with the
    same body when one is already waiting. The page's body is forwarded as it came, so a
    method RAVIS does not offer is RAVIS's refusal to word, not NERVIS's."""
    body = await _json_body(request)
    return await _codex_control(
        request, "POST", _CODEX_SIGN_IN,
        request.app.state.settings.ravis_admin_credential, body,
    )


@router.get("/ravis/codex/sign-in", dependencies=[Depends(require_control)])
async def read_codex_sign_in(request: Request) -> Any:
    """The waiting sign-in, with its page's address, so the dashboard can open it again.

    **A read, and gated anyway.** The token exists for mutations, and every other read on
    this router is open (`test_control_token.test_reads_stay_open`). This one is different
    in what it carries: while a sign-in waits, the answer is the way into it, and RAVIS
    guards it with an admin credential for that reason. Leaving the NERVIS side open would
    hand it to anything able to reach NERVIS's port.
    """
    return await _codex_control(
        request, "GET", _CODEX_SIGN_IN, request.app.state.settings.ravis_admin_credential
    )


@router.delete("/ravis/codex/sign-in", dependencies=[Depends(require_control)])
async def cancel_codex_sign_in(request: Request) -> Any:
    """Cancel the waiting sign-in: `{"cancelled": true|false}`, false when none was waiting."""
    return await _codex_control(
        request, "DELETE", _CODEX_SIGN_IN, request.app.state.settings.ravis_admin_credential
    )


@router.post("/ravis/codex/sign-out", dependencies=[Depends(require_control)])
async def sign_codex_out(request: Request) -> Any:
    """Sign Codex out of the ChatGPT account. RAVIS answers with Codex's whole state, which the
    screen redraws from; 409 while a Codex task is working."""
    body = await _json_body(request)
    return await _codex_control(
        request, "POST", "/api/v1/codex/sign-out",
        request.app.state.settings.ravis_admin_credential, body,
    )


@router.post("/ravis/codex/account/confirm", dependencies=[Depends(require_control)])
async def confirm_codex_account(request: Request) -> Any:
    """"This is my account": the page sends the account hint it showed (`email_hint`, or null
    for an account Codex reports without an email) and RAVIS checks it is still the one
    signed in. The body goes as it came; a missing hint is RAVIS's 422 to explain."""
    body = await _json_body(request)
    return await _codex_control(
        request, "POST", "/api/v1/codex/account/confirm",
        request.app.state.settings.ravis_admin_credential, body,
    )


# ── The Codex card on RAVIS → Dashboard: a task's Stop, a site, a new build ─────────
#
# N2b (14 September 2026). **Stop is the only thing the dashboard can do to a Codex task**
# (owner decision (a)): nothing here approves, answers, steers or starts one, and RAVIS refuses
# NERVIS's credentials on every agent-session route except its owner Stop. The four routes below
# take the sign-in's hop: the page's control token checked, NERVIS's RAVIS admin credential
# presented, RAVIS's status and body handed back as RAVIS said them, and every answer `no-store`.
#
# **What goes into RAVIS's address is held to a shape first.** A task id and a site's name are
# spliced after RAVIS's base URL with the admin credential attached — the reason
# `suppression_lift_path` exists — so one that doesn't fit is refused here with a 400, and RAVIS
# never sees the request.

#: RAVIS's task ids (`as_…`): the only shape a Stop puts into RAVIS's address (design §3.8).
CODEX_TASK_ID = re.compile(r"as_[0-9A-Za-z]{10,40}")
#: The page's own Idempotency-Key: long enough to be unique, and nothing that could end a header.
CODEX_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{16,128}")
#: One label of a host name: letters and digits, with hyphens only inside.
_SITE_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
#: A site's name as NERVIS forwards it: labels joined by single dots. Whether RAVIS allows it as a
#: site at all — never a wildcard, an address or a local name — stays RAVIS's to say.
CODEX_SITE_NAME = re.compile(rf"{_SITE_LABEL}(?:\.{_SITE_LABEL})*")
#: The longest a host name can be spelled (RFC 1035), checked before the pattern runs.
CODEX_SITE_NAME_LENGTH = 253
#: Longer than RAVIS's own worst case for a version report, so RAVIS's answer arrives rather than
#: NERVIS's "RAVIS did not answer": the new build's version and its two schema trees at up to 30 s
#: each, then two throwaway starts of it, each waiting up to 15 s per call. A report RAVIS already
#: holds for the installed build comes back at once.
CODEX_VERSION_TIMEOUT_SECONDS = 210.0


def _codex_refused(message: str) -> JSONResponse:
    """NERVIS's own 400, for a value it will not put into RAVIS's address: nothing is sent."""
    return JSONResponse(
        {"message": message}, status_code=400, headers={"cache-control": "no-store"}
    )


@router.post("/ravis/codex/runs/{sid}/stop", dependencies=[Depends(require_control)])
async def stop_codex_task(sid: str, request: Request) -> Any:
    """Stop one Codex task for the owner, through RAVIS's owner Stop (`owner-stop.json`).

    **The page sends a confirmation, not a command.** Its body is the task's folder name and
    turn as the page showed them. RAVIS checks both against the task and answers 409
    `CONFIRMATION_MISMATCH` when either has moved on, so a page left open can't stop a different
    task, and nothing can stop one by its id alone. NERVIS forwards exactly those two, with
    `source: dashboard` of its own: the page can't pass itself off as the menu bar, whose stops
    RAVIS counts and audits apart.

    **The page's `Idempotency-Key` travels with it**, the one header the page chooses. A click
    retried after a lost answer carries the same key, so RAVIS replays its first answer rather
    than acting twice. The id and the key are held to their shapes before anything is sent.
    """
    if not CODEX_TASK_ID.fullmatch(sid):
        return _codex_refused(f"{sid!r} is not a Codex task id NERVIS will forward to RAVIS")
    key = request.headers.get("idempotency-key", "")
    if not CODEX_IDEMPOTENCY_KEY.fullmatch(key):
        return _codex_refused(
            "The Stop request needs an Idempotency-Key of 16 to 128 letters, digits, - or _."
        )
    page = await _json_body(request)
    confirmation = {"project": page.get("project"), "turn_id": page.get("turn_id")}
    return await _codex_control(
        request, "POST", f"/api/v1/agent-sessions/{sid}/owner-stop",
        request.app.state.settings.ravis_admin_credential,
        {"source": "dashboard", "confirm": confirmation},
        headers={"Idempotency-Key": key},
    )


@router.delete("/ravis/codex/sites/{host}", dependencies=[Depends(require_control)])
async def remove_codex_site(host: str, request: Request) -> Any:
    """Remove a site the owner added to those Codex's commands may reach (RAVIS 0.25.0).

    RAVIS never removes one of its default sites (409 `SITE_NOT_REMOVED`, `default_site`), and the
    card offers Remove only beside the sites the owner added. RAVIS answers with the list as Codex
    holds it afterwards, which the card redraws from. A removed site stops reaching Codex
    conversations started or reopened after this; one already open keeps it until it reopens.
    """
    if len(host) > CODEX_SITE_NAME_LENGTH or not CODEX_SITE_NAME.fullmatch(host):
        return _codex_refused(f"{host!r} is not a site name NERVIS will forward to RAVIS")
    return await _codex_control(
        request, "DELETE", f"/api/v1/codex/sites/{host}",
        request.app.state.settings.ravis_admin_credential,
    )


@router.get("/ravis/codex/version-check", dependencies=[Depends(require_control)])
async def check_codex_version(request: Request) -> Any:
    """RAVIS's report on the Codex build installed now: its seven checks and what changed.

    **A read, gated like the writes,** for the reason RAVIS keeps it on an admin route: a report
    RAVIS doesn't hold yet is work done on request — the new build is run in a throwaway home — not
    a figure already in memory. It spends none of the plan's allowance: nothing is signed in there
    and no model runs.
    """
    return await _codex_control(
        request, "GET", "/api/v1/codex/version-check",
        request.app.state.settings.ravis_admin_credential,
        timeout=CODEX_VERSION_TIMEOUT_SECONDS,
    )


@router.post("/ravis/codex/accept-version", dependencies=[Depends(require_control)])
async def accept_codex_version(request: Request) -> Any:
    """Accept the installed Codex build, named by the sha256 the page's report showed.

    **Accepting is trust, not proof** (review AM1). RAVIS records the build with its file rules
    unproven, so new tasks stay paused until the re-test proves them on that exact build.
    Accepting doesn't start that re-test, and no NERVIS route can: the owner starts it from the
    menu bar, and it uses one short Codex turn of the plan's allowance. RAVIS answers with Codex's
    whole state, which the card redraws from. A sha256 that is no longer the installed build is
    RAVIS's 409 `CODEX_HASH_MISMATCH`, so a report left open can't accept a build it didn't
    describe. RAVIS may run the checks first when it holds no report, hence the longer wait.
    """
    body = await _json_body(request)
    return await _codex_control(
        request, "POST", "/api/v1/codex/accept-version",
        request.app.state.settings.ravis_admin_credential, body,
        timeout=CODEX_VERSION_TIMEOUT_SECONDS,
    )


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
            peer_credential(request, ravis_peer.SERVICE),
        ),
    }
