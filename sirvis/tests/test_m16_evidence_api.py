"""M16 — the surface RAVIS reads (§15.1), and the questions it must answer.

    A RAVIS test client queries `clarvis-agent` on this machine for candidate
    builds and receives provenance-rich evidence.

The acceptance criterion is a *client* rather than an endpoint, so the last
section below is written as one: it asks the way RAVIS asks, and asserts on
what RAVIS would need to route.

Two rules constrain this surface more than any feature does. §12.2 forbids
evidence keyed as model → score, so nothing here ranks — several tests exist to
keep it that way. And §15.1 forbids making RAVIS infer equivalence across
builds, which is why candidates arrive as runtime keys and leave as variants.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi.testclient import TestClient
from tests.conftest_lmstudio import transport

from sirvis.api.security import Scope, mint_token
from sirvis.app import create_app
from sirvis.config import Settings
from sirvis.storage import StoredResult, create_experiment, finish_run, prepare_database, start_run
from sirvis.storage.database import Database
from sirvis.storage.evidence import (
    EvidenceQuery,
    known_roles,
    query_evidence,
    read_evidence,
)

EVIDENCE = "/api/v1/evidence"

# The runtime keys the recorded catalogue offers, and the variant each resolves
# to. Two packagings of one family, which is §15.1's worked example: RAVIS must
# not have to know they are related, or that their evidence disagrees.
MLX_KEY = "qwen2.5-coder-7b-instruct"
GGUF_KEY = "lmstudio-community/granite-4.0-h-tiny"


def a_record(
    *,
    role: str = "agent",
    family: str = "granite-4.0-h-tiny",
    variant: str = "var_gguf",
    runtime: str = "lmstudio",
    fmt: str = "gguf",
    evidence_type: str = "MEASURED",
    validity: str = "VALID",
    context_length: int = 8192,
    evidence_id: str = "ev_one",
) -> dict[str, Any]:
    """One stored evidence payload, in the shape the engine writes."""
    return {
        "evidence_id": evidence_id,
        "machine_id": "machine-1",
        "role": role,
        "target": {
            "model_family": family,
            "variant": variant,
            "source_repository": None,
            "source_revision": None,
            "format": fmt,
            "quantization": "Q4_K_M",
            "runtime": runtime,
            "runtime_version": None,
            "runtime_config": {"context_length": context_length},
        },
        "suite": {"id": "performance-basic", "version": "1"},
        "evidence_type": evidence_type,
        "samples": 5,
        "metrics": {
            "generation_tokens_per_second": {
                "value": 67.0, "unit": "tokens/second", "samples": 5,
                "provenance": {"kind": evidence_type},
            }
        },
        "validity": validity,
        "validity_notes": [],
        "machine_snapshot_id": "snap-1",
        "sirvis_version": "0.0.1",
    }


def stored(database: Database, *records: dict[str, Any]) -> None:
    """Persist records through the real run path, not by hand-writing rows."""
    for index, record in enumerate(records):
        experiment_id = create_experiment(
            database, {"target": {"model_key": "m"}},
            suite_id=record["suite"]["id"], suite_version=record["suite"]["version"],
            environment_mode="shared",
        )
        run_id = start_run(
            database, experiment_id, runtime_key="lmstudio", runtime_snapshot={},
            machine_snapshot_id=None, results_path="",
        )
        finish_run(
            database, run_id, state=_succeeded(), detail="completed",
            results=[StoredResult(
                target_key=f"{record['role']}-{index}",
                evidence_id=record["evidence_id"],
                validity=record["validity"],
                payload=record,
            )],
        )


def _succeeded() -> Any:
    from sirvis.storage import RunState

    return RunState.SUCCEEDED


def a_database(*records: dict[str, Any]) -> Database:
    database = prepare_database(":memory:")
    stored(database, *records)
    return database


def an_app(*records: dict[str, Any]) -> tuple[TestClient, str]:
    settings = Settings(
        database_path=":memory:", lmstudio_base_url="http://127.0.0.1:9",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(
        settings,
        runtime=__import__("sirvis.runtimes", fromlist=["LMStudioAdapter"]).LMStudioAdapter(
            "http://runtime.invalid", client=httpx.AsyncClient(transport=transport())
        ),
    )
    stored(app.state.database, *records)
    return TestClient(app), mint_token(app.state.database, "ravis", {Scope.READ})


def get(client: TestClient, token: str, path: str) -> httpx.Response:
    return client.get(path, headers={"authorization": f"Bearer {token}"})


# ── Filtering, per §15.1's list ──────────────────────────────────────────────


def test_every_filter_the_contract_names_actually_filters() -> None:
    """§15.1 lists machine, role, family, variant, runtime, config, suite, type.

    A filter present in the API but absent from the query would silently match
    everything — a query that looks like it worked and quietly answered a
    different question.
    """
    database = a_database(
        a_record(role="agent", variant="var_gguf", fmt="gguf", evidence_id="ev_a"),
        a_record(role="chat", variant="var_mlx", fmt="mlx", family="qwen2.5-coder",
                 evidence_id="ev_b"),
    )

    for name, value, expected in (
        ("role", "agent", "ev_a"),
        ("model_family", "qwen2.5-coder", "ev_b"),
        ("variant", "var_mlx", "ev_b"),
        ("format", "gguf", "ev_a"),
        ("machine_id", "machine-1", None),
        ("suite", "performance-basic", None),
        ("evidence_type", "MEASURED", None),
    ):
        answer = query_evidence(database, EvidenceQuery(filters={name: value}))
        assert answer.items, f"{name}={value} matched nothing"
        if expected:
            assert [item["evidence_id"] for item in answer.items] == [expected], name


def test_a_filter_that_matches_nothing_returns_nothing() -> None:
    """The other half: a filter that cannot match must not fall back to all."""
    database = a_database(a_record())

    assert query_evidence(database, EvidenceQuery(filters={"role": "planner"})).items == []


def test_runtime_configuration_is_a_subset_match() -> None:
    """§15.1 lets RAVIS constrain on configuration.

    A record carrying *more* configuration than was asked about still matches:
    the extra facts narrow what the evidence is about, they do not disqualify
    it. A co-resident measurement records what it sat beside, and a query for
    `context_length=8192` should still find it.
    """
    database = a_database(a_record(context_length=8192), a_record(
        context_length=32768, evidence_id="ev_big"))

    answer = query_evidence(
        database, EvidenceQuery(runtime_config={"context_length": 8192})
    )

    assert [item["evidence_id"] for item in answer.items] == ["ev_one"]


def test_a_configuration_constraint_that_does_not_match_excludes_the_record() -> None:
    database = a_database(a_record(context_length=8192))

    answer = query_evidence(
        database, EvidenceQuery(runtime_config={"context_length": 999})
    )

    assert answer.items == []


# ── No ranking, ever (§12.2) ─────────────────────────────────────────────────


def test_results_are_ordered_by_recency_and_nothing_else() -> None:
    """§12.2 forbids model → score, and an ordering by quality would be one.

    Recency is a fact about the record. "Best" is a judgement about the model,
    and §15.1 gives that judgement to RAVIS.
    """
    database = a_database(
        a_record(evidence_id="ev_first"),
        a_record(evidence_id="ev_second"),
        a_record(evidence_id="ev_third"),
    )

    answer = query_evidence(database, EvidenceQuery())

    # Newest first; created_at ties break on result_id, so the set is what is
    # asserted rather than a fragile exact order within one clock tick.
    assert len(answer.items) == 3
    assert {item["evidence_id"] for item in answer.items} == {
        "ev_first", "ev_second", "ev_third"
    }


def test_no_response_field_ranks_or_scores_a_build() -> None:
    """Structural, not stylistic: there must be nowhere for a score to live."""
    client, token = an_app(a_record())

    body = get(client, token, EVIDENCE).json()

    forbidden = {"score", "rank", "best", "recommended", "winner"}
    assert not forbidden & set(body)
    assert not forbidden & set(body["items"][0])


# ── Provenance, validity and staleness ───────────────────────────────────────


def test_every_item_carries_its_provenance_and_validity() -> None:
    """"Provenance-rich" is the acceptance criterion's own adjective."""
    client, token = an_app(a_record())

    item = get(client, token, EVIDENCE).json()["items"][0]

    assert item["evidence_type"] == "MEASURED"
    assert item["validity"] == "VALID"
    assert item["metrics"]["generation_tokens_per_second"]["provenance"]["kind"] == "MEASURED"
    assert item["machine_snapshot_id"] == "snap-1"
    assert item["samples"] == 5


