"""M15 — the recommendation engine (§14.3), and the number it refuses to hide.

    SIRVIS recommends a Clarvis chat + agent pair; exclusions and uncertainty
    are reproducible.

§14.3 asks for a weighted score and §12.2 forbids evidence keyed as
`model → score`. The line between them is that a score is a *function output*
carried with its weights, its inputs and its version — never a property of a
model — and most of these tests are about keeping that true when the evidence is
thin, which on this machine it is: the agent profile puts half its weight on
coding and reasoning, and no suite measures either.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from sirvis.app import create_app
from sirvis.config import Settings
from sirvis.core.recommendations import (
    ALGORITHM_VERSION,
    CLARVIS_AGENT,
    MODE_FAST,
    MODE_VERIFIED,
    axis_values,
    combination_score,
    eligible,
    recommend,
    utility,
)

GGUF = "lmstudio-community/granite-4.0-h-tiny"
MLX = "mlx-community/granite-4.0-h-tiny"


def evidence(
    *,
    runtime_key: str = GGUF,
    role: str = "clarvis-agent",
    passed: int = 24,
    total: int = 24,
    throughput: float = 60.3,
    ref: str = "sirvis://evidence/ev_a/res_1",
) -> dict[str, Any]:
    """One evidence record in the shape the store returns it."""
    return {
        "role": role,
        "runtime_key": runtime_key,
        "evidence_ref": ref,
        "metrics": {
            "tool_call_well_formed": {"passed": passed, "total": total,
                                      "phrasings": 8, "rate": passed / total},
            "generation_tokens_per_second": {"median": throughput, "unit": "tokens/second"},
        },
    }


CONTEXTS = {GGUF: 1048576, MLX: 131072}
CAPABLE = {f"{GGUF}:tool_use": "SUPPORTED", f"{MLX}:tool_use": "UNSUPPORTED"}


# ── The score, and what travels with it ──────────────────────────────────────


def test_a_score_never_travels_without_its_coverage() -> None:
    """§12.2's prohibition, kept while §14.3's score is computed.

    Half the agent profile is coding and reasoning and nothing measures either,
    so a score here rests on 45% of the profile. A number presented without that
    fraction would be a confident average of three things and three absences.
    """
    values = axis_values([evidence()], CONTEXTS)

    found = utility(CLARVIS_AGENT, GGUF, values)

    assert found.coverage == pytest.approx(0.45)
    assert set(found.missing) == {"coding", "reasoning", "memory"}
    assert found.score > 0


def test_the_missing_axes_say_why_they_are_missing() -> None:
    """A caller should learn which milestone would close the gap."""
    values = axis_values([evidence()], CONTEXTS)

    missing = utility(CLARVIS_AGENT, GGUF, values).missing

    assert "M18" in missing["coding"]
    assert "installed sizes" in missing["memory"]


def test_the_score_divides_by_covered_weight_not_the_profile_total() -> None:
    """Dividing by the total would punish a build for suites that do not exist.

    That is a fact about SIRVIS, not about the model, and it would make every
    candidate look mediocre in exactly the same way — which is no information.
    """
    values = axis_values([evidence()], CONTEXTS)

    found = utility(CLARVIS_AGENT, GGUF, values)

    # Every measurable axis is at its maximum, so a correctly normalised score
    # is 1.0 despite only 45% of the profile being covered.
    assert found.score == pytest.approx(1.0)


def test_supporting_evidence_travels_with_the_recommendation() -> None:
    """§14.3 lists supporting benchmark IDs among the required output."""
    result = recommend([evidence()], CONTEXTS, CAPABLE, roles=["clarvis-agent"])

    assert result.roles["clarvis-agent"].evidence_ids  # type: ignore[union-attr]
    assert result.as_dict()["roles"]["clarvis-agent"]["supporting_evidence"]


# ── Eligibility is separate from ranking ─────────────────────────────────────


def test_a_required_capability_excludes_rather_than_scores_low() -> None:
    """A build that cannot call tools is not a low-scoring agent — it is not one.

    Weighing it would let a fast enough model out-score its own
    disqualification, which is the failure the separation prevents.
    """
    allowed, reasons = eligible(
        CLARVIS_AGENT, MLX, axis_values([evidence(runtime_key=MLX)], CONTEXTS), CAPABLE
    )

    assert allowed is False
    assert reasons == ("tool_use is UNSUPPORTED",)


def test_an_unknown_context_window_fails_closed() -> None:
    """§9.1's rule, applied where a recommendation could paper over it."""
    allowed, reasons = eligible(CLARVIS_AGENT, GGUF, axis_values([evidence()], {}), CAPABLE)

    assert allowed is False
    assert "fails closed" in reasons[0]


