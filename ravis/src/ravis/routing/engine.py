"""Choosing a model for a request (RAVIS.md §9).

M5 implements the eligibility half of §9.1's pipeline and the smallest honest
version of the ranking half. That asymmetry is deliberate, and worth
understanding before adding to it.

Eligibility is a hard filter: pool invariants, then capability checks that fail
closed. §9.1 is explicit that no preference outweighs a failed hard constraint,
so this runs first and separately, and a candidate removed here is never
reconsidered by scoring.

Ranking, at M5, is declared preference then alphabetical order — and nothing
more. There is no benchmark evidence yet (M13) and no cost data (M15), so any
richer score would be arithmetic over numbers nobody measured. Health (M12) is
deliberately not a score either: §10 says do not keep routing to a failing
provider, which is an exclusion, and turning "it failed twice" into a ranking
weight would be inventing a quality signal out of an availability one.

§9.4 rules out exactly that: routing must be explainable rather than an
opaque oracle, and an explanation that cites an invented weighting is worse than
one that admits the choice was made on stable ordering.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Mapping

from ravis.core.capabilities import ModelCapabilities
from ravis.core.pools import (
    POOL_PREFIX,
    POOLS_BY_ID,
    VirtualModelPool,
    direct_provider,
    direct_target,
    is_pool_id,
    parameter_scale,
    size_rank,
)
from ravis.core.requests import NormalizedRequest
from ravis.policy import RoutingPolicy
from ravis.routing.explain import ExcludedCandidate, RouteDecision
from ravis.routing.requirements import RequestRequirements, analyse, unmet_by, unverified_notes
from ravis.runtime.residency import Residency, ResidencySnapshot, residency_rank
from ravis.runtime.resources import MemoryReading

# How many alternatives a decision publishes. §10 describes the chain as
# Primary → Fallback 1 → Fallback 2, so two is the specified depth rather than
# an arbitrary cap. Deeper is not obviously better: by the third alternative the
# request has already waited through two failures, and a client that has been
# waiting that long is usually better served by an error it can act on.
MAX_FALLBACKS = 2

#: How often an exploring request actually explores. Roughly one turn in
#: twelve: frequent enough that a catalogue's unmeasured models get timed over
#: an afternoon of ordinary use, rare enough that a conversation does not feel
#: like a lottery. It is a default rather than a fixed constant because the
#: right number depends on how much someone is willing to pay in odd answers to
#: learn what their models actually do.
DEFAULT_EXPLORATION_RATE = 0.08


@dataclass(frozen=True)
class Exploration:
    """Permission to occasionally answer with something other than the best pick.

    **Why this carries a dice roll instead of throwing its own.** The engine is
    a pure function of its arguments and §9.7 gates exactly that: identical
    inputs must produce an identical decision, or the determinism test cannot
    exist at all. A `random.random()` inside the ranking would end that property
    for every caller, including the ones that never asked to explore. So the
    dice are thrown at the edge -- in the API layer, which is already impure --
    and only the *result* comes in here. Same roll, same route, still random
    across requests.

    `rate` is the share of requests allowed to explore, `roll` is this request's
    throw. A roll at or above the rate changes nothing, which is the ordinary
    case and the default.
    """

    rate: float = 0.0
    roll: float = 1.0
    prefer_unmeasured: bool = True
    # Models exploration must not pick now, because an exploration pick of
    # theirs recently failed in a way that says something about the model
    # (`HealthRegistry.held_from_exploration`). They stay ordinary candidates.
    avoid: frozenset[str] = frozenset()


class RoutingEngine:
    """Resolves what a client addressed into a model to call.

    Holds no state and performs no I/O: capabilities are passed in. That keeps it
    testable without a provider and keeps the decision reproducible — §9.7's
    determinism gate requires that fixed inputs produce the same decision *and*
    the same explanation.
    """

    def select(
        self,
        requested: str,
        candidates: dict[str, ModelCapabilities],
        residency: ResidencySnapshot | None = None,
        memory: MemoryReading | None = None,
        request: NormalizedRequest | None = None,
        unavailable: Mapping[str, str] | None = None,
        foreign_providers: frozenset[str] = frozenset(),
        remote_models: frozenset[str] = frozenset(),
        chosen: tuple[str, ...] = (),
        observed_ttft_ms: Mapping[str, float] | None = None,
        policy: RoutingPolicy | None = None,
        policy_refusals: Mapping[str, list[str]] | None = None,
        resold: Mapping[str, str] | None = None,
        sticky: str = "",
        expected_session_requests: int | None = None,
        reasoning_share: Mapping[str, float] | None = None,
        role_evidence: Mapping[str, Mapping[str, str]] | None = None,
        explore: Exploration | None = None,
        direct_owners: Mapping[str, str] | None = None,
    ) -> RouteDecision:
        """Resolve a requested model, pool or direct address to a decision.

        Three shapes arrive here, and they are handled differently on purpose:
        a plain model name is passed through untouched, a direct address names
        its target explicitly, and a pool is resolved by invariant.

        Residency and memory are optional and default to unknown, so a caller
        with no runtime visibility gets exactly the behaviour it had before —
        preference then alphabetical — rather than a router quietly acting on
        assumptions about a runtime it cannot see. `request` is optional for the
        same reason: without it only the pool's own invariants apply.

        `unavailable` carries the models whose circuit breaker is currently open
        (§10), each with the reason it opened. It is passed in rather than read
        from anywhere, because this class performs no I/O and holds no state —
        health lives in the reliability layer, and keeping the engine a pure
        function of its arguments is what makes §9.7's determinism gate
        testable.

        `policy_refusals` arrives computed, in the same shape and for the same
        reason as `unavailable`: deciding whether a model is forbidden needs to
        know which provider serves it and whether that provider is on this
        machine, and neither is something a pure function of a capability table
        can answer. The engine's job is to *apply* the refusals and report them,
        which is what keeps §14's rule 14 structural — a policy exclusion is
        removed before ranking, so no score can outweigh it.

        `policy` itself comes along for what it says rather than what it
        forbids: the explanation lines, and `LOCAL_PREFERRED`, which is the one
        rung of the ladder that ranks instead of excluding.

        `reasoning_share` is SIRVIS's measurement of how much of each build's
        output is thinking rather than answer, passed in for the same reason
        `observed_ttft_ms` is: it comes from a service this class does not talk
        to. It only ever breaks a tie, and only when the request set an output
        ceiling for the thinking to eat into — see `_reasoning_rank`.

        `sticky` is the model this session last used (§12.1). A preference and
        never a constraint: it orders candidates that already passed every hard
        filter, so a session can steer a choice among models the caller was
        already permitted and can never reach one it was not. That is what keeps
        a session ID correlation data rather than authorization, which the
        runbook §4.3 requires of every ID it defines.
        """
        policy = policy or RoutingPolicy()
        refusals = policy_refusals or {}
        requirements = analyse(request) if request else RequestRequirements()
        if is_pool_id(requested):
            return self._select_from_pool(
                POOLS_BY_ID[requested],
                candidates,
                residency or ResidencySnapshot(),
                memory or MemoryReading(),
                requirements,
                unavailable or {},
                remote_models,
                chosen,
                observed_ttft_ms or {},
                policy,
                refusals,
                sticky,
                expected_session_requests,
                reasoning_share,
                role_evidence,
                resold or {},
                explore,
                direct_owners,
            )

        target = direct_target(requested)
        if target is not None:
            return self._direct(requested, target, candidates, requirements,
                                direct_provider(requested) in foreign_providers,
                                refusals.get(target) or refusals.get(requested) or [])

        if requested.startswith(POOL_PREFIX):
            return _unknown_address(requested, candidates)

        # A plain model name. RAVIS does not second-guess it: §5.3 puts an
        # explicit request above any inference, and the transparent path exists
        # precisely so a client can address an upstream model directly. The
        # request's own requirements are reported but not enforced — refusing a
        # model the client named by name would be RAVIS overruling an explicit
        # instruction on the strength of capability data it may not have.
        # **Policy still applies to a name the client chose itself.** §5.3 puts
        # an explicit request above inference, and policy is not inference — a
        # `LOCAL_ONLY` request that names a hosted model by hand is the exact
        # case the level exists to stop. Refusing it is §9.2's structured
        # no-route, not a substitution: RAVIS does not pick something else.
        forbidden = refusals.get(requested)
        if forbidden:
            return RouteDecision(
                requested=requested,
                selected=None,
                reason="refused by policy; RAVIS does not route around a policy constraint",
                excluded=[ExcludedCandidate(model=requested, reasons=forbidden)],
                requirements=requirements.describe() + policy.describe(),
            )
        return RouteDecision(
            requested=requested,
            selected=requested,
            reason="named directly by the client; no pool resolution applied",
            # Recorded even though nothing was selected *from* it. It is what
            # lets a failed attempt tell "no upstream offers this model" apart
            # from "the upstream it was guessed onto is unhappy" — the
            # difference between a typo and an outage.
            considered=sorted(candidates),
            requirements=requirements.describe() + policy.describe(),
        )

    def _direct(
        self,
        requested: str,
        target: str,
        candidates: dict[str, ModelCapabilities],
        requirements: RequestRequirements,
        foreign: bool = False,
        forbidden: list[str] | None = None,
    ) -> RouteDecision:
        """Handle `ravis/<provider>/<model>`.

        Bypasses selection but not existence: addressing a model the upstream
        does not offer is a no-route rather than a request forwarded to fail
        confusingly at the provider.

        **Unless the provider is not the one whose catalogue this is.** A
        translated provider (§6, Path B) publishes its own model list, and an
        Anthropic model will never appear in a local runtime's `/v1/models`.
        Checking it against the wrong catalogue would refuse every Path B
        request the moment the local upstream had any models at all — existence
        is real, but only the owning provider can answer it.
        """
        # **This reason string has always said "policy still applies"** — it
        # said so from M5, and until M16 nothing checked any. A direct address
        # bypasses *selection*; it was never meant to bypass a boundary, and a
        # privacy level anybody can step around by naming a model is not one.
        if forbidden:
            return RouteDecision(
                requested=requested,
                selected=None,
                reason="refused by policy; a direct address does not bypass a policy constraint",
                excluded=[ExcludedCandidate(model=target, reasons=forbidden)],
                requirements=requirements.describe(),
            )
        if not foreign and candidates and target not in candidates:
            return RouteDecision(
                requested=requested,
                selected=None,
                reason=f"{target} is not offered by the configured upstream",
                considered=sorted(candidates),
            )
        return RouteDecision(
            requested=requested,
            selected=target,
            reason="direct address to another provider; its own catalogue is authoritative"
            if foreign
            else "direct address; selection bypassed, policy and tracking still apply",
            requirements=requirements.describe(),
        )

    def _select_from_pool(
        self,
        pool: VirtualModelPool,
        candidates: dict[str, ModelCapabilities],
        residency: ResidencySnapshot,
        memory: MemoryReading,
        requirements: RequestRequirements,
        unavailable: Mapping[str, str],
        remote: frozenset[str] = frozenset(),
        chosen: tuple[str, ...] = (),
        observed: Mapping[str, float] | None = None,
        policy: RoutingPolicy | None = None,
        refusals: Mapping[str, list[str]] | None = None,
        sticky: str = "",
        expected_session_requests: int | None = None,
        reasoning: Mapping[str, float] | None = None,
        role_evidence: Mapping[str, Mapping[str, str]] | None = None,
        resold: Mapping[str, str] | None = None,
        explore: Exploration | None = None,
        direct_owners: Mapping[str, str] | None = None,
    ) -> RouteDecision:
        """Resolve a pool to one model, or explain why it cannot be resolved.

        Three independent sets of hard constraints apply, and all are checked
        before anything is ranked (§9.1): the pool's own invariants, which are
        configuration; the request's requirements, derived from what the client
        sent; and the application's policy, resolved from its identity. A
        candidate failing any of them is gone before scoring — which is how §14's
        "privacy constraints can never be overridden by score" is a property of
        the structure rather than a rule someone has to remember.
        """
        policy = policy or RoutingPolicy()
        refusals = refusals or {}
        # **The owner's exclusions go first**, before membership, measurement, an operator's
        # picks or the fallback, so nothing downstream can bring one back.
        barred, candidates = _without_owner_exclusions(pool, candidates)
        decision = RouteDecision(
            requested=pool.pool_id,
            pool_id=pool.pool_id,
            considered=sorted(candidates),
            requirements=_all_requirements(pool, requirements) + policy.describe(),
            unverified=unverified_notes(requirements, candidates),
        )
        # An operator's selection wins outright; the pool's own default applies
        # only when they have made none. Resolved here rather than by the caller
        # because the default is expressed over *this* catalogue — "the small
        # ones" means nothing until you know what is present.
        # **Evidence for this pool's role, where any exists.** A build measured
        # under the role and passing is a member whether or not the pool's
        # hand-written families name it; one measured and failing is not,
        # whether or not they do. See `VirtualModelPool._admits`.
        measured = {
            model: fit[pool.evidence_role]
            for model, fit in (role_evidence or {}).items()
            if pool.evidence_role and pool.evidence_role in fit
        }
        effective = chosen or pool.default_membership(
            sorted(candidates),
            {model: known.price_per_million for model, known in candidates.items()},
            measured,
        )
        by_default = not chosen
        # **An empty membership means nothing qualified, not "no restriction".**
        # `_rank` reads `not chosen or model in chosen`, so a membership that
        # filtered everything out is indistinguishable there from one that was
        # never computed — and the pool silently admits the candidates it had
        # just excluded. Harmless for a pool expressing taste, which falls back
        # to everything on purpose; wrong for one whose ceiling is a contract,
        # where the fallback bills a caller who asked for free.
        if by_default and pool.refuse_above_ceiling and not effective and candidates:
            decision.excluded = _exclusions(
                pool, candidates, requirements, unavailable, remote, effective,
                by_default, refusals, measured,
            )
            decision.excluded += _owner_excluded(barred)
            decision.reason = (
                f"no candidate satisfies {pool.pool_id}; the pool is unavailable"
            )
            return decision
        decision.excluded = _exclusions(
            pool, candidates, requirements, unavailable, remote, effective, by_default,
            refusals, measured,
        )
        decision.excluded += _owner_excluded(barred)
        eligible = _rank(
            pool, candidates, residency, memory, requirements, unavailable, remote,
            effective, observed or {}, refusals, policy, sticky,
            expected_session_requests, reasoning, resold, direct_owners,
        )

        if not eligible:
            # §5.2: a pool with no satisfying candidate is *unavailable*. Never
            # relaxed to "closest match" — that is how an agent ends up on a
            # model that cannot call tools.
            decision.reason = (
                f"no candidate satisfies {pool.pool_id}; the pool is unavailable"
                if candidates
                else "no models are available from the configured upstream"
            )
            return decision

        # **Exploration is applied after ranking, never inside it.** Everything
        # above decides what is *best*; this decides, occasionally and only when
        # switched on, to spend one request learning about something else
        # instead. Keeping the two apart is what lets the reason below tell the
        # truth about which of the two happened.
        trying = _explore_pick(
            eligible, explore, observed or {}, probe=requirements.tool_probe,
            remote=remote, prefers_local=policy.prefers_local,
        )
        decision.selected = trying or eligible[0]
        decision.explored = trying
        # §10 requires every fallback candidate to satisfy the original hard
        # constraints and the pool invariants. Taking them from the ranked
        # eligible list makes that structural: a candidate that failed either
        # check is not in this list to be chosen from.
        #
        # Filtered by identity rather than sliced from index 1, because an
        # explored model can come from anywhere in the ranking -- slicing would
        # leave it listed as its own fallback and drop the model that would
        # actually have won.
        decision.fallbacks = [
            model for model in eligible if model != decision.selected
        ][:MAX_FALLBACKS]
        decision.reason = _exploration_reason(trying, observed or {}) if trying else (
            _selection_reason(
                pool, eligible, residency, memory, expected_session_requests,
                _load_would_not_amortise(expected_session_requests, memory),
                policy.prefers_cheap,
                _reasoning_note(eligible, reasoning or {}, requirements.output_budget),
                remote,
                _probe_note(eligible, requirements, residency, remote, policy),
                _locality_note(eligible, remote, policy),
            )
        )
        return decision


def _unknown_address(requested: str, candidates: dict[str, ModelCapabilities]) -> RouteDecision:
    """A `ravis/…` name that is neither a pool nor a direct address.

    §5.3 says an explicit request outranks any inference RAVIS could make, and
    that is why a plain model name is forwarded untouched. It does not apply
    here, because `ravis/` is **RAVIS's own namespace**: no upstream serves a
    model called `ravis/chat`, so a name in that namespace that RAVIS does not
    recognise is a typo rather than an instruction.

    Forwarding it anyway is what this exists to stop, and the failure was worse
    than an error. `ravis/chat` reached LM Studio, which answered with whatever
    happened to be loaded — so the request succeeded with no pool, no capability
    filtering, no tool invariant and no fallback, and looked entirely fine. It
    also invented a health record for a model nobody has. A 422 that names the
    near-miss turns twenty minutes of confusion into one line.
    """
    suggestion = _nearest_pool(requested)
    advice = (
        f"did you mean {suggestion}?"
        if suggestion
        else f"known pools are: {', '.join(sorted(POOLS_BY_ID))}"
    )
    return RouteDecision(
        requested=requested,
        selected=None,
        reason=(
            f"{requested} is not a pool, and is not a direct address of the form "
            f"{POOL_PREFIX}<provider>/<model> — {advice}"
        ),
        considered=sorted(candidates),
    )


def _nearest_pool(requested: str) -> str:
    """The pool someone probably meant, or empty when nothing is close enough.

    Containment is tried before edit distance, because the mistake this actually
    sees is *dropping the qualifier* — `ravis/chat` for `ravis/clarvis-chat`,
    `ravis/agent` for `ravis/clarvis-agent`. Edit distance is hopeless at that:
    the shared `ravis/` prefix dominates the ratio, and it confidently proposed
    `ravis/cheap` for `chat` and `ravis/fast` for `agent`.

    Edit distance still earns its place for the other shape, a genuine
    misspelling — `clarvis-cat` — where containment finds nothing. The cutoff is
    raised above the default because a wrong suggestion is worse than none: it
    sends the reader to fix something that was never the problem.
    """
    remainder = requested[len(POOL_PREFIX):]
    if not remainder:
        return ""
    contained = sorted(
        (pool for pool in POOLS_BY_ID if remainder in pool[len(POOL_PREFIX):]),
        key=len,
    )
    if contained:
        return contained[0]
    near = get_close_matches(remainder, [p[len(POOL_PREFIX):] for p in POOLS_BY_ID], n=1,
                             cutoff=0.7)
    return f"{POOL_PREFIX}{near[0]}" if near else ""


def _all_requirements(pool: VirtualModelPool, requirements: RequestRequirements) -> list[str]:
    """Every hard constraint in force, from the pool and from the request.

    "none" appears only when *both* sources are empty. Emitting it per source
    produced explanations reading `['none', 'vision REQUIRED …']`, which says the
    opposite of what it means in the place a reader looks first.
    """
    described = _describe_requirements(pool) + requirements.describe()
    return described or ["none"]


def _describe_requirements(pool: VirtualModelPool) -> list[str]:
    """The pool's own invariants, in the words a route explanation will show."""
    described = [
        f"{capability.value} REQUIRED"
        for capability in sorted(pool.requirements.required, key=lambda item: item.value)
    ]
    if pool.requirements.minimum_context:
        described.append(f"minimum context {pool.requirements.minimum_context}")
    return described


