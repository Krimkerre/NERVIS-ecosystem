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

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request

from sirvis.api.security import (
    Scope,
    redacted,
    require,
    require_unauthenticated_post,
    token_summary,
)
from sirvis.core.inventory import Inventory, build_inventory
from sirvis.core.machine import latest_snapshot, machine_identity, record_snapshot
from sirvis.core.recommendations import (
    MODE_FAST,
    MODE_VERIFIED,
    TOOL_CALL_PASS_RATE,
    recommend,
)
from sirvis.core.runtime_sets import (
    RuntimeSet,
    RuntimeSetError,
    RuntimeSetMember,
    estimate_fit,
)
from sirvis.errors import (
    BenchmarkNotFoundError,
    InsufficientMemoryError,
    InvalidConfigurationError,
    ModelNotFoundError,
    ResourceBusyError,
    RuntimeUnreachableError,
)
from sirvis.resources import ConflictPolicy, ResourceExhaustedError, ResourceManager
from sirvis.runtimes import LMStudioAdapter, RuntimeUnavailableError
from sirvis.storage import list_runs, read_result, read_run
from sirvis.storage.evidence import (
    FILTERS,
    EvidenceQuery,
    known_roles,
    query_evidence,
    read_evidence,
)
from sirvis.storage.runtime_sets import (
    find_by_name,
    list_revisions,
    list_runtime_sets,
    read_runtime_set,
    save_runtime_set,
)
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



# ── Runtime Sets (§10) ───────────────────────────────────────────────────────


@router.post("/runtime-sets")
async def define_runtime_set(request: Request) -> dict[str, Any]:
    """Define a set, or re-save one — §10's versioning at its only entry point.

    Idempotent by definition rather than by flag: an unchanged definition
    returns the revision it already had and writes nothing. A UI that saves on
    every edit, or a script re-applying the same YAML, must not walk the
    revision number upward while the combination stays the same — a version
    that increments for no reason is a version nobody reads.
    """
    require(request, Scope.RUNTIME)
    body = await _json_body(request)
    definition = _definition_from(body)
    stored = save_runtime_set(request.app.state.database, definition)
    return await _runtime_set_view(request, stored)


@router.get("/runtime-sets")
async def read_runtime_sets(request: Request) -> dict[str, Any]:
    """Every set's latest revision.

    The fit estimate is deliberately *not* computed here. It needs the runtime's
    inventory, and a list endpoint that reaches a possibly-stopped runtime to
    decorate every row would fail as a whole because one number was
    unavailable — §15.4 requires SIRVIS to work standalone.
    """
    return _listing(
        [item.as_dict() for item in list_runtime_sets(request.app.state.database)]
    )


@router.get("/runtime-sets/{runtime_set_id}")
async def read_one_runtime_set(
    request: Request, runtime_set_id: str, revision: int | None = None
) -> dict[str, Any]:
    """One set — its latest revision, or the one named by `?revision=`.

    Asking for a revision that does not exist is a 404 rather than a fall back
    to the latest. A caller naming revision 2 is asking about the definition a
    result cited, and answering with revision 5 would silently substitute a
    different combination for the one under investigation.
    """
    stored = read_runtime_set(request.app.state.database, runtime_set_id, revision)
    if stored is None:
        raise ModelNotFoundError(
            f"no runtime set {runtime_set_id}"
            + (f" at revision {revision}" if revision is not None else "")
        )
    return await _runtime_set_view(request, stored)


@router.get("/runtime-sets/{runtime_set_id}/revisions")
async def read_runtime_set_revisions(request: Request, runtime_set_id: str) -> dict[str, Any]:
    """Every revision of one set, oldest first.

    §10's gate — *two revisions are distinguishable, and old results retain the
    original revision* — is a claim somebody has to be able to check. This is
    where they check it.
    """
    revisions = list_revisions(request.app.state.database, runtime_set_id)
    if not revisions:
        raise ModelNotFoundError(f"no runtime set {runtime_set_id}")
    return _listing([item.as_dict() for item in revisions])


