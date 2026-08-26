"""What NERVIS advertises to the ecosystem.

The MEP surface itself lives in the shared `ecosystem_protocol` package. What
belongs here is the only part that is genuinely NERVIS's: what NERVIS can do.

**§3.1's list, verbatim, and nothing invented beside it.** Both sibling
services learned this the expensive way — SIRVIS shipped seven capability names
it had made up, of which two matched its own specification by coincidence. A
capability name is what a peer negotiates on, so inventing one advertises a
contract nobody will look for and hides the one they will.

Almost everything here is `unavailable` at M0, and each entry names the
milestone that will change it. That is not modesty: §4.1 forbids advertising an
operation that has not passed conformance, and an optimistic `available` here
becomes a control somebody else's software enables and then cannot use.

**Revisiting this file is part of finishing a milestone**, not a separate
chore — stated at the top because in both sibling services it went stale for
four milestones running, and nothing fails when a service under-advertises. It
just quietly cannot be integrated with.
"""

from __future__ import annotations

from ecosystem_protocol import AVAILABLE, DEGRADED, UNAVAILABLE, Capability, EcosystemSurface

# The build's own version, distinct from the protocol it speaks. Consumers must
# never infer behaviour from it (runbook §4.2) — that is what capabilities are
# for — but it belongs in a bug report.
BUILD_VERSION = "0.0.1"

DECLARED: dict[str, Capability] = {
    "nervis.registry@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="§5.1's registry, probed on a timer, with §5.2 negotiation per operation",
    ),
    # Degraded rather than available, and the distinction is exactly M0's shape.
    # The dashboard is served — that is M0's "web shell" and its exit criterion
    # that a browser opens it. But every figure on it is fetched by the browser
    # from RAVIS and SIRVIS directly, so NERVIS is the page's host and not yet
    # its source. A peer reading `available` would expect to ask NERVIS for
    # dashboard data and get it.
    "nervis.dashboard@1": Capability(
        version="1.0.0",
        state=DEGRADED,
        reason="the shell and this machine's telemetry are served; peer data lands at M2",
    ),
    # Available: the hub itself is complete — §4.4's envelope, HTTP ingestion,
    # bounded persistence with retention, §11.2's filters and an SSE broadcast
    # with replay. What is *available* is the hub, not the ecosystem's traffic:
    # RAVIS and SIRVIS advertise `events@1` as unavailable until their Stage 7
    # milestones, so NERVIS is currently its own only producer. That is a fact
    # about them and is theirs to declare, which is exactly why it does not
    # degrade this.
    "nervis.event_hub@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="§11.1's hub: envelope, ingestion, retention, filters, SSE with replay",
    ),
    "nervis.traces@1": Capability(
        version="1.0.0",
        state=UNAVAILABLE,
        reason="trace correlation lands at M7, on top of M6's event hub",
    ),
    # Degraded rather than available, and the gap is one named thing. §7 lists
    # conversation titles as a RAVIS *background call* carrying §9.6.1's marker;
    # RAVIS defines `may_declare_background_calls` and honours it nowhere, so
    # NERVIS titles a conversation by truncating its first message instead.
    # Everything else §7 asks of the MVP is served.
    "nervis.ravis_chat@1": Capability(
        version="1.0.0",
        state=DEGRADED,
        reason="chat, streaming, history and the route inspector are served; "
        "generated titles wait for RAVIS to honour §9.6.1's background marker",
    ),
    # Degraded, and the missing half is named. §9's read surfaces are served —
    # inventory, state, Runtime Sets, results, evidence, recommendations, with
    # provenance passed through untouched. What M5's exit also asks for is a
    # benchmark launching and its progress streaming, and SIRVIS advertises
    # `sirvis.benchmarks.jobs@1` as unavailable because submit/poll/cancel lands
    # with its queue at M14. §1 forbids inventing the endpoint to get there.
    "nervis.sirvis_views@1": Capability(
        version="1.0.0",
        state=DEGRADED,
        reason="§9's read surfaces are served with provenance intact; "
        "launching and streaming a benchmark waits for SIRVIS M14's job queue",
    ),
    "nervis.clarvis_visibility@1": Capability(
        version="1.0.0",
        state=UNAVAILABLE,
        reason="the Clarvis Bridge integration lands at M8",
    ),
    "nervis.diagnostics@1": Capability(
        version="1.0.0",
        state=UNAVAILABLE,
        reason="AI diagnostics land at M12; unified diagnostics at M17",
    ),
    # §3.1 attaches a condition to this one rather than a milestone: it is
    # advertised *"only for explicitly configured owned services"*. So it stays
    # unavailable even after M16 ships, on any installation that owns none —
    # which is the §4.1 table's per-capability bar, not a blanket one.
    "nervis.supervision@1": Capability(
        version="1.0.0",
        state=UNAVAILABLE,
        reason="supervision lands at M16, and only for explicitly configured owned services",
    ),
    # The other conditional one, and the condition is the point: §3.1 says
    # *"only after security/compatibility gates pass"*. M13 is a spike whose
    # exit may be that this is never built.
    "nervis.code_server_proxy@1": Capability(
        version="1.0.0",
        state=UNAVAILABLE,
        reason="gated on M13's spike report; M14 only if that spike succeeds",
    ),
}


def nervis_surface(service_id: str, machine_id: str, database: object) -> EcosystemSurface:
    """Everything the shared MEP router needs from NERVIS.

    The database check is a closure rather than an object, so the protocol
    package never learns what a NERVIS database is — only that something either
    raises or does not.

    **No peer is a readiness check, and that is M0's exit criterion.** NERVIS
    reads RAVIS, SIRVIS and Clarvis, and none of them being up may make NERVIS
    unready: a control plane that reports itself broken when the things it
    watches are broken is a control plane nobody can use to find out why. Their
    absence is a fact to display, which is what M2's registry is for.
    """

    def database_answers() -> None:
        database.connection.execute("SELECT 1").fetchone()  # type: ignore[attr-defined]

    return EcosystemSurface(
        service_type="nervis",
        service_id=service_id,
        machine_id=machine_id,
        build_version=BUILD_VERSION,
        declared=DECLARED,
        checks={"database": database_answers},
    )