def test_an_excluded_candidate_does_not_set_the_scale() -> None:
    """A build that cannot do the job must not drag down one that can.

    Not academic: the MLX packaging is 83% faster than the build that qualifies
    and fails every realistic tool call. While it set the normalisation scale,
    the admitted build scored 0.55 on throughput for losing to something
    ineligible.
    """
    records = [evidence(), evidence(runtime_key=MLX, throughput=110.5, passed=3)]

    result = recommend(records, CONTEXTS, CAPABLE, roles=["clarvis-agent"])

    found = result.roles["clarvis-agent"]
    assert found is not None and found.runtime_key == GGUF
    assert found.axes["throughput"] == pytest.approx(1.0)
    assert result.excluded["clarvis-agent"][0].runtime_key == MLX


# ── Combinations are combinations (§14.3) ────────────────────────────────────


def test_the_joint_term_is_zero_until_a_pair_has_been_measured() -> None:
    """§10.1: two models that each fit do not prove they work together.

    Zero rather than an optimistic default — a pair nobody ran together has no
    joint evidence, and inventing one asserts exactly what §10.1 denies.
    """
    values = axis_values([evidence()], CONTEXTS)
    one = utility(CLARVIS_AGENT, GGUF, values)

    assert combination_score(one, one, penalties={}) == pytest.approx(one.score * 2)


def test_penalties_subtract_from_the_combination() -> None:
    """The formula §14.3 gives, with each penalty actually applied."""
    values = axis_values([evidence()], CONTEXTS)
    one = utility(CLARVIS_AGENT, GGUF, values)

    scored = combination_score(
        one, one, penalties={"contention": 0.2, "swap": 0.1, "memory": 0.05}
    )

    assert scored == pytest.approx(one.score * 2 - 0.35)


def test_no_combination_score_when_a_role_has_no_candidate() -> None:
    """A pair needs two. Reporting a number for one would be a pair score for a
    single model, which is the shape §14.3 forbids picking independently."""
    result = recommend([evidence()], CONTEXTS, CAPABLE,
                       roles=["clarvis-chat", "clarvis-agent"])

    assert result.combination_score is None
    assert "not every role has a candidate" in " ".join(result.uncertainty)


# ── Uncertainty and exclusions are reproducible (M15's acceptance) ───────────


def test_the_same_inputs_produce_the_same_answer() -> None:
    """M15's acceptance says reproducible, and an opinion that moved between two
    identical calls is one nobody could check."""
    records = [evidence(), evidence(runtime_key=MLX, throughput=110.5, passed=3)]

    first = recommend(records, CONTEXTS, CAPABLE, roles=["clarvis-agent"])
    second = recommend(list(reversed(records)), CONTEXTS, CAPABLE, roles=["clarvis-agent"])

    assert first.as_dict() == second.as_dict()


def test_a_role_with_no_evidence_is_named_rather_than_omitted() -> None:
    """"Nothing was recommended" and "nothing has been measured for chat" call
    for different actions, and only the second names the fix."""
    result = recommend([evidence()], CONTEXTS, CAPABLE,
                       roles=["clarvis-chat", "clarvis-agent"])

    assert result.roles["clarvis-chat"] is None
    assert "no eligible candidate for clarvis-chat" in result.uncertainty


def test_evidence_is_never_borrowed_across_roles() -> None:
    """A throughput measured under the agent suite is about the agent workload.

    Reusing it for chat would be a measurement answering a question nobody asked
    of it — the same conflation §12.2 keys evidence by role to prevent.
    """
    result = recommend([evidence(role="clarvis-agent")], CONTEXTS, CAPABLE,
                       roles=["clarvis-chat"])

    assert result.roles["clarvis-chat"] is None


# ── Modes: fast starts nothing (§14.3) ───────────────────────────────────────


def test_fast_mode_proposes_no_work() -> None:
    """§14.3: `fast` uses existing evidence only and starts no new benchmark.

    It proposes none either — a suggestion to spend twenty minutes of somebody's
    machine is not what they asked for when they asked for the fast answer.
    """
    result = recommend([evidence()], CONTEXTS, CAPABLE,
                       roles=["clarvis-agent"], mode=MODE_FAST)

    assert result.proposed_benchmarks == ()


def test_verified_mode_proposes_the_work_that_would_close_the_gaps() -> None:
    """"May propose or run" — and proposing is all this does.

    Nothing here starts a benchmark. §14.3 is explicit that expensive work never
    begins unless it was asked for, and on this machine expensive means
    gigabytes of memory and a model resident for the duration.
    """
    result = recommend([evidence()], CONTEXTS, CAPABLE,
                       roles=["clarvis-chat", "clarvis-agent"], mode=MODE_VERIFIED)

    proposed = " ".join(result.proposed_benchmarks)
    assert "--clarvis-role clarvis-chat" in proposed


