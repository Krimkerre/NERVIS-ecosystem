"""`ravis/codex` on the OpenAI-compatible surface — RAVIS M29, increment R1.

The contract is `tests/fixtures/relay-contract/openai-refusal.json`, read here rather than copied.
The id is listed only for a client that asks, in both catalogue builders, from a kept answer and
nothing run; and a request naming it gets one OpenAI-shaped 400 that nothing happens before — no
upstream call, no route decision, no event.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.conftest_upstream import RecordingUpstream

from ravis.app import create_app
from ravis.config import Settings
from ravis.core.pools import DEFAULT_POOLS

CONTRACT = json.loads(
    (Path(__file__).parent / "fixtures" / "relay-contract" / "openai-refusal.json").read_text(
        encoding="utf-8"
    )
)
CHAT_REFUSALS = [
    case for case in CONTRACT["examples"] if case["request"]["path"] == "/v1/chat/completions"
]
EMBEDDINGS_REFUSAL = next(
    case for case in CONTRACT["examples"] if case["request"]["path"] == "/v1/embeddings"
)
ASKED = {"X-Clarvis-Engines": "codex"}
#: The listing contract's entry, exactly: no key beyond these three.
ENTRY = {"id": "ravis/codex", "object": "model", "owned_by": "ravis"}
#: Where the entry sits: straight after the pools.
AFTER_THE_POOLS = len(DEFAULT_POOLS)


def _app(**overrides: Any) -> Any:
    return create_app(
        Settings(database_path=":memory:", _env_file=None, **overrides)  # type: ignore[call-arg]
    )


def _ids(listing: dict[str, Any]) -> list[str]:
    return [entry["id"] for entry in listing["data"]]


def _with_upstream(app: Any, upstream: RecordingUpstream) -> Any:
    """Point the app's HTTP client at a recording upstream, as the transparent-proxy tests do."""
    client = httpx.AsyncClient(transport=upstream.transport())
    app.app.state.upstream_client = client
    app.app.state.model_registry.use_client(client)
    return app


# ── Listing ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("header", ["codex", "vscode, codex"])
def test_with_no_upstream_the_registry_lists_codex_after_the_pools_for_a_client_that_asks(
    header: str,
) -> None:
    app = _app(codex_enabled=True)
    assert not app.app.state.transparents  # so this is the builder for "no upstream declared"

    listing = TestClient(app).get("/v1/models", headers={"X-Clarvis-Engines": header}).json()

    assert listing["data"][AFTER_THE_POOLS] == ENTRY


@pytest.mark.parametrize(
    ("enabled", "headers"),
    [(True, {}), (True, {"X-Clarvis-Engines": "codex-preview"}), (False, ASKED)],
    ids=["no header", "another engine named", "Codex switched off"],
)
def test_with_no_upstream_the_registry_leaves_codex_out_otherwise(
    enabled: bool, headers: dict[str, str]
) -> None:
    listing = TestClient(_app(codex_enabled=enabled)).get("/v1/models", headers=headers).json()

    assert "ravis/codex" not in _ids(listing)


def test_with_an_upstream_the_merged_catalogue_lists_codex_between_the_pools_and_its_models() -> (
    None
):
    app = _with_upstream(
        _app(codex_enabled=True, upstream_base_url="http://upstream.invalid"), RecordingUpstream()
    )
    asyncio.run(app.app.state.model_registry.refresh())
    assert app.app.state.transparents  # so this is the merged builder

    listing = TestClient(app).get("/v1/models", headers=ASKED).json()

    assert listing["data"][AFTER_THE_POOLS] == ENTRY
    assert len(listing["data"]) > AFTER_THE_POOLS + 1  # the upstream's own models follow it


def test_with_an_upstream_the_merged_catalogue_leaves_codex_out_without_the_header() -> None:
    app = _with_upstream(
        _app(codex_enabled=True, upstream_base_url="http://upstream.invalid"), RecordingUpstream()
    )
    asyncio.run(app.app.state.model_registry.refresh())

    listing = TestClient(app).get("/v1/models").json()

    assert "ravis/codex" not in _ids(listing)


