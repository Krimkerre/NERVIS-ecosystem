"""RAVIS reads the validity SIRVIS puts on a record (§16 item 7).

SIRVIS does its half correctly: a run whose effective configuration differs
from the requested one is marked `SUSPECT` with a note, and its engine says why
— "the only unacceptable outcome is publishing it as though it answered the
question that was asked".

RAVIS ingested that field and never read it. On the live machine 44 of 93
records are `SUSPECT`, and every one of them fed routing exactly as the 49 clean
ones did. The same shape as the TLS paths in §16 item 2 and the credential in
item 4: a value computed correctly and then applied nowhere.
"""

from __future__ import annotations

from ravis.core.capabilities import CapabilityState
from ravis.evidence.sirvis import EvidenceProvenance, EvidenceRecord, tool_verdict


def _record(validity: str, passed: int = 24, total: int = 24) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id="ev_test",
        runtime_key="rk",
        machine_id="m1",
        role="clarvis-agent",
        model_family="granite",
        variant="var",
        model_format="mlx",
        quantization="4bit",
        runtime="mlx",
        runtime_configuration={},
        suite="clarvis-agent",
        suite_version="1",
        provenance=EvidenceProvenance.MEASURED_BY_SIRVIS,
        validity=validity,
        measured_at="2026-09-04T00:00:00Z",
        age_seconds=1.0,
        samples=total,
        metrics={
            "tool_call_well_formed": {
                "passed": passed, "total": total, "phrasings": 8,
            },
        },
    )


def test_an_invalid_record_cannot_establish_a_capability() -> None:
    """§13.3's rule — "never upgrade; an estimate cannot establish an invariant"
    — applies to a measurement SIRVIS itself disowned, for the same reason.

    Nothing on the live machine is `INVALID` today, which is exactly why this is
    worth pinning now: the guard costs nothing while the case is empty, and the
    first record that arrives with it must not silently route.
    """
    verdict = tool_verdict(_record("INVALID"))

    assert verdict.state is not CapabilityState.SUPPORTED
    assert "invalid" in verdict.detail.lower()


def test_a_suspect_record_still_counts_and_says_so() -> None:
    """Deliberately *not* demoted, and the reasoning matters more than the code.

    21 of the suspect records on this machine are suspect because the machine was
    thermally throttled. That undermines a *rate* — it says nothing about whether
    the model formed well-formed tool calls. Demoting the capability on it would
    discard correct evidence because the room was warm.

    Mapping a warning to the metric it actually undermines is SIRVIS's to
    express, not RAVIS's to infer from prose. Until it does, a suspect record
    counts and the route explanation says it was suspect.
    """
    verdict = tool_verdict(_record("SUSPECT"))

    assert verdict.state is CapabilityState.SUPPORTED


def test_a_valid_record_is_unaffected() -> None:
    assert tool_verdict(_record("VALID")).state is CapabilityState.SUPPORTED


def test_a_suspect_record_is_named_as_one_in_the_explanation() -> None:
    """Counting it is not the same as hiding it.

    §9.7 asks a route to explain itself. A capability resting on a measurement
    its own producer flagged is precisely what that explanation is for — without
    this, a suspect record arrives looking exactly as clean as a clean one.
    """
    verdict = tool_verdict(_record("SUSPECT"))

    assert verdict.state is CapabilityState.SUPPORTED
    assert "SUSPECT" in verdict.detail


def test_a_clean_record_carries_no_caveat() -> None:
    """The falsifier: a caveat on everything would say nothing."""
    assert "SUSPECT" not in tool_verdict(_record("VALID")).detail