def _without_owner_exclusions(
    pool: VirtualModelPool, candidates: dict[str, ModelCapabilities]
) -> tuple[list[str], dict[str, ModelCapabilities]]:
    """The models the owner ruled out of this pool, and the candidates without them."""
    barred = sorted(model for model in candidates if pool.owner_excludes(model))
    return barred, {model: known for model, known in candidates.items() if model not in barred}


def _owner_excluded(models: list[str]) -> list[ExcludedCandidate]:
    """The owner's exclusions, named in the route's explanation rather than silently absent."""
    return [ExcludedCandidate(model, ["ruled out of this pool by the owner"]) for model in models]


def _exclusions(
    pool: VirtualModelPool,
    candidates: dict[str, ModelCapabilities],
    requirements: RequestRequirements,
    unavailable: Mapping[str, str],
    remote: frozenset[str] = frozenset(),
    chosen: tuple[str, ...] = (),
    by_default: bool = False,
    refusals: Mapping[str, list[str]] | None = None,
    measured: Mapping[str, str] | None = None,
) -> list[ExcludedCandidate]:
    """Every candidate that failed, with all of its reasons.

    Pool invariants, request requirements, policy and open circuits are reported
    together and undifferentiated, because the person reading this wants to know
    why a model was not used — not which of four rule sources rejected it.

    An open circuit is listed here, among the hard exclusions, rather than
    treated as a preference. §10 is unambiguous that a failing provider is to be
    routed *away from*, and a soft penalty would keep sending it traffic.
    """
    excluded = []
    for model in sorted(candidates):
        reasons = pool.requirements.unmet_by(candidates[model], remote=model in remote)
        if chosen and model not in chosen:
            # Worded differently for the two cases on purpose: one is fixed by
            # ticking a box and the other by understanding what the pool is for.
            # A measured failure is named as one. "Outside this pool's default
            # tier" would be true and useless for a build that was benchmarked
            # for this exact role and did not clear the bar — the fix for that
            # is a different model, not a ticked box.
            verdict = (measured or {}).get(model, "")
            reasons.append(
                f"measured for {pool.evidence_role} and did not qualify"
                if verdict == "UNSUPPORTED"
                else _default_exclusion(pool) if by_default
                else "not among the models chosen for this pool"
            )
        reasons += unmet_by(requirements, candidates[model])
        reasons += (refusals or {}).get(model, [])
        refused = model in unavailable
        if refused:
            reasons.append(unavailable[model])
        if reasons:
            excluded.append(
                ExcludedCandidate(model=model, reasons=reasons, circuit_open=refused)
            )
    return excluded