def test_the_algorithm_version_is_on_every_recommendation() -> None:
    """§14.3 requires it, for §12.1's reason: a number from v1 is not comparable
    with one from v2, and a stored opinion that cannot say which produced it has
    no provenance."""
    result = recommend([evidence()], CONTEXTS, CAPABLE, roles=["clarvis-agent"])

    assert result.as_dict()["algorithm_version"] == ALGORITHM_VERSION


def test_a_recommendation_expires() -> None:
    """§14.3 asks for an expiry policy. Evidence keeps; an opinion about what to
    run today does not."""
    result = recommend([evidence()], CONTEXTS, CAPABLE, roles=["clarvis-agent"])

    assert result.as_dict()["expires_after_seconds"] > 0


def test_the_fit_tier_is_unknown_and_says_it_is_an_estimate() -> None:
    """§14.2's tiers exist and their input does not — nothing records an
    installed size — so the honest tier is UNKNOWN and the basis is `estimate`.
    """
    payload = recommend([evidence()], CONTEXTS, CAPABLE, roles=["clarvis-agent"]).as_dict()

    assert payload["fit"] == {"tier": "UNKNOWN", "detail": "", "basis": "estimate"}


def test_every_admitted_candidate_is_ranked_not_only_the_winner() -> None:
    """§14.3 asks for ranked candidates, plural.

    Reporting only the top of each role made a build that was considered and
    placed second vanish entirely — absent from the ranking and absent from the
    exclusions, as though nobody had looked at it. Found by running the chat
    role against two builds and noticing one of them was nowhere in the answer.
    """
    fast = evidence(runtime_key="fast-build", role="clarvis-agent", throughput=90.0)
    slow = evidence(runtime_key="slow-build", role="clarvis-agent", throughput=30.0)
    contexts = {"fast-build": 65536, "slow-build": 65536}
    capable = {"fast-build:tool_use": "SUPPORTED", "slow-build:tool_use": "SUPPORTED"}

    result = recommend([fast, slow], contexts, capable, roles=["clarvis-agent"])

    ranked = result.ranked["clarvis-agent"]
    assert [found.runtime_key for found in ranked] == ["fast-build", "slow-build"]
    assert ranked[0].score > ranked[1].score
    assert result.as_dict()["ranked"]["clarvis-agent"][1]["runtime_key"] == "slow-build"


# ── The joint term, once a pair has actually been run ────────────────────────


def a_matrix(*, worst: float = 34.4, chat: float | None = None,
             complete: bool = True) -> dict[str, Any]:
    """An interaction matrix in the shape M10 writes it.

    Both rows are settable because the penalty is the worst across members, and
    a fixture that pinned one row would quietly floor every case at that value —
    which it did, until a spread assertion came back 22.1 instead of 21.2.
    """
    return {
        "runtime_set": "clarvis-recommended", "revision": 1, "complete": complete,
        "members": {"clarvis-chat": "chat-build", "clarvis-agent": "agent-build"},
        "rows": {
            "clarvis-chat": {"degradation_percent": {
                "concurrent": {"tokens_per_second": 22.1 if chat is None else chat}}},
            "clarvis-agent": {"degradation_percent": {"concurrent": {"tokens_per_second": worst}}},
        },
    }


def a_pair() -> list[dict[str, Any]]:
    return [
        evidence(runtime_key="chat-build", role="clarvis-chat"),
        evidence(runtime_key="agent-build", role="clarvis-agent"),
    ]


PAIR_CONTEXTS = {"chat-build": 65536, "agent-build": 65536}
PAIR_CAPABLE = {"chat-build:tool_use": "SUPPORTED", "agent-build:tool_use": "SUPPORTED"}


def test_two_runs_of_one_pair_report_their_spread() -> None:
    """A single number to four decimals from one run is precision this corpus
    has not earned.

    The same pair measured twice gave the agent role 34.4% and then 21.2% —
    thirteen points, from its *alone* baseline drifting 14.8% while the chat
    role held steady. Degradation is computed against alone, so an unstable
    control moves the answer without contention changing at all.
    """
    result = recommend(a_pair(), PAIR_CONTEXTS, PAIR_CAPABLE,
                       matrices=[a_matrix(worst=34.4, chat=22.1),
                                 a_matrix(worst=21.2, chat=21.4)])

    note = " ".join(result.uncertainty)
    assert "median of 2 runs" in note
    assert "21.4–34.4%" in note
    # The median, not the worst: a third run showed the high figure was the
    # outlier, and anchoring on it would penalise a pair for the one measurement
    # its other runs contradict.
    assert result.combination_score == pytest.approx(2.0 - 0.279)


def test_a_single_run_says_it_is_a_single_run() -> None:
    result = recommend(a_pair(), PAIR_CONTEXTS, PAIR_CAPABLE, matrices=[a_matrix()])

    assert "from a single run" in " ".join(result.uncertainty)