def test_the_management_model_list_never_carries_codex() -> None:
    """`/api/v1/models` feeds NERVIS chat and voice, which must not take Codex for a model."""
    response = TestClient(_app(codex_enabled=True)).get("/api/v1/models", headers=ASKED)

    assert response.status_code == 200
    assert "ravis/codex" not in response.text


def test_listing_reads_the_kept_answer_and_starts_no_program(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RAVIS.md §4.3: the listing starts nothing — no launch, no sign-in, no wait."""
    app = _app(codex_enabled=None)
    runtime = app.app.state.codex
    started: list[Any] = []

    def refuse(*args: Any, **_: Any) -> Any:
        started.append(args)
        raise AssertionError("listing the catalogue started a program")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse)
    client = TestClient(app)  # not entered: no lifespan, so no startup check runs either

    runtime.executable = Path("/kept/by/the/startup/check/codex")
    found = client.get("/v1/models", headers=ASKED).json()
    runtime.executable = None
    not_found = client.get("/v1/models", headers=ASKED).json()

    assert found["data"][AFTER_THE_POOLS] == ENTRY
    assert "ravis/codex" not in _ids(not_found)
    assert started == []
    assert runtime.report.state == "checking"


# ── The refusal ─────────────────────────────────────────────────────────────


def _routed_app(upstream: RecordingUpstream, *, codex_enabled: bool) -> Any:
    """A real app with an upstream and an event hub, so anything that ran would leave a trace."""
    return _with_upstream(
        _app(
            codex_enabled=codex_enabled,
            upstream_base_url="http://upstream.invalid",
            nervis_base_url="http://127.0.0.1:8790",
        ),
        upstream,
    )


@pytest.mark.parametrize("case", CHAT_REFUSALS, ids=[case["name"] for case in CHAT_REFUSALS])
def test_chat_answers_the_contracts_400_before_any_routing_upstream_call_or_event(
    case: dict[str, Any],
) -> None:
    upstream = RecordingUpstream()
    app = _routed_app(upstream, codex_enabled=case["request"]["codex_enabled"])

    response = TestClient(app).post(
        case["request"]["path"], json=case["request"]["body"], headers=case["request"]["headers"]
    )

    assert response.status_code == case["response"]["status"]
    assert response.json() == case["response"]["body"]
    assert upstream.requests == []
    assert app.app.state.decision_log.recent(10) == []
    assert list(app.app.state.events._pending) == []


@pytest.mark.parametrize("model", ["ravis/codex-mini", "ravis/codexes", "openai/ravis/codex"])
def test_a_model_merely_named_like_the_engine_is_left_to_routing(model: str) -> None:
    app = _routed_app(RecordingUpstream(), codex_enabled=True)

    response = TestClient(app).post(
        "/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.json().get("error", {}).get("type") != "agent_backend_not_a_chat_model"


@pytest.mark.parametrize(
    "embedding_base_url", ["http://embedder.invalid", ""], ids=["configured", "not configured"]
)
def test_embeddings_answer_the_same_400_before_forwarding(embedding_base_url: str) -> None:
    forwarded: list[httpx.Request] = []

    def forward(request: httpx.Request) -> httpx.Response:
        forwarded.append(request)
        return httpx.Response(200, json={"object": "list", "data": []})

    app = _app(
        codex_enabled=EMBEDDINGS_REFUSAL["request"]["codex_enabled"],
        embedding_base_url=embedding_base_url,
    )
    app.app.state.upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(forward))

    response = TestClient(app).post("/v1/embeddings", json=EMBEDDINGS_REFUSAL["request"]["body"])

    assert response.status_code == EMBEDDINGS_REFUSAL["response"]["status"]
    assert response.json() == EMBEDDINGS_REFUSAL["response"]["body"]
    assert forwarded == []