def _rank(
    pool: VirtualModelPool,
    candidates: dict[str, ModelCapabilities],
    residency: ResidencySnapshot,
    memory: MemoryReading,
    requirements: RequestRequirements,
    unavailable: Mapping[str, str],
    remote: frozenset[str] = frozenset(),
    chosen: tuple[str, ...] = (),
    observed: Mapping[str, float] | None = None,
    refusals: Mapping[str, list[str]] | None = None,
    policy: RoutingPolicy | None = None,
    sticky: str = "",
    expected_session_requests: int | None = None,
    reasoning: Mapping[str, float] | None = None,
    resold: Mapping[str, str] | None = None,
    direct_owners: Mapping[str, str] | None = None,
) -> list[str]:
    """Order the eligible candidates, cheapest-to-reach among equals.

    Residency is a *preference*, never a constraint — §9.2 lists "prefer already
    loaded" as soft, so a cold model is never excluded, only ranked below a warm
    one that is otherwise equal.

    Which preference leads depends on memory. Normally the pool's declared intent
    wins and residency breaks its ties: a coding pool should reach for a coding
    model even if that means a load. **Under memory pressure the order inverts**,
    because loading anything new is the thing to avoid — this is M14's acceptance
    criterion, that pressure produces a safe route change, and it changes the
    route without ever changing what the pool is allowed to select.
    """
    refusals = refusals or {}
    policy = policy or RoutingPolicy()
    # §12.2's tradeoff, decided once for the whole ranking rather than per
    # candidate: the question is about this *request*, not about each model.
    #
    # **Three states, not two, and the third is the important one.** `None`
    # means RAVIS has not watched this application long enough to know how long
    # its sessions run, and the honest response to not knowing is to route
    # exactly as it did before this tradeoff existed rather than to guess
    # "short". Only a *measured* expectation moves anything.
    #
    # **Never loads under memory pressure.** M14's shipped half exists because
    # loading anything new is the thing to avoid when memory is short, and a
    # long session is not a reason to do it anyway — the machine's state
    # outranks the conversation's length. This is where the two halves of M14
    # meet, and reversing it would let a busy conversation force exactly the
    # load the observation half was built to prevent.
    short_session = _load_would_not_amortise(expected_session_requests, memory)
    members = [
        model for model, known in candidates.items()
        if not pool.requirements.unmet_by(known, remote=model in remote)
        and not unmet_by(requirements, known)
        and model not in unavailable
        # Removed here, not penalised in `key` below. §14's rule 14 is that a
        # privacy constraint cannot be overridden by score, and the only way to
        # guarantee that is for the candidate never to reach the scoring.
        and model not in refusals
        # An operator's selection narrows what the invariants already allowed.
        # Applied *after* them, never instead: `ravis/local` promises the
        # request never leaves this machine, and a promise somebody can tick
        # away in a picker is not a promise.
        and (not chosen or model in chosen)
    ]
    pressured = memory.under_pressure
    demoted = _demoted_resellers(members, resold or {}, direct_owners)

    def key(model: str) -> tuple[float | str, ...]:
        # **A tool probe's reach is consulted before everything but privacy
        # (§8.7).** Zero for every candidate of every request that is not a
        # probe, so ordinary traffic sorts exactly as it did before this term
        # existed. For a probe it puts every candidate that answers without a
        # model load ahead of every one that needs a load — ahead of session
        # affinity and of the pool's own preference, for the reasons
        # `_probe_avoids` gives, and behind `_privacy_lean`, which leads `terms`.
        probe = 1.0 if _probe_avoids(model, requirements, residency, remote) else 0.0
        # **Session affinity leads every term below (§12.1).** Sticky routing
        # exists for consistency, prompt caching, context continuity and reduced
        # model-load churn, and a preference that any other term can outvote
        # delivers none of those — the model would change the first time a
        # price or a latency sample moved.
        #
        # It is still only a preference, and the four conditions §12.1 gives for
        # breaking it are already enforced *above* this function rather than
        # here: a model whose capabilities no longer fit, whose context is
        # exceeded, whose provider's circuit is open, or which policy now
        # refuses, is not in `members` at all. So stickiness cannot hold a
        # conversation on a model that stopped being allowed — it can only
        # order the ones that are.
        affinity = 0.0 if sticky and model == sticky else 1.0
        preference = pool.preference_rank(model)
        # Reach, not warmth: a hosted model is not a cold one. See `_reach_rank`.
        warmth = _reach_rank(model, residency, remote)
        # Built in order rather than by prepending, because the order *is* the
        # policy and prepending hid it. `prefer_local` was appended after
        # warmth, so a local model whose residency was unknown lost to a remote
        # one on reach before placement was ever consulted — and residency is
        # unknown for every local model until a runtime reports it.
        #
        # Cost first, then placement, then the pool's own preference and reach:
        # a pool that asked to be cheap means money before anything else, and
        # between two free models the one that costs no network is the cheaper.
        #
        # Heterogeneous because the last component is the model name — the
        # total, reproducible order §9.7's determinism gate requires.
        # §12.2's load-versus-don't tradeoff, ahead of the pool's own
        # preference and behind session affinity.
        #
        # **Ahead of preference, because that is the case §12.2 describes.**
        # Its example is a stronger model that takes fourteen seconds to load:
        # *for one simple question B is the worst choice despite being the
        # stronger model.* Preference already outranks warmth, so without this
        # term RAVIS pays that load for a one-line question — the wrong half of
        # the tradeoff, and the half that is visible to whoever is waiting.
        #
        # It fires only when RAVIS has *measured* that this application's
        # sessions are short. A long expectation lifts nothing and adds nothing:
        # preference already outranks warmth, so "load stronger" is what happens
        # by default and needed no rule of its own. What was missing was the
        # brake, not the accelerator.
        load = 1.0 if short_session and _pays_a_load(model, residency, remote) else 0.0
        # **A reseller copy sorts behind the maker's own, and is not removed.**
        # It was an exclusion first, which is the stronger thing to say and the
        # wrong one: the vendor's catalogue is its own claim about what it
        # serves, and Google's lists models whose generate endpoint answers 404
        # — *"no longer available to new users"*. Excluding the aggregator's
        # working copy in favour of that left no candidate at all, and Gemini
        # became unreachable through a rule about price.
        #
        # Ranked, the same preference costs nothing when the direct route works
        # and keeps the fallback when it does not: the attempt chain walks this
        # order, so the maker is tried first and the reseller still catches the
        # 404. An explicit address is still *refused* — that is a person asking
        # a question, and an answer naming the cheaper route is worth more than
        # a silent substitution.
        #
        # **Only behind a maker's copy that can serve this request.** See
        # `_demoted_resellers`: ranking OpenRouter's Claude behind an Anthropic
        # copy the pool had just refused handed every agent session to Qwen.
        reseller = 1.0 if model in demoted else 0.0
        # **The privacy lean leads the whole key, and nothing else may.** See
        # `_privacy_lean`: every term consulted before it is one more way for a
        # `LOCAL_PREFERRED` request to leave this machine, and five of them were.
        terms: list[float | str] = [
            *_privacy_lean(policy, model, remote),
            probe, affinity, load, reseller,
            *_preference_terms(pool, policy, model, candidates, remote, observed or {}),
        ]
        terms.extend((warmth, preference) if pressured else (preference, warmth))
        # **Speed, after the preference rather than before it.** "Nobody likes a
        # slow chatbot" — but among models a pool considers equally right for
        # the job, not instead of that judgement. `prefer_fast` ranks speed
        # ahead of preference and is correct for `ravis/fast`; a conversational
        # pool that did the same would hand every turn to whichever small build
        # happened to answer quickest, which is the failure `_reach_rank`'s note
        # already describes from the other direction.
        #
        # The bucket is wide on purpose: models within it tie here and a later
        # term decides, so a difference nobody would notice reorders nothing.
        if pool.speed_tiebreak_ms > 0:
            terms.append(_speed_rank(model, observed or {}, pool.speed_tiebreak_ms))
        lead: tuple[float | str, ...] = tuple(terms)
        # Size is the *last* thing consulted, and only for a pool that declared
        # a preference at all.
        #
        # The restriction is not fussiness. Its justification — "among
        # candidates a pool already considers identical, the smaller is both
        # faster and cheaper" — assumes the pool has *expressed* something for
        # them to be equal on. A pool with an empty `prefer` considers
        # everything equal, so size stops being a tiebreak and silently becomes
        # the entire ranking. It did: eight of the thirteen pools resolved to
        # the smallest installed model, which on this machine is a 1.7B nobody
        # has ever benchmarked, and `ravis/balanced` selecting the tiniest thing
        # available is self-evidently not balanced.
        #
        # Pools that say nothing fall back to alphabetical order, which is
        # meaningless — and meaningless is the honest state until M13, because
        # it is at least not systematically biased towards whatever is smallest.
        # **The reasoning tiebreak (M16), ahead of size and applied to every
        # pool.** Both halves of that sentence are deliberate.
        #
        # Ahead of size, because it is the better version of the same idea. Size
        # is a proxy — "smaller is cheaper to run" — and the measured share is
        # the thing itself: how much of the budget this build has actually been
        # observed to spend before it starts answering. Where both have an
        # opinion, the measurement should win.
        #
        # Applied even to a pool that declared nothing, which is exactly where
        # size is *not*, and the difference is what the paragraph above is
        # about. Size became the whole ranking for those pools and biased them
        # towards the smallest thing installed; this cannot, because it is
        # silent unless the request set a ceiling *and* SIRVIS measured this
        # build spending it on thinking. `ravis/auto` declares no preference at
        # all, so gating this the way size is gated would leave the one pool
        # M16 named ranking alphabetically — which is where the defect lives.
        thinking = _reasoning_rank(model, reasoning, requirements.output_budget)
        if not pool.prefer:
            return (*lead, thinking, 1, 0.0, model)
        return (*lead, thinking, *_cost_rank(model, candidates, remote))

    return sorted(members, key=key)