def _definition_from(body: dict[str, Any]) -> RuntimeSet:
    """One request body as a validated definition, or a 422 that says why."""
    members = body.get("models") or body.get("members") or []
    if not isinstance(members, list):
        raise InvalidConfigurationError("models must be a list")
    try:
        return RuntimeSet.define(
            name=str(body.get("name") or ""),
            members=[_member_from(entry) for entry in members],
            purpose=str(body.get("purpose") or ""),
            load_order=body.get("load_order"),
        )
    except RuntimeSetError as failure:
        raise InvalidConfigurationError(str(failure)) from failure


def _member_from(entry: Any) -> RuntimeSetMember:
    """One member entry, in §10's YAML shape."""
    if not isinstance(entry, dict):
        raise InvalidConfigurationError("each member must be an object")
    context = entry.get("context_length")
    return RuntimeSetMember(
        role=str(entry.get("role") or ""),
        model_id=str(entry.get("model") or entry.get("model_id") or ""),
        context_length=int(context) if context is not None else None,
        configuration=entry.get("configuration") or {},
    )


async def _runtime_set_view(request: Request, stored: RuntimeSet) -> dict[str, Any]:
    """A set, plus what can be said about whether it fits — labelled as estimate.

    The estimate is attached rather than stored, because it is a fact about the
    *machine right now* and not about the definition. Storing it on the revision
    would freeze a memory figure into something immutable and let it go stale
    silently, which is the failure §12.1 exists to prevent.
    """
    return stored.as_dict() | {
        "fit": (await _fit_for(request, stored)).as_dict(),
        "snapshot_revision": SNAPSHOT_REVISION,
    }


async def _fit_for(request: Request, stored: RuntimeSet) -> Any:
    """Weigh a set's members against this machine (§10.1).

    A runtime that cannot be reached yields unknown sizes and therefore an
    UNKNOWN verdict, which is the honest answer — not a refusal, and certainly
    not an approval.
    """
    try:
        inventory = await _inventory(request)
        by_key = {
            model.runtime_key: model.installed_size_bytes
            for model in inventory.installed.values()
        }
    except RuntimeUnavailableError:
        by_key = {}
    sizes = {member.model_id: by_key.get(member.model_id) for member in stored.members}
    database = request.app.state.database
    snapshot = latest_snapshot(database, machine_identity(database)) or {}
    return estimate_fit(sizes, snapshot.get("unified_memory_bytes"))


# ── Evidence, the surface RAVIS reads (§15.1) ────────────────────────────────


@router.post("/recommendations")
async def make_recommendation(request: Request) -> dict[str, Any]:
    """§14.3's recommendation, computed from evidence that already exists.

    **A recommendation is an opinion, not a route.** §14.3 closes by saying so:
    *evidence-backed suggestions, not routing commands* — RAVIS decides what to
    route to, and SIRVIS must not invent RAVIS policies or Clarvis requirements.
    So this returns a suggestion with its whole derivation attached, and it
    expires.

    `mode: fast` reads what exists and starts nothing. `mode: verified` reads
    the same thing and *proposes* the benchmarks that would raise coverage,
    without running any: §14.3 forbids expensive work starting unasked, and
    "expensive" here means gigabytes of somebody's memory.

    A POST rather than a GET because §14.3's inputs are a body — profile, roles,
    constraints, mode — and because the result is generated rather than stored:
    two calls a day apart against changed evidence are two different opinions,
    and neither is a resource that was sitting there.
    """
    require_unauthenticated_post(request)
    body = await _json_body(request)
    roles = body.get("roles") or ["clarvis-chat", "clarvis-agent"]
    if not isinstance(roles, list) or not roles:
        raise InvalidConfigurationError("roles must be a non-empty list")
    mode = str(body.get("mode") or MODE_FAST)
    if mode not in (MODE_FAST, MODE_VERIFIED):
        raise InvalidConfigurationError(f"unknown mode {mode!r}; expected fast or verified")

    database = request.app.state.database
    by_variant, contexts = await _build_lookups(request)
    records = []
    for role in roles:
        for item in query_evidence(database, EvidenceQuery(
            filters={"role": str(role)}, limit=200
        )).items:
            variant = str((item.get("target") or {}).get("variant") or "")
            records.append(item | {"runtime_key": by_variant.get(variant, variant)})

    result = recommend(
        records, contexts, _capability_states(records),
        roles=[str(role) for role in roles], mode=mode,
        generated_at=datetime.now(timezone.utc).isoformat(),
        matrices=_interaction_matrices(database),
    )
    return result.as_dict() | {"snapshot_revision": SNAPSHOT_REVISION}


