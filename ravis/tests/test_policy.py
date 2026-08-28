"""M16's policy engine, and the three things the milestone is allowed to claim.

RAVIS.md M16's exit criteria, verbatim: *each hard constraint provably excludes
a top-ranked candidate; a declared background call never selects a paid provider
under the default profile; a pool carries a revision a consumer can pin.*

The first is the one worth being careful about. "Provably excludes a top-ranked
candidate" is a stronger claim than "excludes something", and it is stronger for
a reason: a constraint that only ever removes candidates nobody would have
picked is indistinguishable from a constraint that does nothing. So each of
these arranges for the forbidden model to be the one that *wins* without the
policy, and then shows it losing.
"""

from __future__ import annotations

import json

import pytest

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
)
from ravis.core.pools import DEFAULT_POOLS, POOLS_BY_ID
from ravis.policy import (
    ApplicationPolicies,
    PolicyConfigurationError,
    PrivacyLevel,
    RoutingPolicy,
    background_marked,
    effective_policy,
    load_policies,
    policy_exclusions,
    policy_refusals,
)
from ravis.routing.engine import RoutingEngine

# A cheap local model and an expensive hosted one. The hosted model is better on
# every axis a pool ranks by, so it wins `ravis/auto` outright — which is what
# makes it a fair test of a constraint: policy has to beat a preference, not
# merely agree with one.
LOCAL = "qwen2.5-7b-instruct"
HOSTED = "gpt-4o"


def _catalogue() -> dict[str, ModelCapabilities]:
    advertised = {
        capability: CapabilityClaim(
            capability=capability,
            state=CapabilityState.SUPPORTED,
            provenance=Provenance.ADVERTISED,
        )
        for capability in (Capability.TOOLS, Capability.STREAMING)
    }
    return {
        LOCAL: ModelCapabilities(
            model_id=LOCAL, claims=dict(advertised), context_window=32_000,
            price_per_million=0.0,
        ),
        HOSTED: ModelCapabilities(
            model_id=HOSTED, claims=dict(advertised), context_window=128_000,
            price_per_million=5.0,
        ),
    }


def _provider(model: str) -> str:
    return "openai" if model == HOSTED else "lmstudio"


def _select(policy: RoutingPolicy, pool: str = "ravis/auto"):  # noqa: ANN202
    candidates = _catalogue()
    remote = frozenset({HOSTED})
    return RoutingEngine().select(
        pool,
        candidates,
        remote_models=remote,
        policy=policy,
        policy_refusals=policy_refusals(
            policy, candidates, addressed=pool, provider_of=_provider, remote=remote
        ),
    )


def test_the_hosted_model_wins_when_no_policy_applies() -> None:
    """The control. Every exclusion test below is worthless without it.

    If the hosted model did not win here, a later assertion that policy excluded
    it would prove nothing — the pool might simply never have wanted it.
    """
    decision = _select(RoutingPolicy())

    assert decision.selected == HOSTED


@pytest.mark.parametrize(
    ("policy", "expected_fragment"),
    [
        (RoutingPolicy(privacy=PrivacyLevel.LOCAL_ONLY), "is not this machine"),
        (
            RoutingPolicy(privacy=PrivacyLevel.TRUSTED_PROVIDERS,
                          trusted_providers=frozenset({"lmstudio"})),
            "not a trusted provider",
        ),
        (RoutingPolicy(allowed_providers=frozenset({"lmstudio"})), "allowed providers"),
        (RoutingPolicy(denied_providers=frozenset({"openai"})), "is denied"),
        (RoutingPolicy(excluded_models=("gpt-*",)), "excluded by policy pattern"),
        (RoutingPolicy(background=True), "background call"),
    ],
)
def test_each_hard_constraint_excludes_the_top_ranked_candidate(
    policy: RoutingPolicy, expected_fragment: str
) -> None:
    """M16's first exit criterion, once per constraint.

    The hosted model wins this pool unaided — `test_the_hosted_model_wins...`
    above pins that — so each of these demonstrates a constraint overturning a
    selection rather than agreeing with one that had already been made.
    """
    decision = _select(policy)

    assert decision.selected == LOCAL, "policy did not displace the winner"
    refused = {candidate.model: candidate.reasons for candidate in decision.excluded}
    assert HOSTED in refused
    assert any(expected_fragment in reason for reason in refused[HOSTED]), refused[HOSTED]