def _demoted_resellers(
    members: list[str], resold: Mapping[str, str], direct_owners: Mapping[str, str] | None
) -> frozenset[str]:
    """The aggregator copies to rank behind their maker's own.

    Only where the maker's own can serve: a resold copy is demoted when at least
    one candidate its maker serves directly survived every constraint that
    `members` already applied. Otherwise the aggregator is the only route that
    works, and ranking it last hands the request to whatever nobody resells —
    which, on 11 September 2026, was every Clarvis agent session going to Qwen
    while the pool preferred Claude Sonnet and Anthropic's copies were refused
    for a capability their catalogue does not publish.

    `None` keeps the rule as it was before the owner map existed, so a caller
    that cannot say who serves what still gets the maker-first order.
    """
    if direct_owners is None:
        return frozenset(resold)
    serving = {direct_owners[model] for model in members if model in direct_owners}
    return frozenset(model for model, vendor in resold.items() if vendor in serving)


def _explore_pick(
    eligible: list[str],
    explore: Exploration | None,
    observed: Mapping[str, float],
    probe: bool = False,
    remote: frozenset[str] = frozenset(),
    prefers_local: bool = False,
) -> str:
    """The model this request will try instead of the winner, or `""`.

    **The whole point is the models nobody has ever timed.** A pool that ranks
    on speed hands each turn to whatever is already known to be fast, and a
    model nothing has measured sorts as merely average -- enough to keep it out
    of first place indefinitely. That is the loop this breaks: never chosen,
    never measured, never chosen. The only way to learn what a model costs in
    practice is to spend one request finding out.

    Prefers a candidate with no timing at all when asked to, and falls back to
    the full eligible list once everything has been measured -- "explore" still
    means something then, because a single old sample from a machine under
    different load is not a current figure.

    Returns `""` for the ordinary case rather than the winner, so the caller can
    tell an exploration apart from an ordinary win and *say so*. Quietly
    promoting a model and then explaining the choice as though it had won on
    merit would be the same class of untruth as the cold-start line that was
    removed from this file a day earlier.

    **Never on a tool probe (§8.7).** A probe exists to be answered by whatever
    is cheapest to reach, and the ranking has just put that first; a pick from
    further down is the one remaining way it could land on a cold model anyway.
    Nor would it buy a timing worth having — a one-token reply says little about
    how fast a model answers the requests that follow.

    **Never off this machine for a request that prefers it (§14).** This runs
    after ranking, so `_privacy_lean` leading the key does not reach it: the
    hosted candidates sit straight below the local ones, and a roll rescaled
    across the alternatives landed on them — one turn in twelve of a
    `LOCAL_PREFERRED` conversation sent away to take a timing. And an explored
    pick is recorded as the session's model like any other. So when the winner
    is local, hosted alternatives are not explored; local ones still are. When
    the winner is hosted, nothing local was eligible, and exploring among hosted
    models sends the request nowhere it was not already going.
    """
    if explore is None or explore.rate <= 0 or not eligible or probe:
        return ""
    if explore.roll >= explore.rate:
        return ""
    # **The winner is not a candidate for exploration.** Obvious in hindsight
    # and wrong in the first draft, which drew from the whole eligible list: the
    # roll is rescaled into an index, so every roll well under the rate landed on
    # index 0 -- the model that was going to be chosen anyway. Roughly one
    # exploration in three silently did nothing, and the two tests that caught it
    # only did so because they asserted the answer *changed*.
    # And not a model an exploration recently failed on: exploration prefers
    # the untimed, a model that always fails is never timed, and without this
    # it would be drawn again at every exploration.
    alternatives = [model for model in eligible[1:] if model not in explore.avoid]
    # The privacy line from the docstring. Filtered rather than truncated at the
    # first hosted model, so it does not depend on the ranking having grouped
    # them — though `_privacy_lean` guarantees that it has.
    if prefers_local and eligible[0] not in remote:
        alternatives = [model for model in alternatives if model not in remote]
    if not alternatives:
        return ""
    unmeasured = [model for model in alternatives if model not in observed]
    field = unmeasured if (explore.prefer_unmeasured and unmeasured) else alternatives
    # The same roll is rescaled into an index rather than drawn again, for the
    # purity reason above. Rescaled across the field so every candidate is
    # reachable: using the raw roll would only ever land in the first `rate`
    # share of the list, which at a rate of 0.08 is the first model alone.
    return field[min(int(explore.roll / explore.rate * len(field)), len(field) - 1)]