def _interaction_matrices(database: Any) -> list[dict[str, Any]]:
    """Every measured pair, newest first.

    A recommendation about a pair should stand on the pair having been run
    together where one has, and §10.1 is why it cannot be inferred otherwise:
    on this machine co-residency was nearly free and concurrency cost a third of
    throughput, and neither is predictable from either model alone.
    """
    found: list[dict[str, Any]] = []
    # `list_runs` returns (runs, cursor) — iterating the call itself walks the
    # tuple, not the runs. mypy caught it; the loop had been silently reading a
    # list and a cursor string as though both were runs.
    runs, _ = list_runs(database, limit=50)
    for run in runs:
        results: Any = run.get("results")
        for result in results if isinstance(results, list) else []:
            matrix = result.get("interaction_matrix") if isinstance(result, dict) else None
            if isinstance(matrix, dict) and matrix not in found:
                found.append(_with_members(database, matrix))
    return found


def _with_members(database: Any, matrix: dict[str, Any]) -> dict[str, Any]:
    """Name the builds a matrix measured, resolving it when it did not.

    Matrices written before the `members` field existed carry a runtime set name
    and a revision and no builds. That revision is immutable by construction
    (§10), so reading the definition back gives exactly the members that ran —
    which is the whole reason a revision is pinned at use rather than resolved
    at read.
    """
    if matrix.get("members"):
        return matrix
    stored = find_by_name(database, str(matrix.get("runtime_set") or ""))
    if stored is None:
        return matrix
    pinned = read_runtime_set(database, stored.runtime_set_id, matrix.get("revision"))
    if pinned is None:
        return matrix
    return matrix | {
        "members": {member.role: member.model_id for member in pinned.members}
    }


async def _build_lookups(request: Request) -> tuple[dict[str, str], dict[str, int | None]]:
    """Variant → runtime key, and runtime key → declared context.

    Both come from §6's inventory, and both are absent when the runtime is not
    running — which leaves a recommendation able to score nothing and say so,
    rather than unable to answer at all (§15.4).
    """
    try:
        inventory = await _inventory(request)
    except RuntimeUnavailableError:
        return {}, {}
    by_variant = {
        build.variant_id: build.runtime_key for build in inventory.installed.values()
    }
    contexts = {
        build.runtime_key: build.declared_context.value
        for build in inventory.installed.values()
    }
    return by_variant, contexts


def _capability_states(records: list[dict[str, Any]]) -> dict[str, str]:
    """Which capabilities the evidence establishes, keyed `runtime_key:capability`.

    Derived from the trials rather than from a catalogue, which is M13's whole
    point: a build is tool-capable here because attempts were made and counted,
    and a flag saying `tool_use` is not evidence of anything.
    """
    states: dict[str, str] = {}
    for record in records:
        rate = (record.get("metrics") or {}).get("tool_call_well_formed")
        key = record.get("runtime_key")
        if not isinstance(rate, dict) or not key or not rate.get("total"):
            continue
        passed, total = rate["passed"], rate["total"]
        states[f"{key}:tool_use"] = (
            "SUPPORTED" if passed / total >= TOOL_CALL_PASS_RATE else "UNSUPPORTED"
        )
    return states


