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

from sirvis.core.evidence import ValidityScope


def test_thermal_pressure_is_timing_only() -> None:
    """The case that makes the whole distinction worth having."""
    from sirvis.benchmarks.engine import _thermal_warnings

    assert _thermal_warnings.scope is ValidityScope.TIMING


def test_swap_is_timing_only() -> None:
    from sirvis.benchmarks.engine import _swap_warnings

    assert _swap_warnings.scope is ValidityScope.TIMING


def test_generation_trouble_is_about_the_output() -> None:
    """Tokens that never arrived as content are a fact about what came back."""
    from sirvis.benchmarks.engine import _generation_warnings

    assert _generation_warnings.scope is ValidityScope.OUTPUT


def test_a_configuration_mismatch_taints_everything() -> None:
    """It ran under settings nobody asked for, so no metric on it answers the
    question that was asked — which is the engine's own wording."""
    from sirvis.benchmarks.engine import _configuration_warnings

    assert _configuration_warnings.scope is ValidityScope.CONDITIONS


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

    published = record.as_dict()
    assert published["validity_scopes"] == ["TIMING"]