def _exploration_reason(model: str, observed: Mapping[str, float]) -> str:
    """Why this answer came from somewhere other than the top of the ranking.

    Named as a deliberate act, because it was one. An explanation that let this
    read as an ordinary win would make every *other* route explanation less
    trustworthy too.
    """
    unmeasured = model not in observed
    return (
        f"trying {model} on purpose rather than the usual pick — "
        + (
            "nothing has timed it yet, and a model that is never chosen is never "
            "measured"
            if unmeasured
            else "an occasional re-check, so its timing does not go stale"
        )
        + ". Switch this off under Model in chat's settings."
    )


def _default_exclusion(pool: VirtualModelPool) -> str:
    """Why a pool's own default left this candidate out.

    Named rather than generic, because the two defaults are fixed differently. A
    tier exclusion is corrected by ticking the model; a price exclusion usually
    means the model is not free and the pool is the free one, which is a
    different conversation.

    The first version interpolated `default_tier` unconditionally and produced
    "outside this pool's default  tier" — two spaces and no tier — for every
    price-filtered pool, because `ravis/cheap` has no tier at all.
    """
    if pool.default_tier and pool.max_price_per_million is not None:
        return (
            f"outside this pool's default {pool.default_tier} tier, or above its "
            f"${pool.max_price_per_million:g} per-million ceiling — tick it to include it"
        )
    if pool.default_tier:
        return f"outside this pool's default {pool.default_tier} tier — tick it to include it"
    if pool.max_price_per_million is not None:
        ceiling = pool.max_price_per_million
        return (
            "costs more than nothing per token, and this pool is the free one"
            if ceiling == 0
            else f"above this pool's ${ceiling:g} per-million ceiling"
        ) + " — tick it to include it"
    return "not among this pool's default members — tick it to include it"


def _privacy_lean(policy: RoutingPolicy, model: str, remote: frozenset[str]) -> list[float]:
    """`LOCAL_PREFERRED`'s term, or nothing, and why it is consulted first (§14).

    §14's rule — *privacy constraints can never be overridden by score* — holds
    by construction for the levels that exclude, because a refused candidate
    never reaches `_rank`. `LOCAL_PREFERRED` is the one level that ranks
    instead, so its protection is entirely its position in the key: any term
    consulted before it can send a request off this machine against a level the
    caller's identity asked for. Five terms could, and each was the budget-lean
    bug `_preference_terms` records, again. §12.2's load brake sent a one-shot
    request to a hosted model to spare a cold local one its load, and did the
    same for a long session under memory pressure. Session affinity held a
    conversation on a hosted model when a local one qualified. A tool probe's
    preference for whatever needs no load had a special case of its own to stay
    behind privacy, and still handed the tie it left to those two. And a pool's
    `prefer_remote` and `prefer_fast` ranked ahead of it inside
    `_preference_terms`.

    **Ahead of session affinity too, and deliberately.** Affinity buys a warm
    prompt cache, a consistent voice and fewer reloads (§12.1), and those are
    real — but they are score, and §12.1 itself lists a policy change among the
    reasons to break it. What settles it is that a chat request is stateless:
    every turn re-sends the whole conversation, so each turn held on a hosted
    model exports the entire history again, plus whatever is new. A
    `LOCAL_PREFERRED` session becomes sticky to a hosted model through a turn no
    local model could take (a circuit open, a runtime restarting), an
    exploration, or one of the pulls above — so affinity leading would turn a
    moment's outage into a whole conversation sent away. Coming home costs a
    cache miss and perhaps a load, which is exactly what the level is for.

    **Under memory pressure this means paying a load.** That was already the
    outcome for every application RAVIS had not measured yet — pressure ranks
    residency behind the preference terms, and privacy was one of them — so the
    brake only made measured and unmeasured applications disagree about it.

    **Inside each group every other term still decides.** Every local
    candidate scores zero here and every hosted one scores one, so among local
    models affinity, the brake, the probe and the pool's preference order them
    exactly as before, and the same holds among hosted ones when nothing local
    is eligible. Nothing is excluded: a hosted model stays in the fallback chain,
    behind the local ones (§9.2). Exploration runs after ranking and so cannot
    be reached from here; `_explore_pick` holds the same line for itself.

    **Empty rather than zero for every other policy**, so no other request's key
    changes shape, and the rule `_preference_terms` keeps — a term is present
    only when something asked for it — holds here too.
    """
    if not policy.prefers_local:
        return []
    return [0.0 if model not in remote else 1.0]


