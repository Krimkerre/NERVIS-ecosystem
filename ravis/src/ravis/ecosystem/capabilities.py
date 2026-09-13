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

from importlib import metadata

from ecosystem_protocol import AVAILABLE, DEGRADED, Capability, EcosystemSurface

from ravis.core.pools import DEFAULT_POOLS


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
BUILD_VERSION = _installed_version("ravis")

DECLARED: dict[str, Capability] = {
    # Available because `ravis conformance clarvis` passes all twenty-four
    # checks against the transparent route, and M9 verified it against a real
    # client. Sixteen when this was written; the count is pinned by
    # `test_every_expected_check_is_present`, so a number here that drifts is a
    # comment nobody re-read rather than a suite that shrank.
    # That is the "provider *and* gateway conformance" §4.1 asks for.
    "ravis.openai_compatible.chat_completions@1": Capability(
        version="1.0.0", state=AVAILABLE
    ),
    # §4.1's table sets a condition **per capability**, not one blanket
    # conformance bar: `chat_completions@1` says "conformance passes", and this
    # one says *"a translated adapter ships"*. Anthropic's did, at M4.
    #
    # **M8's adapters do not count and the first version of this comment said
    # they did.** `LmStudioAdapter` and `OllamaAdapter` subclass
    # `GenericOpenAiAdapter`, carry `protocol_mode = OPENAI_TRANSPARENT`, and
    # define no `complete()` or `stream()` — they describe an upstream that
    # already speaks the client's protocol, which is the opposite of
    # translation. Citing the Ollama run as evidence for this capability was
    # citing §6's Path A for a Path B condition.
    #
    # The old reason said "translated execution path is M3b (runbook Stage 5)",
    # which became false the moment M3b landed and read as though the work were
    # pending. That is under-advertising, and it fails silently: a peer simply
    # never negotiates a surface that works.
    "ravis.providers.native@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="translated adapters ship: Anthropic (M4) and Google Gemini (M7)",
    ),
    # §9.7 explanations are recorded per decision and served at
    # /api/v1/route-decisions, including the excluded candidates and why.
    "ravis.routing.explanations@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="every decision records its excluded candidates and why (§9.7)",
    ),
    # Available, and the comment that used to sit here argued for DEGRADED on
    # the grounds that `VirtualModelPool` carried "no version or revision at
    # all". M16 gave it both -- a declared version and a revision derived from
    # the definition, so a consumer may pin one and be told when the pool's
    # behaviour changes under it, which is precisely what §4.1's
    # "profiles are versioned and revisioned" asks for.
    #
    # The comment stayed as written while the declaration beneath it moved, so
    # this block argued for one state and published the other. Worth naming
    # rather than quietly deleting: a stale comment next to a live declaration
    # is read as the reason for the declaration.
    "ravis.virtual_profiles@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="pools route, are queryable, and carry a revision a consumer can pin",
        # Counted rather than remembered — the constant once said 13 while
        # fourteen pools were being served, and this comment then said 14 while
        # eighteen were: the kind of number that only ever gets checked when
        # somebody is already confused.
        constraints={"pools": len(DEFAULT_POOLS), "versioned": True},
    ),
    # §4.1's advertise-when for this one is "session isolation tests pass", and
    # it is the isolation rather than the feature that gates it: a session that
    # merged across applications would be worse than none, because a consumer
    # would trust the correlation it showed.
    "ravis.sessions@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="§12.1's sessions: affinity, sticky routing, retention, and isolation "
        "keyed to the application rather than to a name",
    ),
    # Available since M15, and the reason is worth stating precisely: the
    # capability is *usage and cost*, not *billing*. RAVIS estimates from
    # published prices and reported tokens, labels every figure ESTIMATED, and
    # reports how many calls it could not price — which is the whole of what
    # §14 asks for. A peer must still never render one of these as an invoice.
    "ravis.usage_cost@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="usage records and estimated cost from published prices; "
        "never billed, and unpriced calls are counted rather than assumed free",
    ),
    # Degraded, and the reason has had to be corrected twice. First it said the
    # surface was read-only long after `PUT /pools/{pool_key}/members` began
    # persisting a narrowing to disk. Then it went on naming `_may_write` as
    # inert on a loopback bind after that bypass was closed — STATUS.md even
    # recorded the sentence as fixed — so a peer was told about a hole that no
    # longer existed while the gaps that do exist went unnamed. A reason is a
    # claim about the code beside it; when the code moves, so must the sentence.
    "ravis.management@1": Capability(
        version="1.0.0",
        state=DEGRADED,
        reason="reads shipped at M18a; provider, credential and pool writes, and "
        "lifting a tool-refusal suppression early, need "
        "an admin. credential, are audited to the hub and return their "
        "post-state, and a pool's membership write honours If-Match (M18b). "
        "Still degraded: §15.1's Idempotency-Key is not accepted, and its "
        "profile-activation, evidence-refresh and route-test writes and its "
        "diagnostics, settings, runtime-state and SIRVIS reads are not built",
    ),
    "ravis.events@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="route selected, refused and completed, and a model suppressed "
        "for tool requests after refusing tools, published to NERVIS's hub "
        "under the request's trace_id (M18b)",
    ),
    # Degraded rather than available: real and working, but scoped to one
    # configured local model with no routing, no fallback and no conformance
    # suite yet — §4.1's bar for AVAILABLE. Pulled forward from RAVIS.md's own
    # "later" note because NERVIS chat's knowledge lookup needs it now.
    "ravis.embeddings@1": Capability(
        version="1.0.0",
        state=DEGRADED,
        reason="POST /v1/embeddings forwards to one configured local runtime "
        "(default: Ollama's nomic-embed-text) — no multi-provider routing, no "
        "fallback chain and no conformance suite yet, unlike the chat path",
    ),
    # RAVIS.md §4.1: advertised once `/api/v1/codex` and its routes pass their tests, which
    # they do from M29's second increment (R2). **Never a readiness check**: Codex may still be
    # not installed, paused for re-testing or signed out, and `/api/v1/codex` says which. The
    # contract's constraints also name the relay, `/api/v1/agent-sessions`; that route and its
    # own capability, `ravis.agent_sessions@1`, arrive with M29's third increment, so the
    # `relay` constraint is added back then rather than pointing at nothing now.
    "ravis.codex_runtime@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="Codex's state, sign-in, account, version and file-rules re-test routes are "
        "served; whether Codex can take work is /api/v1/codex's answer, not this",
        constraints={
            "backend_id": "ravis/clarvis-codex",
            "roles": ["agent"],
            "state_endpoint": "/api/v1/codex",
        },
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
        # **Deliberately not a check for the event publisher.** A failing check
        # makes `ready` false, and a dead collector making the product
        # advertise itself as degraded is exactly the coupling Stage 7 forbids.
        # A drop is logged instead; see `EventPublisher._report_drop`.
        checks={"database": database_answers},
    )
