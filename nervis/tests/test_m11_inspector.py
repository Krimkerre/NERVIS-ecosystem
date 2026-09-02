"""M11 — the API Inspector (§11.4).

The exit has four clauses and three of them are about restraint: credentials are
never displayed, content follows the privacy setting, and the two execution
paths are distinguished *honestly*. That last word is doing real work — §11.4
says outright **do not imply RAVIS normalized content it actually passed through
untouched** — so the stage list is chosen by the path rather than being one list
with rows left blank.
"""

from __future__ import annotations

from typing import Any

import pytest

from nervis import inspector
from nervis.storage import prepare_database


@pytest.fixture()
def database() -> Any:
    return prepare_database(":memory:")


def decision(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "decision_id": "d1", "request_id": "r1", "application_id": "nervis",
        "decided_at": "2026-09-02T15:00:00Z", "requested": "a-model", "pool": None,
        "selected": "a-model", "reason": "named directly by the client",
        "execution_path": "TRANSLATED_NATIVE", "fallbacks": [], "requirements": [],
        "excluded": [], "unverified": [], "considered": [f"m{n}" for n in range(40)],
        "execution": {"provider": "anthropic", "attempts": [
            {"model": "a-model", "outcome": "succeeded", "elapsed_ms": 640.0, "ttft_ms": 120.0}]},
    }
    base.update(over)
    return base


# ── The two paths ───────────────────────────────────────────────────────────


def test_a_transparent_route_has_no_normalized_request_row() -> None:
    """§11.4 verbatim: do not imply RAVIS normalized what it passed through.

    An empty row labelled "normalized request" is that implication. The row is
    absent rather than blank, which is the difference between "this did not
    happen" and "this happened and we have nothing".
    """
    names = [s.name for s in inspector.stages(decision(execution_path="TRANSPARENT"))]
    assert "normalized request" not in names
    assert "native events" not in names
    assert "upstream destination" in names


def test_a_translated_route_shows_every_stage_it_went_through() -> None:
    names = [s.name for s in inspector.stages(decision())]
    for expected in ("normalized request", "native provider request",
                     "native events", "normalized events"):
        assert expected in names


def test_an_unknown_path_is_treated_as_translated_rather_than_transparent() -> None:
    """The error worth avoiding is the one that shows fewer stages.

    A future `TRANSPARENT_CACHED` matched on a prefix would claim a path nobody
    verified and hide four stages; treated as translated it shows them, each
    labelled unpublished, which is recoverable.
    """
    assert not inspector.is_transparent("TRANSPARENT_CACHED")
    names = [s.name for s in inspector.stages(decision(execution_path="SOMETHING_NEW"))]
    assert "normalized request" in names


# ── Content follows the setting ─────────────────────────────────────────────


def test_content_is_withheld_until_the_setting_is_on() -> None:
    turn = {"request": "the question", "response": "the answer"}
    stages = inspector.stages(decision(), turn, allow_content=False)
    request = next(s for s in stages if s.name == "request")
    assert request.state == inspector.WITHHELD
    assert not request.content
    assert "the question" not in str(request.as_dict())


def test_content_appears_once_the_setting_is_on() -> None:
    turn = {"request": "the question", "response": "the answer"}
    stages = inspector.stages(decision(), turn, allow_content=True)
    request = next(s for s in stages if s.name == "request")
    assert request.state == inspector.HELD_BY_NERVIS
    assert request.content == "the question"


def test_the_setting_is_off_until_somebody_turns_it_on(database: Any) -> None:
    """The inspector's subject is the route; content is opt-in."""
    assert inspector.show_content(database) is False
    inspector.set_show_content(database, True)
    assert inspector.show_content(database) is True


# ── Absence has reasons, and they differ ────────────────────────────────────


def test_a_background_call_is_not_reported_as_somebody_elses_request() -> None:
    """It was NERVIS's request; it was simply not a chat turn.

    The title generator's decision sits one row above the chat it named, and
    calling it "not NERVIS's" is false. §9.6.1's background calls are NERVIS's
    own and are not stored as conversations.
    """
    stage = next(s for s in inspector.stages(decision(), None) if s.name == "request")
    assert stage.state == inspector.NOT_PUBLISHED
    assert "background call" in stage.detail


def test_another_applications_request_says_so() -> None:
    stage = next(
        s for s in inspector.stages(decision(application_id="clarvis"), None)
        if s.name == "request"
    )
    assert "NERVIS was not the client" in stage.detail


def test_an_unpublished_stage_names_what_is_missing_rather_than_being_dropped() -> None:
    """A stage list with honest gaps specifies the surface RAVIS lacks.

    Deleting the rows would quietly redefine §11.4 as whatever was easy to
    build, and nobody would ever notice the inspector was incomplete.
    """
    stage = next(s for s in inspector.stages(decision()) if s.name == "normalized request")
    assert stage.state == inspector.NOT_PUBLISHED
    assert "publishes no surface" in stage.detail


# ── Credentials, and the allowlist that keeps them out ──────────────────────


def test_only_allowlisted_fields_leave_the_decision() -> None:
    """A field RAVIS adds later reaches no screen until somebody adds it here.

    Written as an allowlist rather than a redaction filter because a filter has
    to anticipate the name of the thing it is hiding, and the next credential
    field will not be called `api_key`.
    """
    smuggled = decision(api_key="sk-live-should-never-appear",
                        authorization="Bearer nope", upstream_headers={"x-api-key": "no"})
    found = inspector.inspect(smuggled).as_dict()
    rendered = str(found)
    assert "sk-live-should-never-appear" not in rendered
    assert "Bearer nope" not in rendered
    assert set(found["decision"]) == set(inspector.DECISION_FIELDS)


def test_the_considered_list_is_counted_rather_than_printed() -> None:
    """550 models on this machine. A screen that prints them is one nobody reads."""
    found = inspector.inspect(decision()).as_dict()
    assert found["considered_count"] == 40
    assert len(found["considered_sample"]) == inspector.CONSIDERED_SAMPLE


def test_a_time_to_first_token_nobody_measured_is_not_reported_as_zero() -> None:
    """§6.3's rule: unknown values stay unknown.

    `ttft_ms` is null on a non-streamed call, and "0 ms to first token" would be
    a measurement nobody made — the fastest number on the screen, invented.
    """
    unstreamed = decision(execution={"provider": "x", "attempts": [
        {"model": "a-model", "outcome": "succeeded", "elapsed_ms": 640.0, "ttft_ms": None}]})
    stage = next(s for s in inspector.stages(unstreamed) if s.name == "stream metadata")
    assert "not measured" in stage.detail
    # Not a substring check: "640 ms end to end" contains "0 ms", which is how
    # the first version of this assertion failed on a correct implementation.
    assert "to first token" not in stage.detail.replace("time to first token not measured", "")


def test_a_sub_millisecond_timing_is_not_rounded_into_zero() -> None:
    """`round(0.42)` is 0, and "0 ms end to end" is a different claim.

    Seen on the real screen: a streamed call to a hosted provider reported as
    instantaneous. The number came from RAVIS; making it nonsense was NERVIS's
    doing, on the one screen whose purpose is showing what really happened.

    Whether 0.42 ms is *believable* for a network call is the reader's to judge
    — an inspector that silently corrects its subject is not an inspector.
    """
    quick = decision(execution={"provider": "x", "attempts": [
        {"model": "m", "outcome": "succeeded", "elapsed_ms": 0.42, "ttft_ms": 0.35}]})
    stage = next(s for s in inspector.stages(quick) if s.name == "stream metadata")
    assert "0.42 ms" in stage.detail
    assert "0 ms end to end" not in stage.detail
