#!/usr/bin/env python3
"""§13.4's pairwise gate, against a real SIRVIS rather than a hand-written one.

**The gate existed and its fixtures did not come from SIRVIS.** `RAVIS.md` §13.4
asks that *"real SIRVIS measured, estimated, unknown, stale, tombstoned,
runtime-down and unsupported-major fixtures produce deterministic route
effects."* `ravis/tests/test_sirvis_evidence.py` says in its own docstring that it
is that gate — and it serves seven hand-authored JSON payloads through
`httpx.MockTransport`. That proves RAVIS's *reader* copes with seven shapes. It
cannot prove SIRVIS *emits* them: rename a field in SIRVIS tomorrow and those
tests still pass while routing silently stops seeing evidence. Two descriptions
of one contract, one enforced by tests and the other by nobody — the shape this
repository keeps finding.

Here the payload is whatever a real SIRVIS application answers. The record is
built from SIRVIS's own dataclasses, its trial rate comes from SIRVIS's own trial
harness, it is filed through the same `create_experiment` → `start_run` →
`finish_run` path a benchmark uses, and it is read back over HTTP from SIRVIS's
own route into RAVIS's own `EvidenceStore` across an ASGI transport. Nothing
between the two is written by this file.

**What is still not real, stated rather than glossed.** The *numbers* are not
measured: a genuine tool-call rate needs a model, and a gate that loaded one
would not run from a clean clone. SIRVIS's `run_tool_trials` is driven against a
scripted runtime, so the rate is derived by SIRVIS's own code from scripted
calls rather than typed here — which is as close as an offline gate gets, and the
live measured path is `tools/acceptance_run.py`'s job. Two states cannot be
produced by a real SIRVIS at all: it always emits evidence major `0` and protocol
`1.x`, so `unsupported-major` is forced by rebinding those, and the file says so
where it does it.

**Two states are indistinguishable at RAVIS today, and the gate records that
rather than hiding it.** A tombstoned build and an unmeasured build produce the
same route effect, because RAVIS never reads the `tombstones` key — while
`SIRVIS.md` §15.1 says the tombstone exists precisely because *"a build with no
evidence and a build whose evidence was withdrawn are indistinguishable on every
other surface, and lead to different decisions"*. Runtime-down is the same story:
SIRVIS answers `unresolved_candidates` saying it could not ask the runtime, and
RAVIS reads only `items`. §13.4 asks for *deterministic* route effects and gets
them; it does not get *distinct* ones. Asserted as it is, named as a gap.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
for package in ("nervis", "ravis", "sirvis", "protocol"):
    sys.path.insert(0, str(ROOT / package / "src"))

os.environ.setdefault("SIRVIS_ALLOWED_HOSTS", '["127.0.0.1","localhost","::1","testserver"]')

import httpx

#: One installed build for SIRVIS to resolve candidates against. Shaped like LM
#: Studio's own `/api/v0/models` entry because SIRVIS's adapter parses that shape
#: — a stub in any other shape would be testing the stub.
INSTALLED = [{
    "id": "granite-4.0-h-tiny", "object": "model", "type": "llm",
    "publisher": "lmstudio-community", "arch": "granitehybrid",
    "compatibility_type": "gguf", "quantization": "Q4_K_M",
    "state": "not-loaded", "max_context_length": 131072,
    "capabilities": ["tool_use"],
}]

MODEL = "granite-4.0-h-tiny"
ROLE = "clarvis-agent"

failures: list[str] = []


def record(check: str, held: bool, detail: str) -> None:
    print(f"  {'PASS' if held else 'FAIL'}  {check}")
    if not held:
        failures.append(f"{check}: {detail}")


def runtime(reachable: bool = True) -> Any:
    """SIRVIS's own adapter, over a transport that answers like LM Studio.

    Injected through `create_app(settings, runtime=…)` rather than assigned onto
    `app.state` afterwards: the Resource Manager captures the adapter at
    construction, and a swap after the fact once left a live adapter inside it
    and loaded a real model onto somebody's machine.
    """
    from sirvis.runtimes.lmstudio import LMStudioAdapter

    def handle(request: httpx.Request) -> httpx.Response:
        if not reachable:
            raise httpx.ConnectError("LM Studio is not running")
        return httpx.Response(200, json={"object": "list", "data": INSTALLED})

    return LMStudioAdapter(base_url="http://127.0.0.1:1234",
                           client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))


def variant_of(app: Any) -> str:
    """The variant id SIRVIS files this build's evidence under.

    Asked of SIRVIS rather than derived here. Evidence is filed by *variant* and
    RAVIS queries by *runtime key*, and the mapping between them needs §6's
    inventory — which is SIRVIS's, not this file's. A record built with an
    invented variant id is filed where nothing will look for it, which is exactly
    what happened on the first run of this gate: every measured state came back
    "no SIRVIS evidence for this build".
    """
    async def ask() -> str:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://127.0.0.1") as client:
            answered = await client.get(f"/api/v1/evidence?candidate={MODEL}")
        return str((answered.json().get("candidate_variants") or {}).get(MODEL, ""))

    return asyncio.run(ask())


def a_record(*, variant: str, passing: bool = True, estimated: bool = False) -> Any:
    """One evidence record, built and rated by SIRVIS's own code.

    The trial rate comes from `run_tool_trials` and `tool_rate` — SIRVIS's
    harness, driven against a scripted runtime — so `phrasings`, `repetitions`,
    `outcomes` and the method string are derived rather than typed. What a
    scripted runtime cannot supply is a real model's behaviour; that is
    `acceptance_run.py`'s half of this contract.
    """
    from sirvis.benchmarks.clarvis_roles import (
        TOOL_PROMPTS,
        run_tool_trials,
        tool_rate,
    )
    from sirvis.core.evidence import (
        EvidenceIdentity,
        EvidenceKind,
        EvidenceRecord,
        Provenance,
        Validity,
    )

    scripted = _scripted_runtime(passing)
    reliability = asyncio.run(run_tool_trials(scripted, MODEL, repetitions=3))
    rate = tool_rate(reliability, phrasings=len(TOOL_PROMPTS))
    if estimated:
        rate = type(rate)(
            passed=rate.passed, total=rate.total,
            provenance=Provenance(kind=EvidenceKind.ESTIMATED, method="interpolated"),
            phrasings=rate.phrasings, repetitions_each=rate.repetitions_each,
            outcomes=dict(rate.outcomes),
        )
    identity = EvidenceIdentity(
        machine_id="m1", model_family=MODEL, model_variant=variant,
        runtime="lmstudio", role=ROLE, benchmark_suite="clarvis-role-clarvis-agent",
        benchmark_version="1", model_format="gguf", quantization="Q4_K_M",
    )
    return EvidenceRecord(
        identity=identity, measurements={}, rates={"tool_call_well_formed": rate},
        validity=Validity.VALID, machine_snapshot_id="snap",
    )


def _scripted_runtime(passing: bool) -> Any:
    """A runtime that streams the same answer to every phrasing.

    Deltas rather than an assembled call, because that is what a runtime emits
    and what `absorb_tool_deltas` exists to reassemble. The failing script drops
    the `arguments` piece — `granite-4.0-h-tiny` really does stream a call whose
    arguments never arrive, and SIRVIS names the outcome `lost-arguments`, so
    the failure mode is one this ecosystem has seen rather than one invented for
    a fixture.
    """
    from sirvis.runtimes.base import GenerationChunk

    class Scripted:
        async def stream_generate(self, model_key: str, messages: Any, *,
                                  tools: Any = None, **kwargs: Any) -> Any:
            call: dict[str, Any] = {"index": 0, "id": "call_1",
                                    "function": {"name": "read_file"}}
            if passing:
                call["function"]["arguments"] = '{"path": "README.md"}'
            yield GenerationChunk(content="", tool_calls=[call])
            yield GenerationChunk(content="", finish_reason="tool_calls")

    return Scripted()


def a_sirvis(*, runtime_reachable: bool = True) -> Any:
    """A real SIRVIS application on an empty in-memory database."""
    from sirvis.app import create_app
    from sirvis.config import Settings

    settings = Settings(database_path=":memory:", _env_file=None)  # type: ignore[call-arg]
    return create_app(settings, runtime=runtime(runtime_reachable))


def file_it(app: Any, evidence: Any) -> str:
    """Store one record the way a benchmark stores one, and return its result id.

    `create_experiment` → `start_run` → `finish_run`, which is the engine's own
    path (`benchmarks/engine.py`): the payload is `record.as_dict()`, the
    evidence id is the record's own derived identity, and nothing here writes a
    row by hand. A fixture filed any other way would be a fixture testing this
    file's idea of the schema.
    """
    from sirvis.storage import (
        RunState,
        StoredResult,
        create_experiment,
        finish_run,
        start_run,
    )

    database = app.state.database
    experiment = create_experiment(
        database, {"target": {"model": MODEL}}, suite_id="clarvis-role-clarvis-agent",
        suite_version="1", environment_mode="shared",
    )
    run = start_run(database, experiment, runtime_key="lmstudio", runtime_snapshot={},
                    machine_snapshot_id=None, results_path=None)
    stored = StoredResult(target_key=MODEL, evidence_id=evidence.identity.evidence_id,
                          validity=evidence.validity.value, payload=evidence.as_dict())
    return finish_run(database, run, state=RunState.SUCCEEDED, detail="", results=[stored])[0]


async def ask_ravis(app: Any, *, max_age: float = 30 * 24 * 3600) -> Any:
    """RAVIS's own evidence store, reading a real SIRVIS over an ASGI transport.

    The store never builds a client — it takes one — so the whole of RAVIS's read
    path runs here: version negotiation, the capability check, the evidence
    query and the context read. The host must be one SIRVIS answers to, or its
    §16 item 5 middleware refuses before any of that happens.
    """
    from ravis.evidence.sirvis import EvidenceStore

    store = EvidenceStore(base_url="http://127.0.0.1", role=ROLE, max_age_seconds=max_age)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://127.0.0.1") as client:
        await store.refresh(client, [MODEL])
    return store


def verdict_of(store: Any) -> Any:
    from ravis.evidence.sirvis import tool_verdict

    return tool_verdict(store.trial_record_for(MODEL))


def main() -> int:
    from ravis.core.capabilities import CapabilityState
    from ravis.evidence.sirvis import SourceState

    print("§13.4's states, seeded into a real SIRVIS and read by a real RAVIS\n")

    # ── measured, passing ────────────────────────────────────────────────────
    app = a_sirvis()
    file_it(app, a_record(variant=variant_of(app), passing=True))
    store = asyncio.run(ask_ravis(app))
    seen = verdict_of(store)
    record("measured — SUPPORTED, with the count in the reason",
           seen.state is CapabilityState.SUPPORTED and "24/24" in seen.detail,
           f"{seen.state} {seen.detail!r}")
    record("measured — the source reports itself fresh",
           store.state is SourceState.FRESH, str(store.state))

    # ── measured, failing ────────────────────────────────────────────────────
    app = a_sirvis()
    file_it(app, a_record(variant=variant_of(app), passing=False))
    seen = verdict_of(asyncio.run(ask_ravis(app)))
    record("measured-and-failed — UNSUPPORTED, not UNKNOWN",
           seen.state is CapabilityState.UNSUPPORTED, f"{seen.state} {seen.detail!r}")
    record("measured-and-failed — the reason names how it failed",
           "lost-arguments" in seen.detail, seen.detail)

    # ── estimated ────────────────────────────────────────────────────────────
    app = a_sirvis()
    file_it(app, a_record(variant=variant_of(app), estimated=True))
    seen = verdict_of(asyncio.run(ask_ravis(app)))
    record("estimated — UNKNOWN, because an estimate is not a measurement",
           seen.state is CapabilityState.UNKNOWN, f"{seen.state} {seen.detail!r}")

    # ── unknown: nothing filed at all ────────────────────────────────────────
    app = a_sirvis()
    store = asyncio.run(ask_ravis(app))
    seen = verdict_of(store)
    record("unknown — UNKNOWN, and the source is still fresh",
           seen.state is CapabilityState.UNKNOWN and store.state is SourceState.FRESH,
           f"{seen.state} {store.state}")

    # ── stale ────────────────────────────────────────────────────────────────
    # Age is the row's `created_at`, which no writer sets, so the record is aged
    # by narrowing the window rather than by backdating it: the same comparison
    # runs either way, and a gate that reached into the schema to move a
    # timestamp would be testing the reach.
    app = a_sirvis()
    file_it(app, a_record(variant=variant_of(app), passing=True))
    store = asyncio.run(ask_ravis(app, max_age=-1))
    seen = verdict_of(store)
    record("stale — withheld and UNKNOWN rather than served old",
           seen.state is CapabilityState.UNKNOWN, f"{seen.state} {seen.detail!r}")
    record("stale — the source degrades, so a reader can tell why",
           store.state is SourceState.DEGRADED, str(store.state))

    # ── tombstoned ───────────────────────────────────────────────────────────
    from sirvis.storage import delete_result

    app = a_sirvis()
    result_id = file_it(app, a_record(variant=variant_of(app), passing=True))
    delete_result(app.state.database, result_id, reason="withdrawn by the operator",
                  requested_by="pairwise_check")
    store = asyncio.run(ask_ravis(app))
    seen = verdict_of(store)
    record("tombstoned — the claim is withdrawn, deterministically",
           seen.state is CapabilityState.UNKNOWN, f"{seen.state} {seen.detail!r}")
    # **The gap, asserted as it is.** §13.4 asks for a deterministic effect and
    # gets one; `SIRVIS.md` §15.1 says the tombstone exists because "a build with
    # no evidence and a build whose evidence was withdrawn are indistinguishable
    # on every other surface, and lead to different decisions" — and RAVIS never
    # reads the key, so at RAVIS they are indistinguishable too.
    record("tombstoned — RAVIS cannot yet tell it from never-measured (known gap)",
           seen.detail == "no SIRVIS evidence for this build", seen.detail)

    # ── runtime down ─────────────────────────────────────────────────────────
    app = a_sirvis(runtime_reachable=False)
    file_it(app, a_record(variant=variant_of(app), passing=True))
    store = asyncio.run(ask_ravis(app))
    seen = verdict_of(store)
    record("runtime-down — no claim is invented from evidence it could not resolve",
           seen.state is CapabilityState.UNKNOWN, f"{seen.state} {seen.detail!r}")

    # ── unsupported protocol major ───────────────────────────────────────────
    # Forced, because a real SIRVIS cannot answer anything but 1.x: the version
    # is a module constant and that is deliberate — "a protocol version that two
    # services disagree about is not a protocol". Rebinding it is the only way to
    # see the refusal RAVIS is written to make.
    import ecosystem_protocol.routes as protocol_routes

    held = protocol_routes.PROTOCOL_VERSION
    protocol_routes.PROTOCOL_VERSION = "2.0.0"
    try:
        app = a_sirvis()
        file_it(app, a_record(variant=variant_of(app), passing=True))
        store = asyncio.run(ask_ravis(app))
    finally:
        protocol_routes.PROTOCOL_VERSION = held
    record("unsupported-major — the source degrades and says which version",
           store.state is SourceState.DEGRADED and "2.0.0" in store.detail,
           f"{store.state} {store.detail!r}")
    record("unsupported-major — no evidence is kept from a version it cannot read",
           store.record_for(MODEL) is None, str(store.record_for(MODEL)))

    if failures:
        print(f"\n{len(failures)} pairwise assertion(s) failed:\n")
        for failure in failures:
            print(f"  • {failure}")
        return 1
    print("\nEvery state was filed through SIRVIS's own run path and read over HTTP "
          "by RAVIS's own store.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
