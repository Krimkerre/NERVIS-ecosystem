"""SIRVIS's measurements of models loaded together, read by RAVIS (§12.3, M20's third part).

SIRVIS files a Runtime Set's interaction matrix on each member's evidence record, and
`/api/v1/evidence` — which RAVIS already reads — returns it. These tests drive the real
`EvidenceStore` against canned responses in that shape (captured from the 24 August runs
of `clarvis-recommended`) and the real `/api/v1/health`.
"""

from __future__ import annotations

import copy
from typing import Any

from tests.test_fallback import ScriptedUpstream, _app_with
from tests.test_sirvis_evidence import GGUF, MLX, payload, record, store_with

from ravis.evidence.co_residency import read_pair

MATRIX: dict[str, Any] = {
    "runtime_set": "clarvis-recommended",
    "revision": 1,
    "members": {"chat": "qwen/qwen3-4b-2507", "agent": GGUF},
    "load_order": ["chat", "agent"],
    "conditions": ["alone", "sequential", "alternating", "concurrent"],
    "rows": {
        "chat": {
            "figures": {"alone": {"tokens_per_second": 49.3, "samples": 3}},
            "degradation_percent": {
                "sequential": {"time_to_first_token": -0.68, "tokens_per_second": 0.27},
                "concurrent": {"time_to_first_token": 11.93, "tokens_per_second": 21.19},
            },
        },
        "agent": {
            "degradation_percent": {
                "concurrent": {"time_to_first_token": 6.09, "tokens_per_second": None},
            },
        },
    },
    "memory": {
        "alone": {"lowest_available_bytes": 8660140032, "swap_used_bytes": 184025088},
        "concurrent": {"lowest_available_bytes": 6331777024, "swap_used_bytes": 184025088},
    },
    "thermal": {"alone": "nominal", "concurrent": "fair"},
    "complete": True,
    "co_residency_failure": None,
    "basis": "measured in this run",
}


def measured(
    *, variant: str = "var_gguf", age: float = 60.0, matrix: dict[str, Any] | None = None,
    **options: Any,
) -> dict[str, Any]:
    """An evidence item carrying an interaction matrix, as a Runtime Set's run files it."""
    item = record(variant=variant, age=age, **options)
    item["interaction_matrix"] = copy.deepcopy(MATRIX if matrix is None else matrix)
    item["validity_notes"] = ["the machine's thermal state changed during this run"]
    item["run_id"] = f"run_{int(age)}"
    return item


def test_a_measured_pair_is_read_whole_with_its_slowdowns_and_memory() -> None:
    clock = [1000.0]
    store = store_with(payload(measured(), measured(variant="var_mlx")), clock=lambda: clock[0])

    reading = store.co_residency()
    assert reading["state"] == "fresh"
    [pair] = reading["pairs"]
    assert pair["runtime_set"] == "clarvis-recommended" and pair["revision"] == 1
    assert pair["members"] == {"chat": "qwen/qwen3-4b-2507", "agent": GGUF}
    assert pair["slowdown_percent"]["chat"]["concurrent"] == {
        "time_to_first_token": 11.93, "tokens_per_second": 21.19}
    assert pair["slowdown_percent"]["chat"]["sequential"]["time_to_first_token"] == -0.68, (
        "a negative slowdown is kept as SIRVIS sent it")
    assert pair["slowdown_percent"]["agent"]["concurrent"]["tokens_per_second"] is None, (
        "a missing figure stays missing")
    assert pair["lowest_free_bytes"] == {"alone": 8660140032, "concurrent": 6331777024}
    assert pair["thermal"] == {"alone": "nominal", "concurrent": "fair"}
    assert pair["notes"] == ["the machine's thermal state changed during this run"]
    assert pair["complete"] is True and pair["failure"] == ""
    assert (pair["age_seconds"], pair["past_window"]) == (60.0, False)

    clock[0] += 30
    assert store.co_residency()["pairs"][0]["age_seconds"] == 90.0, "the age is the age now"