def test_staleness_travels_as_both_a_stamp_and_an_age() -> None:
    """§15.1 asks for staleness timestamps.

    Both, because a consumer applies its own threshold: the stamp is the fact
    and the age is the derivation, and an age stored at insert time would be a
    number that was true once.
    """
    database = a_database(a_record())
    later = datetime.now(timezone.utc) + timedelta(hours=2)

    item = query_evidence(database, EvidenceQuery(), now=later).items[0]

    assert item["measured_at"]
    assert item["age_seconds"] >= 7200 - 5


def test_an_invalid_record_is_returned_rather_than_hidden() -> None:
    """§11.8: never hide an integrity problem.

    Filtering INVALID evidence out by default would make a corpus look healthier
    than it is, and RAVIS cannot weigh what it is not shown.
    """
    database = a_database(a_record(validity="INVALID", evidence_id="ev_bad"))

    answer = query_evidence(database, EvidenceQuery())

    assert [item["evidence_id"] for item in answer.items] == ["ev_bad"]
    assert answer.items[0]["validity"] == "INVALID"


def test_a_stable_reference_travels_on_every_item() -> None:
    """§15.1: stable evidence references, resolvable during the retention window.

    Carried rather than constructed by the caller — the moment a consumer builds
    one by concatenating fields, the format is owned by the consumer.
    """
    database = a_database(a_record())

    item = query_evidence(database, EvidenceQuery()).items[0]

    assert item["evidence_ref"].startswith("sirvis://evidence/ev_one/")
    assert item["retention_days"] == 365


