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

**Then a third variant of the same failure.** The names above were invented
here. SIRVIS.md §4.1 publishes a table of eight, and this file declared seven
under different names — `sirvis.evidence.query@1` for §4.1's
`sirvis.benchmarks.results@1`, `sirvis.models.inventory@1` for its
`sirvis.inventory.read@1`, and so on. Only `recommendations` and `events`
matched. A capability name is the thing a peer negotiates on, so inventing one
is not a cosmetic divergence: it advertises a contract that no consumer written
against the specification will look for, and hides the eight it will. The
declarations below are §4.1's table, verbatim.
"""

from __future__ import annotations

from importlib import metadata

from ecosystem_protocol import AVAILABLE, UNAVAILABLE, Capability, EcosystemSurface


def _installed_version(distribution: str) -> str:
    """This package's version, or a marker when it is not installed.

    `PackageNotFoundError` is reachable — running from a source checkout that
    was never `pip install -e`'d — and an exception there would stop the service
    starting over a string used in bug reports. "unknown" is the honest answer
    and is visibly not a version, rather than a plausible-looking default that
    would be reported as fact.
    """
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:  # pragma: no cover - install-time only
        return "unknown"


# The build's own version, distinct from the protocol it speaks. Consumers must
# never infer behaviour from this (runbook §4.2) — that is what capabilities are
# for — but it belongs in a bug report.
#
# **Read from the package rather than written twice.** `pyproject.toml` already
# carries a version and this file carried another; both said `0.0.1` for months,
# which is the only reason nobody noticed they were two numbers. A hardcoded
# copy of a value that lives somewhere else is a drift waiting for the first
# person to update one of them.
#
# The scheme is `0.<milestones completed>.<patch>`: the minor is how much of
# this service's own plan has shipped, so it moves when a milestone lands and
# `1.0.0` means the plan is finished. It is informational by §4.2's rule — a
# peer that needs to know what this build can *do* reads the capabilities.
BUILD_VERSION = _installed_version("sirvis")

DECLARED: dict[str, Capability] = {
    # §4.1's table, in its order. The `@<major>` here is the shorthand every
    # docstring and UI label uses; `ecosystem_protocol` strips it on the wire,
    # where `id` carries the identifier alone.
    "sirvis.inventory.read@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="machines (M1), runtimes (M2), models and instances (M3)",
    ),
    "sirvis.runtime.state.read@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="residency and session reads from the Resource Manager, M8",
    ),
    "sirvis.runtime.control@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="lease, renew and release through the one owner of load/unload, M8",
    ),
    # Benchmarks run, but they run synchronously through the engine. §4.1 means
    # something narrower by "jobs": submit, poll, cancel. Declaring this
    # available because benchmarking works would be §4.1's exact prohibition —
    # advertising an operation that has not passed conformance because a
    # neighbouring one has.
    "sirvis.benchmarks.jobs@1": Capability(
        version="1.0.0",
        state=UNAVAILABLE,
        reason="submit/poll/cancel lands with the queue at M14; M6 runs synchronously",
    ),
    "sirvis.benchmarks.results@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="runs and results (M6), the evidence schema (M7), the query API RAVIS reads (M16)",
    ),
    "sirvis.runtime_sets@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="versioned multi-model combinations with revisions, M9",
    ),
    "sirvis.recommendations@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="§14.3's weighted recommendation (M15), with coverage on every score",
    ),
    "sirvis.events@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="a benchmark run publishes started and completed or failed under a "
        "trace of its own, and a recommendation under the caller's (M21)",
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
