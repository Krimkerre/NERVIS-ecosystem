"""What NERVIS reads from SIRVIS (§9), and the one thing it must not do.

**Never duplicate SIRVIS persistence or benchmark logic.** §9 says it outright,
and M5's exit repeats it — *"no benchmark business logic exists in NERVIS"*.
Nothing here computes, aggregates, ranks or stores. A body arrives as SIRVIS
sent it and leaves the same way, which is not laziness: §9 requires views to
preserve `MEASURED`, `ESTIMATED`, `UNKNOWN`, timestamps, staleness, method,
sample count, units and evidence links, and the surest way to preserve them is
to be in no position to drop them.

**`recommendations` is a POST, and the only one.** §14.3 makes it a POST because
its inputs are a body and the result is generated rather than stored — two calls
a day apart against changed evidence are two different opinions. It reads
nothing and changes nothing, so it stays in this read-only module.

**`jobs` is listed and refuses.** M5's exit also asks for a benchmark to launch
and its progress to stream; SIRVIS advertises `sirvis.benchmarks.jobs@1` as
*unavailable* because submit/poll/cancel lands with its queue at M14. Listing
the surface is how that shows as planned-and-absent rather than as a gap a
reader has to infer — and §1 forbids inventing the endpoint in the meantime.
"""

from __future__ import annotations

from nervis.peers.reader import Surface

SERVICE = "sirvis"

# §9's list: machine, runtime and model inventory, state, Runtime Sets,
# benchmark results, recommendations, provenance.
SURFACES: tuple[Surface, ...] = (
    Surface("system", "/api/v1/system", "sirvis.inventory.read", "Machine"),
    Surface("models", "/api/v1/models", "sirvis.inventory.read", "Models"),
    Surface("runtimes", "/api/v1/runtimes", "sirvis.inventory.read", "Runtimes"),
    Surface("residency", "/api/v1/runtime/residency", "sirvis.runtime.state.read", "Residency"),
    Surface("runtime_sets", "/api/v1/runtime-sets", "sirvis.runtime_sets", "Runtime Sets"),
    Surface("runs", "/api/v1/benchmark-runs", "sirvis.benchmarks.results", "Benchmark runs"),
    Surface("evidence", "/api/v1/evidence", "sirvis.benchmarks.results", "Evidence"),
    Surface(
        "recommendations", "/api/v1/recommendations", "sirvis.recommendations",
        "Recommendations", method="POST",
    ),
    # Refuses today. See the module docstring.
    Surface("jobs", "/api/v1/benchmark-jobs", "sirvis.benchmarks.jobs", "Benchmark jobs"),
)

BY_KEY: dict[str, Surface] = {surface.key: surface for surface in SURFACES}
