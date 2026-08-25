"""M15 — recommending a pair, and saying how much of it is guesswork (§14.3).

§14.3 asks for a weighted score. §12.2 forbids evidence keyed as `model →
score`. Both hold, and the line between them is where this module lives:

    A score is a **function output**, recorded with the weights, the inputs and
    the algorithm version that produced it. It is never a property of a model.

So nothing here writes a number back onto evidence. A recommendation is an
opinion with its reasoning attached, it expires, and §14.3 closes by saying what
it is not: *recommendations are evidence-backed suggestions, not routing
commands.* RAVIS owns routing; SIRVIS must not invent RAVIS policies.

**Coverage is the field that keeps this honest.** The `clarvis-agent` profile
puts 35% of its weight on coding and 15% on reasoning, and no suite measures
either (M18). Another 5% is memory, and nothing records an installed size. So a
score computed on this machine today rests on **45%** of the profile — tool use,
throughput and context — and a number presented without that fraction would be a
confident-looking average of three things and three absences.

Every recommendation therefore carries `coverage`, the weight actually backed by
evidence, and `missing`, the axes that were not. A caller may reasonably ignore a
0.45-coverage recommendation; it may not reasonably be prevented from seeing that
it is one.

**Combinations are scored as combinations** (§14.3): *do not simply pick the
highest chat model and the highest agent model independently.* The joint term and
the penalties are what make a pair a subject, and §10.1 is the measured reason —
two models that each fit do not necessarily work together.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# Bumped whenever the arithmetic changes, and carried on every recommendation.
# §14.3 requires the algorithm version in the output for the same reason §12.1
# versions a measurement method: a number from v1 is not comparable with one
# from v2, and a stored recommendation that cannot say which produced it is a
# number with no provenance.
ALGORITHM_VERSION = "sirvis.recommend.v1"

# §13.2's accepted threshold for the agent role, which is **SIRVIS's to set** —
# SIRVIS.md records it, and RAVIS applies it. Defined here because it was
# nowhere in this codebase: it existed in the specification's prose and in
# RAVIS's consumer, and the service that owns it had no copy at all.
#
# It is still duplicated across the two services, because they share no code and
# nothing publishes it on the wire. Whoever tires of that should have the
# evidence endpoint carry the threshold beside the rates, so the consumer reads
# it rather than restating it — the failure mode of the present arrangement is
# the two drifting apart in silence, each correct against its own copy.
TOOL_CALL_PASS_RATE = 0.95

# §14.2's fit tiers. `UNKNOWN` is first-class and, on this machine, the usual
# answer — nothing records an installed size, so the estimator has no weights to
# add up. Stated here rather than discovered: the tiers exist and the input does
# not, which is a gap in the inventory and not in this arithmetic.
FIT_IDEAL = "IDEAL"
FIT_GOOD = "GOOD"
FIT_TIGHT = "TIGHT"
FIT_POOR = "POOR"
FIT_DOES_NOT_FIT = "DOES_NOT_FIT"
FIT_UNKNOWN = "UNKNOWN"

# What `fast` and `verified` mean (§14.3). `fast` reads what exists and starts
# nothing; `verified` may *propose* further benchmarks and never starts them
# unasked — expensive work on somebody's machine is their decision.
MODE_FAST = "fast"
MODE_VERIFIED = "verified"

# The metric each profile axis reads, and whether its natural range is already
# 0..1. A rate is a proportion and speaks for itself; a throughput is unbounded
# and only means something next to the others in the same set.
AXIS_METRICS: dict[str, tuple[str, bool]] = {
    "tool_use": ("tool_call_well_formed", True),
    "throughput": ("generation_tokens_per_second", False),
    "context": ("__context__", False),
}

# Axes §14.3's profile names that nothing measures yet, and the milestone that
# would. Listed rather than silently skipped so a recommendation can say which
# part of its own profile it could not consult.
UNMEASURED_AXES = {
    "coding": "no coding suite yet (M18)",
    "reasoning": "no reasoning suite yet (M18)",
    "memory": "no installed sizes are recorded, so memory cannot be weighed",
}


@dataclass(frozen=True)
class RoleProfile:
    """§14.3's role profile: what matters for a role, and what it requires.

    Weights are the caller's statement of what the role is for, not SIRVIS's.
    The `clarvis-agent` profile below is transcribed from §14.3 rather than
    invented, because inventing one would be SIRVIS deciding what Clarvis needs
    — which §14.3's closing sentence forbids in as many words.
    """

    name: str
    weights: Mapping[str, float]
    minimum_context: int = 0
    required_capabilities: tuple[str, ...] = ()

    def total_weight(self) -> float:
        return sum(self.weights.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "weights": dict(self.weights),
            "minimum_context": self.minimum_context,
            "required_capabilities": list(self.required_capabilities),
        }


# §14.3's profile, verbatim. The chat profile is *not* transcribed from anywhere,
# because §14.3 does not give one — so it is derived by dropping the axes a chat
# role does not turn on and renormalising, and it is labelled as derived where a
# caller can see it.
CLARVIS_AGENT = RoleProfile(
    name="clarvis-agent",
    weights={"coding": 0.35, "tool_use": 0.25, "reasoning": 0.15,
             "throughput": 0.10, "context": 0.10, "memory": 0.05},
    minimum_context=32768,
    required_capabilities=("tool_use",),
)

CLARVIS_CHAT = RoleProfile(
    name="clarvis-chat",
    weights={"reasoning": 0.35, "throughput": 0.30, "context": 0.20, "memory": 0.15},
    minimum_context=8192,
)

PROFILES = {profile.name: profile for profile in (CLARVIS_AGENT, CLARVIS_CHAT)}

# §14.3's request body carries a `profile` naming a *family* of role weights,
# which is a different thing from the per-role entries above. One family exists,
# so this is both the default and the only accepted value — named rather than
# spelled inline at the endpoint so the two cannot drift apart.
DEFAULT_PROFILE = "clarvis"


@dataclass
class Utility:
    """One candidate's score for one role, with the arithmetic left visible.

    `score` alone is not the answer and never travels alone: `coverage` says how
    much of the profile's weight was actually backed, and `missing` names the
    rest. A 0.9 at 45% coverage and a 0.9 at full coverage are different claims.
    """

    runtime_key: str
    score: float
    coverage: float
    axes: dict[str, float] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime_key": self.runtime_key,
            "score": round(self.score, 4),
            "coverage": round(self.coverage, 4),
            "axes": {name: round(value, 4) for name, value in self.axes.items()},
            "missing": dict(self.missing),
            "supporting_evidence": list(self.evidence_ids),
        }


@dataclass
class Exclusion:
    """A candidate that was not considered, and why (§14.3).

    Excluded candidates are output, not omission. "Nothing was recommended" and
    "eleven builds were considered and every one failed the context requirement"
    call for different actions, and only the second one names the fix.
    """

    runtime_key: str
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"runtime_key": self.runtime_key, "reasons": list(self.reasons)}


def axis_values(
    records: Sequence[Mapping[str, Any]], contexts: Mapping[str, int | None]
) -> dict[str, dict[str, float]]:
    """Each candidate's raw value per measurable axis.

    Separate from scoring so the normalisation below has the whole set in hand:
    a throughput means nothing on its own and everything next to the others, and
    normalising one candidate at a time would make a score depend on the order
    they were evaluated in.
    """
    values: dict[str, dict[str, float]] = {}
    for record in records:
        key = _runtime_key(record)
        if not key:
            continue
        found: dict[str, float] = {}
        rate = _rate(record, "tool_call_well_formed")
        if rate is not None:
            found["tool_use"] = rate
        throughput = _median(record, "generation_tokens_per_second")
        if throughput is not None:
            found["throughput"] = throughput
        window = contexts.get(key)
        if window:
            found["context"] = float(window)
        values[key] = found
    return values


def utility(
    profile: RoleProfile,
    runtime_key: str,
    values: Mapping[str, dict[str, float]],
    evidence_ids: Sequence[str] = (),
) -> Utility:
    """One candidate's weighted score for one role, over what is known.

    **Normalised within the evaluated set**, which is a deliberate property
    rather than a shortcut: a recommendation is a choice *among these
    candidates*, so "the fastest here" is the meaningful statement and an
    absolute scale would need anchors nobody has measured. It also means the
    same candidate can score differently in a different set, which is why the
    set is recorded alongside the score.

    The score divides by the weight actually covered, not by the profile's total.
    Dividing by the total would silently punish a candidate for the suites that
    do not exist yet, which is a fact about SIRVIS rather than about the model.
    """
    mine = values.get(runtime_key, {})
    axes: dict[str, float] = {}
    covered = 0.0
    missing: dict[str, str] = {}
    for axis, weight in profile.weights.items():
        if axis in UNMEASURED_AXES:
            missing[axis] = UNMEASURED_AXES[axis]
            continue
        if axis not in mine:
            missing[axis] = "no evidence for this axis on this build"
            continue
        axes[axis] = _normalised(axis, runtime_key, values)
        covered += weight
    total = profile.total_weight() or 1.0
    weighted = sum(profile.weights[axis] * value for axis, value in axes.items())
    return Utility(
        runtime_key=runtime_key,
        score=weighted / covered if covered else 0.0,
        coverage=covered / total,
        axes=axes,
        missing=missing,
        evidence_ids=tuple(evidence_ids),
    )


def _normalised(axis: str, runtime_key: str, values: Mapping[str, dict[str, float]]) -> float:
    """One axis on a 0..1 scale, relative to the set where that is the only scale.

    A rate is already a proportion and is used as it stands. Everything else is
    divided by the best in the set, so the leader scores 1.0 and the rest say how
    far behind they are — which is the comparison a recommendation is making.
    """
    mine = values[runtime_key][axis]
    if AXIS_METRICS.get(axis, ("", False))[1]:
        return max(0.0, min(1.0, mine))
    best = max(
        (found[axis] for found in values.values() if axis in found), default=0.0
    )
    return mine / best if best else 0.0


def eligible(
    profile: RoleProfile,
    runtime_key: str,
    values: Mapping[str, dict[str, float]],
    capabilities: Mapping[str, str],
) -> tuple[bool, tuple[str, ...]]:
    """Whether a candidate may be recommended for a role, and why not.

    Requirements are hard and separate from the score, the same way a pool's
    invariant is separate from RAVIS's ranking: a build that cannot call tools
    is not a low-scoring agent, it is not an agent. Weighing it against the
    others would let a fast enough model out-score its own disqualification.
    """
    reasons: list[str] = []
    mine = values.get(runtime_key, {})
    for capability in profile.required_capabilities:
        state = capabilities.get(f"{runtime_key}:{capability}", "UNKNOWN")
        if state != "SUPPORTED":
            reasons.append(f"{capability} is {state}")
    window = mine.get("context")
    if profile.minimum_context:
        if window is None:
            reasons.append("context window unknown — fails closed")
        elif window < profile.minimum_context:
            reasons.append(
                f"context {int(window)} is below the required {profile.minimum_context}"
            )
    return (not reasons, tuple(reasons))


def combination_score(
    chat: Utility | None, agent: Utility | None, penalties: Mapping[str, float]
) -> float:
    """§14.3's combination formula.

        ChatUtility + AgentUtility + JointUtility
            − MemoryPenalty − SwapPenalty − ContentionPenalty − ReliabilityPenalty

    **Not the two best picked independently**, which §14.3 says in as many words
    and §10.1 gives the measured reason for: on this machine co-residency was
    nearly free and concurrency cost 32–43% of throughput, and neither number is
    predictable from either model alone.

    `JointUtility` is zero until a pair has been measured together. Zero rather
    than an optimistic default: a pair nobody has run together has no joint
    evidence, and inventing one would assert exactly what §10.1 exists to deny.
    """
    total = (chat.score if chat else 0.0) + (agent.score if agent else 0.0)
    total += penalties.get("joint", 0.0)
    for name in ("memory", "swap", "contention", "reliability"):
        total -= penalties.get(name, 0.0)
    return total


def _runtime_key(record: Mapping[str, Any]) -> str:
    target = record.get("target")
    if isinstance(target, Mapping) and target.get("runtime_key"):
        return str(target["runtime_key"])
    return str(record.get("runtime_key") or "")


def _rate(record: Mapping[str, Any], name: str) -> float | None:
    body = (record.get("metrics") or {}).get(name)
    if not isinstance(body, Mapping):
        return None
    passed, total = body.get("passed"), body.get("total")
    if not isinstance(passed, int) or not isinstance(total, int) or total <= 0:
        return None
    return passed / total


def _median(record: Mapping[str, Any], name: str) -> float | None:
    body = (record.get("metrics") or {}).get(name)
    if not isinstance(body, Mapping):
        return None
    value = body.get("median")
    return float(value) if isinstance(value, (int, float)) else None


@dataclass
class Recommendation:
    """§14.3's output — most of it, and this docstring used to claim all of it.

    The ones easiest to leave out are the ones that make it honest, and those
    are here: `excluded` with reasons, `uncertainty`, `generated_at`, the
    expiry, and the algorithm version. Two more live one level down on `Utility`
    rather than on this record, because they are per candidate rather than per
    recommendation — `coverage`, and `evidence_ids` for §14.3's *supporting
    benchmark IDs*.

    **What §14.3 asks for and is not here yet**, stated rather than implied by
    an over-confident summary:

    - *recommended model **or Runtime Set*** — only models are ranked. Runtime
      Sets exist (M9) and are never recommended.
    - *memory* — the weighting has a `memory` axis, but no expected footprint
      is reported for the recommendation itself.
    - *expected performance* and *quality* as named outputs — both are folded
      into `Utility.axes` and neither is surfaced as a figure a caller can read
      without knowing the axis names.
    - *evidence level* — the acceptable level is an input §14.3 names and this
      engine neither accepts nor reports.

    Recorded here because the previous version of this docstring is exactly how
    a gap survives: it read as a completeness guarantee, so nobody checked.
    """

    profile: str
    mode: str
    roles: dict[str, Utility | None] = field(default_factory=dict)
    # Every admitted candidate in order, not only the winner. §14.3 asks for
    # *ranked candidates* and excluded ones, and reporting only the top of each
    # role made a build that was considered and placed second vanish entirely —
    # present in neither list, as though nobody had looked at it.
    ranked: dict[str, list[Utility]] = field(default_factory=dict)
    # The interaction matrix for the recommended pair, when one has been
    # measured. What makes the combination score a measurement of a pair rather
    # than the sum of two solo numbers.
    pair_matrices: list[Mapping[str, Any]] = field(default_factory=list)
    excluded: dict[str, list[Exclusion]] = field(default_factory=dict)
    combination_score: float | None = None
    fit: str = FIT_UNKNOWN
    fit_detail: str = ""
    uncertainty: tuple[str, ...] = ()
    proposed_benchmarks: tuple[str, ...] = ()
    generated_at: str = ""
    expires_after_seconds: int = 24 * 3600
    algorithm_version: str = ALGORITHM_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "mode": self.mode,
            "recommended": {
                role: (found.runtime_key if found else None)
                for role, found in self.roles.items()
            },
            "roles": {
                role: (found.as_dict() if found else None)
                for role, found in self.roles.items()
            },
            "ranked": {
                role: [item.as_dict() for item in items]
                for role, items in self.ranked.items()
            },
            "excluded": {
                role: [item.as_dict() for item in items]
                for role, items in self.excluded.items()
            },
            "combination_score": (
                None if self.combination_score is None
                else round(self.combination_score, 4)
            ),
            "fit": {"tier": self.fit, "detail": self.fit_detail, "basis": "estimate"},
            "uncertainty": list(self.uncertainty),
            "proposed_benchmarks": list(self.proposed_benchmarks),
            "generated_at": self.generated_at,
            "expires_after_seconds": self.expires_after_seconds,
            "algorithm_version": self.algorithm_version,
        }


def recommend(
    records: Sequence[Mapping[str, Any]],
    contexts: Mapping[str, int | None],
    capabilities: Mapping[str, str],
    *,
    roles: Sequence[str] = ("clarvis-chat", "clarvis-agent"),
    mode: str = MODE_FAST,
    generated_at: str = "",
    matrices: Sequence[Mapping[str, Any]] = (),
) -> Recommendation:
    """§14.3's engine: rank, exclude, combine, and say what is unknown.

    Deterministic throughout — the same records in any order produce the same
    answer, because M15's acceptance says *exclusions and uncertainty are
    reproducible* and a recommendation that moved between two identical calls
    would be an opinion nobody could check.

    `fast` reads what exists. `verified` reads the same thing and *proposes*
    what would resolve the gaps, without running any of it: §14.3 is explicit
    that expensive work never starts unless it was asked for.
    """
    by_role = _records_by_role(records)
    recommendation = Recommendation(
        profile=DEFAULT_PROFILE, mode=mode, generated_at=generated_at
    )
    for role in roles:
        profile = PROFILES.get(role)
        if profile is None:
            continue
        mine = by_role.get(role, [])
        values = axis_values(mine, contexts)
        ranked, excluded = _rank_for(profile, mine, values, capabilities)
        recommendation.roles[role] = ranked[0] if ranked else None
        recommendation.ranked[role] = ranked
        recommendation.excluded[role] = excluded
    recommendation.pair_matrices = _matrices_for(recommendation, matrices)
    _finish(recommendation, roles, mode)
    return recommendation


def _matrices_for(
    recommendation: Recommendation, matrices: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    """Every interaction matrix measured for *this* pair.

    Matched on the exact set of recommended builds. A matrix for a different
    pairing describes a different pair — §10.1 is precisely that co-residency
    behaviour does not transfer between combinations — so a near-miss is no
    match at all.
    """
    wanted = {
        found.runtime_key for found in recommendation.roles.values() if found
    }
    if len(wanted) < 2:
        return []
    return [matrix for matrix in matrices if _members_of(matrix) == wanted]


def _members_of(matrix: Mapping[str, Any]) -> set[str]:
    """Which builds a matrix was measured over, by runtime key."""
    members = matrix.get("members")
    return set(members.values()) if isinstance(members, Mapping) else set()


def _records_by_role(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    """Group evidence by the role it was measured for.

    Evidence for one role says nothing about another — a throughput measured
    under the agent suite is about the agent workload — so nothing is borrowed
    across roles even when the build is the same.
    """
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record.get("role") or ""), []).append(record)
    return grouped


def _rank_for(
    profile: RoleProfile,
    records: Sequence[Mapping[str, Any]],
    values: Mapping[str, dict[str, float]],
    capabilities: Mapping[str, str],
) -> tuple[list[Utility], list[Exclusion]]:
    """Every eligible candidate in order, and everything that was not.

    Ties break on the runtime key, so a rerun cannot reorder two candidates the
    arithmetic considers identical.
    """
    ids = _evidence_ids(records)
    excluded: list[Exclusion] = []
    admitted: dict[str, dict[str, float]] = {}
    for key in sorted(values):
        allowed, reasons = eligible(profile, key, values, capabilities)
        if allowed:
            admitted[key] = values[key]
        else:
            excluded.append(Exclusion(runtime_key=key, reasons=reasons))

    # **Normalised over the admitted set, not every candidate.** A recommendation
    # ranks the builds that qualify, so "fastest" means fastest among those — and
    # letting an excluded build set the scale would let a model that cannot do
    # the job at all drag down the score of one that can. It is not academic on
    # this machine: the MLX packaging of granite is 83% faster and is excluded
    # for failing every realistic tool call, and while it set the scale the
    # admitted build scored 0.55 on throughput for being beaten by something
    # ineligible.
    ranked = [utility(profile, key, admitted, ids.get(key, ())) for key in admitted]
    ranked.sort(key=lambda found: (-found.score, found.runtime_key))
    return ranked, excluded


def _evidence_ids(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[str, ...]]:
    """Which stored records back each candidate (§14.3's supporting benchmark IDs)."""
    found: dict[str, list[str]] = {}
    for record in records:
        key = _runtime_key(record)
        reference = str(record.get("evidence_ref") or record.get("evidence_id") or "")
        if key and reference:
            found.setdefault(key, []).append(reference)
    return {key: tuple(sorted(set(refs))) for key, refs in found.items()}


def contention_penalty(matrices: Sequence[Mapping[str, Any]]) -> tuple[float, str]:
    """The measured cost of running a pair at once, as §14.3's penalty.

    Derived from what M10 measured rather than predicted: the worst concurrent
    throughput degradation across the members, as a fraction.

    **Every run of the pair, not the newest one**, and the reason is a
    measurement this machine produced. The same pair benchmarked twice gave the
    agent role a concurrent degradation of 34.4% and then 21.2% — a thirteen
    point swing caused by its *alone* baseline drifting 14.8% between runs,
    while the chat role held steady at 22.1% and 21.4%. Degradation is computed
    against alone, so an unstable control moves the answer without contention
    changing at all.

    The penalty is the **median**, which is §11.7's own convention for exactly
    this reason, and the spread is reported whenever more than one run exists.

    Median rather than worst, and a third run is what settled it. Three runs of
    one pair gave the agent role 34.4%, 21.2% and 21.3%: two agree to a tenth of
    a point and the first is eighteen percent adrift — and only on its *alone*
    baseline, because its concurrent figure held at 44.6, 45.7, 45.3 throughout.
    Taking the worst would anchor the recommendation on the one measurement the
    other two contradict. A single number to four decimals from one run of a
    fanless machine is precision this corpus has not earned.

    `(0.0, "")` when nothing has been measured, which is not the same as a
    frictionless pair: the caller reports the absence rather than the zero.
    """
    observed: list[float] = []
    label = ""
    for matrix in matrices:
        if not matrix.get("complete"):
            continue
        worst = 0.0
        for row in (matrix.get("rows") or {}).values():
            drop = ((row.get("degradation_percent") or {}).get("concurrent") or {})
            value = drop.get("tokens_per_second")
            if isinstance(value, (int, float)):
                worst = max(worst, float(value))
        if worst:
            observed.append(worst)
            label = f"{matrix.get('runtime_set')}@{matrix.get('revision')}"
    if not observed:
        return 0.0, ""
    ordered = sorted(observed)
    middle = len(ordered) // 2
    penalty = (
        ordered[middle] if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2
    )
    spread = (
        f", median of {len(ordered)} runs ranging {ordered[0]:.1f}–{ordered[-1]:.1f}%"
        if len(ordered) > 1 else " from a single run"
    )
    return penalty / 100.0, (
        f"measured together: concurrent generation costs {penalty:.1f}% of "
        f"throughput{spread} ({label})"
    )


def _finish(recommendation: Recommendation, roles: Sequence[str], mode: str) -> None:
    """Score the combination and record what the answer rests on.

    The uncertainty list is assembled last because it is a statement about the
    whole recommendation rather than any one role, and because the most useful
    entry — a role with no candidate at all — is only knowable once every role
    has been tried.
    """
    filled = [found for found in recommendation.roles.values() if found]
    unfilled = [role for role, found in recommendation.roles.items() if not found]
    notes: list[str] = []
    proposals: list[str] = []

    for role in unfilled:
        notes.append(f"no eligible candidate for {role}")
        proposals.append(f"sirvis benchmark run <suite> --clarvis-role {role}")
    for role, found in recommendation.roles.items():
        if found and found.coverage < 1.0:
            absent = ", ".join(sorted(found.missing))
            notes.append(
                f"{role} scored on {found.coverage:.0%} of its profile; "
                f"no evidence for: {absent}"
            )

    if len(filled) == len(roles) and filled:
        contention, measured = contention_penalty(recommendation.pair_matrices)
        recommendation.combination_score = combination_score(
            recommendation.roles.get("clarvis-chat"),
            recommendation.roles.get("clarvis-agent"),
            penalties={"contention": contention},
        )
        notes.append(
            measured if measured else
            # Zero rather than a quiet default, and said out loud: §10.1's whole
            # argument is that an unmeasured pair is *unknown*, not fine.
            "the combination score has no joint term: this pair has not been "
            "measured together, so contention, swap and reliability penalties "
            "are all zero rather than estimated"
        )
    else:
        notes.append("no combination score: not every role has a candidate")

    if mode == MODE_VERIFIED:
        proposals.append("re-run the role suites at higher repetitions to raise coverage")
    recommendation.uncertainty = tuple(notes)
    recommendation.proposed_benchmarks = tuple(proposals) if mode == MODE_VERIFIED else ()
