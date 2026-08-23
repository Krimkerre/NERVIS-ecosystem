"""What RAVIS can actually do right now, stated for peers to plan around.

ECOSYSTEM_RUNBOOK.md §4.1 is unusually strict, and the strictness is the point:

    Do not advertise streaming, tools, JSON or structured output, vision,
    embeddings or audio unless that exact operation passes provider *and*
    gateway conformance.

NERVIS disables controls from this list and Clarvis decides whether a route is
usable from it. An honest "not yet, because X" is useful to a peer; an
optimistic "available" is a bug in somebody else's product.

**These went stale once and it is worth knowing how.** Everything here was
written `unavailable` at M0 with the milestone that would change it, and then
M1, M2, M5 and M18a shipped without anyone coming back. RAVIS spent Stage 3
telling every peer it could not do things it demonstrably could. Nothing failed,
because understating is the safe direction — but a capability list that lags the
build is a capability list nobody can act on. **Finishing a milestone means
revisiting this file.**
"""

from __future__ import annotations

from ecosystem_protocol import AVAILABLE, DEGRADED, UNAVAILABLE, Capability, EcosystemSurface

# The build's own version, distinct from the protocol it speaks. Consumers must
# never infer behaviour from this (runbook §4.2) — that is what capabilities are
# for — but it belongs in a bug report.
BUILD_VERSION = "0.0.1"

DECLARED: dict[str, Capability] = {
    # Available because `ravis conformance clarvis` passes all sixteen checks
    # against the transparent route, and M9 verified it against a real client.
    # That is the "provider *and* gateway conformance" §4.1 asks for.
    "ravis.openai_compatible.chat_completions@1": Capability(
        version="1.0.0", state=AVAILABLE
    ),
    "ravis.providers.native@1": Capability(
        version="1.0.0",
        state=UNAVAILABLE,
        reason="translated execution path is M3b (runbook Stage 5)",
    ),
    # §9.7 explanations are recorded per decision and served at
    # /api/v1/route-decisions, including the excluded candidates and why.
    "ravis.routing.explanations@1": Capability(version="1.0.0", state=AVAILABLE),
    "ravis.virtual_profiles@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        constraints={"pools": 13},
    ),
    "ravis.sessions@1": Capability(
        version="1.0.0", state=UNAVAILABLE, reason="sessions land at M11"
    ),
    # Degraded rather than unavailable: /api/v1/usage counts real traffic, and
    # says `cost_available: false` because pricing is M15. A peer can render the
    # counts today and must not render a spend figure.
    "ravis.usage_cost@1": Capability(
        version="1.0.0",
        state=DEGRADED,
        reason="request counts are real; monetary cost needs the M15 pricing engine",
    ),
    # Degraded for the same reason in the other direction: M18a shipped the
    # reads, M18b owns every mutation. A peer may query and must not expect to
    # change anything.
    "ravis.management@1": Capability(
        version="1.0.0",
        state=DEGRADED,
        reason="read-only surface shipped at M18a; mutations land at M18b",
    ),
    "ravis.events@1": Capability(
        version="1.0.0",
        state=UNAVAILABLE,
        reason="event publication lands at M18b (runbook Stage 7)",
    ),
}


def ravis_surface(service_id: str, machine_id: str, database: object) -> EcosystemSurface:
    """Everything the shared MEP router needs from RAVIS.

    The database check is passed as a closure rather than as an object, so the
    protocol package never learns what a RAVIS database is — it only learns that
    something either raises or does not.
    """

    def database_answers() -> None:
        """Confirm the database answers a trivial query.

        Deliberately trivial: this runs on every health poll, and a check that
        costs real work becomes the reason a service is reported unhealthy.
        """
        database.connection.execute("SELECT 1").fetchone()  # type: ignore[attr-defined]

    return EcosystemSurface(
        service_type="ravis",
        service_id=service_id,
        machine_id=machine_id,
        build_version=BUILD_VERSION,
        declared=DECLARED,
        checks={"database": database_answers},
    )
