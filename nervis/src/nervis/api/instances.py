"""`/api/v1/registry/instances` — §5.1's authenticated local dynamic registration.

The receiving half of M8: everything NERVIS needs so that a Clarvis Bridge can
announce itself, without NERVIS gaining anything it is forbidden to have.

Three rules shape every handler here, and they are worth stating together
because each one alone reads like caution and together they are the design:

1. **The registrant proves it is the user** (`enrollment.py`) — a claim from a
   process that cannot read the enrollment secret is not read at all.
2. **The registrant may not describe itself freely** (`instances.py`) — it
   sends a port and an id, and NERVIS builds the endpoint and the label.
3. **NERVIS gains no way to act on the registrant.** There is no handler in
   this file, or anywhere else, that sends anything to a registered instance
   other than a read. `CLARVIS.md` §6.7's list — approve a gate, invoke a tool,
   expand the workspace root — is enforced by there being no code that could,
   which is the only enforcement that survives somebody adding a feature later
   without reading the specification.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

from nervis import clarvis
from nervis.bridges import read_config as read_bridge_config
from nervis.bridges import read_status as read_bridge_status
from nervis.enrollment import matches, presented_secret
from nervis.errors import NotFoundError, RefusedError, UnauthorizedError
from nervis.instances import LEASE_SECONDS, Instance, Instances, RegistrationRefusedError

router = APIRouter(prefix="/api/v1/registry/instances", tags=["registry"])


@router.get("")
async def list_instances(request: Request) -> dict[str, Any]:
    """Every registered instance, live or lately gone.

    Readable without the enrollment secret, and deliberately so: this is the
    dashboard's view, it contains no token and no path, and requiring the
    secret to read it would mean the browser had to hold the one credential
    that grants registration.
    """
    instances: Instances = request.app.state.instances
    now = request.app.state.instances_clock()
    return {
        "items": [one.as_dict(now) for one in instances.all()],
        "lease_seconds": LEASE_SECONDS,
    }


@router.post("", status_code=201)
async def register(request: Request) -> dict[str, Any]:
    """Register one instance, returning the token that authenticates it both ways.

    The token appears in this response and nowhere else. Storing it where the
    dashboard could read it would make a browser tab sufficient to impersonate
    an editor window.

    **One token, two directions, decided at Stage 8.** It is what the instance
    renews and deregisters with, and — since the Stage 8 amendment to
    `CLARVIS.md` §6.1 — also what a Bridge requires on every read of itself,
    and therefore what NERVIS presents when it reads one.

    The alternative was the Bridge minting its own and handing it over at
    registration, which is what §6.1 originally said. `CLAIMABLE` refuses that
    by design: NERVIS does not want a credential belonging to a registrant.
    Inverting the issuer costs nothing, because registration is already gated
    by the enrolment secret — a process that cannot read that file cannot
    obtain a token, cannot register, and cannot have its port read.
    """
    _require_enrollment(request)
    instances: Instances = request.app.state.instances
    try:
        instance, token = instances.register(await _claim(request))
    except RegistrationRefusedError as refusal:
        raise RefusedError(str(refusal)) from refusal
    return {
        "instance": instance.as_dict(request.app.state.instances_clock()),
        "token": token,
        "lease_seconds": LEASE_SECONDS,
        # Said out loud in the response because it is the thing an integrator
        # most needs to know and least expects: this is not a handshake that
        # grants NERVIS anything.
        "notice": (
            "NERVIS reads this instance and never acts on it. It will not resolve gates, "
            "invoke tools, change safety settings, or keep the service alive."
        ),
    }


def _live(request: Request, service: str, instance_id: str) -> Instance:
    """One registered instance that is still holding its lease, or a 404.

    **`read_diagnostics` has said "a dead window is a 404 the same as an
    unknown one" since it was written, and nothing implemented it.** `find()`
    returns whatever is in the registry, and a lapsed row stays there until the
    sweep collects it — so a window that closed an hour ago was still answered
    for, and NERVIS went to its port and asked. On a laptop that port is very
    often somebody else's process by then.

    Both facts are the same 404 on purpose: an id that never existed and a
    lease that expired are both "NERVIS does not have this", and telling a
    caller which one it is says something about a window that is gone.
    """
    instances: Instances = request.app.state.instances
    found = instances.find(service, instance_id)
    if found is None or not found.is_live(request.app.state.instances_clock()):
        raise NotFoundError(f"no registered {service} instance {instance_id}")
    return found


@router.get("/{service}/{instance_id}/status")
async def read_status(service: str, instance_id: str, request: Request) -> dict[str, Any]:
    """One Bridge's own `/v1/status`, read by NERVIS and passed through an allowlist.

    **The one place in this file that talks to a registrant, and it is a read.**
    The module docstring above says there is no handler that sends anything to a
    registered instance other than a read; this is that read, and it stays true
    because `read_status` issues a `GET` and has no other verb available to it.

    **Its own endpoint rather than a field on the list.** The list is drawn on
    every dashboard poll and is deliberately cheap; folding a network round trip
    per instance into it would make one busy editor window slow the whole
    screen. Asking per row also means a dead window costs only its own row.

    **Readable without the enrollment secret, like the list.** What comes back
    carries no token, no path and no port — the token NERVIS presents to the
    Bridge is held here and never travels outward. Requiring the secret would
    mean the browser had to hold the one credential that grants registration,
    which is the trade `list_instances` already refused for the same reason.
    """
    instance = _live(request, service, instance_id)
    return await read_bridge_status(
        request.app.state.probe_client, instance, request.app.state.instances_clock()
    )


@router.get("/{service}/{instance_id}/diagnostics")
async def read_diagnostics(service: str, instance_id: str, request: Request) -> dict[str, Any]:
    """M9 — one window's status, agent run, tasks, gate and recent events.

    **One request per window, and it is still three reads.** The screen needs
    the registry row, the Bridge's own status and the events that window
    forwarded, and asking the browser to join those would put the isolation rule
    in the least trustworthy place. §6.6 says events and status from one window
    never appear under another; the join happens here, keyed on the id the
    registry holds.

    **The events are filtered, not sliced.** `instance_id` selects on
    `source.instance_id` inside the envelope, so a window that has published
    nothing gets an empty list rather than the hub's recent traffic — which is
    the difference between "this window is quiet" and "here is somebody else's
    editor".

    A dead window is a 404 the same as an unknown one, because a lease that
    expired and an id that never existed are both "NERVIS does not have this".
    """
    instance = _live(request, service, instance_id)
    status = await read_bridge_status(
        request.app.state.probe_client, instance, request.app.state.instances_clock()
    )
    events = request.app.state.hub.query(
        instance_id=instance_id, limit=200, latest=True
    )
    return clarvis.diagnostics(
        instance.as_dict(request.app.state.instances_clock()), status, events
    )


@router.get("/{service}/{instance_id}/config")
async def read_config(service: str, instance_id: str, request: Request) -> dict[str, Any]:
    """One Bridge's published settings (`CLARVIS.md` §6.2), and where each lives.

    **A read, and the only kind there will be.** §6.7 forbids NERVIS changing a
    Clarvis setting, and this endpoint is what makes that restriction bearable
    rather than merely enforced: NERVIS can say what Clarvis is configured to do
    and name the setting to change, which is the whole of what a reader wanted
    when they asked whether the dashboard could manage the editor.

    Its own endpoint rather than a field on `/status` for the same reason status
    has one: what Clarvis is *doing* changes by the second and what it is
    *configured to do* changes when somebody edits a setting, and a poll should
    not carry both.
    """
    instance = _live(request, service, instance_id)
    return await read_bridge_config(
        request.app.state.probe_client, instance, request.app.state.instances_clock()
    )


@router.post("/{service}/{instance_id}/heartbeat")
async def heartbeat(service: str, instance_id: str, request: Request) -> dict[str, Any]:
    """Extend the lease. The instance's own token, not the enrollment secret.

    Separating the two credentials is what keeps one leaked heartbeat token
    from being a registration capability: it renews the instance it belongs to
    and can do nothing else.
    """
    instances: Instances = request.app.state.instances
    try:
        instance = instances.renew(service, instance_id, _bearer(request))
    except RegistrationRefusedError as refusal:
        raise UnauthorizedError(str(refusal)) from refusal
    return {"instance": instance.as_dict(request.app.state.instances_clock())}


@router.delete("/{service}/{instance_id}", status_code=204)
async def deregister(service: str, instance_id: str, request: Request) -> Response:
    """An extension host closing, said rather than waited for."""
    instances: Instances = request.app.state.instances
    try:
        instances.deregister(service, instance_id, _bearer(request))
    except RegistrationRefusedError as refusal:
        raise UnauthorizedError(str(refusal)) from refusal
    return Response(status_code=204)


def _require_enrollment(request: Request) -> None:
    """§5.1's *"do not accept an unauthenticated process's claimed service type"*.

    Checked before the body is read, so an unauthorised caller never gets as
    far as having its JSON parsed.
    """
    if not matches(_bearer(request), request.app.state.enrollment_secret):
        raise UnauthorizedError(
            "registration requires the enrollment secret, which is readable only by "
            "the user NERVIS runs as"
        )


def _bearer(request: Request) -> str:
    return presented_secret(request.headers.get("authorization"))


async def _claim(request: Request) -> dict[str, Any]:
    """The body, as a mapping, refusing anything else.

    A JSON array or bare string would otherwise reach the allowlist and produce
    a confusing error about `service` being absent.
    """
    try:
        body = await request.json()
    except ValueError as failure:
        raise RefusedError("the registration body must be JSON") from failure
    if not isinstance(body, dict):
        raise RefusedError("the registration body must be a JSON object")
    return body
