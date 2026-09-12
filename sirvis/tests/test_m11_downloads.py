"""M11 — the model browser and downloads (SIRVIS §8).

The exit, verbatim: *"Browser reload does not lose download state; disk warnings
fire; nothing auto-deletes."* A reload is a fresh read of stored rows, so it is
tested as a download whose progress every read finds where the last pass left it
while LM Studio carries it. Disk warnings are tested at both ends: the check, and
the refusal a request gets until it confirms. Nothing here deletes a file, and
nothing in `downloads.py` can.

No test reaches Hugging Face or LM Studio: both are recorded transports handed to
the app, the same way runtime reads are.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from sirvis import catalog, downloads
from sirvis.api.security import Scope, mint_token
from sirvis.app import create_app
from sirvis.config import Settings
from sirvis.ecosystem import DECLARED
from sirvis.runtimes import LMStudioAdapter, variants

GIB = 1024**3

GGUF_REPO = "lmstudio-community/Tiny-GGUF"
MLX_REPO = "mlx-community/Tiny-4bit"

GGUF_MODEL: dict[str, Any] = {
    "id": GGUF_REPO, "tags": ["gguf"], "gated": False, "downloads": 10, "likes": 1,
    "gguf": {"architecture": "llama", "context_length": 8192, "total": 135_000_000},
    "siblings": [
        {"rfilename": "Tiny-Q4_K_M.gguf", "size": 2 * GIB},
        {"rfilename": "Tiny-Q8_0-00001-of-00002.gguf", "size": 3 * GIB},
        {"rfilename": "Tiny-Q8_0-00002-of-00002.gguf", "size": 1 * GIB},
        {"rfilename": "mmproj-Tiny-F16.gguf", "size": 1 * GIB},
        {"rfilename": "README.md", "size": 100},
    ],
}
MLX_MODEL: dict[str, Any] = {
    "id": MLX_REPO, "tags": ["mlx"], "gated": False,
    "siblings": [
        {"rfilename": "model.safetensors", "size": 3 * GIB},
        {"rfilename": "config.json", "size": 1000},
    ],
}


def hub(models: dict[str, dict[str, Any]]) -> httpx.AsyncClient:
    """Hugging Face's two reads, answered from `models`."""

    def answer(request: httpx.Request) -> httpx.Response:
        repo = request.url.path.removeprefix("/api/models/")
        if repo in models:
            return httpx.Response(200, json=models[repo])
        return httpx.Response(404, json={"error": "Repository not found"})

    return httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="https://hf.test")


class FakeLMStudio:
    """LM Studio's download API as its documentation describes it, and nothing more."""

    def __init__(self, start: dict[str, Any], statuses: list[dict[str, Any]] | None = None) -> None:
        self.start = start
        self.statuses = list(statuses or [])
        self.requests: list[httpx.Request] = []

    def client(self) -> httpx.AsyncClient:
        def answer(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if request.method == "POST" and request.url.path == "/api/v1/models/download":
                return httpx.Response(200, json=self.start)
            if request.url.path.startswith("/api/v1/models/download/status/"):
                return httpx.Response(200, json=self.statuses.pop(0))
            return httpx.Response(200, json={"error": {"message": "Unexpected endpoint"}})

        return httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="http://lm.test")


