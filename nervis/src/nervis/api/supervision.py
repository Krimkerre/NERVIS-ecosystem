"""`/api/v1/supervision` — starting and stopping what NERVIS started (§12, M16).

**Three verbs and no fourth.** §12: *"there is no free-form command, script or
process-selection path, and an operation that is not in the set does not exist
rather than failing at validation."* So an unknown verb is a 404 — the surface
cannot be enumerated by probing it for near misses.

Every refusal here is a rule holding rather than an error, which is why they all
come back as `RefusedError`: switched off, not owned, not started by NERVIS, or a
circuit an operator has to clear. A caller that could not tell those apart would
report a boundary working as something going wrong.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from nervis import supervision
from nervis.ecosystem import advertise_supervision
from nervis.errors import NotFoundError, RefusedError

router = APIRouter(prefix="/api/v1/supervision", tags=["supervision"])


@router.get("")
async def read_supervision(request: Request) -> dict[str, Any]:
    """Every registered service, what NERVIS may do to it, and why not.

    The reason travels with each entry rather than being inferred from the mode,
    because "external" and "owned but never started" refuse for different
    reasons and a screen that showed one control disabled for both would be
    telling somebody the wrong thing about their own machine.
    """
    database = request.app.state.database
    on = supervision.enabled(database)
    services = []
    for entry in request.app.state.registry.all():
        declared = entry.declaration.ownership.value
        mode = supervision.mode_of(database, entry.key, declared)
        record = supervision.launched(database, entry.key)
        try:
            supervision.may_control(database, entry.key, mode)
            refusal = ""
        except RefusedError as declined:
            refusal = str(declined)
        services.append({
            "service": entry.key,
            "label": entry.declaration.label,
            "declared": declared,
            "mode": mode,
            "adapter": supervision.adapter(database, entry.key).as_dict(),
            "started_by_nervis": bool(record and supervision.still_running(record)),
            "pid": record.pid if record else None,
            "circuit": supervision.circuit(database, entry.key),
            "why_not": refusal,
        })
    return {"enabled": on, "operations": list(supervision.OPERATIONS),
            "services": services, "history": supervision.history(database)}


@router.post("/enable")
async def set_enabled(request: Request) -> dict[str, Any]:
    """The family switch. §12: every switch defaults to off."""
    body = await request.json()
    supervision.enable(request.app.state.database, bool(body.get("enabled")))
    return {"enabled": supervision.enabled(request.app.state.database)}


@router.post("/adapter/{service}")
async def set_adapter(service: str, request: Request) -> dict[str, Any]:
    """Declare how a service is started, which is what makes it supervisable."""
    body = await request.json()
    plan = supervision.configure(
        request.app.state.database, service,
        str(body.get("executable") or ""),
        [str(a) for a in (body.get("args") or [])],
        str(body.get("cwd") or ""),
    )
    # **Re-advertised here, not only at startup.** `advertise_voice` learned this
    # first and says why: *a capability that needs a restart is a capability
    # that lies for as long as the process lives.* Revoking the last adapter
    # left `nervis.supervision@1` reading `available` on a machine that owned
    # nothing — observed, not imagined.
    _readvertise(request)
    return plan.as_dict()


def _readvertise(request: Request) -> None:
    """Match the advertisement to what is configured, right now."""
    database = request.app.state.database
    advertise_supervision(request.app.state.ecosystem, sum(
        1 for entry in request.app.state.registry.all()
        if supervision.adapter(database, entry.key).configured
    ))


@router.post("/{service}/{operation}")
async def control(service: str, operation: str, request: Request) -> dict[str, Any]:
    """One of the three verbs, against one registered service.

    **An unknown verb is a 404 and an unknown service is a 404.** Both are §12's
    "does not exist rather than failing at validation": a 422 describing which
    field was wrong is a map of the surface, and this surface is closed.
    """
    if operation not in supervision.OPERATIONS:
        raise NotFoundError(f"no supervision operation {operation!r}")
    database = request.app.state.database
    entry = request.app.state.registry.get(service)
    if entry is None:
        raise NotFoundError(f"no registered service {service!r}")

    declared = entry.declaration.ownership.value
    mode = supervision.mode_of(database, service, declared)
    plan = supervision.adapter(database, service)

    if operation == "stop":
        outcome = supervision.stop(database, service, mode)
        _audit(request, service, operation, outcome)
        return {"service": service, "operation": operation, "outcome": outcome}

    if not plan.configured:
        raise RefusedError(
            f"{service} has no configured executable, so NERVIS has no way to "
            "start it"
        )
    doer = supervision.start if operation == "start" else supervision.restart
    record = doer(database, service, mode, plan.executable, plan.args, plan.cwd)
    _audit(request, service, operation, f"pid {record.pid}")
    return {"service": service, "operation": operation,
            "launched": record.as_dict()}


@router.post("/{service}/circuit/clear")
async def clear(service: str, request: Request) -> dict[str, Any]:
    """An operator clearing a supervision circuit.

    Its own endpoint rather than a side effect of a successful control, because
    §12 says the circuit stays open *"until an operator with control authority
    clears it, so a crash-loop cannot be re-entered by retry"* — and a retry
    that cleared it would be exactly that retry.
    """
    supervision.clear_circuit(request.app.state.database, service)
    _audit(request, service, "circuit.clear", "cleared")
    return {"service": service, "circuit": supervision.circuit(
        request.app.state.database, service)}


def _audit(request: Request, service: str, operation: str, outcome: str) -> None:
    """Every attempt, published. §12 asks for audit and this is the whole of it.

    Successes and refusals alike: a control surface that recorded only what
    worked cannot answer "did something try to stop this", which is the question
    an audit trail exists for.
    """
    hub = getattr(request.app.state, "hub", None)
    if hub is None:
        return
    hub.emit(
        "nervis.supervision.attempted",
        severity="warning" if operation in ("stop", "restart") else "info",
        subject={"type": "service", "id": service},
        data={"service": service, "operation": operation, "outcome": outcome},
    )