def _preference_terms(
    pool: VirtualModelPool,
    policy: RoutingPolicy,
    model: str,
    candidates: dict[str, ModelCapabilities],
    remote: frozenset[str],
    observed: Mapping[str, float],
) -> list[float]:
    """The declared preferences, in the order they are consulted.

    Lifted out of `key` when adding §14's budget bands took `_rank` past the
    complexity gate. The order *is* the policy, so it stays one readable list
    rather than being spread across the caller — and each entry is only
    appended when something actually asked for it, since an unconditional term
    becomes the whole ranking for every pool that declares nothing.

    **`prefer_remote` ranks before speed and `prefer_local` after it.** The
    asymmetry was found live: a pool that prefers hosted models is saying
    *where* first and *which* second, and putting speed ahead of it let a
    resident 1.5B model — 181 ms against gpt-4o-mini's 484 ms — win the
    conversation the pool exists to keep away from it. `prefer_local` has no
    such problem, because the pools declaring it rank on cost first by design.
    """
    terms: list[float] = []
    if pool.prefer_remote:
        terms.append(0.0 if model in remote else 1.0)
    if pool.prefer_fast:
        terms.append(_speed_rank(model, observed, pool.speed_bucket_ms))
    # **`LOCAL_PREFERRED`'s term is no longer in this list, and why is worth
    # keeping.** It sat here, and these terms carried two claims the order
    # underneath contradicted. A budget lean was said to sit "behind privacy,
    # which is never traded for money" while being appended *before* it, and
    # "ahead of the pool's own cost preference" while being appended *after*
    # `pool.prefer_cheap`. Earlier terms dominate a tuple sort, so both were
    # exactly inverted: an account approaching its budget would move a request
    # off-device to save a fraction of a cent.
    #
    # Moving privacy up this list fixed that instance and left the class.
    # `prefer_remote` and `prefer_fast` above were still ahead of it, and the
    # probe, affinity and load-brake terms were ahead of this whole list in
    # `_rank` — each a way off the machine that the budget fix had not looked
    # for. It lives in `_privacy_lean` now, consulted before every one of them.
    #
    # A budget being approached leans cheaper without refusing anything (§14's
    # two middle bands), and leads the pool's own cost preference: a budget is a
    # fact about the account, `prefer_cheap` is a fact about what the pool is
    # for.
    if policy.prefers_cheap:
        terms.append(_price_rank(candidates.get(model)))
    if pool.prefer_cheap:
        terms.append(_price_rank(candidates.get(model)))
    # The pool's own placement preference is a default, so it comes last.
    if pool.prefer_local:
        terms.append(0.0 if model not in remote else 1.0)
    return terms


def _reasoning_rank(
    model: str, reasoning: Mapping[str, float] | None, budget: int | None
) -> float:
    """How much of a capped answer this build has been measured to spend thinking.

    **The thing being ranked is fit, not quality.** RAVIS is not saying a model
    that reasons is a worse model — §13.1 forbids reducing evidence to a score
    and this reduces nothing. It is saying something narrower and entirely
    mechanical: `max_tokens` is one budget shared between thinking and
    answering, so a build measured to spend two thirds of it thinking returns a
    third of the answer the caller asked for, and can return none at all. That
    is the failure this exists to prevent, and it is visible from outside as a
    reply that arrives empty rather than as a routing decision.

    **Silent in three situations, each for its own reason.**

    No budget: an uncapped request has nothing for thinking to crowd out. It
    still costs latency and tokens, but those are the pool's business — a pool
    that ranks on speed or price already says so, and inventing the preference
    for pools that did not would be the invisible policy §9.4 forbids.

    No measurement: 0.0, the same value a build measured never to think gets.
    Not last, which would penalise every build SIRVIS has not reached — most of
    the catalogue — and turn absence into a verdict, the confusion §12.1 exists
    to keep apart. A tie here simply falls through to whatever ranked next.

    An estimate: also 0.0, decided one layer down in `measured_share`. A share
    inferred from a runtime that hid its token counts is not established, and
    §9.1 fails closed on what is not established.

    So the term is dormant except where a request set a ceiling *and* SIRVIS
    counted the tokens, which is precisely the case M16 named.
    """
    if not budget or not reasoning:
        return 0.0
    return reasoning.get(model, 0.0)


def _reasoning_note(
    eligible: list[str], reasoning: Mapping[str, float], budget: int | None
) -> str:
    """Say so when a measured reasoning share moved a candidate down.

    Only when it *changed* something. A note on every capped request would say
    "nothing thought too much" thousands of times and train a reader to skip the
    line that matters, and §9.7 asks an explanation to separate what decided a
    route from what merely applied to it.

    The arithmetic is shown rather than the fraction alone, because "0.68" is a
    number and "leaves about 20 of your 64 tokens for the answer" is the reason.
    """
    if not budget or not eligible:
        return ""
    demoted = [
        (model, reasoning[model])
        for model in eligible[1:]
        if reasoning.get(model, 0.0) > reasoning.get(eligible[0], 0.0)
    ]
    if not demoted:
        return ""
    model, share = max(demoted, key=lambda item: item[1])
    return (
        f"{model} ranked lower because SIRVIS measured it spending {share:.0%} of its "
        f"output on reasoning, which at max_tokens={budget} leaves about "
        f"{int(budget * (1 - share))} tokens for the answer itself — a tiebreak on "
        f"what fits the budget, not on quality"
    )


def _reach_rank(
    model: str, residency: ResidencySnapshot, remote: frozenset[str]
) -> float:
    """How much work stands between the router and a first token.

    **A remote model is not a cold one, and treating them alike was the
    switchboard's central error.** `Residency.UNKNOWN` ranked 2, tied with
    `COLD`, and every cloud model is UNKNOWN because a cloud model has no
    residency to report. So RAVIS believed that calling an API and loading a
    seventy-billion-parameter model off disk cost about the same. One is a
    network round trip; the other is tens of seconds and gigabytes of RAM.

    The ordering that follows is fact rather than estimate. A resident local
    model needs neither a load nor a network hop. A remote model needs no load —
    that is what "hosted" means — and one round trip. A cold local model needs
    the load, which is the largest of the three by orders of magnitude.

    What this deliberately does *not* claim is how long any of it takes. RAVIS
    measures time-to-first-token per model in `HealthRegistry`, but only for
    models it has actually called: a handful out of six hundred. Ranking a
    catalogue on four samples would be the invented measurement §9.4 forbids,
    so this ranks the *steps required*, which is knowable for every model.
    """
    if model in remote:
        return _REMOTE_REACH
    return residency_rank(residency.state_of(model))


# How many requests a session must have made before RAVIS will pay a model load
# for it (§12.2's load-versus-don't tradeoff).
#
# **A declared policy, not a computed break-even, and the difference matters.**
# The real threshold is load time divided by the per-request advantage, and
# RAVIS can measure neither: no runtime publishes a load duration, SIRVIS
# measures `load_seconds` only inside a Runtime Set benchmark and does not
# expose it through the evidence API, and §13 deliberately refuses to reduce a
# model's quality to one comparable number. Computing a break-even from figures
# that do not exist would be §9.4's invented measurement with arithmetic on top.
#
# So this is a stated choice with a stated reason: a conversation that has run
# eight turns is a conversation rather than a question, and the cost of being
# wrong is asymmetric — paying a load for a one-line question wastes tens of
# seconds of somebody's attention, while declining one for a long session costs
# a slightly weaker model. When SIRVIS publishes load seconds this becomes
# arithmetic and this constant should disappear.
LOAD_AMORTISES_AFTER_REQUESTS = 8


def _load_would_not_amortise(
    expected_session_requests: int | None, memory: MemoryReading
) -> bool:
    """Whether §12.2 says to avoid paying a model load on this request.

    **Three states, and the third decides most requests.** `None` means RAVIS
    has not watched this application long enough to know how long its sessions
    run, and the honest response to not knowing is to route exactly as it did
    before this tradeoff existed rather than to guess "short". Only a measured
    expectation moves anything.

    **A measured expectation plus memory pressure is also short**, because
    pressure is a reason to avoid a load whatever the session length — the
    machine's state outranks the conversation's. It is gated on having an
    expectation at all so that this function never overrides the pressure
    handling that M14's shipped half already does on its own.

    One definition, used by the ranking and by the explanation, so a decision
    cannot be taken for a reason the explanation does not give.
    """
    if expected_session_requests is None:
        return False
    return (
        expected_session_requests < LOAD_AMORTISES_AFTER_REQUESTS
        or memory.under_pressure
    )