# ── Incremental reads ────────────────────────────────────────────────────────


def test_a_cursor_pages_without_repeating_a_row() -> None:
    """§15.1 asks for incremental reads.

    The cursor is the last row already returned, so the next page must start
    strictly after it — `<=` would hand that row back every time and a consumer
    paging to exhaustion would never exhaust.
    """
    database = a_database(*[a_record(evidence_id=f"ev_{n}") for n in range(5)])

    first = query_evidence(database, EvidenceQuery(limit=2))
    assert first.next_cursor is not None
    second = query_evidence(database, EvidenceQuery(limit=2, since=first.next_cursor))

    assert len(first.items) == 2
    seen = {item["result_id"] for item in first.items}
    assert not seen & {item["result_id"] for item in second.items}


def test_the_last_page_offers_no_cursor() -> None:
    database = a_database(a_record())

    assert query_evidence(database, EvidenceQuery(limit=10)).next_cursor is None


# ── An identity is conditions, not a record (§12.2) ──────────────────────────


def test_one_identity_can_hold_several_results() -> None:
    """Running one suite against one build twice is two results, one identity.

    Returning "the" record would mean silently picking one, which is the
    judgement this surface refuses to make everywhere else.
    """
    database = a_database(a_record(evidence_id="ev_same"), a_record(evidence_id="ev_same"))

    records = read_evidence(database, "ev_same")

    assert len(records) == 2
    assert {record["result_id"] for record in records} != {None}


def test_an_unknown_identity_is_a_404() -> None:
    client, token = an_app(a_record())

    assert get(client, token, f"{EVIDENCE}/ev_nothing").status_code == 404


# ── Candidates: SIRVIS resolves, RAVIS does not infer (§15.1) ────────────────