def an_api(
    monkeypatch: pytest.MonkeyPatch,
    *,
    free: int = 400 * GIB,
    installed: frozenset[str] | None = None,
    lmstudio: FakeLMStudio | None = None,
) -> tuple[TestClient, Any, str]:
    """An app whose Hugging Face, LM Studio, disk and installed builds are all stated.

    The watcher waits an hour before its first pass, so a test drives `advance`
    itself and nothing runs behind its back.
    """
    settings = Settings(
        database_path=":memory:", lmstudio_base_url="http://127.0.0.1:9",
        lmstudio_cli_path="/nonexistent/lms", huggingface_base_url="http://127.0.0.1:9",
        download_poll_seconds=3600.0,
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    app.state.hub_client = hub({GGUF_REPO: GGUF_MODEL, MLX_REPO: MLX_MODEL})
    if lmstudio is not None:
        app.state.download_client = lmstudio.client()
    monkeypatch.setattr(downloads, "free_bytes", lambda _path: free)
    monkeypatch.setattr(LMStudioAdapter, "installed_paths", lambda _self: installed)
    return TestClient(app), app, mint_token(app.state.database, "operator", {Scope.ADMIN})


def as_operator(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


# ── Discover ────────────────────────────────────────────────────────────────


def test_a_gguf_model_offers_a_variant_per_quantization_with_split_files_counted_together() -> None:
    installed = frozenset({f"{GGUF_REPO}/Tiny-Q4_K_M.gguf"})

    detail = asyncio.run(catalog.model(hub({GGUF_REPO: GGUF_MODEL}), GGUF_REPO, installed))

    assert (detail["format"], detail["architecture"], detail["gated"]) == ("gguf", "llama", False)
    assert [
        (variant["quantization"], variant["size_bytes"], variant["installed"])
        for variant in detail["variants"]
    ] == [("Q4_K_M", 2 * GIB, True), ("Q8_0", 4 * GIB, False)], (
        "the vision projector and the readme are not variants anybody downloads"
    )


def test_an_mlx_repository_is_one_variant_the_size_of_its_folder() -> None:
    detail = asyncio.run(catalog.model(hub({MLX_REPO: MLX_MODEL}), MLX_REPO, frozenset({MLX_REPO})))

    [variant] = detail["variants"]
    assert (variant["quantization"], variant["size_bytes"], variant["installed"]) == (
        "4bit", 3 * GIB + 1000, True,
    )


def test_a_search_merges_both_formats_most_downloaded_first_and_marks_what_is_installed() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        rows = {
            "gguf": [{"id": GGUF_REPO, "downloads": 5}],
            "mlx": [{"id": MLX_REPO, "downloads": 9}, {"id": "../escape", "downloads": 99}],
        }
        return httpx.Response(200, json=rows[request.url.params["filter"]])

    client = httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="https://hf.test")
    installed = frozenset({f"{GGUF_REPO}/Tiny-Q4_K_M.gguf"})

    found = asyncio.run(catalog.search(client, "tiny", "any", 10, installed))["items"]

    assert [(row["repo_id"], row["format"], row["installed"]) for row in found] == [
        (MLX_REPO, "mlx", False), (GGUF_REPO, "gguf", True),
    ], "an id that is not `owner/name` never reaches a URL"


# ── The disk check ──────────────────────────────────────────────────────────


def check(free: int, needed: int | None) -> downloads.DiskCheck:
    return downloads.check_disk(free, needed, low_disk_bytes=20 * GIB, large_share=0.5)


def test_the_disk_check_refuses_what_does_not_fit_and_warns_before_it_gets_tight() -> None:
    assert not check(10 * GIB, 11 * GIB).fits
    assert check(400 * GIB, 4 * GIB).warnings == ()
    assert len(check(6 * GIB, 4 * GIB).warnings) == 2, (
        "under the reserve afterwards, and more than half of what is free"
    )
    unknown = check(100 * GIB, None)
    assert unknown.fits and unknown.warnings, "an unknown size is said, not assumed small"


def test_a_download_needs_admin_and_is_held_until_its_warnings_are_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, app, token = an_api(monkeypatch, free=6 * GIB)
    reader = mint_token(app.state.database, "reader", {Scope.READ})
    body = {"repo_id": GGUF_REPO, "quantization": "Q8_0"}

    refused = client.post("/api/v1/downloads", json=body, headers=as_operator(reader))
    held = client.post("/api/v1/downloads", json=body, headers=as_operator(token))
    confirmed = client.post(
        "/api/v1/downloads", json={**body, "confirm": True}, headers=as_operator(token)
    )

    assert refused.status_code == 403
    assert held.status_code == 409
    error = held.json()["error"]
    assert error["code"] == "DISK_SPACE" and error["details"]["confirm_required"] is True
    assert len(error["details"]["warnings"]) == 2
    assert confirmed.status_code == 202
    download = confirmed.json()["download"]
    assert (download["status"], download["expected_bytes"], len(download["warnings"])) == (
        "queued", 4 * GIB, 2,
    ), "what the person was warned about is kept with the job"


def test_a_download_that_does_not_fit_is_refused_even_when_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _app, token = an_api(monkeypatch, free=3 * GIB)

    answer = client.post(
        "/api/v1/downloads",
        json={"repo_id": GGUF_REPO, "quantization": "Q8_0", "confirm": True},
        headers=as_operator(token),
    )

    assert answer.status_code == 409
    details = answer.json()["error"]["details"]
    assert details["fits"] is False and "confirm_required" not in details
    assert client.get("/api/v1/downloads").json()["items"] == [], "nothing was queued"


# ── Downloads, carried by LM Studio ─────────────────────────────────────────


def test_lmstudio_carries_a_download_and_every_read_finds_it_where_it_got_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M11's exit, "a browser reload does not lose download state": a reload is a
    fresh read, and each read finds the progress the last pass stored."""
    lmstudio = FakeLMStudio(
        start={"job_id": "job-1", "status": "downloading", "total_size_bytes": 2 * GIB},
        statuses=[
            {"job_id": "job-1", "status": "downloading", "downloaded_bytes": GIB,
             "total_size_bytes": 2 * GIB, "bytes_per_second": 50_000_000,
             "estimated_completion": "2026-09-12T12:01:00Z"},
            {"job_id": "job-1", "status": "completed", "downloaded_bytes": 2 * GIB,
             "total_size_bytes": 2 * GIB, "completed_at": "2026-09-12T12:01:00Z"},
        ],
    )
    client, app, token = an_api(monkeypatch, lmstudio=lmstudio)
    queued = client.post(
        "/api/v1/downloads", json={"repo_id": GGUF_REPO, "quantization": "Q4_K_M"},
        headers=as_operator(token),
    ).json()["download"]

    asyncio.run(downloads.advance(app))
    halfway = client.get(f"/api/v1/downloads/{queued['download_id']}").json()["download"]
    asyncio.run(downloads.advance(app))
    done = client.get("/api/v1/downloads").json()["items"][0]

    assert json.loads(lmstudio.requests[0].content) == {
        "model": f"https://huggingface.co/{GGUF_REPO}", "quantization": "Q4_K_M",
    }
    assert (halfway["status"], halfway["progress"], halfway["bytes_per_second"]) == (
        "downloading", 0.5, 50_000_000,
    )
    assert (done["status"], done["progress"], done["completed_at"]) == (
        "completed", 1.0, "2026-09-12T12:01:00Z",
    )
    assert done["cancellable"] is False, "LM Studio publishes no cancel"


def test_lmstudio_already_holding_it_or_forgetting_it_ends_the_download_with_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, app, token = an_api(
        monkeypatch, lmstudio=FakeLMStudio({"status": "already_downloaded"})
    )
    first = client.post(
        "/api/v1/downloads", json={"repo_id": GGUF_REPO, "quantization": "Q4_K_M"},
        headers=as_operator(token),
    ).json()["download"]
    asyncio.run(downloads.advance(app))
    present = client.get(f"/api/v1/downloads/{first['download_id']}").json()["download"]

    forgotten = FakeLMStudio(
        {"job_id": "job-2", "status": "downloading"},
        [{"error": {"type": "job_not_found", "message": "Download job with id 'job-2' not found"}}],
    )
    app.state.download_client = forgotten.client()
    second = client.post(
        "/api/v1/downloads", json={"repo_id": GGUF_REPO, "quantization": "Q8_0"},
        headers=as_operator(token),
    ).json()["download"]
    asyncio.run(downloads.advance(app))
    failed = client.get(f"/api/v1/downloads/{second['download_id']}").json()["download"]

    assert (present["status"], present["progress"]) == ("already_present", 1.0)
    assert failed["status"] == "failed" and "no longer knows" in failed["detail"]


def test_a_variant_already_installed_is_recorded_without_asking_lmstudio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lmstudio = FakeLMStudio({"job_id": "never", "status": "downloading"})
    client, app, token = an_api(
        monkeypatch, installed=frozenset({f"{GGUF_REPO}/Tiny-Q4_K_M.gguf"}), lmstudio=lmstudio
    )

    download = client.post(
        "/api/v1/downloads", json={"repo_id": GGUF_REPO, "quantization": "Q4_K_M"},
        headers=as_operator(token),
    ).json()["download"]
    asyncio.run(downloads.advance(app))

    assert download["status"] == "already_present"
    assert lmstudio.requests == [], "nothing was asked of LM Studio"


def test_a_gated_model_is_not_offered_and_an_unknown_quantization_lists_the_real_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, app, token = an_api(monkeypatch)
    app.state.hub_client = hub(
        {GGUF_REPO: {**GGUF_MODEL, "gated": "auto"}, "some/model": GGUF_MODEL}
    )

    gated = client.post(
        "/api/v1/downloads", json={"repo_id": GGUF_REPO, "quantization": "Q4_K_M"},
        headers=as_operator(token),
    )
    unknown = client.post(
        "/api/v1/downloads", json={"repo_id": "some/model", "quantization": "Q2_K"},
        headers=as_operator(token),
    )

    assert gated.json()["error"]["code"] == "INVALID_CONFIGURATION"
    assert "gated" in gated.json()["error"]["message"]
    assert unknown.json()["error"]["details"]["available"] == ["Q4_K_M", "Q8_0"]


def test_both_capabilities_are_advertised_and_a_model_reads_with_its_disk_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _app, _token = an_api(monkeypatch)

    detail = client.get(f"/api/v1/catalog/{GGUF_REPO}").json()
    wrong = client.get("/api/v1/catalog", params={"format": "safetensors"})

    assert {"sirvis.catalog.read@1", "sirvis.downloads@1"} <= set(DECLARED)
    assert [variant["disk"]["fits"] for variant in detail["variants"]] == [True, True]
    assert wrong.json()["error"]["code"] == "UNSUPPORTED_PARAMETER"


def test_a_model_hugging_face_will_not_show_is_not_found_rather_than_an_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Measured 12 September 2026: a misspelled repository answers 401, not 404, and
    was reported as the catalogue being unavailable."""
    client, app, _token = an_api(monkeypatch)
    refusal = httpx.Response(401, json={"error": "Invalid username or password."})
    app.state.hub_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: refusal), base_url="https://hf.test"
    )

    answer = client.get("/api/v1/catalog/someone/misspelled-GGUF")

    assert answer.json()["error"]["code"] == "MODEL_NOT_FOUND"