def test_the_newest_run_of_a_set_is_the_reading_and_other_sets_stay_apart() -> None:
    older = measured(age=5000.0, matrix={**MATRIX, "thermal": {"alone": "serious"}})
    other = measured(variant="var_mlx", matrix={**MATRIX, "runtime_set": "clarvis-balanced"})
    revised = measured(age=10.0, matrix={**MATRIX, "revision": 2})
    store = store_with(payload(older, measured(age=100.0), other, revised))

    pairs = store.co_residency()["pairs"]
    assert [(p["runtime_set"], p["revision"]) for p in pairs] == [
        ("clarvis-balanced", 1), ("clarvis-recommended", 1), ("clarvis-recommended", 2)]
    assert pairs[1]["age_seconds"] == 100.0
    assert pairs[1]["thermal"] == {"alone": "nominal", "concurrent": "fair"}


def test_a_failed_co_loading_is_a_result() -> None:
    failed = {**MATRIX, "complete": False, "conditions": ["alone"],
              "co_residency_failure": "the agent model did not fit beside the chat model"}
    [pair] = store_with(payload(measured(matrix=failed))).co_residency()["pairs"]
    assert pair["complete"] is False
    assert pair["failure"] == "the agent model did not fit beside the chat model"
    assert pair["conditions"] == ["alone"]


def test_a_pair_past_the_window_is_marked_not_hidden() -> None:
    store = store_with(payload(measured(age=40 * 24 * 3600)), max_age_seconds=30 * 24 * 3600)
    [pair] = store.co_residency()["pairs"]
    assert pair["past_window"] is True


def test_what_cannot_be_read_is_skipped_and_a_record_without_a_matrix_adds_no_pair() -> None:
    no_members = measured(matrix={**MATRIX, "members": {}})
    no_name = measured(matrix={**MATRIX, "runtime_set": ""})
    future = measured(version="9.0.0")
    store = store_with(payload(record(), no_members, no_name, future))
    assert store.co_residency()["pairs"] == []
    assert read_pair({"interaction_matrix": "not a matrix"}) is None
    assert read_pair("not an item") is None


def test_hostile_numbers_are_not_numbers() -> None:
    odd = copy.deepcopy(MATRIX)
    odd["rows"]["chat"]["degradation_percent"]["concurrent"] = {
        "time_to_first_token": True, "tokens_per_second": "21"}
    odd["memory"]["alone"]["lowest_available_bytes"] = "8660140032"
    pair = read_pair({"interaction_matrix": odd})
    assert pair is not None
    assert pair["slowdown_percent"]["chat"]["concurrent"] == {
        "time_to_first_token": None, "tokens_per_second": None}
    assert pair["lowest_free_bytes"]["alone"] is None


def test_pairs_go_when_sirvis_stops_answering() -> None:
    """§13.3: a cached measurement is never presented after its source went away."""
    import asyncio

    import httpx

    store = store_with(payload(measured()))
    assert store.co_residency()["pairs"]

    def down(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content="down")

    client = httpx.AsyncClient(transport=httpx.MockTransport(down))
    asyncio.run(store.refresh(client, [GGUF, MLX]))
    reading = store.co_residency()
    assert (reading["state"], reading["pairs"]) == ("degraded", [])


def test_health_carries_the_pairs_and_no_longer_lists_them_as_not_read() -> None:
    models = {"talker": {"context_window": "32000"}}
    with _app_with(ScriptedUpstream(models)) as client:
        absent = client.get("/api/v1/health").json()["load"]
        client.app.app.state.evidence = store_with(payload(measured()))  # type: ignore[attr-defined]
        present = client.get("/api/v1/health").json()["load"]

    assert absent["co_residency"]["state"] == "absent"
    assert absent["co_residency"]["pairs"] == []
    assert not any("SIRVIS" in line for line in absent["not_read"])
    assert any("credits" in line for line in absent["not_read"])
    [pair] = present["co_residency"]["pairs"]
    assert pair["members"]["agent"] == GGUF
