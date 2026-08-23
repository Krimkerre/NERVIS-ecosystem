"""`/api/v1` — the reads M1 and M2 make possible (§4.2).

Three endpoints, and each answers one question the rest of SIRVIS is built on:
what machine is this, what runtime is there, and what does that runtime hold.

`/ecosystem/*` stays canonical for negotiation (§4.2); this is the surface a
person or a script reads. List responses carry §4.2's `{items, next_cursor,
snapshot_revision}` envelope from the start, because adding it later is a
contract change and the whole point of versioning from the first commit is not
to need one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from sirvis.core.machine import latest_snapshot, machine_identity, record_snapshot
from sirvis.runtimes import LMStudioAdapter, RuntimeUnavailableError
from sirvis.telemetry import detect_system

router = APIRouter(prefix="/api/v1", tags=["sirvis"])

# Moves when the underlying set changes, so a consumer can tell a real change
# from a re-read (§4.2). One value for now; per-collection revisions arrive when
# something actually changes independently.
SNAPSHOT_REVISION = 1


def _listing(items: list[dict[str, Any]]) -> dict[str, Any]:
    """§4.2's list envelope. `next_cursor` is null until a collection is unbounded."""
    return {"items": items, "next_cursor": None, "snapshot_revision": SNAPSHOT_REVISION}


@router.get("/system")
async def read_system(request: Request) -> dict[str, Any]:
    """This machine, detected now and recorded immutably.

    Detecting on read rather than serving a cached snapshot is deliberate: free
    disk, swap and thermal state are the fields that change, and they are
    exactly the ones §11.8 uses to decide whether a benchmark result is valid.
    A cached answer would be a description of the machine at some other moment.

    Each read appends a snapshot, so the history is a record of the conditions
    work actually ran under rather than a single row overwritten in place.
    """
    database = request.app.state.database
    return record_snapshot(database, detect_system()) | {
        "snapshot_revision": SNAPSHOT_REVISION
    }


@router.get("/machines/{machine_id}")
async def read_machine(request: Request, machine_id: str) -> dict[str, Any]:
    """The most recent snapshot for a machine, or an explicit absence.

    A 404 would be wrong here for the local machine: it exists, it simply has
    not been captured yet. The two are different states and §14.4 says to keep
    them so — a caller that has never called `/system` gets `snapshot: null`
    rather than being told the machine does not exist.
    """
    database = request.app.state.database
    known = machine_identity(database)
    return {
        "machine_id": machine_id,
        "is_local_machine": machine_id == known,
        "snapshot": latest_snapshot(database, machine_id),
        "snapshot_revision": SNAPSHOT_REVISION,
    }


@router.get("/runtimes")
async def read_runtimes(request: Request) -> dict[str, Any]:
    """Every configured runtime and what it currently reports.

    A runtime that is not running is reported `stopped` with the reason, not
    omitted and not an error. §15.4 requires SIRVIS to work standalone, and on a
    laptop with LM Studio closed the correct answer to "what runtimes are there"
    is "this one, and it is off".
    """
    adapter: LMStudioAdapter = request.app.state.lmstudio
    info = await adapter.health()
    row = info.as_dict()
    row["lifecycle_available"] = adapter.lifecycle_available()
    return _listing([row])


@router.get("/runtimes/{runtime_key}/models")
async def read_runtime_models(request: Request, runtime_key: str) -> dict[str, Any]:
    """What one runtime holds, as the runtime reports it.

    Unreshaped on purpose: mapping a runtime's records onto SIRVIS's model
    domain is M3, and doing it here as well would put the mapping in two places
    for the one milestone where nobody would notice them diverging.
    """
    adapter: LMStudioAdapter = request.app.state.lmstudio
    if runtime_key != "lmstudio":
        return _listing([])
    try:
        installed = await adapter.list_models()
    except RuntimeUnavailableError as failure:
        # An unreachable runtime is an empty inventory with a reason, not a 500.
        return {**_listing([]), "detail": str(failure)}
    return _listing(installed)