SMOLLM2 = "unsloth/SmolLM2-135M-Instruct-GGUF/SmolLM2-135M-Instruct-Q4_K_M.gguf"


def test_installed_paths_come_from_both_of_the_clis_listings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Found live on 12 September 2026: the variants listing leaves out every model
    downloaded by link, so a file SIRVIS had just downloaded read as not installed
    and asking again queued a second download. The row shapes are the CLI's own."""
    grouped = [{"modelKey": "qwen/qwen3.5-9b", "variants": [{
        "modelKey": "qwen/qwen3.5-9b@4bit", "path": "qwen/qwen3.5-9b",
        "indexedModelIdentifier": "qwen/qwen3.5-9b@lmstudio-community/Qwen3.5-9B-MLX-4bit",
    }]}]
    plain = [{
        "modelKey": "smollm2-135m-instruct", "variants": ["smollm2-135m-instruct"],
        "path": SMOLLM2, "indexedModelIdentifier": SMOLLM2,
    }]

    def listing(_binary: str | None, arguments: list[str]) -> list[object]:
        return grouped if "--variants" in arguments else plain

    monkeypatch.setattr(variants, "_run", listing)
    paths = variants.installed_paths("/somewhere/lms")
    monkeypatch.setattr(variants, "_run", lambda _binary, _arguments: None)

    assert paths is not None
    assert {"lmstudio-community/Qwen3.5-9B-MLX-4bit", SMOLLM2} <= paths
    assert variants.installed_paths("/somewhere/lms") is None, "nobody to ask is not nothing"


