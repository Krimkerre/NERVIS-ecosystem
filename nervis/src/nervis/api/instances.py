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

from nervis.enrollment import matches, presented_secret
from nervis.errors import RefusedError, UnauthorizedError
from nervis.instances import LEASE_SECONDS, Instances, RegistrationRefusedError

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
    """Register one instance, returning the token it renews with.

    The token appears in this response and nowhere else. Storing it where the
    dashboard could read it would make a browser tab sufficient to impersonate
    an editor window.
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