def test_a_measured_pair_penalises_the_combination() -> None:
    """§14.3 subtracts a contention penalty, and M10 is where it comes from.

    Derived from what was measured rather than predicted: the worst concurrent
    throughput loss across the members. A pair that gives up a third of its
    throughput when both generate is a third worse at the thing a pair is for.
    """
    result = recommend(a_pair(), PAIR_CONTEXTS, PAIR_CAPABLE, matrices=[a_matrix()])

    assert result.combination_score == pytest.approx(2.0 - 0.344)
    assert "measured together" in " ".join(result.uncertainty)


def test_the_median_ignores_a_single_outlying_run() -> None:
    """Three runs of one pair gave 34.4%, 21.2% and 21.3%.

    Two agree to a tenth of a point; the first is eighteen percent adrift on its
    alone baseline while its concurrent figure held steady. A worst-case penalty
    would anchor on the measurement the other two contradict.
    """
    result = recommend(a_pair(), PAIR_CONTEXTS, PAIR_CAPABLE, matrices=[
        a_matrix(worst=34.4, chat=22.1),
        a_matrix(worst=21.2, chat=21.4),
        a_matrix(worst=21.3, chat=21.5),
    ])

    assert result.combination_score == pytest.approx(2.0 - 0.215)


def test_an_unmeasured_pair_says_so_rather_than_scoring_zero_penalty() -> None:
    """§10.1: an unmeasured pair is unknown, not frictionless.

    The arithmetic is the same either way — no penalty — so the difference has
    to be said out loud or the two are indistinguishable in the output.
    """
    result = recommend(a_pair(), PAIR_CONTEXTS, PAIR_CAPABLE, matrices=[])

    assert result.combination_score == pytest.approx(2.0)
    assert "has not been measured together" in " ".join(result.uncertainty)


def test_a_matrix_for_a_different_pair_does_not_count() -> None:
    """§10.1 again: co-residency behaviour does not transfer between pairings.

    A near-miss is no match — the same build behaved differently in different
    pairings on this machine, which is the finding that makes a pair a subject.
    """
    other = a_matrix()
    other["members"] = {"clarvis-chat": "someone-else", "clarvis-agent": "agent-build"}

    result = recommend(a_pair(), PAIR_CONTEXTS, PAIR_CAPABLE, matrices=[other])

    assert "has not been measured together" in " ".join(result.uncertainty)


def test_an_incomplete_matrix_carries_no_penalty() -> None:
    """A run whose members never went co-resident measured no contention.

    §10's gate makes that a result rather than a failure, and the result is
    "we do not know", not "there is none".
    """
    result = recommend(a_pair(), PAIR_CONTEXTS, PAIR_CAPABLE,
                       matrices=[a_matrix(complete=False)])

    assert result.combination_score == pytest.approx(2.0)


# ── The endpoint's inputs: two were read, four were documented ───────────────


def an_api() -> TestClient:
    """A running SIRVIS with an empty results database.

    Empty is enough here: these tests are about which request bodies the
    endpoint accepts, which is decided before any evidence is read.
    """
    settings = Settings(
        database_path=":memory:",
        lmstudio_base_url="http://127.0.0.1:9",
        _env_file=None,  # type: ignore[call-arg]
    )
    return TestClient(create_app(settings))


def test_the_documented_default_request_is_accepted() -> None:
    """The body §14.3 prints, minus the constraints — and the one NERVIS sends."""
    response = an_api().post(
        "/api/v1/recommendations",
        json={"profile": "clarvis", "roles": ["clarvis-chat"], "mode": "fast"},
    )

    assert response.status_code == 200
    assert response.json()["profile"] == "clarvis"


def test_constraints_are_refused_rather_than_silently_dropped() -> None:
    """§14.3's own example body carries `{"avoid_swap": true}`.

    The handler never read it. Answering 200 to that request returns a
    recommendation that may swap, to a caller who asked for one that would not —
    and nothing in the response would say so. §4.1's "never a stub returning
    plausible data" is the same rule one layer up.
    """
    response = an_api().post(
        "/api/v1/recommendations",
        json={"roles": ["clarvis-chat"], "constraints": {"avoid_swap": True}},
    )

    # 422 rather than 400, and `UNSUPPORTED_PARAMETER` rather than
    # `INVALID_CONFIGURATION`: the request is coherent and documented, and §7.1
    # requires "cannot do that" to be distinguishable from "malformed".
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNSUPPORTED_PARAMETER"
    assert "constraints are not implemented" in response.text


def test_an_unknown_profile_is_refused() -> None:
    """One family is defined. Scoring with `clarvis` weights under another
    name would answer a question nobody asked."""
    response = an_api().post(
        "/api/v1/recommendations",
        json={"profile": "something-else", "roles": ["clarvis-chat"]},
    )

    assert response.status_code == 422
    assert "unknown profile" in response.text
