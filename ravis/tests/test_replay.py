"""Routing a stored decision again, against today's world, calling nobody (M21).

The owner chose the dry run on 18 September 2026 over a replay that really calls
a provider: a real one costs money per replay and needs the prompt, which RAVIS
does not keep. So these tests are about two things — that the answer is about
*this* request rather than a generic one, and that nothing leaves the process.
"""

from __future__ import annotations

from typing import Any

from tests.test_fallback import (
    TWO_CODERS,
    ScriptedUpstream,
    _app_with,
    _state,
    _with_tools,
)

from ravis.api.catalogue import UPSTREAM_PROVIDER
from ravis.reliability.failures import FailureClass

# One model that can call tools and one that cannot, so the agent pool has a
# choice to make and losing the capable one changes the answer.
ONE_CODER: dict[str, dict[str, str]] = {
    "coder-a": TWO_CODERS["coder-a"],
    "talker": {"tools": "UNSUPPORTED", "context_window": "32000"},
}
PLAIN: dict[str, Any] = {"model": "talker", "messages": [{"role": "user", "content": "hi"}]}


def _decide(client: Any, body: dict[str, Any]) -> str:
    """Make one routing decision through the real chat path, and return its id."""
    client.post("/v1/chat/completions", json=body)
    items = client.get("/api/v1/route-decisions?limit=1").json()["items"]
    return str(items[0]["decision_id"])


def test_a_replay_says_what_would_be_chosen_now() -> None:
    upstream = ScriptedUpstream(ONE_CODER)
    with _app_with(upstream) as client:
        decision_id = _decide(client, PLAIN)
        answer = client.get(f"/api/v1/route-decisions/{decision_id}/replay").json()

    assert answer["then"]["selected"] == "talker"
    assert answer["now"]["selected"] == "talker"
    assert answer["changed"] is False


def test_a_replay_contacts_no_provider() -> None:
    """The whole point of the dry run, asserted rather than assumed.

    The upstream records who asked it for a completion. Routing is a pure
    function of what it is handed, so a replay *cannot* call out — this is the
    test that notices if that ever stops being true.
    """
    upstream = ScriptedUpstream(ONE_CODER)
    with _app_with(upstream) as client:
        decision_id = _decide(client, PLAIN)
        served = list(upstream.served)
        client.get(f"/api/v1/route-decisions/{decision_id}/replay")

    assert upstream.served == served


def test_a_replay_keeps_the_requirements_the_request_arrived_with() -> None:
    """A request carrying tools must still require tools when it is routed again.

    Without the stored constraints the replay would route an unconstrained
    request — and answer confidently about a decision nobody ever made.
    """
    upstream = ScriptedUpstream(ONE_CODER)
    with _app_with(upstream) as client:
        decision_id = _decide(client, _with_tools())
        answer = client.get(f"/api/v1/route-decisions/{decision_id}/replay").json()

    assert any("tools REQUIRED" in line for line in answer["now"]["requirements"])
    assert answer["now"]["selected"] == "coder-a"


def test_a_replay_notices_that_the_answer_would_be_different_today() -> None:
    """The question replay exists to answer: would RAVIS still choose that?

    The pool's only tool-capable model has since gone unavailable, which is the
    ordinary version of this — a model withdrawn, a provider down, a price that
    moved a build out of a cheap pool's ceiling.
    """
    upstream = ScriptedUpstream(ONE_CODER)
    with _app_with(upstream, breaker_failure_threshold=1) as client:
        decision_id = _decide(client, _with_tools())
        health = _state(client).health
        health.record(FailureClass.MODEL_UNAVAILABLE, "coder-a", UPSTREAM_PROVIDER,
                      health.clock())
        answer = client.get(f"/api/v1/route-decisions/{decision_id}/replay").json()

    assert answer["then"]["selected"] == "coder-a"
    assert answer["now"]["selected"] is None, "the pool's only tool model is unavailable"
    assert answer["changed"] is True


def test_a_replay_reads_todays_evidence_rather_than_the_old_decisions() -> None:
    """Evidence admits and excludes candidates, so leaving it out answers wrongly.

    The agent pool routed to `coder-a` when SIRVIS had measured nothing. An
    operator narrowing the pool to the other model is a fact about today in the
    same way a withdrawn model is, and the replay has to see it.
    """
    upstream = ScriptedUpstream(ONE_CODER)
    with _app_with(upstream) as client:
        decision_id = _decide(client, _with_tools())
        _state(client).pool_membership.set_for("ravis/clarvis-agent", ("talker",))
        answer = client.get(f"/api/v1/route-decisions/{decision_id}/replay").json()

    assert answer["then"]["selected"] == "coder-a"
    assert answer["now"]["selected"] is None, "the operator's pick cannot call tools"
    assert answer["changed"] is True


def test_a_replay_names_what_it_did_not_reproduce() -> None:
    """Silence about session affinity would read as "affinity did not apply"."""
    upstream = ScriptedUpstream(ONE_CODER)
    with _app_with(upstream) as client:
        decision_id = _decide(client, PLAIN)
        answer = client.get(f"/api/v1/route-decisions/{decision_id}/replay").json()

    assert any("session affinity" in line for line in answer["not_reproduced"])
    assert "nothing was sent to a provider" in answer["note"]


def test_replaying_an_unknown_decision_says_there_is_nothing_to_replay() -> None:
    upstream = ScriptedUpstream(ONE_CODER)
    with _app_with(upstream) as client:
        answer = client.get("/api/v1/route-decisions/doesnotexist/replay")

    assert answer.status_code == 404
    assert "nothing to route again" in answer.json()["error"]["message"]


def test_a_stored_decision_carries_no_message() -> None:
    """§9.7 and runbook §9: identifiers and reasons, never content.

    Replay made the store richer, which is exactly when this needs asserting:
    the constraints are derived facts, and a prompt reaching the table would be
    a privacy change made by accident.
    """
    upstream = ScriptedUpstream(ONE_CODER)
    secret = "SECRET-PROMPT-DO-NOT-STORE"
    with _app_with(upstream) as client:
        _decide(client, {"model": "talker", "messages": [{"role": "user", "content": secret}]})
        rows = _state(client).decision_log.database.connection.execute(
            "SELECT explanation FROM route_decision"
        ).fetchall()

    assert rows, "nothing was stored to check"
    assert all(secret not in row["explanation"] for row in rows)
