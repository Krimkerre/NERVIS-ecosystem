"""§11: *"Expect and handle: LM Studio unavailable."*

`/api/v1/models` did not. With the runtime down it raised
`RuntimeUnavailableError` out of the handler and answered **500** — and a 500
carries no CORS headers, so a browser saw "Failed to fetch" and could not even
report the status. Every other caller of `_inventory` already degraded; these
two were the exceptions, and they are the two a dashboard opens with.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sirvis.runtimes.base import RuntimeUnavailableError


class DeadRuntime:
    """A runtime that is installed and not listening."""

    async def list_models(self):  # noqa: ANN201
        raise RuntimeUnavailableError("GET /api/v0/models: All connection attempts failed")


@pytest.fixture()
def client(settings) -> TestClient:
    from sirvis.app import create_app

    made = TestClient(create_app(settings))
    made.__enter__()
    made.app.state.lmstudio = DeadRuntime()
    yield made
    made.__exit__(None, None, None)


def test_the_listing_answers_rather_than_failing(client: TestClient) -> None:
    response = client.get("/api/v1/models")

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_an_absent_runtime_is_not_an_empty_shelf(client: TestClient) -> None:
    """The distinction the collection cannot make on its own.

    "The runtime is not running" and "nothing is installed" are different facts
    and an empty `items` says both. So the runtime's state travels beside the
    items rather than being inferred from their absence.
    """
    runtime = client.get("/api/v1/models").json()["runtime"]

    assert runtime["available"] is False
    assert "connection attempts failed" in runtime["detail"]


def test_a_reachable_runtime_says_so_too(client: TestClient) -> None:
    """Reported in both directions, or a consumer would have to treat a missing
    key as "probably fine" — which is the guess this exists to remove."""

    class EmptyRuntime:
        async def list_models(self):  # noqa: ANN201
            return []

    client.app.state.lmstudio = EmptyRuntime()

    body = client.get("/api/v1/models").json()
    assert body["items"] == []
    assert body["runtime"] == {"available": True, "detail": ""}


def test_one_build_is_502_and_never_404(client: TestClient) -> None:
    """A 404 would claim the build does not exist. What is true is that nothing
    could be asked — and a caller told "no such model" deletes it from a list,
    while a caller told "the runtime is down" waits."""
    response = client.get("/api/v1/models/anything")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "RUNTIME_UNAVAILABLE"


def test_the_runtime_key_lookup_still_refuses(client: TestClient) -> None:
    """`?runtime_key=` is RAVIS's identity lookup, and it is the caller that
    would act on a wrong answer by dropping a model from its catalogue. It gets
    the same 502 as the single-build lookup, never an empty list."""
    response = client.get("/api/v1/models?runtime_key=whatever")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "RUNTIME_UNAVAILABLE"
