"""A validity warning says what it undermines, not just that something is off.

**Why a single record-level flag was not enough.** On the machine this was
written against, 44 of 93 records were `SUSPECT` and RAVIS treated them exactly
as it treated clean ones. The obvious correction — stop trusting suspect records
— is wrong: twenty-one of those were suspect because the machine was thermally
throttled, which the engine's own docstring measures at a *48% swing in
tokens/second* and which says nothing whatever about whether the model formed
well-formed tool calls. Demoting a capability on it discards a correct
measurement because the room was warm.

So the scope travels with the warning. Three, because the producers already
divide this way and no finer division is currently observable:

  TIMING      — the rate numbers describe something other than the model
                (thermal pressure, swap)
  OUTPUT      — what the model produced is in question (tokens that never
                arrived as content, unexpected stops)
  CONDITIONS  — the run did not answer the question asked, so nothing on it
                is safe (configuration mismatch, prompt adaptation)
"""

from __future__ import annotations

from typing import Any

from sirvis.core.evidence import ValidityScope


def _outcome(*, thermal_before: str = "", thermal_after: str = "",
             suppression: str = "") -> Any:
    """An outcome carrying only the fields these warnings read."""
    from sirvis.benchmarks.engine import ExperimentOutcome

    outcome = ExperimentOutcome(
        experiment_id="e", run_id="r", state="complete", detail="",
        results_path="",
    )
    outcome.thermal_before = thermal_before
    outcome.thermal_after = thermal_after or thermal_before
    outcome.thinking_suppression = suppression
    return outcome


def _repetition(*, hidden: int = 0, content: str = "an answer") -> Any:
    """A measured repetition, with only the fields these warnings read."""
    from sirvis.benchmarks.engine import Repetition

    return Repetition(
        test_id="t", phase="measured", index=0, total_seconds=1.0,
        content=content, completion_tokens=hidden + 10,
        reasoning_tokens=hidden or None,
    )


def test_thermal_pressure_is_timing_only() -> None:
    """The case that makes the whole distinction worth having."""
    from sirvis.benchmarks.engine import _thermal_warnings

    outcome = _outcome(thermal_before="fair", thermal_after="fair")
    assert [scope for scope, _ in _thermal_warnings(outcome)] == [ValidityScope.TIMING]


def test_reasoning_tokens_are_a_timing_fact_not_an_output_one() -> None:
    """The correction, and why the scope belongs to the warning not the producer.

    `gemma-4-e4b` on this machine scored **24/24 and 21/24 well-formed tool
    calls** while carrying "6 repetition(s) generated tokens that never arrived
    as content — a median of 328 of them". That reads like the model produced
    nothing. It is the opposite: those are reasoning tokens, and the note itself
    says where they go — "spent before the first answer token: the throughput
    here covers the answer only, and the time-to-first-token includes the
    thinking". The fact recorded is which window they landed in, which is rate
    accounting.

    Scoping it `OUTPUT` — or `CONDITIONS`, which is what tagging the whole
    producer did — would have demoted a model that answered every tool call
    correctly. The mistake scopes exist to prevent, reached by a different route.
    """
    from sirvis.benchmarks.engine import _suppression_warnings

    scoped = _suppression_warnings(_outcome(), [_repetition(hidden=328)])
    assert scoped, "the fixture must actually produce the warning"
    scope, note = scoped[0]
    assert "never arrived as content" in note
    assert scope is ValidityScope.TIMING


def test_a_suppressed_prompt_is_a_conditions_fact() -> None:
    """Its neighbour in the same producer, and genuinely CONDITIONS: the run
    answered a different question from the one the suite declares."""
    from sirvis.benchmarks.engine import _suppression_warnings

    scoped = _suppression_warnings(_outcome(suppression="no_think_suffix"), [])
    assert [scope for scope, _ in scoped] == [ValidityScope.CONDITIONS]


def test_a_repetition_that_returned_nothing_is_an_output_fact() -> None:
    """"The call succeeded but the model produced nothing" is a statement about
    what came back, which is what a correctness claim is made from."""
    from sirvis.benchmarks.engine import _generation_warnings

    scoped = _generation_warnings([_repetition(content="")])
    assert [scope for scope, _ in scoped] == [ValidityScope.OUTPUT]


def test_a_configuration_mismatch_taints_everything() -> None:
    """It ran under settings nobody asked for, so no metric on it answers the
    question that was asked — the engine's own wording."""
    from sirvis.benchmarks.engine import LoadedModel, _configuration_warnings

    class _Spec:
        model_key = "m"
        load = {"context_length": 32768}

    loaded = LoadedModel(
        model_key="m", state="loaded", effective={"context_length": 8192}, ignored=()
    )
    scoped = _configuration_warnings(_Spec(), [loaded])  # type: ignore[arg-type]

    assert [scope for scope, _ in scoped] == [ValidityScope.CONDITIONS]


def test_the_record_publishes_the_scopes_it_carries() -> None:
    """A reader must be able to ask "is the timing trustworthy" without parsing
    prose, which is the whole failure this replaces."""
    from sirvis.core.evidence import EvidenceIdentity, EvidenceRecord, Validity

    record = EvidenceRecord(
        identity=EvidenceIdentity(
            machine_id="m1", model_family="granite", model_variant="v",
            runtime="mlx", role="clarvis-agent", benchmark_suite="clarvis-agent",
            benchmark_version="1",
        ),
        validity=Validity.SUSPECT,
        validity_notes=("the machine reported thermal pressure 'fair'",),
        validity_scopes=(ValidityScope.TIMING,),
    )

    assert record.as_dict()["validity_scopes"] == ["TIMING"]