@router.get("/evidence")
async def read_evidence_index(request: Request) -> dict[str, Any]:
    """§15.1's question, as an endpoint.

    **Unauthenticated, like every other read on this service.** §4.5 requires a
    scope on every *mutating* endpoint even on loopback, and permits reads to go
    unauthenticated "where a peer needs them for negotiation" — which is exactly
    what this surface is for: §15.1 names benchmark evidence as something RAVIS
    consumes. It shipped gated behind `Scope.READ` and that was a mistake, found
    by wiring the dashboard: the browser sends no token, `live()` swallows the
    401 as a fallback to mocks, and the screen would have looked like it worked.

    > Give me the best measured evidence for role `clarvis-agent` on this
    > machine for these candidate builds, under these runtime configuration
    > constraints.

    **Nothing here ranks anything.** §12.2 forbids evidence keyed as model →
    score, and a `best` flag would be that rule broken by a different spelling —
    so this filters, orders by recency, and hands RAVIS every metric with its
    provenance and validity attached. Which candidate is *best* is a routing
    decision, and §15.1 says routing decisions are RAVIS's.

    Query parameters are the filters §15.1 lists — machine, role, family,
    variant, runtime, format, quantization, suite, suite version, evidence type
    and validity — plus `candidate` (repeatable runtime keys), `config.<key>`
    constraints, `since` for incremental reads, and `limit`.
    """
    parameters = request.query_params
    candidates = parameters.getlist("candidate")
    resolved, unresolved, by_key = await _resolve_candidates(request, candidates)
    if candidates and not resolved:
        # Every candidate is unknown to this machine. Answering with an empty
        # list would say "these builds have no evidence", which is a different
        # and much more actionable claim than "these builds are not installed".
        return _listing([]) | {
            "unresolved_candidates": unresolved,
            "resolved_candidates": [],
            "candidate_variants": {},
        }

    answer = query_evidence(request.app.state.database, EvidenceQuery(
        filters={name: parameters[name] for name, _ in FILTERS if parameters.get(name)},
        candidates=tuple(resolved),
        runtime_config=_config_constraints(parameters),
        since=parameters.get("since"),
        limit=min(int(parameters.get("limit", 50)), 200),
    ))
    response: dict[str, Any] = {
        "items": answer.items,
        "next_cursor": answer.next_cursor,
        "snapshot_revision": SNAPSHOT_REVISION,
        # Both halves reported, always. A consumer that asked about four builds
        # and got evidence for two needs to know which two, and why the others
        # are absent.
        "resolved_candidates": sorted(resolved),
        "unresolved_candidates": unresolved,
        # Which runtime key each variant came from. Added when RAVIS tried to
        # consume this surface and could not: it asks by runtime key, evidence
        # comes back keyed by variant, and without this mapping a consumer
        # holding several candidates cannot tell which record answers which
        # question — leaving it to match on names, which §15.1 forbids and this
        # endpoint exists to prevent.
        "candidate_variants": by_key,
        # §15.1 asks for tombstones. There are none, and there is no mechanism
        # to produce one: evidence is append-only and nothing in SIRVIS deletes
        # a result. Reported as an empty list rather than omitted so a consumer
        # can code against the field before deletion exists — and so that
        # whoever adds retention knows exactly what they have to start filling.
        "tombstones": [],
    }
    if parameters.get("role") and not answer.items:
        # A role that matched nothing is reported *with the roles that exist*,
        # because the two ways to get an empty list here are very different
        # findings. RAVIS names its pools `ravis/clarvis-agent`; this machine's
        # evidence is filed under `agent`. SIRVIS must not invent a rule mapping
        # one to the other — §15.1 forbids inferring equivalence — but a silent
        # empty list would read as "measured, nothing found" when the truth is
        # "nobody has agreed what this role is called".
        response["available_roles"] = known_roles(request.app.state.database)
    return response


@router.get("/evidence/{evidence_id}")
async def read_evidence_identity(request: Request, evidence_id: str) -> dict[str, Any]:
    """Every result filed under one evidence identity (§12.2).

    A list, not a record. An evidence ID identifies *measurement conditions*, so
    running one suite against one build twice is two results under one identity
    — and picking one to return would be the ranking this surface refuses to do
    everywhere else.
    """
    records = read_evidence(request.app.state.database, evidence_id)
    if not records:
        raise BenchmarkNotFoundError(f"no evidence {evidence_id}")
    return _listing(records)


