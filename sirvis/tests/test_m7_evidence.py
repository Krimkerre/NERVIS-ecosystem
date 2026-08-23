"""M7 — the evidence schema, and Stage 4's exit criterion verbatim (§21.2).

    No scalar-only canonical score; individual repetitions preserved; median
    and spread available; ESTIMATED/UNKNOWN never become MEASURED.

Everything RAVIS eventually routes on comes out of these shapes, so a number
that overstates what was observed here becomes a wrong route on somebody else's
machine. Most of these tests are about what the schema *refuses* to represent.
"""

from __future__ import annotations

import pytest

from sirvis.core.evidence import (
    EvidenceIdentity,
    EvidenceKind,
    EvidenceRecord,
    Measurement,
    Provenance,
    RoleVerdict,
    TrialRate,
    Validity,
    combine,
)

MEASURED = Provenance(EvidenceKind.MEASURED, method="sirvis.benchmark.tokens_per_second.v1")
ESTIMATED = Provenance(EvidenceKind.ESTIMATED, method="interpolated from a comparable build")
UNKNOWN = Provenance(EvidenceKind.UNKNOWN, notes="never benchmarked on this machine")


def _identity(**overrides: object) -> EvidenceIdentity:
    base = {
        "machine_id": "machine-1",
        "model_family": "granite-4.0-h-tiny",
        "model_variant": "var_mlx_4bit",
        "runtime": "mlx",
        "role": "clarvis-agent",
        "benchmark_suite": "clarvis-agent",
        "benchmark_version": "1",
    }
    return EvidenceIdentity(**{**base, **overrides})  # type: ignore[arg-type]


# ── No scalar-only canonical score ───────────────────────────────────────────


def test_a_measurement_cannot_be_built_without_its_repetitions() -> None:
    """The exit criterion, enforced by there being no other constructor.

    A headline with no samples behind it is the thing §11.7 forbids, and the way
    to forbid it is to make the samples the input rather than an optional extra
    somebody omits under deadline.
    """
    with pytest.raises(ValueError, match="at least one repetition"):
        Measurement(repetitions=(), unit="tok/s", direction="higher", provenance=MEASURED)


def test_the_summary_always_travels_with_the_samples() -> None:
    """§11.7: store every repetition. A consumer that wants to know whether the
    headline means anything has to be able to see the spread behind it."""
    measurement = Measurement(
        repetitions=(38.4, 35.9, 40.8, 37.1, 39.0),
        unit="tok/s", direction="higher", provenance=MEASURED,
    )

    published = measurement.as_dict()

    assert published["repetitions"] == [38.4, 35.9, 40.8, 37.1, 39.0]
    assert published["median"] == 38.4
    assert published["samples"] == 5


def test_an_evidence_record_has_nowhere_to_put_a_score() -> None:
    """§12.2: evidence is never keyed as model → score.

    Structural rather than a review comment — metrics are named, united,
    directed and provenanced, and a single ranking number has no field to
    occupy.
    """
    record = EvidenceRecord(identity=_identity())

    assert "score" not in record.as_dict()
    assert not hasattr(record, "score")


# ── Median and spread, and the honesty of absence ────────────────────────────


def test_a_single_take_has_a_median_and_no_spread() -> None:
    """None rather than 0.0.

    One observation has no spread; reporting zero would claim perfect
    consistency measured once, which is the most misleading number this schema
    could produce.
    """
    single = Measurement((38.4,), unit="tok/s", direction="higher", provenance=MEASURED)

    assert single.median == 38.4
    assert single.spread is None


def test_percentiles_are_absent_below_the_sample_size_that_supports_them() -> None:
    """§11.7 asks for these "where sample size permits" — this is where it does
    not. With four takes a P10 describes the sample, not the thing sampled."""
    small = Measurement((1.0, 2.0, 3.0, 4.0), unit="s", direction="lower",
                        provenance=MEASURED)
    enough = Measurement((1.0, 2.0, 3.0, 4.0, 5.0), unit="s", direction="lower",
                         provenance=MEASURED)

    assert small.percentile(0.10) is None
    assert enough.percentile(0.10) is not None


