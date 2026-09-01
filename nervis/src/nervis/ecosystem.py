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

from importlib import metadata

from ecosystem_protocol import AVAILABLE, DEGRADED, UNAVAILABLE, Capability, EcosystemSurface


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
BUILD_VERSION = _installed_version("nervis")

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
    # Degraded, and the reason had to be rewritten rather than kept: it said
    # "peer data lands at M2" while M2 has shipped and `nervis.registry@1` is
    # advertised `available`, so this row was describing a limit that no longer
    # existed. What actually keeps it short of available is the runbook's own
    # Stage 6 wording — the prototype has to stop being one — and screens that
    # still render invented data mark themselves rather than being listed here.
    "nervis.dashboard@1": Capability(
        version="1.0.0",
        state=DEGRADED,
        reason="the shell, this machine's telemetry, live peer data and §25's "
        "rebuilt render layer are served (Stage 6). Degraded because some cards "
        "still draw a transcription when their service has nothing to say — and "
        "are faded and labelled when they do",
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
    # Degraded, and the missing half is other people's. §11.2's correlation,
    # waterfall, gap marking and skew reporting all work — on the events the hub
    # holds. Today that is NERVIS's own spans plus anything posted to it, because
    # **Both peers publish now**, so a trace can genuinely carry more than one
    # lane — verified with a single `traceparent` sent to RAVIS and SIRVIS,
    # which produced two spans under one trace.
    #
    # Available rather than degraded, and the distinction is about *whose*
    # capability this is. It describes what NERVIS serves: correlation, the
    # waterfall, and gaps marked rather than interpolated. Whether a given trace
    # has two lanes depends on the peers being configured with a hub to publish
    # to, which is their fact and not NERVIS's — and the view already says which
    # services recorded nothing rather than drawing bars for them.
    "nervis.traces@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="§11.2's correlation, waterfall and gap marking, across every "
        "service that publishes — RAVIS since its M18b, SIRVIS since its M21",
    ),
    # **The named gap has closed, and this is now a fact about configuration.**
    # It read "generated titles wait for RAVIS to honour §9.6.1's marker" —
    # true until RAVIS M16, and stale the moment M16 landed. A reason naming a
    # milestone that has since shipped is the same defect as a capability stuck
    # on `unavailable`: a peer reads it and plans around a limit that is gone.
    #
    # Titles are generated now, but only from an authenticated identity, so the
    # honest advertisement depends on whether a RAVIS client credential is
    # configured — the same shape as `nervis.voice@1` and for the same reason.
    # `advertise_chat` sets it at startup; this literal is the unconfigured case.
    "nervis.ravis_chat@1": Capability(
        version="1.0.0",
        state=DEGRADED,
        reason="chat, streaming, history and the route inspector are served; "
        "generated titles need a RAVIS client credential, and none is configured",
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
        reason="§9's read surfaces are served with provenance intact, and a "
        "benchmark can be submitted, polled and cancelled through SIRVIS's M14 "
        "queue. Degraded because a run is not streamed while it happens",
    ),
    # Degraded, not unavailable, and the distinction is the whole point of
    # publishing a reason. M8a shipped the receiving half: a Bridge registers
    # with a port and token it generated, two extension hosts stay separate,
    # leases expire, and nothing that could resolve a gate exists. What is
    # missing is M8b -- reading status, mode and gate state from a *running*
    # Bridge -- and that is blocked on Clarvis building the Bridge at all,
    # which §1 forbids inventing.
    #
    # This said "lands at M8" for as long as M8a has been shipped, so a peer
    # asking whether it could register was told no by a service that would
    # have accepted the registration.
    # **Both halves ship now, and the reason said otherwise for two days.**
    # It read "blocked on Clarvis building the Bridge" after Clarvis had built
    # it -- M14 signed off 29 Aug, and NERVIS M8b landed the same day with
    # `test_m8b_status.py` covering a live read, the issued token, a refused
    # token, a closed window and the field allowlist.
    #
    # It surfaced the way these always do: chat, asked what it could not do,
    # told somebody Clarvis had to finish building its Bridge first. A stale
    # reason is not a harmless comment. §4.1 makes it the sentence a peer reads
    # to decide what not to attempt, and this one told them not to bother.
    #
    # Supervision is a separate capability and stays unavailable; seeing a
    # Clarvis is not driving one, and §6.7 keeps those apart deliberately.
    "nervis.clarvis_visibility@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="M8a's registration -- per-extension-host instances, leases, "
        "redaction -- and M8b's read of a registered Bridge's own /v1/status "
        "through the token NERVIS issued it, every field through an allowlist",
    ),
    "nervis.diagnostics@1": Capability(
        version="1.0.0",
        state=DEGRADED,
        reason="§11.5's Analyze ships at M12 — a bounded, redacted, fenced packet "
        "sent through RAVIS, previewable before it is sent; §17's unified "
        "diagnostics across every service are still M17",
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
    # The third condition-gated capability, and §18.2 states the condition
    # outright: voice is *"advertised only when configured"*. So the declared
    # state is not a milestone marker like most of the entries above — it is a
    # live reading of whether this installation holds a Fish Audio key, flipped
    # by `advertise_voice` when one is entered or removed. A peer that
    # negotiates this and gets `available` may ask NERVIS to speak; one that
    # sees `unavailable` knows the machine has no voice rather than guessing
    # from a failed call.
    "nervis.voice@1": Capability(
        version="1.0.0",
        state=UNAVAILABLE,
        reason="no voice credential is configured",
    ),
    # M21's centre. Available rather than conditional: unlike voice, there is
    # nothing to configure — the store is part of the database and the probe
    # loop is its first producer, so this is true on every installation the
    # moment it starts. The screens that read it gate on this rather than on
    # NERVIS being reachable, which is the distinction Stage 6 settled.
    "nervis.notifications@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="M21's durable notification centre: post, list, unread count, "
        "per-note read and dismissal",
    ),
    # M22's record. Available on every installation for the same reason the
    # notification centre is: the store is part of the database and the chat
    # surface is its only producer, so there is nothing to configure.
    #
    # **What it advertises is deliberately modest.** It says NERVIS remembers
    # what became of an offer and will show that on the next one. It does not
    # say NERVIS acts on it — nothing here changes what is proposed, because a
    # preference the person cannot see is one they cannot argue with.
    "nervis.proposal_memory@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="M22's record of what became of each offer: accepted, declined "
        "or edited, shown on the next offer and erasable in one act",
    ),
    # M23's file. What it advertises is the *storage and retrieval*, not any
    # judgement: NERVIS keeps what it was told beside the notes it shipped
    # with, retrieves both the same way, and prefers its own where they clash.
    "nervis.learned_notes@1": Capability(
        version="1.0.0",
        state=AVAILABLE,
        reason="M23's learned notes: appended on confirmation, indexed with the "
        "shipped notes, overruled by them on a clash, and editable as a file",
    ),
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
        declared=dict(DECLARED),
        checks={"database": database_answers},
    )