def test_a_candidate_runtime_key_resolves_to_the_variant_its_evidence_uses() -> None:
    """"Do not force RAVIS to infer equivalence across builds", mechanically.

    RAVIS knows a runtime key and nothing else. Evidence is filed by variant.
    The mapping needs §6's inventory, which is SIRVIS's — a RAVIS doing this
    itself would be matching on names, which §15.1 forbids.
    """
    client, token = an_app(a_record(variant=_variant_of(GGUF_KEY)))

    body = get(client, token, f"{EVIDENCE}?candidate={GGUF_KEY}").json()

    assert body["resolved_candidates"] == [_variant_of(GGUF_KEY)]
    assert body["unresolved_candidates"] == []
    assert len(body["items"]) == 1


def test_a_candidate_this_machine_does_not_have_is_named_not_silently_dropped() -> None:
    """"Not installed" and "measured, no evidence" are different findings.

    An empty list for both would tell RAVIS a build was measured and found
    wanting when in fact it is absent.
    """
    client, token = an_app(a_record())

    body = get(client, token, f"{EVIDENCE}?candidate=not-installed-anywhere").json()

    assert body["unresolved_candidates"] == ["not-installed-anywhere"]
    assert body["items"] == []


def test_tombstones_are_reported_as_empty_rather_than_omitted() -> None:
    """§15.1 asks for tombstones; nothing deletes evidence, so there are none.

    The field is present so a consumer can code against it before deletion
    exists — and so whoever adds retention knows what they must start filling.
    """
    client, token = an_app(a_record())

    assert get(client, token, EVIDENCE).json()["tombstones"] == []


# ── The role vocabulary seam, found while building this ──────────────────────


def test_an_unmatched_role_reports_the_roles_that_exist() -> None:
    """RAVIS names its pools `ravis/clarvis-agent`; evidence is filed as `agent`.

    SIRVIS must not invent a rule mapping one to the other — that is precisely
    the equivalence-inference §15.1 forbids, just performed by the other side.
    What it can do is stop the mismatch presenting as "measured, nothing found"
    when the truth is "nobody has agreed what this role is called".
    """
    client, token = an_app(a_record(role="agent"))

    body = get(client, token, f"{EVIDENCE}?role=clarvis-agent").json()

    assert body["items"] == []
    assert body["available_roles"] == ["agent"]


def test_known_roles_lists_what_the_corpus_holds() -> None:
    database = a_database(a_record(role="agent"), a_record(role="chat", evidence_id="ev_c"))

    assert known_roles(database) == ["agent", "chat"]


# ── The acceptance criterion, written as the client it names ─────────────────


def test_a_ravis_client_asks_for_a_role_and_candidates_and_can_route_on_the_answer() -> None:
    """§15.1's question end to end, and what RAVIS needs back to act on it.

    > Give me the best measured evidence for role `clarvis-agent` on this
    > machine for these candidate builds, under these runtime configuration
    > constraints.

    The client asks with what it has — a role, two runtime keys, a context
    constraint — and must get back enough to choose *without* SIRVIS having
    chosen: every metric with its provenance, its validity, its age, and a
    reference it can cite later.
    """
    client, token = an_app(
        a_record(role="agent", variant=_variant_of(GGUF_KEY), fmt="gguf",
                 evidence_id="ev_gguf", context_length=8192),
        a_record(role="agent", variant=_variant_of(MLX_KEY), fmt="mlx",
                 family="qwen2.5-coder", evidence_id="ev_mlx", context_length=8192),
    )

    body = get(client, token, (
        f"{EVIDENCE}?role=agent"
        f"&candidate={GGUF_KEY}&candidate={MLX_KEY}"
        "&evidence_type=MEASURED&config.context_length=8192"
    )).json()

    assert len(body["resolved_candidates"]) == 2, "both candidates resolved by SIRVIS"
    assert body["unresolved_candidates"] == []
    assert {item["evidence_id"] for item in body["items"]} == {"ev_gguf", "ev_mlx"}

    # Everything RAVIS needs to decide, on every item — and no decision made.
    for item in body["items"]:
        assert item["evidence_type"] == "MEASURED"
        assert item["validity"] in {"VALID", "VALID_WITH_WARNINGS", "INVALID"}
        assert item["age_seconds"] is not None
        assert item["evidence_ref"].startswith("sirvis://evidence/")
        assert item["metrics"]["generation_tokens_per_second"]["provenance"]["kind"]
        assert item["target"]["format"] in {"gguf", "mlx"}

    # The two packagings stay distinguishable, which is the point of §12.2's
    # identity: RAVIS is told they are different evidence, not asked to work it
    # out from names.
    assert {item["target"]["format"] for item in body["items"]} == {"gguf", "mlx"}