# ── Ordering, and what fits this machine ─────────────────────────────────────


def hub_search(
    rows: dict[str, list[dict[str, Any]]], seen: list[httpx.Request] | None = None
) -> httpx.AsyncClient:
    """Hugging Face's search, answering each format's rows and keeping what was asked."""

    def answer(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=rows.get(request.url.params["filter"], []))

    return httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="https://hf.test")


def test_a_search_asks_hugging_face_for_the_order_and_every_field_a_row_shows() -> None:
    seen: list[httpx.Request] = []

    asyncio.run(catalog.search(hub_search({}, seen), "qwen", "gguf", 10, frozenset(), sort="likes"))

    [request] = seen
    assert (request.url.params["sort"], request.url.params["limit"]) == ("likes", "10")
    assert set(request.url.params.get_list("expand[]")) == set(catalog.EXPANDED)


def test_all_time_downloads_rank_a_wider_page_because_hugging_face_cannot_sort_by_them() -> None:
    """Measured 12 September 2026: `sort=downloadsAllTime` answers HTTP 400."""
    seen: list[httpx.Request] = []
    rows = {"gguf": [
        {"id": "a/recent", "downloads": 900, "downloadsAllTime": 1_000},
        {"id": "b/classic", "downloads": 100, "downloadsAllTime": 50_000},
        {"id": "c/uncounted", "downloads": 500},
    ]}

    found = asyncio.run(catalog.search(
        hub_search(rows, seen), "", "gguf", 2, frozenset(), sort="downloads_all_time",
    ))

    assert (seen[0].url.params["sort"], seen[0].url.params["limit"]) == (
        "downloads", str(catalog.LOCAL_PAGE),
    )
    assert [row["repo_id"] for row in found["items"]] == ["b/classic", "a/recent"]


