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

from fastapi import APIRouter, HTTPException, Request

from sirvis.core.inventory import Inventory, build_inventory
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


async def _inventory(request: Request) -> Inventory:
    """The §6 domain, derived on read from what the runtime currently reports.

    Derived rather than stored, and that is a decision worth stating. §6's exit
    requires stable IDs to survive a restart, and these are *hashes of the
    attributes that identify a build* — so a fresh inventory on an empty
    database produces the same IDs as the one before it. Persisting them would
    add a table whose only job is to reproduce what the derivation already
    guarantees, and a second source of truth that can disagree with the runtime.

    A table arrives when something needs to record what cannot be re-derived —
    an installed size, a download source, a benchmark's reference to a build
    that has since been deleted. Not before.
    """
    adapter = request.app.state.lmstudio
    return build_inventory(await adapter.list_models())


@router.get("/models")
async def read_models(request: Request, runtime_key: str | None = None) -> dict[str, Any]:
    """Installed builds, or the one a runtime calls `runtime_key` (§4.2, §6).

    The `runtime_key` form is the lookup RAVIS uses instead of matching on
    names, which §15.1 forbids it doing. It resolves on a **cold** model
    deliberately: the load-or-don't decision needs evidence about a build
    exactly when it is not loaded.

    **404 is a correct answer** and stays distinguishable from a guess (§6).
    Nothing here falls back to a nearest match, because a fuzzy hit would be a
    guess wearing an identity's clothes — and RAVIS would route on it.
    """
    inventory = await _inventory(request)
    if runtime_key is None:
        return _listing([_describe(inventory, model.runtime_key) for model in
                         inventory.installed.values()])

    found = inventory.by_runtime_key(runtime_key)
    if found is None:
        raise HTTPException(
            status_code=404,
            detail=f"no installed build carries the runtime key {runtime_key!r}",
        )
    return _describe(inventory, found.runtime_key) | {"snapshot_revision": SNAPSHOT_REVISION}


@router.get("/models/{local_model_id}")
async def read_model(request: Request, local_model_id: str) -> dict[str, Any]:
    """One installed build, with its variant, its family and any live instances."""
    inventory = await _inventory(request)
    for model in inventory.installed.values():
        if model.local_model_id == local_model_id:
            return _describe(inventory, model.runtime_key) | {
                "snapshot_revision": SNAPSHOT_REVISION
            }
    raise HTTPException(status_code=404, detail=f"no installed build {local_model_id!r}")


def _describe(inventory: Inventory, runtime_key: str) -> dict[str, Any]:
    """One installed build with everything §6 keeps separate, kept separate.

    Artifact, loadable configuration and running instance are distinct in the
    response because §6 requires it — and because on this machine the difference
    between a build's advertised context and a loaded instance's effective one
    has already caused a wrong route.
    """
    model = inventory.installed[runtime_key]
    variant = inventory.variants[model.variant_id]
    family = inventory.families[variant.family_id]
    instances = inventory.instances_of(model.local_model_id)
    return {
        **model.as_dict(),
        "variant": {
            **variant.as_dict(),
            "family_architecture_disagrees": variant.variant_id
            in inventory.architecture_disagreements,
        },
        "family": family.as_dict(),
        "instances": [instance.as_dict() for instance in instances],
        "is_loaded": bool(instances),
    }


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