def test_a_background_call_never_selects_a_paid_provider() -> None:
    """M16's second exit criterion, stated in the milestone's own words.

    Checked against the *default* profile — no allow-list, no privacy level,
    nothing configured — because that is the condition §9.6.1 attaches to the
    gate, and a guarantee that needs configuration to hold is not the guarantee.
    """
    decision = _select(RoutingPolicy(background=True))

    assert decision.selected == LOCAL
    assert _catalogue()[decision.selected].price_per_million == 0.0


def test_an_unpriced_hosted_model_counts_as_paid() -> None:
    """The half of the gate that is easy to get wrong and expensive to miss.

    OpenAI, Google and Anthropic publish no pricing in their catalogues, so
    `price_per_million` is `None` for exactly the providers a background call
    most needs to avoid. Reading absence as free would leave §9.6.1's gate
    holding only for providers that happened to publish a number.
    """
    unpriced = {
        LOCAL: _catalogue()[LOCAL],
        HOSTED: ModelCapabilities(model_id=HOSTED, price_per_million=None),
    }

    refused = policy_exclusions(
        RoutingPolicy(background=True), unpriced,
        provider_of=_provider, remote=frozenset({HOSTED}),
    )

    assert HOSTED in refused
    assert LOCAL not in refused


def test_a_background_marker_is_never_inferred() -> None:
    """§9.6.1: RAVIS never infers the class from the shape of a request.

    Only the boolean `true` counts. A string is a client bug, and coercing it
    means the day someone sends `"false"` their real work goes to a cheap model.
    """
    assert background_marked({"background": True}) is True
    assert background_marked({"background": "true"}) is False
    assert background_marked({"background": 1}) is False
    assert background_marked({}) is False


def test_policy_applies_to_a_directly_addressed_model() -> None:
    """A direct address bypasses selection. It must not bypass a boundary.

    `_direct`'s reason string has claimed "policy and tracking still apply"
    since M5, and until now no policy was applied to it. A privacy level that a
    client steps around by naming a model is not a privacy level.
    """
    policy = RoutingPolicy(privacy=PrivacyLevel.LOCAL_ONLY)
    candidates = _catalogue()
    addressed = "ravis/openai/gpt-4o"

    decision = RoutingEngine().select(
        addressed,
        candidates,
        remote_models=frozenset({HOSTED}),
        foreign_providers=frozenset({"openai"}),
        policy=policy,
        policy_refusals=policy_refusals(
            policy, candidates, addressed=addressed,
            provider_of=lambda _: "openai", remote=frozenset({HOSTED}),
        ),
    )

    assert decision.selected is None, "a direct address must not escape LOCAL_ONLY"
    assert "policy" in decision.reason


def test_a_no_route_is_never_quietly_substituted() -> None:
    """§9.2: a constraint that cannot be satisfied returns a structured no-route.

    The failure this rules out is worse than a refusal — routing *around* the
    constraint to something that fits would answer a LOCAL_ONLY request with a
    hosted model and report success.
    """
    only_hosted = {HOSTED: _catalogue()[HOSTED]}
    policy = RoutingPolicy(privacy=PrivacyLevel.LOCAL_ONLY)

    decision = RoutingEngine().select(
        "ravis/auto",
        only_hosted,
        remote_models=frozenset({HOSTED}),
        policy=policy,
        policy_refusals=policy_refusals(
            policy, only_hosted, addressed="ravis/auto",
            provider_of=_provider, remote=frozenset({HOSTED}),
        ),
    )

    assert decision.selected is None
    assert decision.routed is False


def test_local_preferred_ranks_without_excluding() -> None:
    """The one rung of the ladder that is a preference, and stays one.

    It has to change the answer — or it is not a preference either — while
    leaving the hosted model eligible, which is what separates it from
    `LOCAL_ONLY`.
    """
    decision = _select(RoutingPolicy(privacy=PrivacyLevel.LOCAL_PREFERRED))

    assert decision.selected == LOCAL
    excluded = {candidate.model for candidate in decision.excluded}
    assert HOSTED not in excluded, "LOCAL_PREFERRED must not exclude anything"


def test_an_unconfigured_deployment_routes_exactly_as_before() -> None:
    """M16 must not be a silent behaviour change for anyone who did not ask.

    The empty policy is the default, and the default has to be inert.
    """
    assert _select(RoutingPolicy()).selected == _select(ApplicationPolicies().default).selected


