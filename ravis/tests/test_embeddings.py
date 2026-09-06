"""`POST /v1/embeddings` — one configured local model, no routing (RAVIS.md §4.2).

Same shape as `test_fallback.py`: an in-process fake transport (runbook §14.5),
route-level `TestClient` calls, real assertions on what actually came back.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi.testclient import TestClient

from ravis.app import create_app
from ravis.config import Settings

EMBEDDING_URL = "http://embedder.invalid"


def _app_with(handler: Any, **overrides: Any) -> TestClient:
    """The real application, with only the transport underneath it replaced.

    No chat upstream configured — embeddings touch no pool and no catalogue,
    so unlike `test_fallback.py`'s `_app_with` this needs neither a model
    catalogue nor a routable upstream to be a legitimate, working state.
    """
    settings = Settings(
        database_path=":memory:",
        _env_file=None,  # type: ignore[call-arg]
        **{"embedding_base_url": EMBEDDING_URL, "embedding_model": "all-minilm", **overrides},
    )
    app = create_app(settings)
    fake_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.app.state.upstream_client = fake_client
    return TestClient(app)


def test_a_configured_model_answers_with_real_vectors() -> None:
    """The ordinary case: forward to the one configured local model."""
    seen: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        return httpx.Response(200, json={
            "object": "list",
            "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
            "model": "all-minilm",
        })

    with _app_with(handle) as client:
        response = client.post("/v1/embeddings", json={"input": "how does RAVIS decide"})

    assert response.status_code == 200
    body = response.json()
    assert body["data"][0]["embedding"] == [0.1, 0.2, 0.3]
    assert body["model"] == "all-minilm"
    assert seen == [{"model": "all-minilm", "input": "how does RAVIS decide"}]


def test_no_model_configured_refuses_rather_than_guessing() -> None:
    """An empty `embedding_base_url` is a real, expected state — refuse it
    honestly rather than reaching for an address nobody configured."""
    with _app_with(lambda _: httpx.Response(200), embedding_base_url="") as client:
        response = client.post("/v1/embeddings", json={"input": "hello"})

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "embedding_not_configured"


def test_missing_input_is_a_client_error_not_a_forwarded_request() -> None:
    """§4.5: a malformed request is refused before anything is forwarded."""
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200)

    with _app_with(handle) as client:
        response = client.post("/v1/embeddings", json={})

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert not seen, "the request was forwarded despite having no input"


def test_an_unreachable_runtime_refuses_with_a_stated_cause() -> None:
    """A dead local runtime is a 503 naming the cause, not a 500."""

    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with _app_with(handle) as client:
        response = client.post("/v1/embeddings", json={"input": "hello"})

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "embedding_runtime_unreachable"


def test_a_runtime_error_response_is_relayed_as_a_refusal() -> None:
    """The runtime answered, but not with success — a 502, not a fabricated vector."""

    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(404, json={"error": "model 'all-minilm' not found"})

    with _app_with(handle) as client:
        response = client.post("/v1/embeddings", json={"input": "hello"})

    assert response.status_code == 502
    assert response.json()["error"]["type"] == "embedding_runtime_refused"


def test_a_malformed_success_body_is_a_refusal_not_a_crash() -> None:
    """A 200 with a shape this route did not ask for is still not an answer —
    the same lesson `test_fallback.py` learned about a 200 carrying an error."""

    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"unexpected": "shape"})

    with _app_with(handle) as client:
        response = client.post("/v1/embeddings", json={"input": "hello"})

    assert response.status_code == 502
    assert response.json()["error"]["type"] == "embedding_runtime_refused"
