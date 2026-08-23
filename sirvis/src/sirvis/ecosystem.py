"""What SIRVIS advertises to the ecosystem.

The MEP surface itself lives in the shared `ecosystem_protocol` package. What
belongs here is the only part that is genuinely SIRVIS's: what SIRVIS can do.

Everything is `unavailable` at M0 and each entry names the milestone that will
change it. That is not modesty — §4.1 forbids advertising an operation that has
not passed conformance, and RAVIS routes on this list. An optimistic
`available` here becomes a wrong route on somebody else's machine.

**The sibling service let these go stale**, advertising `unavailable` for four
milestones after the work shipped. Revisiting this file is part of finishing a
milestone, not a separate chore.

**And then this file did the same thing**, which is worth recording rather than
quietly fixing: M1, M2, M3, M6, M7 and M16 all shipped while every entry below
still said `unavailable`. It was found when RAVIS tried to negotiate
`sirvis.evidence.query@1` before reading evidence and was told the surface it
had just been built against did not exist. A warning in a docstring is not a
mechanism, and the reason this went unnoticed is that nothing fails when a
service under-advertises — it just quietly cannot be integrated with.
"""

from __future__ import annotations

from ecosystem_protocol import AVAILABLE, UNAVAILABLE, Capability, EcosystemSurface

# The build's own version, distinct from the protocol it speaks. Consumers must
# never infer behaviour from it (runbook §4.2) — that is what capabilities are
# for — but it belongs in a bug report.
BUILD_VERSION = "0.0.1"

DECLARED: dict[str, Capability] = {
    "sirvis.system.snapshot@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="machine detection, M1",
    ),
    "sirvis.runtime.lmstudio@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="the LM Studio adapter, M2",
    ),
    "sirvis.models.inventory@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="the model domain and inventory, M3",
    ),
    "sirvis.benchmarks.single_model@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="the benchmark engine, M6 — run live against 19 builds",
    ),
    # The one RAVIS is waiting on. Named here from the start so a peer can see
    # it is planned and absent, rather than having to infer it from silence.
    "sirvis.evidence.query@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="the evidence schema (M7) and the query API RAVIS reads (M16)",
    ),
    "sirvis.recommendations@1": Capability(
        version="1.0.0",
        state=UNAVAILABLE,
        reason="the recommendation engine lands at M15",
    ),
    "sirvis.events@1": Capability(
        version="1.0.0",
        state=UNAVAILABLE,
        reason="event publication lands at M21 (runbook Stage 7)",
    ),
}


def sirvis_surface(service_id: str, machine_id: str, database: object) -> EcosystemSurface:
    """Everything the shared MEP router needs from SIRVIS.

    The database check is a closure rather than an object, so the protocol
    package never learns what a SIRVIS database is — only that something either
    raises or does not.

    **The runtime is deliberately not a readiness check.** §21's M0 exit requires
    SIRVIS to start with no runtime dependency present, and §15.4 requires it to
    work standalone: LM Studio being absent is a fact to report, not a reason to
    call this service unready. Reporting otherwise would make every consumer
    treat a laptop with nothing loaded as a broken SIRVIS.
    """

    def database_answers() -> None:
        database.connection.execute("SELECT 1").fetchone()  # type: ignore[attr-defined]

    return EcosystemSurface(
        service_type="sirvis",
        service_id=service_id,
        machine_id=machine_id,
        build_version=BUILD_VERSION,
        declared=DECLARED,
        checks={"database": database_answers},
    )
