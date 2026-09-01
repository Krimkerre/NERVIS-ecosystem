"""What became of each offer (NERVIS.md M22).

M22's difficulty is not the table. It is three rules that a working
implementation breaks without any symptom:

  1. **No outcome is inferred from silence.** A person who closed the tab did
     not decline. So an unanswered offer must leave no row, and there must be no
     code path that could produce one later.
  2. **A preference is visible in the proposal that uses it**, never applied
     behind it. What is remembered decorates an offer; it never composes one.
  3. **Clearing restores the unlearned proposal exactly.** Which is only true if
     nothing about composing a proposal ever read the record in the first place.

The third is tested by composing the same proposal either side of a record —
if `propose` ever grows a database argument, this file fails.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis import commands, proposals
from nervis.app import create_app
from nervis.config import Settings
from nervis.storage.database import prepare_database

# One model, so a proposal has something unambiguous to name.
CATALOGUE = [{"model_id": "qwen3-4b", "state": "available", "local": True}]


@pytest.fixture()
def database() -> Any:
    return prepare_database(":memory:")


@pytest.fixture()
def client(tmp_path: Any) -> Any:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"),
        workspace_path=str(tmp_path),
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        yield client


# ── Silence is not an answer ────────────────────────────────────────────────


def test_an_unanswered_offer_leaves_no_record(database: Any) -> None:
    """The whole of rule 1, stated as the absence it is.

    A proposal is made and nothing is done with it. There is no sweeper, no
    expiry, no "stale offers count as declined" — so there is nothing to assert
    except that the record stays empty, which is exactly the point.
    """
    offer = commands.propose("benchmark qwen3-4b", CATALOGUE)
    assert offer is not None
    assert proposals.recent(database) == []
    assert proposals.history(database, offer.operation, offer.target).answered == 0


def test_nothing_can_record_an_outcome_that_is_not_an_answer(database: Any) -> None:
    """`ignored`, `expired` and `timed_out` are all the same mistake."""
    for invented in ("ignored", "expired", "timed_out", "abandoned"):
        with pytest.raises(ValueError, match="not one of"):
            proposals.record(
                database, proposal_id="p", operation="o", outcome=invented
            )


# ── The three real outcomes ─────────────────────────────────────────────────


def test_an_edit_must_say_what_it_was_changed_to(database: Any) -> None:
    """`edited` is the only outcome carrying what somebody actually wanted.

    Recording it without `edited_to` keeps the fact that they changed their mind
    and throws away what they changed it to, which is the half worth having.
    """
    with pytest.raises(ValueError, match="changed to"):
        proposals.record(
            database, proposal_id="p", operation="o", outcome="edited"
        )
    proposals.record(
        database, proposal_id="p", operation="o", target="a",
        outcome="edited", edited_to="b",
    )
    assert proposals.history(database, "o", "a").last_edited_to == "b"


def test_one_decision_counts_once(database: Any) -> None:
    """A second press on a slow connection is not a second opinion."""
    for _ in range(3):
        proposals.record(
            database, proposal_id="p", operation="o", target="t", outcome="declined"
        )
    assert proposals.history(database, "o", "t").declined == 1


def test_history_does_not_generalise_across_targets(database: Any) -> None:
    """Refusing to benchmark one model says nothing about another.

    A record that generalised would be NERVIS inventing a preference nobody
    expressed — and it would say "you have declined this twice" about an offer
    the person had never seen.
    """
    for note in ("p1", "p2"):
        proposals.record(
            database, proposal_id=note, operation="sirvis.benchmark.submit",
            target="qwen3-4b", outcome="declined",
        )
    assert proposals.history(database, "sirvis.benchmark.submit", "qwen3-4b").declined == 2
    other = proposals.history(database, "sirvis.benchmark.submit", "granite-4-micro")
    assert other.answered == 0
    assert other.sentence() == ""


# ── What is learned is said, not applied ────────────────────────────────────


def test_a_single_decline_says_nothing(database: Any) -> None:
    """One refusal is a person who did not want it that once."""
    proposals.record(
        database, proposal_id="p", operation="o", target="t", outcome="declined"
    )
    assert proposals.history(database, "o", "t").sentence() == ""


def test_a_pattern_is_stated_as_a_fact_the_person_can_check(database: Any) -> None:
    """"You declined this twice" is checkable; "you probably don't want this"
    is NERVIS having an opinion about somebody."""
    for note in ("p1", "p2"):
        proposals.record(
            database, proposal_id=note, operation="o", target="t", outcome="declined"
        )
    said = proposals.history(database, "o", "t").sentence()
    assert said == "you have declined this 2 times"


def test_composing_a_proposal_never_reads_the_record(database: Any) -> None:
    """Rule 3, tested where it is actually true rather than where it is claimed.

    `propose` takes no database and never will — so a cleared record cannot
    leave anything behind to unlearn, because nothing about composing an offer
    consulted it. If this signature ever grows a store, that guarantee is gone
    and this test is how it gets noticed.
    """
    before = commands.propose("benchmark qwen3-4b", CATALOGUE)
    for note in ("p1", "p2", "p3"):
        proposals.record(
            database, proposal_id=note, operation="sirvis.benchmark.submit",
            target="qwen3-4b", outcome="declined",
        )
    after = commands.propose("benchmark qwen3-4b", CATALOGUE)
    assert before is not None and after is not None
    assert before.as_dict() == after.as_dict()

    proposals.forget(database)
    assert commands.propose("benchmark qwen3-4b", CATALOGUE).as_dict() == before.as_dict()


def test_forgetting_takes_all_of_it(database: Any) -> None:
    """All or nothing. A partial forget leaves a record whose *shape* is a
    preference — the ones somebody chose to keep — and no offer card can show
    that."""
    for note, target in (("p1", "a"), ("p2", "b")):
        proposals.record(
            database, proposal_id=note, operation="o", target=target, outcome="declined"
        )
    assert proposals.forget(database) == 2
    assert proposals.recent(database) == []


# ── The API ─────────────────────────────────────────────────────────────────


def test_an_offer_carries_an_id_before_anybody_answers_it(client: TestClient) -> None:
    """The id is minted when the offer is made, not when it is answered.

    Necessarily: the answer has to have something to be filed against. It is
    also what makes silence recordable-as-nothing — the id exists, and no row
    ever appears under it.
    """
    from nervis.api import chat as chat_api

    offer = chat_api._remembered(
        client.app.state.database,  # type: ignore[attr-defined]
        commands.propose("benchmark qwen3-4b", CATALOGUE),
    )
    assert offer is not None
    assert offer.proposal_id.startswith("pr_")
    assert offer.history is None  # nothing has happened to it yet


def test_the_offer_carries_its_own_past_once_there_is_one(client: TestClient) -> None:
    from nervis.api import chat as chat_api

    database = client.app.state.database  # type: ignore[attr-defined]
    plain = commands.propose("benchmark qwen3-4b", CATALOGUE)
    assert plain is not None
    for note in ("p1", "p2"):
        proposals.record(
            database, proposal_id=note, operation=plain.operation,
            target=plain.target, outcome="declined",
        )
    decorated = chat_api._remembered(database, plain)
    assert decorated is not None
    assert decorated.history is not None
    assert decorated.history["sentence"] == "you have declined this 2 times"
    # …and the offer itself is untouched. Decoration, not composition.
    assert decorated.summary == plain.summary
    assert decorated.target == plain.target
    assert decorated.ready == plain.ready


def test_the_endpoint_refuses_an_invented_outcome(client: TestClient) -> None:
    answer = client.post("/api/v1/proposals/outcome", json={
        "proposal_id": "pr_1", "operation": "o", "outcome": "ignored",
    })
    assert answer.status_code == 409


def test_recording_returns_the_updated_history(client: TestClient) -> None:
    """So the card that just filed an answer can show what it now knows without
    a second request that could disagree with the first."""
    for note in ("pr_1", "pr_2"):
        body = client.post("/api/v1/proposals/outcome", json={
            "proposal_id": note, "operation": "o", "target": "t",
            "outcome": "declined",
        }).json()
    assert body["history"]["declined"] == 2
    assert body["history"]["sentence"] == "you have declined this 2 times"


def test_clearing_is_reachable_and_reports_what_it_took(client: TestClient) -> None:
    client.post("/api/v1/proposals/outcome", json={
        "proposal_id": "pr_1", "operation": "o", "outcome": "accepted",
    })
    assert client.request("DELETE", "/api/v1/proposals").json() == {"forgotten": 1}
    assert client.get("/api/v1/proposals").json()["count"] == 0


def test_the_capability_is_advertised(client: TestClient) -> None:
    declared = client.get("/ecosystem/capabilities").json()["capabilities"]
    entry = next(c for c in declared if c["id"] == "nervis.proposal_memory")
    assert entry["state"] == "available"
    assert entry["reason"]
