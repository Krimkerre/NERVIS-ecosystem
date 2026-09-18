"""Routing a stored decision again, against today — and calling nobody (M21's dry run).

**What this answers.** A route decision is a record of a judgement made with the
catalogue, prices, residency and health of one moment. Prices change, a model is
added or withdrawn, a pool's evidence arrives, a provider starts refusing. The
question worth asking afterwards is *would RAVIS still choose that?* — and until
decisions were stored there was nothing to ask it of.

**What it does not do.** No provider is contacted and no answer is generated. The
owner chose the dry run on 18 September 2026 over replaying the request for real,
and the two reasons stand on their own: a real replay costs money per call, and
it would need the prompt, which RAVIS deliberately does not keep — §9.7 and
runbook §9 keep content out of a route explanation, so there is nothing stored
here to send. That is a property of the store rather than a rule this module
follows: `engine.select` performs no I/O, so the only thing a replay *can* do is
decide.

**Faithful about the request, honest about the rest.** The constraints that
decided the original route are stored with it — which capabilities the request
required and why, how much context it estimated, what it capped its answer at,
whether it was a probe, and the policy in force. Those are replayed exactly. Two
things are not reproduced and are named in the answer rather than quietly
omitted: session affinity, because the conversation it belonged to has moved on,
and exploration, because spending a request to learn about a model is a decision
about a live request and not about a record of one.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from ravis.api.catalogue import (
    assemble,
    chosen_models,
    direct_providers,
    observed_ttft,
    provider_of,
    reasoning_shares,
    role_evidence,
)
from ravis.api.management.decisions import RecordedDecision
from ravis.core.capabilities import Capability
from ravis.cost import BudgetBand
from ravis.policy import (
    PrivacyLevel,
    RoutingPolicy,
    direct_owners,
    policy_refusals,
    resold_models,
)
from ravis.reliability.strain import strain_of
from ravis.routing.explain import RouteDecision
from ravis.routing.requirements import RequestRequirements

# Said in the answer rather than left for a reader to notice. Both are facts
# about a live request that a record of one cannot carry.
NOT_REPRODUCED = (
    "session affinity, because the conversation this belonged to has moved on",
    "exploration, which spends a live request to learn about a model",
)


def constraints_of(
    requirements: RequestRequirements, policy: RoutingPolicy
) -> dict[str, Any]:
    """What decided this route, in a shape it can be decided from again.

    Derived facts only — which capabilities were needed, how much context was
    estimated, what the identity's policy allowed. No message, no prompt, no
    credential: there is nothing here that was not already in the explanation,
    only in a form arithmetic can read.
    """
    return {
        "required": sorted(capability.value for capability in requirements.required),
        "reasons": {
            capability.value: reason for capability, reason in requirements.reasons.items()
        },
        "context_tokens": requirements.estimated_context_tokens,
        "output_budget": requirements.output_budget,
        "tool_probe": requirements.tool_probe,
        "policy": {
            "privacy": policy.privacy.value,
            # `None` and the empty set are different answers here, and the
            # difference is "every provider" against "no provider".
            "allowed_providers": (
                None if policy.allowed_providers is None else sorted(policy.allowed_providers)
            ),
            "denied_providers": sorted(policy.denied_providers),
            "excluded_models": list(policy.excluded_models),
            "trusted_providers": sorted(policy.trusted_providers),
            "background": policy.background,
            "budget_band": policy.budget_band.value,
            "budget_hard": policy.budget_hard,
            "background_declined": policy.background_declined,
        },
    }


def _requirements_from(stored: dict[str, Any]) -> RequestRequirements:
    """The stored constraints as the object the engine reads."""
    reasons = stored.get("reasons", {})
    return RequestRequirements(
        required={Capability(name) for name in stored.get("required", ())},
        reasons={Capability(name): reason for name, reason in reasons.items()},
        estimated_context_tokens=stored.get("context_tokens", 0),
        output_budget=stored.get("output_budget"),
        tool_probe=stored.get("tool_probe", False),
    )


def _policy_from(stored: dict[str, Any]) -> RoutingPolicy:
    """The stored policy as it was, defaults where a record predates a field."""
    allowed = stored.get("allowed_providers")
    return RoutingPolicy(
        privacy=PrivacyLevel(stored.get("privacy", PrivacyLevel.NORMAL.value)),
        allowed_providers=None if allowed is None else frozenset(allowed),
        denied_providers=frozenset(stored.get("denied_providers", ())),
        excluded_models=tuple(stored.get("excluded_models", ())),
        trusted_providers=frozenset(stored.get("trusted_providers", ())),
        background=stored.get("background", False),
        budget_band=BudgetBand(stored.get("budget_band", BudgetBand.NORMAL.value)),
        budget_hard=stored.get("budget_hard", False),
        background_declined=stored.get("background_declined", False),
    )


async def replay(request: Request, recorded: RecordedDecision) -> dict[str, Any]:
    """Route the stored request again against today's world, contacting nobody."""
    stored = recorded.constraints
    requirements = _requirements_from(stored)
    policy = _policy_from(stored.get("policy", {}))
    offered = await assemble(request)
    candidates, remote = offered.candidates, offered.remote
    health = request.app.state.health
    direct = await direct_providers(request)
    # `provider_of` reads the owner map off the request, exactly as the chat
    # path leaves it there — one resolution for both, which is the lesson the
    # audit of 9 September 2026 left behind.
    request.state.translated_owners = offered.translated_owners
    owner_of = provider_of(request)
    now: RouteDecision = request.app.state.routing_engine.select(
        recorded.decision.requested,
        candidates,
        residency=offered.residency,
        memory=request.app.state.memory,
        requirements=requirements,
        foreign_providers=frozenset(getattr(request.app.state, "translating", {})),
        remote_models=remote,
        policy=policy,
        policy_refusals=policy_refusals(
            policy,
            candidates,
            addressed=recorded.decision.requested,
            provider_of=owner_of,
            remote=remote,
        ),
        unavailable=health.unavailable(list(candidates), owner_of),
        resold=resold_models(candidates, owner_of, direct),
        direct_owners=direct_owners(candidates, owner_of, direct),
        strain=strain_of(list(candidates), owner_of, getattr(request.app.state, "load", None),
                         health),
        # Everything below is a fact about *today* rather than about the
        # original request, which is the whole question being asked — and each
        # of them changes the answer. Evidence admits and excludes candidates,
        # so a replay without it would report a pool as open that today's
        # router would find empty; an operator's narrowing of a pool is the
        # same kind of fact; and a speed-ranking pool orders on measurements
        # that have moved since.
        chosen=chosen_models(request, recorded.decision.requested),
        observed_ttft_ms=observed_ttft(request),
        reasoning_share=reasoning_shares(request, list(candidates)),
        role_evidence=role_evidence(request, list(candidates)),
    )
    then = recorded.decision
    return {
        "decision_id": recorded.decision_id,
        "then": recorded.as_dict(),
        "now": now.as_dict(),
        # The one thing a reader is actually asking. Both halves matter: a
        # decision that would be made again is as much of an answer as one that
        # would not, and a reader should not have to compare two blobs to learn
        # which they are looking at.
        "changed": then.selected != now.selected,
        "not_reproduced": list(NOT_REPRODUCED),
        # Said plainly, because "replay" means "send it again" everywhere else.
        "note": "nothing was sent to a provider; this is what the router would choose now",
    }
