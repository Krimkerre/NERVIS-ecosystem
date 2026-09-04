"""A result says what was asked for as well as what ran (§16 item 7).

SIRVIS already refuses to publish a mismatch as a clean measurement: it
compares the requested load against what the runtime reports resident, records
a validity warning, and marks the record `SUSPECT`. The engine's own docstring
states the principle better than the audit did — "a benchmark run at 8K on a
request for 32K is a perfectly good measurement — of a model at 8K — and the
only unacceptable outcome is publishing it as though it answered the question
that was asked".

What was missing is the *data*. The stored configuration is
`{**spec.load, **effective}`, so effective values overwrite requested ones and
only the merged result survives. A reader sees `context_length: 8192` and cannot
tell whether 8192 was asked for or was the runtime overriding a request for
32768 — the prose warning says so, and prose is not something a router can read.
"""

from __future__ import annotations

from typing import Any

import pytest

from sirvis.core.evidence import EvidenceIdentity, EvidenceRecord


def _record(requested: dict[str, Any], effective: dict[str, Any]) -> EvidenceRecord:
    """A record shaped like the engine builds one, with the two configs split."""
    return EvidenceRecord(
        identity=EvidenceIdentity(
            machine_id="machine-1",
            model_family="granite-4.0-h-tiny",
            model_variant="var_mlx_4bit",
            runtime="mlx",
            role="clarvis-agent",
            benchmark_suite="clarvis-agent",
            benchmark_version="1",
            runtime_configuration=effective,
        ),
        requested_configuration=requested,
    )


@pytest.fixture()
def sample_record() -> EvidenceRecord:
    """Asked for 32768, ran at 8192 — the case the warning describes in prose."""
    return _record({"context_length": 32768}, {"context_length": 8192})


@pytest.fixture()
def matching_record() -> EvidenceRecord:
    return _record({"context_length": 8192}, {"context_length": 8192})


def test_the_record_carries_what_was_asked_for(sample_record: Any) -> None:
    """Both, side by side, so the comparison needs no parsing."""
    published = sample_record.as_dict()

    assert "requested_configuration" in published
    assert published["requested_configuration"]["context_length"] == 32768
    # The effective one keeps its existing home and meaning — identity is keyed
    # on what ran (§12.2), and this must not move it.
    assert published["target"]["runtime_config"]["context_length"] == 8192


def test_a_matching_run_says_so_rather_than_omitting_it(matching_record: Any) -> None:
    """Absent would be ambiguous: "asked for nothing" and "asked for what it got"
    are different facts, and a reader cannot distinguish them from a missing key."""
    published = matching_record.as_dict()

    assert published["requested_configuration"]["context_length"] == 8192