def advertise_chat(surface: EcosystemSurface, credentialed: bool) -> None:
    """Say whether chat can generate titles, which depends on a credential.

    §7 wants conversation titles produced as a RAVIS background call, and
    RAVIS §9.6.1 honours the marker only from an authenticated identity. So the
    difference between `degraded` and `available` here is not what NERVIS has
    built — it is whether an operator has given it a credential to present.

    Mirrors `advertise_voice` exactly, including the revision bump, so a peer
    caching capabilities learns the answer changed without a restart.
    """
    declared = surface.declared
    if not isinstance(declared, dict):  # pragma: no cover - constructed as a dict
        return
    declared["nervis.ravis_chat@1"] = Capability(
        version="1.0.0",
        state=AVAILABLE if credentialed else DEGRADED,
        reason=(
            "chat, streaming, history, the route inspector, and generated titles "
            "as RAVIS background calls (§9.6.1)"
            if credentialed
            else "chat, streaming, history and the route inspector are served; "
            "generated titles need a RAVIS client credential, and none is configured"
        ),
    )
    surface.revision += 1


def advertise_voice(surface: EcosystemSurface, configured: bool) -> None:
    """Turn `nervis.voice@1` on or off, per §18.2's "only when configured".

    Mutates this surface's own copy of the declaration rather than the module's,
    and bumps `revision` so a peer that caches capabilities can tell the answer
    changed. Called at startup and again whenever the credential is entered or
    removed, so the advertisement matches the machine without a restart —
    a capability that needs one is a capability that lies for as long as the
    process lives.
    """
    declared = surface.declared
    if not isinstance(declared, dict):  # pragma: no cover - constructed as a dict
        return
    declared["nervis.voice@1"] = Capability(
        version="1.0.0",
        state=AVAILABLE if configured else UNAVAILABLE,
        reason=(
            "a Fish Audio credential is configured and NERVIS can speak"
            if configured
            else "no voice credential is configured"
        ),
    )
    surface.revision += 1
