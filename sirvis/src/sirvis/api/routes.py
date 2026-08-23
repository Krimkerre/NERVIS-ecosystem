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

from sirvis.api.security import Scope, redacted, require, token_summary
from sirvis.core.inventory import Inventory, build_inventory
from sirvis.core.machine import latest_snapshot, machine_identity, record_snapshot
from sirvis.errors import (
    BenchmarkNotFoundError,
    InvalidConfigurationError,
    ModelNotFoundError,
    ResourceBusyError,
    RuntimeUnreachableError,
)
from sirvis.resources import ConflictPolicy, ResourceExhaustedError, ResourceManager
from sirvis.runtimes import LMStudioAdapter, RuntimeUnavailableError
from sirvis.storage import list_runs, read_result, read_run
from sirvis.telemetry import detect_system

router = APIRouter(prefix="/api/v1", tags=["sirvis"])

# Moves when the underlying set changes, so a consumer can tell a real change
# from a re-read (§4.2). One value for now; per-collection revisions arrive when
# something actually changes independently.
SNAPSHOT_REVISION = 1


def _listing(items: list[dict[str, Any]]) -> dict[str, Any]:
    """§4.2's list envelope. `next_cursor` is null until a collection is unbounded."""
    return {"items": items, "next_cursor": None, "snapshot_revision": SNAPSHOT_REVISION}


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
        "capabilities": {
            capability_id: capability.state
            for capability_id, capability in sorted(surface.declared.items())
        },
        # Presence, never the value (§4.5). A few characters of a secret narrows
        # a search, so not even a prefix.
        "api_token": redacted("yes" if token_summary(request.app.state.database) else None),
        "snapshot_revision": SNAPSHOT_REVISION,
    }