def test_direction_is_required_because_a_unit_does_not_imply_it() -> None:
    """Without it a consumer cannot rank two numbers, and guessing from the unit
    is how tokens-per-second and time-to-first-token get sorted the same way."""
    with pytest.raises(ValueError, match="direction"):
        Measurement((1.0,), unit="s", direction="down", provenance=MEASURED)


# ── Provenance is never upgraded ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        ([EvidenceKind.MEASURED, EvidenceKind.MEASURED], EvidenceKind.MEASURED),
        ([EvidenceKind.MEASURED, EvidenceKind.ESTIMATED], EvidenceKind.PARTIALLY_MEASURED),
        ([EvidenceKind.MEASURED, EvidenceKind.UNKNOWN], EvidenceKind.PARTIALLY_MEASURED),
        ([EvidenceKind.ESTIMATED, EvidenceKind.UNKNOWN], EvidenceKind.ESTIMATED),
        ([EvidenceKind.UNKNOWN], EvidenceKind.UNKNOWN),
    ],
)
def test_aggregation_can_only_weaken_provenance(
    inputs: list[EvidenceKind], expected: EvidenceKind
) -> None:
    """§12.1: aggregation cannot promote evidence."""
    assert combine(inputs) is expected


def test_aggregating_nothing_is_unknown_rather_than_measured() -> None:
    """The vacuous-truth bug: "all zero inputs were measured" is technically
    true and completely wrong. Aggregating no evidence produces no evidence."""
    assert combine([]) is EvidenceKind.UNKNOWN


def test_a_records_provenance_is_derived_and_cannot_be_asserted() -> None:
    """If it were a field, a caller could write MEASURED onto a record holding
    an estimate — the one thing §12.1 says cannot happen."""
    record = EvidenceRecord(
        identity=_identity(),
        measurements={
            "generation_tok_s": Measurement((38.4, 39.1), unit="tok/s", direction="higher",
                                            provenance=MEASURED),
            "memory_peak_gb": Measurement((4.3,), unit="GB", direction="lower",
                                          provenance=ESTIMATED),
        },
    )

    assert record.evidence_type is EvidenceKind.PARTIALLY_MEASURED
    with pytest.raises(AttributeError):
        record.evidence_type = EvidenceKind.MEASURED  # type: ignore[misc]


def test_a_consumer_that_does_not_know_partially_measured_reads_it_as_estimated() -> None:
    """§12.1, verbatim: as ESTIMATED, never as MEASURED."""
    assert (
        EvidenceKind.PARTIALLY_MEASURED.for_consumers_without_partial()
        is EvidenceKind.ESTIMATED
    )
    assert EvidenceKind.MEASURED.for_consumers_without_partial() is EvidenceKind.MEASURED


def test_a_measured_value_must_name_its_method() -> None:
    """A MEASURED claim nobody can reproduce or compare is not evidence."""
    with pytest.raises(ValueError, match="name the method"):
        Provenance(EvidenceKind.MEASURED)


def test_an_unknown_value_must_say_why_it_is_unknown() -> None:
    """Otherwise it tells a reader nothing the absence of a value did not."""
    with pytest.raises(ValueError, match="why it is unknown"):
        Provenance(EvidenceKind.UNKNOWN)


# ── Identity: evidence is about a build, not a model ─────────────────────────


def test_two_packagings_of_one_model_are_different_evidence() -> None:
    """§12.2's own example, and this machine's most consequential pair.

    The same weights as MLX 4-bit and as GGUF Q4_K_M reach 1/8 and 8/8 on the
    same tool-call trial. Sharing an evidence key would make one of those two
    results overwrite the other.
    """
    mlx = _identity(model_variant="var_mlx", model_format="mlx", quantization="4bit",
                    runtime="mlx")
    gguf = _identity(model_variant="var_gguf", model_format="gguf", quantization="Q4_K_M",
                     runtime="llama.cpp")

    assert mlx.evidence_id != gguf.evidence_id


def test_the_same_conditions_produce_the_same_key() -> None:
    """Two runs of one suite against one build are recognisably about the same
    thing, without a table to remember it."""
    assert _identity().evidence_id == _identity().evidence_id


def test_configuration_order_does_not_change_the_key() -> None:
    """A dictionary's insertion order is not part of what a configuration is.

    Without canonicalising, the same 32K run would produce two evidence IDs
    depending on which key happened to be written first.
    """
    one = _identity(runtime_configuration={"context_length": 32768, "gpu_offload": "max"})
    other = _identity(runtime_configuration={"gpu_offload": "max", "context_length": 32768})

    assert one.evidence_id == other.evidence_id