def _pays_a_load(model: str, residency: ResidencySnapshot, remote: frozenset[str]) -> bool:
    """Whether choosing this model means waiting for a local load first.

    Remote models are excluded rather than merely ranked lower, which is the
    distinction `_reach_rank` exists to make: a hosted model needs a round trip
    and no load, so no amount of session length changes what it costs. Only a
    cold local model has a one-time cost to amortise.

    Unknown residency counts as cold. A local model nobody has reported on may
    or may not be resident, and assuming it is would be assuming the cheaper of
    two answers — the direction that spends a user's time when it is wrong.
    """
    if model in remote:
        return False
    return residency_rank(residency.state_of(model)) >= _COLD_REACH


def _probe_avoids(
    model: str,
    requirements: RequestRequirements,
    residency: ResidencySnapshot,
    remote: frozenset[str],
) -> bool:
    """Whether a tool probe should rank this candidate behind the others (§8.7).

    §8.7: *RAVIS must not route a tiny tool probe to a cold or unsupported
    candidate and thereby make the pool appear incapable.* Unsupported was
    already impossible — a tools-bearing request excludes every model that
    cannot call them before anything is ranked. Cold was the default: the pool's
    declared preference outranks residency, so a probe to `ravis/clarvis-agent`
    went to the preferred coder whether or not it was loaded. Clarvis gives the
    probe ten seconds (`AbortSignal.timeout(10_000)` in `supportsTools`), against
    the fourteen-second load §12.2 uses as its example, so the probe times out or
    lands on a model being loaded, and the agent is reported as unable to call
    tools.

    **Why this outranks session affinity and the pool's preference.** Every
    candidate still in the ranking can call tools — that is what eligibility
    means for a request carrying one — so any of them answers the probe
    truthfully on the pool's behalf. The choice changes only how long the answer
    takes, and nothing a preference buys (a stronger model, a continued
    conversation, a cached prompt) exists in a one-token reply. Affinity's own
    case for itself includes "reduced model-load churn", which argues for this.

    **A hosted candidate needs no load, so it ranks with a resident one** —
    whatever the caller's privacy level. This used to score a hosted candidate
    as cold under `LOCAL_PREFERRED`, so a probe would not leave the machine to
    spare itself a load. That kept this term from moving it, and not the two
    behind: the hosted candidate tied the cold local one, and session affinity
    or the short-session brake broke the tie off the machine. `_privacy_lean`
    now leads the key, so every local candidate is already ahead of every
    hosted one. Within each of those groups the special case gave every member
    it touched the same score, so removing it reorders nothing: a resident local
    model still wins a probe, and a cold one is still paid for rather than the
    request leaving.

    **Nothing is excluded and nothing is refused.** When every candidate is
    avoided they all tie, and the probe routes exactly as it did before. A
    residency preference never excludes (§9.2), and refusing the probe would
    turn a preference into a constraint and leave the pool looking incapable for
    as long as nothing happens to be loaded — the outcome §8.7 exists to stop.

    Unknown residency counts as cold, by `_pays_a_load`'s rule and for its
    reason. Reusing it rather than restating it keeps this and §12.2's tradeoff
    from disagreeing about what a load is.
    """
    return requirements.tool_probe and _pays_a_load(model, residency, remote)


def _probe_note(
    eligible: list[str],
    requirements: RequestRequirements,
    residency: ResidencySnapshot,
    remote: frozenset[str],
    policy: RoutingPolicy,
) -> str:
    """Say so when a request was treated as a tool probe, and what that did.

    Said on every probe rather than only when it moved something, unlike
    `_reasoning_note`. Probes are rare — one per model per client session — and
    "why did the probe land there?" is exactly what an operator asks when an
    agent reports it cannot call tools, so the answer belongs on the decision
    whichever way it went. Two sentences, because the two outcomes send a reader
    to different places: one was steered, the other could not be.

    Reads `eligible[0]` as the selection, which holds because `_explore_pick`
    never explores on a probe.
    """
    if not requirements.tool_probe or not eligible:
        return ""
    recognised = "recognised as a one-tool, one-token capability probe (§8.7)"
    if not _probe_avoids(eligible[0], requirements, residency, remote):
        return (
            f"{recognised}, so candidates that answer without a model load were ranked "
            "ahead of any that must be loaded — a probe left waiting on a load can time "
            "out and make the pool look unable to call tools"
        )
    why = (
        "every candidate needs a model load or would leave this machine, which the "
        "privacy level prefers it not to"
        if policy.prefers_local
        else "every candidate needs a model load"
    )
    return (
        f"{recognised}, but {why}, so it was routed as usual rather than refused — "
        "preferring what is loaded ranks candidates and never excludes one (§9.2)"
    )


def _locality_note(eligible: list[str], remote: frozenset[str], policy: RoutingPolicy) -> str:
    """Say what `LOCAL_PREFERRED` did, whenever a hosted candidate was in the running.

    Silent when every eligible candidate is local, because the lean then ordered
    nothing. Otherwise said on every decision rather than only when it changed
    the winner, for `_probe_note`'s reason: "why did this pay a load instead of
    going hosted?" and "why did this leave the machine?" are both what an
    operator asks, and telling whether the lean moved anything would mean
    ranking everything a second time without it.

    Reads `eligible[0]` as the selection. An exploration carries its own reason
    and never reaches this.
    """
    if not policy.prefers_local or not any(model in remote for model in eligible):
        return ""
    if eligible[0] in remote:
        return (
            "the privacy level prefers this machine, but no local candidate was eligible, "
            "so a hosted one was used — that level ranks candidates and never excludes one "
            "(§14)"
        )
    return (
        "the privacy level prefers this machine, so every local candidate was ranked "
        "ahead of every hosted one, before session affinity, load cost, speed, price or "
        "the pool's own preference was weighed (§14)"
    )


# What `_reach_rank` returns for a cold local model, and for an unknown one.
_COLD_REACH = 2.0

# Strictly between WARM (1) and COLD (2), which is the whole point and which the
# first attempt got wrong: it was set to 2, tying with COLD, and reproduced the
# exact conflation it was written to remove. A float rather than renumbering
# `_RESIDENCY_RANK`, because those values mean something to the residency layer
# and nothing here should redefine them.
_REMOTE_REACH = 1.5

# Where an unpriced model sorts in a cheap pool: last.
#
# **Not free.** Only OpenRouter and the local runtimes publish a price — OpenAI,
# Google and Anthropic ship catalogues with no pricing at all — so treating
# absence as zero would hand every cheap route to the providers that happen to
# say least about themselves. Sorting unknown last is the fail-closed direction:
# a model whose cost nobody stated is not chosen *for* its cost.
_UNPRICED = float("inf")


# Where an unmeasured model sorts in a speed-ranked pool: with the middle, not
# at the back.
#
# Every model starts unmeasured, and RAVIS only measures a model by routing to
# it. Sorting unmeasured last would close the loop: never chosen, never
# measured, never chosen. Neutral lets an untimed candidate still win on the
# terms below — the pool's declared preference, its size tier, reach — and get
# its first samples, after which it ranks on what it actually did.
_UNMEASURED_MS = 1_000.0


def _speed_rank(
    model: str, observed: Mapping[str, float], bucket_ms: float = 0.0
) -> float:
    """Median time-to-first-token, for models with enough samples to mean it.

    `Observations.ttft_for_ranking` has already dropped anything below the
    sample floor, so a model missing here is one RAVIS has not timed enough —
    not one that was slow.

    `bucket_ms` widens the comparison so models within a window count as equally
    fast and a later term decides between them. It is how a pool uses speed
    *and* cost without weighting one against the other: 380 ms and 420 ms are
    not a difference anybody would trade money for, and treating that ordering
    as meaningful lets a rounding error outrank a published price.
    """
    measured = observed.get(model, _UNMEASURED_MS)
    if bucket_ms <= 0:
        return measured
    return math.ceil(measured / bucket_ms) * bucket_ms


def _price_rank(known: ModelCapabilities | None) -> float:
    """What a million tokens costs, or infinity when nobody published it."""
    if known is None or known.price_per_million is None:
        return _UNPRICED
    return known.price_per_million