def test_what_fits_is_estimated_from_the_parameter_count_and_what_is_hidden_is_counted() -> None:
    memory = 24 * GIB
    rows = {"gguf": [
        {"id": "a/small-GGUF", "downloads": 3, "gguf": {"total": 8_000_000_000}},
        {"id": "b/huge-GGUF", "downloads": 2, "gguf": {"total": 70_000_000_000}},
        {"id": "c/nameless-GGUF", "downloads": 1},
    ]}

    found = asyncio.run(catalog.search(
        hub_search(rows), "", "gguf", 10, frozenset(), memory_bytes=memory, fits_only=True,
    ))

    [row] = found["items"]
    assert (row["repo_id"], row["fits"]) == ("a/small-GGUF", True)
    assert row["estimated_bytes"] == int(8_000_000_000 * catalog.GGUF_BYTES_PER_PARAMETER)
    assert (found["fits_filter"], found["hidden_too_large"], found["hidden_unknown_size"]) == (
        "applied", 1, 1,
    ), "a model with no parameter count is hidden as unknown, not passed as fitting"
    assert found["fit_budget_bytes"] == int(memory * catalog.FIT_SHARE)


def test_an_mlx_estimate_reads_the_precision_from_the_repository_name() -> None:
    rows = {"mlx": [
        {"id": "a/Model-bf16", "safetensors": {"total": 8_000_000_000}},
        {"id": "a/Model", "safetensors": {"total": 8_000_000_000}},
        {"id": "a/Model-4bit", "safetensors": {"total": 8_000_000_000}},
    ]}

    found = asyncio.run(catalog.search(
        hub_search(rows), "", "mlx", 10, frozenset(), sort="smallest",
    ))

    assert [(row["repo_id"], row["estimated_bytes"]) for row in found["items"]] == [
        ("a/Model-4bit", int(8_000_000_000 * 4 / 8 * catalog.MLX_OVERHEAD)),
        ("a/Model-bf16", int(8_000_000_000 * 16 / 8 * catalog.MLX_OVERHEAD)),
        ("a/Model", None),
    ], "smallest first, and a name that states no precision has no estimate and sorts last"


def test_without_the_machines_memory_the_fit_filter_hides_nothing_and_says_so() -> None:
    rows = {"gguf": [{"id": "a/small-GGUF", "gguf": {"total": 1_000}}]}

    found = asyncio.run(catalog.search(
        hub_search(rows), "", "gguf", 10, frozenset(), fits_only=True,
    ))

    assert found["fits_filter"] == "unavailable"
    assert [row["fits"] for row in found["items"]] == [None]


def test_the_catalog_route_orders_filters_and_refuses_an_order_it_does_not_know(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, app, _token = an_api(monkeypatch)
    app.state.machine_memory_bytes = 24 * GIB
    app.state.hub_client = hub_search({"gguf": [
        {"id": "a/small-GGUF", "likes": 1, "gguf": {"total": 8_000_000_000}},
        {"id": "b/huge-GGUF", "likes": 9, "gguf": {"total": 70_000_000_000}},
    ]})

    unknown = client.get("/api/v1/catalog", params={"sort": "loudest"})
    fitting = client.get(
        "/api/v1/catalog", params={"format": "gguf", "sort": "likes", "fits": "true"}
    ).json()

    assert unknown.json()["error"]["code"] == "UNSUPPORTED_PARAMETER"
    assert [row["repo_id"] for row in fitting["items"]] == ["a/small-GGUF"]
    assert (fitting["sort"], fitting["memory_bytes"], fitting["hidden_too_large"]) == (
        "likes", 24 * GIB, 1,
    )


def test_a_count_that_disagrees_with_the_name_by_tenfold_is_replaced_by_the_name() -> None:
    """Measured 12 September 2026: a 27B model split into shards counted 2.67 million
    parameters — its first shard — and ranked as a 1.6 MB download that fits anything."""
    rows = {"gguf": [
        {"id": "a/Model-27B-GGUF-shards", "gguf": {"total": 2_670_000}},
        {"id": "b/Model-30B-A3B-GGUF", "downloads": 1},
        {"id": "c/Model-8B-GGUF", "gguf": {"total": 8_190_000_000}},
        {"id": "d/Model-4bit-GGUF"},
    ]}

    found = asyncio.run(catalog.search(hub_search(rows), "", "gguf", 10, frozenset()))

    by_repo = {
        row["repo_id"]: (row["parameters"], row["parameters_source"]) for row in found["items"]
    }
    assert by_repo["a/Model-27B-GGUF-shards"] == (27_000_000_000, "name")
    assert by_repo["b/Model-30B-A3B-GGUF"] == (30_000_000_000, "name"), (
        "no count, so the name's size — the total, not the active experts"
    )
    assert by_repo["c/Model-8B-GGUF"] == (8_190_000_000, "count"), "a count near the name is kept"
    assert by_repo["d/Model-4bit-GGUF"] == (None, None), "`4bit` is a precision, not a size"
