"""The capability model: what is known, how well, and what fails closed."""

from __future__ import annotations

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
)


def _claim(state: CapabilityState, provenance: Provenance) -> CapabilityClaim:
    return CapabilityClaim(capability=Capability.TOOLS, state=state, provenance=provenance)


def test_an_unrecorded_capability_is_unknown() -> None:
    """Not having asked is a state, and a different one from a negative answer."""
    assert ModelCapabilities("m").state_of(Capability.TOOLS) is CapabilityState.UNKNOWN


def test_unknown_does_not_satisfy_a_requirement() -> None:
    """§9.1: an unknown capability fails closed where a pool requires it."""
    assert ModelCapabilities("m").satisfies(Capability.TOOLS) is False


def test_partial_does_not_satisfy_a_requirement() -> None:
    """A pool invariant is a promise, and "sometimes" breaks it."""
    known = ModelCapabilities("m")
    known.record(_claim(CapabilityState.PARTIAL, Provenance.MEASURED))

    assert known.satisfies(Capability.TOOLS) is False


def test_measurement_beats_advertisement() -> None:
    """The disagreement this whole design exists for.

    A model advertising tool support while losing most tool calls to its
    runtime's parser was observed on a real machine. The measurement has to win.
    """
    known = ModelCapabilities("m")
    known.record(_claim(CapabilityState.SUPPORTED, Provenance.ADVERTISED))
    known.record(_claim(CapabilityState.UNSUPPORTED, Provenance.MEASURED))

    assert known.state_of(Capability.TOOLS) is CapabilityState.UNSUPPORTED


def test_advertisement_does_not_overwrite_a_measurement() -> None:
    """The same rule in the other direction: re-reading a catalogue must not
    undo what was measured."""
    known = ModelCapabilities("m")
    known.record(_claim(CapabilityState.SUPPORTED, Provenance.MEASURED))
    known.record(_claim(CapabilityState.UNSUPPORTED, Provenance.ADVERTISED))

    assert known.state_of(Capability.TOOLS) is CapabilityState.SUPPORTED


def test_configuration_beats_measurement() -> None:
    """An operator knows things about their deployment RAVIS cannot observe."""
    known = ModelCapabilities("m")
    known.record(_claim(CapabilityState.UNSUPPORTED, Provenance.MEASURED))
    known.record(_claim(CapabilityState.SUPPORTED, Provenance.CONFIGURED))

    assert known.state_of(Capability.TOOLS) is CapabilityState.SUPPORTED


def test_an_equal_ranked_claim_does_not_churn_the_incumbent() -> None:
    """Re-reading the same source should not rewrite what is already held."""
    known = ModelCapabilities("m")
    known.record(CapabilityClaim(Capability.TOOLS, CapabilityState.SUPPORTED,
                                 Provenance.MEASURED, detail="first"))
    known.record(CapabilityClaim(Capability.TOOLS, CapabilityState.SUPPORTED,
                                 Provenance.MEASURED, detail="second"))

    assert known.claims[Capability.TOOLS].detail == "first"


def test_an_unknown_context_window_does_not_meet_a_minimum() -> None:
    """§12 forbids silently truncating context; an unknown ceiling fails closed."""
    assert ModelCapabilities("m").meets_context(32768) is False


def test_a_known_context_window_is_compared_honestly() -> None:
    known = ModelCapabilities("m", context_window=32768)

    assert known.meets_context(32768) is True
    assert known.meets_context(32769) is False