@router.get("/tokens")
async def read_tokens(request: Request) -> dict[str, Any]:
    """What tokens exist, and nothing that would let anyone use one.

    A read, but an authenticated one — the list of what credentials exist is
    itself worth protecting, since it tells an attacker which scopes are worth
    stealing. Requires `admin`, because knowing the shape of the key ring is an
    administrative fact rather than an operational one.
    """
    require(request, Scope.ADMIN)
    return _listing(token_summary(request.app.state.database))


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
        raise ModelNotFoundError(
            f"no installed build carries the runtime key {runtime_key!r}",
            runtime_key=runtime_key,
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
    raise ModelNotFoundError(
        f"no installed build {local_model_id!r}", local_model_id=local_model_id
    )


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


@router.get("/benchmark-runs")
async def read_benchmark_runs(
    request: Request, limit: int = 20, cursor: str | None = None
) -> dict[str, Any]:
    """Recent benchmark runs, newest first (§4.2).

    §4.2 names the detail path for this group and defines a list envelope with
    `limit` and `cursor`; a group carrying an envelope nobody can request would
    be a contract for a response that never exists, so the list is built here.

    Each run arrives with its results embedded. They are one per
    `ExperimentTarget` and there is currently one target per run, so a caller
    that had to fetch them separately would make two requests for one answer —
    and §17's whole reason for recording the cardinality is that the drill from
    a run to its evidence is the path RAVIS and NERVIS actually walk.
    """
    runs, following = list_runs(request.app.state.database, limit=limit, cursor=cursor)
    return {
        "items": runs,
        "next_cursor": following,
        "snapshot_revision": SNAPSHOT_REVISION,
    }


@router.get("/benchmark-runs/{run_id}")
async def read_benchmark_run(request: Request, run_id: str) -> dict[str, Any]:
    """One run and its results, or a 404 that means what it says.

    A 404 here is a real absence, unlike `/machines/{id}`: a run either happened
    or it did not, and there is no third state where the run exists but has not
    been captured yet.
    """
    found = read_run(request.app.state.database, run_id)
    if found is None:
        raise BenchmarkNotFoundError(f"no benchmark run {run_id!r}", run_id=run_id)
    return found | {"snapshot_revision": SNAPSHOT_REVISION}


@router.get("/benchmark-results/{result_id}")
async def read_benchmark_result(request: Request, result_id: str) -> dict[str, Any]:
    """One result — the evidence envelope, with the run that produced it.

    This is the end of the drill-through §17 describes: a route decision keeps
    a `source_run_id`, the run names its results, and a result carries the
    provenance, the repetitions and the validity notes that say whether any of
    it should be believed.
    """
    found = read_result(request.app.state.database, result_id)
    if found is None:
        raise BenchmarkNotFoundError(f"no benchmark result {result_id!r}", result_id=result_id)
    return found | {"snapshot_revision": SNAPSHOT_REVISION}


@router.post("/runtime/sessions")
async def open_session(request: Request) -> dict[str, Any]:
    """Acquire one or more models under a lease (§9).

    The shape §9 specifies: a set of models with roles, and a lease. A session
    is how an external client says "I am using these" so that nothing else
    unloads them — and the lease is how SIRVIS recovers when that client dies
    without saying it has finished, which is indistinguishable from it being
    slow.
    """
    require(request, Scope.RUNTIME)
    body = await _json_body(request)
    wanted = body.get("models") or []
    if not isinstance(wanted, list) or not wanted:
        raise InvalidConfigurationError("models must be a non-empty list")

    manager: ResourceManager = request.app.state.resources
    owner = str(body.get("owner") or "anonymous")
    seconds = body.get("lease_seconds")
    session_id: str | None = None
    lease = None
    try:
        for entry in wanted:
            model_key = str((entry or {}).get("model_id") or "").strip()
            if not model_key:
                raise InvalidConfigurationError("each model needs a model_id")
            lease = await manager.acquire(
                owner=owner,
                model_key=model_key,
                configuration=(entry or {}).get("configuration"),
                policy=ConflictPolicy(str(body.get("policy") or "wait")),
                lease_seconds=float(seconds) if seconds else None,
                session_id=session_id,
            )
            session_id = lease.session_id
    except ResourceExhaustedError as failure:
        # Partial acquisition is released rather than left dangling: a session
        # that got two of its three models is not a session anybody asked for,
        # and leaving the two held would strand them behind a lease nobody owns.
        if session_id:
            await manager.release(session_id)
        raise ResourceBusyError(str(failure)) from failure
    except RuntimeUnavailableError as failure:
        if session_id:
            await manager.release(session_id)
        raise RuntimeUnreachableError(str(failure)) from failure

    assert lease is not None  # the loop ran at least once, or 422 was raised
    return lease.as_dict() | {"snapshot_revision": SNAPSHOT_REVISION}


@router.delete("/runtime/sessions/{session_id}")
async def close_session(request: Request, session_id: str) -> dict[str, Any]:
    """Release a session's claims. Idempotent (§9)."""
    require(request, Scope.RUNTIME)
    manager: ResourceManager = request.app.state.resources
    unloaded = await manager.release(session_id)
    return {"session_id": session_id, "state": "released", "unloaded": unloaded}


@router.post("/runtime/sessions/{session_id}/renew")
async def renew_session(request: Request, session_id: str) -> dict[str, Any]:
    """Extend a lease before it lapses — §9's heartbeat.

    A lapsed lease is a 404 rather than a silent re-creation: its models have
    already been released, and handing back a fresh lease would tell the client
    it still holds something it does not.
    """
    require(request, Scope.RUNTIME)
    manager: ResourceManager = request.app.state.resources
    renewed = manager.renew(session_id)
    if renewed is None:
        raise BenchmarkNotFoundError(
            f"session {session_id!r} has lapsed; its models were already released",
            session_id=session_id,
        )
    return renewed.as_dict()


@router.get("/runtime/residency")
async def read_residency(request: Request) -> dict[str, Any]:
    """What is loaded, who holds it, and what the runtime holds that SIRVIS does not.

    The view §9 exists to make possible. `foreign` is the interesting column:
    models the runtime loaded by itself — LM Studio does this when a request
    wants more context than the running copy has — which occupy memory SIRVIS
    is accounting for and does not own.
    """
    manager: ResourceManager = request.app.state.resources
    return manager.residency() | {
        "foreign": await manager.foreign_instances(),
        "snapshot_revision": SNAPSHOT_REVISION,
    }


async def _json_body(request: Request) -> dict[str, Any]:
    """The request body, or a 422 that says so.

    The content type was already required to be JSON by `require` — that is a
    CSRF defence rather than a parsing convenience (§4.5) — so anything
    unparseable here is a malformed request rather than a hostile one.
    """
    try:
        parsed = await request.json()
    except ValueError as failure:
        raise InvalidConfigurationError("body is not valid JSON") from failure
    if not isinstance(parsed, dict):
        raise InvalidConfigurationError("body must be a JSON object")
    return parsed


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
