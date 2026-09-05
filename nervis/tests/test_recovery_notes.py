"""Services that recover together are one note, not one each.

**Reported from a real boot:** three notes in a row — *"RAVIS is back to
healthy"*, *"SIRVIS is back to healthy"*, *"code-server is back to healthy"* —
for a single event, which is the stack coming up. Three lines saying one thing is
how a notification centre teaches somebody to close it without reading, and this
centre already has the scars: it once opened with four notes about Ollama on a
machine that never had Ollama.

Coalescing is per sweep and per destination state, which is the honest grouping:
one probe pass is one observation of the ecosystem, and services that moved to
different states did not have the same thing happen to them.
"""

from __future__ import annotations

from nervis.app import WRITTEN_STATE, _phrase_for


def test_one_service_reads_as_it_always_did() -> None:
    assert _phrase_for(["RAVIS"], "healthy") == "RAVIS is back to healthy"


def test_two_services_are_joined_with_and() -> None:
    assert _phrase_for(["RAVIS", "SIRVIS"], "healthy") == "RAVIS and SIRVIS are back to healthy"


def test_three_services_read_as_a_list() -> None:
    """The exact sentence from the boot that prompted this."""
    assert _phrase_for(["RAVIS", "SIRVIS", "code-server"], "healthy") == (
        "RAVIS, SIRVIS and code-server are back to healthy")


def test_the_verb_agrees_for_every_state_a_service_can_reach() -> None:
    """Plural is not "is" with an s.

    Each state has its own two forms because English does not derive them: "has
    stopped answering" becomes "have stopped answering", and a rule that
    appended a letter would produce "has stopped answerings".
    """
    for state in WRITTEN_STATE:
        one = _phrase_for(["A"], state)
        many = _phrase_for(["A", "B"], state)
        assert one.startswith("A "), one
        assert many.startswith("A and B "), many
        assert one != many.replace("A and B", "A"), f"{state} reads identically in both forms"


def test_an_unknown_state_still_produces_a_sentence() -> None:
    """A state nobody wrote a phrase for is a state that still happened."""
    assert _phrase_for(["RAVIS"], "quarantined") == "RAVIS is now quarantined"
    assert _phrase_for(["A", "B"], "quarantined") == "A and B are now quarantined"


def test_services_that_moved_differently_are_not_merged() -> None:
    """Grouped by destination, because that is what makes them one event.

    A RAVIS that came back and a SIRVIS that stopped answering in the same sweep
    are two things, and one line saying both would be the flood's opposite
    failure: a sentence nobody can act on.
    """
    from nervis.app import _grouped

    changes = [("RAVIS", "healthy"), ("SIRVIS", "unreachable"), ("code-server", "healthy")]
    assert _grouped(changes) == {
        "healthy": ["RAVIS", "code-server"],
        "unreachable": ["SIRVIS"],
    }