def test_changing_the_role_changes_the_evidence() -> None:
    """§12.2 puts role in the identity: the same build measured for chat is not
    evidence about it as an agent."""
    assert _identity(role="clarvis-chat").evidence_id != _identity(role="clarvis-agent").evidence_id


def test_changing_the_context_length_changes_the_evidence() -> None:
    """The distinction M2 found in the wild: a build loaded at 8192 is not the
    build loaded at 32768, and measuring one says nothing about the other."""
    small = _identity(runtime_configuration={"context_length": 8192})
    large = _identity(runtime_configuration={"context_length": 32768})

    assert small.evidence_id != large.evidence_id


# ── Trial rates, validity and verdicts ───────────────────────────────────────


def test_a_pass_rate_keeps_the_attempt_count_not_just_the_ratio() -> None:
    """23/24 and 230/240 are the same rate and different amounts of evidence."""
    rate = TrialRate(passed=23, total=24, provenance=MEASURED, phrasings=8,
                     repetitions_each=3)

    assert rate.rate == pytest.approx(0.9583, abs=1e-4)
    assert rate.as_dict()["passed"] == 23
    assert rate.as_dict()["total"] == 24


def test_validity_is_separate_from_provenance() -> None:
    """§11.8: a number can be genuinely MEASURED and still not comparable.

    Taken while the machine was thermally throttled, or swapping. Collapsing the
    two would leave a consumer unable to tell "we did not measure this" from
    "we measured it under conditions that make it useless".
    """
    record = EvidenceRecord(
        identity=_identity(),
        measurements={"tok_s": Measurement((9.1, 9.4), unit="tok/s", direction="higher",
                                           provenance=MEASURED)},
        validity=Validity.SUSPECT,
        validity_notes=("swap grew by 4 GB during the run",),
    )

    assert record.evidence_type is EvidenceKind.MEASURED
    assert record.as_dict()["validity"] == "SUSPECT"
    assert record.as_dict()["validity_notes"] == ["swap grew by 4 GB during the run"]


def test_a_verdict_belongs_to_a_build_and_a_role_not_a_family() -> None:
    """§12.5: do not mark the whole family bad.

    One build can pass for chat and fail for agent on tool-call reliability,
    which is precisely what this machine's granite packagings do.
    """
    verdict = RoleVerdict(
        identity=_identity(role="clarvis-agent",
                           runtime_configuration={"context_length": 32768}),
        passed=False,
        reason="tool-call reliability 1/8, below the threshold",
    )

    published = verdict.as_dict()

    assert published["verdict"] == "FAIL"
    assert published["role"] == "clarvis-agent"
    assert published["runtime_config"] == {"context_length": 32768}
    assert "model_family" not in published


def test_a_failing_verdict_must_say_why() -> None:
    """A bare FAIL is unactionable, and §12.5's example is a reason, not a flag."""
    with pytest.raises(ValueError, match="must say why"):
        RoleVerdict(identity=_identity(), passed=False, reason="")


def test_the_envelope_matches_the_shape_the_spec_publishes() -> None:
    """§12.3's envelope, so a consumer written against the document works."""
    record = EvidenceRecord(
        identity=_identity(runtime_configuration={"context_length": 32768}),
        measurements={
            "generation_tok_s": Measurement((38.4, 35.9, 40.8, 37.1, 39.0),
                                            unit="tok/s", direction="higher",
                                            provenance=MEASURED)
        },
        rates={
            "tool_call_pass_rate": TrialRate(passed=23, total=24, provenance=MEASURED,
                                             phrasings=8, repetitions_each=3)
        },
    )

    body = record.as_dict()

    assert body["evidence_type"] == "MEASURED"
    assert body["role"] == "clarvis-agent"
    assert body["target"]["runtime_config"] == {"context_length": 32768}
    assert body["suite"] == {"id": "clarvis-agent", "version": "1"}
    assert body["samples"] == 5
    assert body["metrics"]["generation_tok_s"]["median"] == 38.4
    assert body["metrics"]["tool_call_pass_rate"]["total"] == 24
    assert body["validity"] == "VALID"