class TestPoolRevisions:
    """M16's third exit criterion: a pool carries a revision a consumer can pin."""

    def test_every_pool_carries_a_version_and_a_revision(self) -> None:
        for pool in DEFAULT_POOLS:
            assert pool.version
            assert pool.revision

    def test_a_revision_is_stable_across_reads(self) -> None:
        """A pin is worthless if the value moves on its own."""
        pool = POOLS_BY_ID["ravis/auto"]

        assert pool.revision == pool.revision

    def test_revisions_distinguish_pools(self) -> None:
        """§5.4: `clarvis-chat` and `clarvis-agent` revisions are independent."""
        revisions = {pool.pool_id: pool.revision for pool in DEFAULT_POOLS}

        assert revisions["ravis/clarvis-chat"] != revisions["ravis/clarvis-agent"]
        assert len(set(revisions.values())) == len(revisions), "a revision must identify one pool"

    def test_behaviour_moves_the_revision_and_wording_does_not(self) -> None:
        """What a pin is *for*: it tracks selection, not prose.

        A consumer pinned to a revision wants to hear about a pool that starts
        choosing differently. Invalidating their pin because somebody fixed a
        typo in a description trains them to stop pinning.
        """
        pool = POOLS_BY_ID["ravis/auto"]
        import dataclasses

        reworded = dataclasses.replace(pool, description="Reworded, same behaviour.")
        rebehaved = dataclasses.replace(pool, prefer_cheap=not pool.prefer_cheap)

        assert reworded.revision == pool.revision
        assert rebehaved.revision != pool.revision


class TestPolicyConfiguration:
    """A policy nobody can set is the unenforced field this milestone removed."""

    def test_an_absent_file_is_the_empty_policy(self, tmp_path) -> None:  # noqa: ANN001
        policies = load_policies(tmp_path / "nothing.json")

        assert policies.for_application("anything") == RoutingPolicy()

    def test_a_configured_application_gets_its_policy(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "policies.json"
        path.write_text(json.dumps({
            "applications": {"clarvis": {"privacy": "LOCAL_ONLY",
                                         "denied_providers": ["openai"]}}
        }))

        policies = load_policies(path)

        assert policies.for_application("clarvis").privacy is PrivacyLevel.LOCAL_ONLY
        assert policies.for_application("other") == RoutingPolicy()

    def test_a_malformed_policy_file_fails_closed(self, tmp_path) -> None:  # noqa: ANN001
        """The opposite of `providers.json`, and deliberately so.

        A corrupted provider file should leave providers working. A corrupted
        policy file that reads as "nothing is restricted" turns LOCAL_ONLY off
        without telling anyone — the exact failure §14 rule 14 exists to stop.
        """
        path = tmp_path / "policies.json"
        path.write_text("{ this is not json")

        with pytest.raises(PolicyConfigurationError):
            load_policies(path)

    def test_an_unknown_privacy_level_is_refused_not_ignored(self, tmp_path) -> None:  # noqa: ANN001
        """A typo must not read as "no privacy level"."""
        path = tmp_path / "policies.json"
        path.write_text(json.dumps({"default": {"privacy": "LOCAL-ONLY"}}))

        with pytest.raises(PolicyConfigurationError, match="unknown privacy"):
            load_policies(path)

    def test_an_empty_allow_list_permits_nothing(self, tmp_path) -> None:  # noqa: ANN001
        """Absent and empty are different answers, and both are configurable."""
        path = tmp_path / "policies.json"
        path.write_text(json.dumps({"default": {"allowed_providers": []}}))

        policy = load_policies(path).default

        assert policy.allowed_providers == frozenset()
        refused = policy_exclusions(
            policy, _catalogue(), provider_of=_provider, remote=frozenset({HOSTED})
        )
        assert set(refused) == {LOCAL, HOSTED}, "an empty allow-list permits no provider"


def test_an_unhonoured_background_marker_is_stated_not_swallowed() -> None:
    """Found live, and it is an explanation bug rather than a security one.

    An anonymous caller marked a request as background, was routed to a paid
    provider — exactly what §9.6.1 requires, since the marker is honoured only
    from an authenticated identity — and the route explanation read
    `requirements: none`. The routing was right and the account of it was
    silent about the single thing the caller had asked for.
    """
    ignored = effective_policy(
        RoutingPolicy(),
        metadata={"background": True},
        may_declare_background=False,
        ceiling=PrivacyLevel.NORMAL,
    )

    assert ignored.background is False, "the marker must not be honoured"
    assert ignored.background_declined is True
    assert any("ignored" in line for line in ignored.describe()), ignored.describe()


def test_an_honoured_marker_does_not_also_report_itself_ignored() -> None:
    """The two states are exclusive, and a line saying both would be worse than either."""
    honoured = effective_policy(
        RoutingPolicy(),
        metadata={"background": True},
        may_declare_background=True,
        ceiling=PrivacyLevel.NORMAL,
    )

    assert honoured.background is True
    assert honoured.background_declined is False
    assert not any("ignored" in line for line in honoured.describe())
