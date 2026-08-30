"""M13 — Clarvis role benchmarks (§13.2), and the claim they refuse to make.

    Role-specific verdicts exist; agent evidence includes tool-call
    reliability; no metadata-only capability claim.

The third clause is the one with teeth, and it is why this milestone exists at
all: a catalogue flag saying `tool_use` is not evidence that a build calls
tools, and on this machine the two disagree in both directions. Every assertion
below about tool support traces to attempts that were made and counted.

The assembly tests are the load-bearing ones. They encode failures that cost
real debugging sessions in `clarvis-firstrun`, and a reimplementation would have
to rediscover each of them — which is exactly why M12 says wrap rather than
rewrite.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import pytest

from sirvis.benchmarks.clarvis_roles import (
    GAVE_UP,
    LOST_ARGUMENTS,
    NO_CALL,
    RETRIED,
    ROLE_AGENT,
    ROLE_CHAT,
    TOOL_PROMPTS,
    USED_RESULT,
    AssembledCall,
    ToolReliability,
    ToolTrial,
    absorb_tool_deltas,
    followup_rate,
    role_spec,
    role_tests,
    run_followup,
    run_tool_trials,
    score_followup,
    tool_rate,
)
from sirvis.core.evidence import EvidenceKind
from sirvis.runtimes.base import GenerationChunk


def deltas(*frames: dict[str, Any]) -> list[dict[str, Any]]:
    return list(frames)


class FakeToolRuntime:
    """A runtime that streams whatever tool-call deltas a test hands it.

    Deltas rather than finished calls, because the difference between those two
    is the whole subject: a build that returns a well-formed call unstreamed can
    stream one whose arguments never arrive.
    """

    def __init__(self, script: list[list[dict[str, Any]]] | None = None,
                 text: str = "", fail: bool = False) -> None:
        self.script = script or []
        self.text = text
        self.fail = fail
        self.turns = 0
        self.sent: list[dict[str, Any]] = []

    def stream_generate(
        self, model_key: str, messages: list[dict[str, Any]], **options: Any
    ) -> AsyncIterator[GenerationChunk]:
        self.sent.append({"model": model_key, "messages": messages, "options": options})
        turn = self.turns
        self.turns += 1

        async def chunks() -> AsyncIterator[GenerationChunk]:
            if self.fail:
                raise RuntimeError("the runtime fell over")
            if self.text:
                yield GenerationChunk(content=self.text)
            for frame in (self.script[turn] if turn < len(self.script) else []):
                await asyncio.sleep(0)
                yield GenerationChunk(tool_calls=[frame])
            yield GenerationChunk(finish_reason="stop")

        return chunks()


# ── Assembly: the three failures this wraps rather than rediscovers ──────────


def test_a_call_survives_frames_that_omit_its_name() -> None:
    """The opening frame carries id and name; every frame after carries only args.

    A merge that overwrote with the absent value would erase the name on the
    second frame of every call — and every call has a second frame.
    """
    calls = absorb_tool_deltas(deltas(
        {"index": 0, "id": "call_1", "function": {"name": "readFile", "arguments": '{"pa'}},
        {"index": 0, "function": {"arguments": 'th":"src/'}},
        {"index": 0, "function": {"arguments": 'main.go"}'}},
    ))

    assert len(calls) == 1
    assert calls[0].name == "readFile"
    assert calls[0].id == "call_1"
    assert calls[0].path == "src/main.go"


def test_parallel_calls_are_kept_apart_by_index() -> None:
    """Fragments interleave, and only the index says which call they belong to."""
    calls = absorb_tool_deltas(deltas(
        {"index": 0, "id": "a", "function": {"name": "readFile", "arguments": '{"path":"'}},
        {"index": 1, "id": "b", "function": {"name": "listFiles", "arguments": '{"recur'}},
        {"index": 0, "function": {"arguments": 'one.go"}'}},
        {"index": 1, "function": {"arguments": 'sive":true}'}},
    ))

    assert [call.name for call in calls] == ["readFile", "listFiles"]
    assert calls[0].path == "one.go"


def test_arguments_that_never_arrive_are_not_a_usable_call() -> None:
    """The failure that looks most like success.

    A `tool_calls` array arrived, the name is right, and Clarvis still cannot
    dispatch it. `granite-4.0-h-tiny` streams exactly this while returning a
    well-formed call unstreamed — so a harness reading a finished response
    scores it 3/3 on a request it always fails.
    """
    calls = absorb_tool_deltas(deltas(
        {"index": 0, "id": "a", "function": {"name": "readFile"}},
    ))

    assert calls[0].name == "readFile"
    assert calls[0].path is None
    assert not calls[0].is_well_formed


def test_unparseable_arguments_are_absent_rather_than_guessed() -> None:
    calls = absorb_tool_deltas(deltas(
        {"index": 0, "function": {"name": "readFile", "arguments": '{"path": "unter'}},
    ))

    assert calls[0].path is None


# ── The trials ───────────────────────────────────────────────────────────────


def well_formed(path: str = "src/main.go") -> list[dict[str, Any]]:
    return [{"index": 0, "id": "c", "function": {"name": "readFile",
                                                 "arguments": '{"path":"%s"}' % path}}]


def test_every_phrasing_is_attempted() -> None:
    """Eight ways a person asks for a file, not one that happens to work.

    `granite-4.0-h-tiny` passed a single-prompt check three times out of three
    and lost the filename on every one of these.
    """
    runtime = FakeToolRuntime(script=[well_formed() for _ in range(len(TOOL_PROMPTS) + 2)])

    reliability = asyncio.run(run_tool_trials(runtime, "m"))

    assert reliability.total == len(TOOL_PROMPTS) == 8
    assert [t.prompt for t in reliability.trials] == list(TOOL_PROMPTS)


def test_a_build_that_loses_arguments_scores_zero_not_eight() -> None:
    """The distinction the whole harness exists for."""
    lost = [{"index": 0, "id": "c", "function": {"name": "readFile"}}]
    runtime = FakeToolRuntime(script=[lost for _ in range(12)])

    reliability = asyncio.run(run_tool_trials(runtime, "m"))

    assert reliability.passed == 0
    assert reliability.outcomes == {LOST_ARGUMENTS: 8}


def test_a_runtime_failure_is_a_failed_attempt_not_a_skipped_one() -> None:
    """A build that makes the runtime fall over has not passed those prompts.

    Dropping them would publish eight in eight for a build that answered none.
    """
    runtime = FakeToolRuntime(fail=True)

    reliability = asyncio.run(run_tool_trials(runtime, "m"))

    assert reliability.total == 8
    assert reliability.passed == 0
    assert reliability.outcomes == {NO_CALL: 8}


def test_the_trials_stream_and_send_the_tool() -> None:
    """Clarvis streams every reply it makes, so the measurement streams."""
    runtime = FakeToolRuntime(script=[well_formed() for _ in range(12)])

    asyncio.run(run_tool_trials(runtime, "m"))

    assert runtime.sent[0]["options"]["tools"][0]["function"]["name"] == "readFile"


# ── The follow-up turn (F17) ─────────────────────────────────────────────────


def test_using_the_tool_result_is_the_only_pass() -> None:
    """It was told the path was wrong and to use listFiles. It did."""
    listing = [{"index": 0, "id": "d", "function": {"name": "listFiles",
                                                    "arguments": '{"recursive":true}'}}]
    runtime = FakeToolRuntime(script=[well_formed(), listing])

    assert asyncio.run(run_followup(runtime, "m")) == USED_RESULT


def test_asking_for_the_same_dead_path_again_is_the_f17_signature() -> None:
    """The failure one-shot code generation cannot see.

    The model that did this writes perfectly good Python — it retried a dead
    path four times, twice after being told plainly to use a different tool.
    """
    runtime = FakeToolRuntime(script=[well_formed(), well_formed()])

    assert asyncio.run(run_followup(runtime, "m")) == RETRIED


def test_a_first_turn_with_no_call_never_reaches_the_follow_up() -> None:
    runtime = FakeToolRuntime(script=[[]])

    assert asyncio.run(run_followup(runtime, "m")) == NO_CALL


def test_scoring_keeps_five_failures_apart_rather_than_one_boolean() -> None:
    """`retried` and `gave-up` are different bugs with different fixes."""
    asked = AssembledCall(index=0, name="readFile", arguments='{"path":"a.go"}')

    assert score_followup([], [], "") == NO_CALL
    assert score_followup([AssembledCall(index=0, name="readFile")], [], "") == LOST_ARGUMENTS
    assert score_followup([asked], [], "") == GAVE_UP
    assert score_followup([asked], [], "sorry, I cannot") == "answered-in-prose"
    assert score_followup([asked], [asked], "") == RETRIED


# ── The evidence contract (§13.2) ────────────────────────────────────────────


def test_reliability_becomes_a_rate_not_a_measurement() -> None:
    """A median over ones and zeros is meaningless; a rate is not.

    Both halves travel: 6/8 and 60/80 are different amounts of evidence for the
    same rate, and a router weighing them needs to know which it has.
    """
    reliability = ToolReliability(
        trials=[ToolTrial(prompt=p, calls=absorb_tool_deltas(well_formed()))
                for p in TOOL_PROMPTS],
        followup=USED_RESULT,
    )

    rate = tool_rate(reliability, phrasings=8)

    assert (rate.passed, rate.total) == (8, 8)
    assert rate.rate == 1.0
    assert rate.phrasings == 8
    assert rate.provenance.kind is EvidenceKind.MEASURED
    assert rate.provenance.method == "clarvis.tool_call.streamed.v1"


def test_the_follow_up_rate_records_which_outcome_happened() -> None:
    """A boolean would discard the difference between retrying and giving up."""
    rate = followup_rate(ToolReliability(followup=RETRIED))

    assert rate.passed == 0
    assert "retried" in (rate.provenance.notes or "")


# ── Roles: the vocabulary RAVIS actually asks in ─────────────────────────────


def test_the_role_is_spelled_the_way_ravis_names_its_pool() -> None:
    """M16's seam, closed by measuring the role rather than mapping the name.

    RAVIS asks for `clarvis-agent`. Evidence filed under `agent` cannot answer
    that, and teaching either side a mapping is the equivalence-inference §15.1
    forbids.
    """
    assert role_spec(ROLE_AGENT, "m").role == "clarvis-agent"
    assert role_spec(ROLE_CHAT, "m").role == "clarvis-chat"


def test_only_the_agent_role_runs_the_tool_trials() -> None:
    """§13.2 makes tool-call reliability part of what the agent role *is*.

    And equally: a chat verdict does not need it, so the chat run does not spend
    nine generations proving something its role does not turn on.
    """
    assert role_spec(ROLE_AGENT, "m").tool_trials is True
    assert role_spec(ROLE_CHAT, "m").tool_trials is False


def test_the_role_workload_is_clarvis_own_scenes() -> None:
    """A role benchmark on invented prompts measures a workload nobody has."""
    chat = {test.id for test in role_tests(ROLE_CHAT)}
    agent = {test.id for test in role_tests(ROLE_AGENT)}

    assert chat == {"quip", "briefing", "explain"}
    assert agent == {"code", "code2"}
    assert all(test.generation.temperature == 0 for test in role_tests(ROLE_CHAT))


def test_an_unknown_role_is_refused_rather_than_measured() -> None:
    with pytest.raises(ValueError, match="unknown Clarvis role"):
        role_spec("planner", "m")


# ── No metadata-only capability claim ────────────────────────────────────────


def test_a_run_without_trials_carries_no_tool_rate_at_all() -> None:
    """Absence and a rate of zero are different claims (§12.1).

    A `0/0` in the field a router reads to decide whether a build calls tools
    would be neither — a rate nobody measured, sitting where a measurement goes.
    """
    from sirvis.benchmarks.engine import ExperimentOutcome, _rates
    from sirvis.storage import RunState

    outcome = ExperimentOutcome(
        experiment_id="e", run_id="r", state=RunState.SUCCEEDED,
        detail="", results_path="",
    )

    assert _rates(outcome) == {}


def test_a_run_with_trials_carries_both_rates() -> None:
    from sirvis.benchmarks.engine import ExperimentOutcome, _rates
    from sirvis.storage import RunState

    outcome = ExperimentOutcome(
        experiment_id="e", run_id="r", state=RunState.SUCCEEDED,
        detail="", results_path="",
    )
    outcome.tool_reliability = ToolReliability(
        trials=[ToolTrial(prompt=p, calls=absorb_tool_deltas(well_formed()))
                for p in TOOL_PROMPTS],
        followup=USED_RESULT,
    )

    rates = _rates(outcome)

    assert set(rates) == {"tool_call_well_formed", "tool_followup_used_result"}
    assert rates["tool_call_well_formed"].passed == 8


def test_the_specs_repetition_count_reaches_the_trials() -> None:
    """§13.2's threshold is eight phrasings *times three repetitions*.

    A trial runner fixed at one repetition can never satisfy the axis the
    threshold turns on, which is what shipped: `--repetitions 3` produced three
    prose repetitions and eight tool attempts, so no run from the CLI could ever
    establish the capability. Caught before spending model time on a run that
    could not have counted.
    """
    runtime = FakeToolRuntime(script=[well_formed() for _ in range(40)])

    reliability = asyncio.run(run_tool_trials(runtime, "m", repetitions=3))

    assert reliability.total == len(TOOL_PROMPTS) * 3 == 24


def test_a_run_below_the_coverage_minimum_is_unknown_not_a_pass() -> None:
    """§13.1 states the threshold on three axes and the code compared one.

    "`tool_call_pass_rate` >= 0.95 over >= 8 distinct prompt phrasings x >= 3
    repetitions each", and the section closes: "A run that covers fewer
    phrasings or fewer repetitions than the minimum yields `UNKNOWN`, never a
    pass."

    `TrialRate` records both axes and the evidence endpoint returns them;
    nothing read either. A build measured at one repetition per phrasing -- a
    third of the required evidence -- was declared agent-capable on a perfect
    rate, and this machine's corpus holds exactly such a record.

    UNKNOWN rather than UNSUPPORTED: too little evidence is not evidence of
    failure, and `eligible()` fails closed on any state that is not SUPPORTED.
    """
    from sirvis.api.routes import _capability_states

    def record(key: str, phrasings: int, repetitions: int) -> dict[str, object]:
        return {
            "runtime_key": key,
            "metrics": {
                "tool_call_well_formed": {
                    "passed": 24, "total": 24, "rate": 1.0,
                    "phrasings": phrasings, "repetitions": repetitions,
                }
            },
        }

    states = _capability_states([
        record("covered", 8, 3),
        record("too-few-repetitions", 8, 1),
        record("too-few-phrasings", 4, 3),
    ])

    assert states["covered:tool_use"] == "SUPPORTED", "meets all three axes"
    assert states["too-few-repetitions:tool_use"] == "UNKNOWN", (
        "a perfect rate over one repetition each is not a pass"
    )
    assert states["too-few-phrasings:tool_use"] == "UNKNOWN"


def test_a_missing_coverage_axis_is_unknown_rather_than_assumed() -> None:
    """An older record that predates the two fields must not be read as covered.

    Absence and zero are different, and the direction that matters is the one
    that would let an unmeasured build through.
    """
    from sirvis.api.routes import _capability_states

    states = _capability_states([{
        "runtime_key": "no-coverage-fields",
        "metrics": {"tool_call_well_formed": {"passed": 24, "total": 24, "rate": 1.0}},
    }])

    assert states["no-coverage-fields:tool_use"] == "UNKNOWN"


async def test_the_engine_passes_its_repetition_count_to_the_trials(tmp_path: Any) -> None:
    """The seam the test above documents but does not cross.

    `test_the_specs_repetition_count_reaches_the_trials` calls `run_tool_trials`
    directly, passing `repetitions=3` itself and asserting the callee honours
    it. The defect it describes lived in the *caller*: the engine used to invoke
    the trial runner without forwarding `spec.repetitions`, so `--repetitions 3`
    produced three prose repetitions and eight tool attempts, and no run from
    the CLI could establish the capability at all. A test that supplies the
    argument cannot notice the caller failing to.

    This drives `run_experiment` end to end and asserts the count that comes out
    the far side: eight phrasings times three repetitions.
    """
    from tests.test_m6_benchmark import (
        SNAPSHOT,
        FakeRuntime,
        SteadyProbe,
        Ticking,
        _spec,
    )

    from sirvis.benchmarks import ExperimentSpec, run_experiment
    from sirvis.resources.manager import ResourceManager
    from sirvis.storage.database import prepare_database

    class _ToolCapableRuntime(FakeRuntime):
        """The engine's runtime, answering the tool prompts with real calls.

        `FakeToolRuntime` implements only what the trial runner needs, and the
        seam under test is the engine -- which loads, unloads and lists models
        first. Composed rather than reimplemented so this stays a test of the
        engine's forwarding, not of a second fake.
        """

        def stream_generate(self, model_key, messages, **options):  # type: ignore[no-untyped-def]
            asked = str(messages[-1].get("content", "")) if messages else ""
            if any(prompt in asked for prompt in TOOL_PROMPTS):
                return FakeToolRuntime(script=[well_formed()]).stream_generate(
                    model_key, messages, **options
                )
            return super().stream_generate(model_key, messages, **options)

    spec: ExperimentSpec = _spec(tool_trials=True, repetitions=3, warmups=0)
    runtime = _ToolCapableRuntime()
    outcome = await run_experiment(
        spec, runtime=runtime, resources=ResourceManager(runtime=runtime),
        database=prepare_database(":memory:"), results_root=str(tmp_path),
        probe=SteadyProbe(), snapshot=SNAPSHOT, clock=Ticking(),
        thermal=lambda: "nominal",
    )

    assert outcome.tool_reliability is not None
    assert outcome.tool_reliability.total == len(TOOL_PROMPTS) * 3, (
        "the spec's repetition count has to reach the trials through the engine, "
        "not only when a test hands it over directly"
    )


def test_a_runtime_that_falls_over_is_not_recorded_against_the_model() -> None:
    """§11.8: an integrity problem is flagged, never hidden -- and never blamed
    on the thing being measured.

    `except Exception: return NO_CALL` turned any runtime fault -- a disconnect,
    a timeout, a malformed stream, a crashed LM Studio -- into `no-call`, which
    in this vocabulary means *the model declined to call the tool*. That verdict
    was published in a record marked MEASURED, and RAVIS reads exactly this
    field to decide whether a build may serve the agent role. A broken runtime
    therefore disqualified a model that had done nothing wrong.

    `trial-failed` sits outside the behavioural vocabulary on purpose: every
    other outcome is something the model did, and this one is something that
    happened to the run.
    """
    import asyncio

    from sirvis.benchmarks.clarvis_roles import TRIAL_FAILED, run_tool_trials

    reliability = asyncio.run(run_tool_trials(FakeToolRuntime(fail=True), "m"))

    assert reliability.followup == TRIAL_FAILED
    assert reliability.followup != "no-call", "the model did not decline anything"


def test_a_failed_trial_measures_nothing_rather_than_measuring_a_failure() -> None:
    """Nought out of nought, not nought out of one.

    §12.1's lattice reads an absence as unmeasured and a zero as
    measured-and-failed. A run that fell over is not entitled to the second
    claim, and a rate of 0/1 is exactly that claim in the form RAVIS consumes.
    """
    from sirvis.benchmarks.clarvis_roles import TRIAL_FAILED, ToolReliability, followup_rate

    assert followup_rate(ToolReliability(trials=[], followup=TRIAL_FAILED)) is None, (
        "no evidence, rather than bad evidence"
    )


def test_the_rate_carries_how_the_failures_failed() -> None:
    """`3/24` reads as "cannot call tools". On this machine the build behind
    that number calls `readFile` every time and sends empty arguments — it loses
    the filename. Different defect, different fix, and a rate says neither."""
    from sirvis.benchmarks.clarvis_roles import TOOL_PROMPTS, tool_rate

    # The outcome is derived from what came back, not set: a call with empty
    # arguments is `lost-arguments`, a well-formed one is `used-result`.
    lost = AssembledCall(index=0, id="c", name="readFile", arguments="")
    good = AssembledCall(index=0, id="c", name="readFile",
                         arguments='{"path": "src/main.go"}')
    reliability = ToolReliability(trials=[
        ToolTrial(prompt=TOOL_PROMPTS[index % len(TOOL_PROMPTS)], calls=[call])
        for index, call in enumerate([lost] * 21 + [good] * 3)
    ])

    rate = tool_rate(reliability, phrasings=len(TOOL_PROMPTS))

    assert rate.passed == 3 and rate.total == 24
    assert rate.outcomes == {"lost-arguments": 21, "used-result": 3}
    # Sorted by count in the published shape: the first line of a failure report
    # should be the failure that happened most.
    assert list(rate.as_dict()["outcomes"]) == ["lost-arguments", "used-result"]


def test_a_tally_that_does_not_add_up_is_refused() -> None:
    """A partial tally would be read as a complete account of the attempts."""
    import pytest

    from sirvis.core.evidence import EvidenceKind, Provenance, TrialRate

    with pytest.raises(ValueError, match="covers 5 attempts, not 24"):
        TrialRate(
            passed=3, total=24,
            provenance=Provenance(kind=EvidenceKind.MEASURED, method="x"),
            outcomes={"lost-arguments": 5},
        )