async def _resolve_candidates(
    request: Request, candidates: list[str]
) -> tuple[list[str], list[str], dict[str, str]]:
    """Runtime keys to the variants their evidence is filed under (§15.1).

    This is the "do not force RAVIS to infer equivalence across builds" clause
    made mechanical. RAVIS knows a runtime key and nothing else; evidence is
    filed by variant; and the mapping needs §6's inventory, which is SIRVIS's.
    A RAVIS doing this itself would be matching on names, which §15.1 forbids
    and §6 exists to replace.

    **An unreachable runtime resolves nothing and says so**, rather than
    resolving to an empty set that reads like "no evidence". §15.4 makes a
    stopped runtime the ordinary case, and the ordinary case must not produce a
    confidently wrong answer.
    """
    if not candidates:
        return [], [], {}
    try:
        inventory = await _inventory(request)
    except RuntimeUnavailableError:
        return [], sorted(candidates), {}
    resolved, unresolved, by_key = [], [], {}
    for key in candidates:
        build = inventory.by_runtime_key(key)
        if build is None:
            unresolved.append(key)
            continue
        resolved.append(build.variant_id)
        by_key[key] = build.variant_id
    return resolved, sorted(unresolved), by_key


def _config_constraints(parameters: Any) -> dict[str, str]:
    """`config.context_length=8192` style constraints, as a mapping.

    Prefixed rather than free: a bare `context_length` parameter would collide
    with a filter name the moment one is added, and a consumer would not find
    out — the query would simply stop constraining on it.
    """
    return {
        name[len("config."):]: value
        for name, value in parameters.items()
        if name.startswith("config.") and len(name) > len("config.")
    }


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
    wanted, pinned = await _session_members(request, body)

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
    return lease.as_dict() | pinned | {"snapshot_revision": SNAPSHOT_REVISION}


async def _session_members(
    request: Request, body: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """What to acquire, from an explicit list or from a Runtime Set (§9, §10).

    Returns the members and whatever the response must carry about *where they
    came from*. For a set that second half is the point: a session opened
    against `clarvis-balanced` records the revision it resolved, so a result
    produced under it names the definition that was actually loaded rather than
    whatever the name means by the time anyone reads it. §10 calls a set
    immutable **at use**, and resolving the revision once — here — is what that
    means in practice.
    """
    named = body.get("runtime_set") or body.get("runtime_set_id")
    if not named:
        wanted = body.get("models") or []
        if not isinstance(wanted, list) or not wanted:
            raise InvalidConfigurationError("models must be a non-empty list")
        return list(wanted), {}

    stored = _resolve_set(request, str(named), body.get("revision"))
    fit = await _fit_for(request, stored)
    if fit.verdict == fit.REFUSED:
        # §4.3's own example message. Refused only when the weights *alone*
        # exceed the machine, which is arithmetic rather than a prediction —
        # a set that clears this bar has not been approved, only un-refused.
        raise InsufficientMemoryError(
            f"Requested Runtime Set cannot be loaded safely. {fit.detail}"
        )
    members = [
        {"model_id": member.model_id, "role": member.role,
         "configuration": _member_configuration(member)}
        for member in stored.members_in_load_order()
    ]
    return members, {
        "runtime_set_id": stored.runtime_set_id,
        "revision": stored.revision,
        "roles": {member.role: member.model_id for member in stored.members},
        "fit": fit.as_dict(),
    }


def _resolve_set(request: Request, named: str, revision: Any) -> RuntimeSet:
    """A set by ID or by name, at a revision if one was asked for."""
    database = request.app.state.database
    wanted_revision = int(revision) if revision is not None else None
    stored = read_runtime_set(database, named, wanted_revision)
    if stored is None and wanted_revision is None:
        stored = find_by_name(database, named)
    if stored is None:
        raise ModelNotFoundError(f"no runtime set {named!r}")
    return stored


def _member_configuration(member: Any) -> dict[str, Any]:
    """A member's load configuration, with its context length folded in.

    The context length is part of what makes two co-resident models fit or not
    — §10 stores it per member for exactly that reason — so it has to reach the
    runtime rather than stay a label on the definition.
    """
    configuration = dict(member.configuration)
    if member.context_length is not None:
        configuration.setdefault("context_length", member.context_length)
    return configuration


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
