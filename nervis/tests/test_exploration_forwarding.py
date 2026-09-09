"""The two exploration switches reaching RAVIS, which is the half that can rot.

The switches themselves live in chat's Model settings and the decision they
affect is made inside RAVIS. NERVIS's entire job is to carry them, and carrying
is exactly the kind of work that breaks silently: a field the browser sets, that
looks right in the form and in the request, and that the thing in the middle
quietly drops.

`FORWARDED` exists to prevent that for sampling parameters. These two are not
sampling parameters -- they change routing, not generation -- so they are easy
to leave out of a list whose name and neighbours are all about temperature and
top-p. Hence a test rather than trust.
"""

from __future__ import annotations

from nervis.api.chat import FORWARDED, _completion_payload


def _sent(**asked: object) -> dict:
    return _completion_payload(dict(asked), "ravis/chat", [], "how fast is it?")


def test_both_switches_travel_to_ravis() -> None:
    payload = _sent(explore=True, explore_prefer_unmeasured=True)

    assert payload["explore"] is True
    assert payload["explore_prefer_unmeasured"] is True


def test_off_still_travels_rather_than_vanishing() -> None:
    """**False is a decision and absent is not.** RAVIS reads a missing
    `explore` as "no", so this happens to be harmless today -- but the second
    switch is read as *true* when absent, so a `False` silently dropped here
    would turn preferring-unmeasured back on behind the person who switched it
    off."""
    payload = _sent(explore=True, explore_prefer_unmeasured=False)

    assert payload["explore_prefer_unmeasured"] is False


def test_a_turn_that_asked_for_neither_sends_neither() -> None:
    """Only what was supplied travels. NERVIS does not fill in a default it was
    never given -- inventing one here would be NERVIS making a routing choice
    nobody made."""
    payload = _sent()

    assert "explore" not in payload
    assert "explore_prefer_unmeasured" not in payload


def test_they_are_listed_where_the_forwarding_actually_happens() -> None:
    """Pins the mechanism, not just today's outcome: the payload builder passes
    exactly the names in `FORWARDED`, so a rename that misses this list would
    otherwise show up as a switch that silently stopped working."""
    assert "explore" in FORWARDED
    assert "explore_prefer_unmeasured" in FORWARDED