def _selection_reason(
    pool: VirtualModelPool,
    eligible: list[str],
    residency: ResidencySnapshot,
    memory: MemoryReading,
    expected_session_requests: int | None = None,
    short_session: bool = False,
    budget_leans_cheap: bool = False,
    reasoning_note: str = "",
    remote: frozenset[str] = frozenset(),
    probe_note: str = "",
    locality_note: str = "",
) -> str:
    """Say honestly why the winner won.

    Which, at M5, is usually "it sorted first". Saying so is the point: §9.7
    requires an explanation to separate facts from unknowns, and claiming a
    quality judgement RAVIS has no evidence for would be the opaque magic §9.4
    forbids. This sentence changes when SIRVIS evidence lands at M13.
    """
    selected = eligible[0]
    matched = next((fragment for fragment in pool.prefer if fragment in selected), "")
    basis = (
        f"matches the pool's declared preference '{matched}'"
        if matched
        else "first eligible candidate in stable order"
    )
    parts = [basis]
    # The privacy lean, then the probe, straight after the basis and in the
    # order the key consults them: either can put a less-preferred model first,
    # and the reader should meet that reason before the lines about residency and
    # memory that follow. It matters most for the lean, whose typical win — a
    # cold local model over a hosted one, for a client whose sessions are one
    # request long — reads as §12.2 misfiring unless something ahead of that
    # line says what outranked it.
    parts.append(locality_note)
    parts.append(probe_note)

    # §12.2's tradeoff, said out loud when it changed anything. §9.7 wants an
    # explanation that separates facts from estimates, and "we declined to load
    # a better model" is precisely the kind of decision that looks like a bug
    # from outside if nobody says it was a decision.
    if short_session and expected_session_requests is not None:
        parts.append(
            f"this application's sessions run about {expected_session_requests} request(s), "
            f"so a one-time model load would not amortise and a resident model was "
            f"preferred over one that must be loaded (§12.2)"
        )

    # **A hosted model has no residency, and saying it does is a false cost.**
    # This read `residency.known` alone, so a cloud model — `UNKNOWN` by
    # definition, because a provider's API has no notion of being loaded —
    # produced "not currently loaded; using it will cost a load" about a
    # network call. `_reach_rank` had already learned this and says so at
    # length: "a remote model is not a cold one, and treating them alike was
    # the switchboard's central error". The ranking was fixed and the sentence
    # explaining it was not, which is how the two came to contradict each other
    # a few hundred lines apart.
    state = residency.state_of(selected)
    if selected in remote:
        parts.append("hosted, so there is no load to pay — one network round trip")
    elif state in (Residency.HOT, Residency.WARM):
        parts.append(f"already loaded ({state.value}), so no load is required")
    elif residency.known:
        parts.append(f"not currently loaded ({state.value}); using it will cost a load")

    if memory.under_pressure and memory.free_fraction is not None:
        parts.append(
            f"memory is tight ({memory.free_fraction:.0%} free), so already-loaded "
            "models were preferred over the pool's usual ordering"
        )

    # Before the size note, in the order the two tiebreaks are consulted. They
    # are usually both silent, and when both speak the reader should see the
    # measured one first — size only broke what the measurement left tied.
    parts.append(reasoning_note)
    parts.append(_size_note(pool, eligible, residency, memory))
    others = len(eligible) - 1
    tail = f"; {others} other eligible candidate(s) ranked lower" if others else ""
    # **Eligibility and order are different questions**, and this sentence used
    # to answer only one of them — it said "no benchmark evidence is available
    # yet" unconditionally, which stopped being true at M13 and then appeared
    # inside decisions that were only possible *because* evidence existed. What
    # is still true is narrower: evidence admits and excludes, and nothing here
    # ranks one admitted build above another on quality.
    #
    # **Then the identical mistake was made one milestone later**, in the same
    # sentence: "No cost data is available yet" outlived M15 by a whole
    # milestone. Cost now both excludes — a pool with a per-million ceiling
    # drops anything above it before ranking — and ranks, when the pool is a
    # cheap one or a budget band leans that way. So it is said conditionally,
    # from the two facts actually in scope, rather than asserted.
    cost = _cost_note(pool, budget_leans_cheap)
    # **And a third time, in the sentence that documents the first two.** This
    # said "evidence decides eligibility rather than order" unconditionally,
    # which M16's reasoning tiebreak makes false: a measured reasoning share now
    # orders candidates when the request caps its output. The claim worth
    # keeping is the narrower one that was always the point — nothing ranks one
    # admitted build above another on *quality* — so that half is stated
    # unconditionally and the half that changed is stated from what happened.
    orders = "and it ordered them here" if reasoning_note else "though it did not order these"
    return (
        f"{'. '.join(part for part in parts if part)}. "
        f"Evidence admits and excludes builds, {orders}; where it does order, it is on "
        f"what fits the request rather than on quality, and nothing ranks one admitted "
        f"build above another on how good it is. {cost}, and health is used to exclude "
        f"rather than to rank{tail}"
    )


def _cost_rank(
    model: str,
    candidates: Mapping[str, ModelCapabilities],
    remote: frozenset[str],
) -> tuple[int, float, str]:
    """The tiebreak between equals: cheaper to run first, in the currency that applies.

    **Parameter count is a cost only where the parameters are yours.** For a
    model on this machine, smaller means less memory and a shorter load, which
    is what `size_rank` has always meant. For a hosted one it means nothing: the
    bill is per token, a larger model is routinely cheaper than a smaller one,
    and a 7B beat a frontier build on a tiebreak that was measuring the wrong
    thing — which is how a greeting came back answered by a 7B.

    So hosted candidates are ordered by published price, which is their actual
    cost to run. An unpriced one sorts after every priced one rather than ahead
    of them: `None` means nobody published a figure, not that it is free, which
    is the same rule the cheap pool's ceiling already applies.

    Local and hosted rarely meet here — reach is ranked in `lead`, well above
    this — so the two currencies are compared within their own kind almost
    always, and the model name keeps the order total either way.
    """
    if model in remote:
        published = candidates.get(model)
        price = published.price_per_million if published is not None else None
        return (0, price, model) if price is not None else (1, 0.0, model)
    return size_rank(model)


def _cost_note(pool: VirtualModelPool, budget_leans_cheap: bool) -> str:
    """What published prices did to this decision, if anything.

    Three different sentences, because they send a reader to three different
    places: a ceiling that removed candidates, an ordering that moved them, or
    prices recorded and not consulted.
    """
    ranks = pool.prefer_cheap or budget_leans_cheap
    if pool.max_price_per_million is not None:
        ordered = " and ranked cheapest-first among the rest" if ranks else ""
        return (
            f"Published prices excluded anything above this pool's "
            f"${pool.max_price_per_million:g} per-million ceiling{ordered}"
        )
    if ranks:
        why = "this pool prefers cheap" if pool.prefer_cheap else "a budget band leans cheaper"
        return f"Published prices ordered the candidates because {why}"
    return "Published prices are recorded per call but did not order this pool"


def _size_note(
    pool: VirtualModelPool,
    eligible: list[str],
    residency: ResidencySnapshot,
    memory: MemoryReading,
) -> str:
    """Say so when size, rather than anything meaningful, broke the tie.

    §9.7 requires an explanation to separate facts from unknowns, and "it is
    smaller" is a weak reason that must not be allowed to read as a strong one.
    Naming it is also what stops the tiebreak becoming invisible policy: a
    reader who disagrees with it can see it happening.

    Returns an empty string when nothing was tied, which is the common case.
    """
    del memory  # Pressure changes the ordering, not whether size was the tiebreak.
    selected = eligible[0]
    peers = [
        model
        for model in eligible[1:]
        if pool.preference_rank(model) == pool.preference_rank(selected)
        and residency_rank(residency.state_of(model))
        == residency_rank(residency.state_of(selected))
    ]
    if not peers:
        return ""
    scale = parameter_scale(selected)
    measure = f"smallest at {scale:g}B" if scale is not None else "first in stable order"
    return (
        f"{len(peers)} other candidate(s) matched this pool exactly as well, and it was "
        f"chosen as the {measure} — a tiebreak on cost to run, not on quality"
    )