def test_the_client_never_needs_the_sirvis_database() -> None:
    """§15.1: "RAVIS must never need SIRVIS's database."

    Everything the acceptance criterion asks for arrives in one JSON response —
    so this asserts the response is self-contained rather than a set of IDs to
    look up somewhere only SIRVIS can reach.
    """
    client, token = an_app(a_record())

    body = get(client, token, EVIDENCE).json()

    item = body["items"][0]
    assert json.loads(json.dumps(item)) == item, "the answer is plain JSON"
    assert item["metrics"], "metrics inline, not referenced"
    assert item["target"]["runtime_config"], "conditions inline, not referenced"


def _variant_of(runtime_key: str) -> str:
    """The variant ID the recorded catalogue derives for a runtime key.

    Computed through the real inventory rather than hardcoded: if §6's
    derivation changes, this test should follow it rather than pin a stale hash.
    """
    import asyncio

    from sirvis.core.inventory import build_inventory
    from sirvis.runtimes import LMStudioAdapter

    adapter = LMStudioAdapter(
        "http://runtime.invalid", client=httpx.AsyncClient(transport=transport())
    )
    inventory = build_inventory(asyncio.run(adapter.list_models()))
    build = inventory.by_runtime_key(runtime_key)
    assert build is not None, f"{runtime_key} is not in the recorded catalogue"
    return build.variant_id


def test_a_configuration_constraint_is_applied_before_the_limit() -> None:
    """It ran in Python *after* the SQL LIMIT, so a full page could be emptied.

    §15.1's headline question is evidence "under these runtime configuration
    constraints", and that constraint was the one filter not pushed into SQL.
    A page of `limit` newer records that all failed the constraint was fetched,
    truncated, and only then filtered -- so the endpoint answered "no evidence
    matches" while the matching record sat one page further in. `next_cursor`
    came from the unfiltered page, so paging could not recover it either.

    The record to look for is chosen *after* insertion -- whichever one the
    store happens to return last -- so the test cannot be satisfied by the
    wanted row landing inside the limit by luck. An earlier version of this test
    hard-coded the target and passed against the very bug it was written for.
    """
    database = a_database(*(
        a_record(context_length=1024 * (n + 1), evidence_id=f"ev_{n}") for n in range(6)
    ))
    ordering = query_evidence(database, EvidenceQuery(limit=10)).items
    furthest = ordering[-1]
    wanted = furthest["target"]["runtime_config"]["context_length"]

    answer = query_evidence(
        database, EvidenceQuery(runtime_config={"context_length": wanted}, limit=1)
    )

    assert [item["evidence_id"] for item in answer.items] == [furthest["evidence_id"]], (
        "a record past the limit is still found when the constraint reaches SQL"
    )


def test_a_configuration_key_that_is_not_an_identifier_matches_nothing() -> None:
    """The keys arrive from a query string and the JSON path is built from them.

    Bound as a parameter rather than interpolated, and anything that is not an
    identifier is refused outright: `json_extract` raises on a malformed path,
    and a 500 on a odd query string would be a worse answer than an empty one.
    """
    database = a_database(a_record(context_length=8192))

    answer = query_evidence(
        database, EvidenceQuery(runtime_config={"context_length' OR '1'='1": "x"})
    )

    assert answer.items == []
