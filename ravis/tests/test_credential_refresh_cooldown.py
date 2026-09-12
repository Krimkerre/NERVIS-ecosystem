"""A credential write is idempotent in effect, not only in state.

Saving a key re-reads that provider's catalogue, because a new key is usually
why the catalogue was empty. Saving the *same* key twice always left the same
state — which is why §15.1's `Idempotency-Key` was left off these writes — but
the second write repeated a live network refresh anyway. STATUS.md named the
honest repair as a refresh cooldown rather than a replay cache; these tests pin
it from both sides, because a cooldown that swallowed a real key change would be
a worse bug than the one it fixes.

The registry is the real `ModelRegistry` over an in-process upstream (§14.5), so
`refreshed_at` and `last_error` are set by the code production runs rather than
by a fake's idea of it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ravis.api.management import audit
from ravis.api.management import credentials as credential_api
from ravis.app import create_app
from ravis.config import Settings
from ravis.registry import ModelRegistry
from ravis.upstream import Upstream

ADMIN_SECRET = "admin-secret-for-tests"
PATH = "/api/v1/providers/credentials/demo"


class _CountingUpstream:
    """An OpenAI-compatible catalogue that counts its fetches and can be made to fail."""

    def __init__(self) -> None:
        self.fetches = 0
        self.failing = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v0/models":
            # The residency probe `refresh()` makes after a successful fetch.
            # This is not LM Studio, so a 404 — and it is not a catalogue
            # fetch, so it is not counted.
            return httpx.Response(404, json={"error": "not found"})
        self.fetches += 1
        if self.failing:
            return httpx.Response(503, json={"error": {"message": "unavailable"}})
        return httpx.Response(
            200, json={"object": "list", "data": [{"id": "demo/one"}, {"id": "demo/two"}]}
        )


@dataclass
class _Spec:
    kind: str = "demo"


@dataclass
class _Built:
    """The two parts of a transparent upstream a credential write reads."""

    registry: ModelRegistry
    spec: _Spec


class _World:
    """One RAVIS with one declared upstream, `demo`, and what its writes audited."""

    def __init__(
        self, client: TestClient, upstream: _CountingUpstream, registry: ModelRegistry
    ) -> None:
        self.client = client
        self.upstream = upstream
        self.registry = registry
        self.audits: list[dict[str, Any]] = []

    def put(self, secret: str) -> dict[str, Any]:
        response = self.client.put(PATH, json={"secret": secret})
        assert response.status_code == 200, response.text
        return dict(response.json())


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_World]:
    # The store reads its directory from the environment when the app is built,
    # so this must come first — a test must never write the operator's file.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app = create_app(Settings(database_path=":memory:", _env_file=None))  # type: ignore[call-arg]
    upstream = _CountingUpstream()
    registry = ModelRegistry(
        Upstream(base_url="http://demo.invalid", declared_key=""),
        httpx.AsyncClient(transport=httpx.MockTransport(upstream.handle)),
        ttl_seconds=300.0,
    )
    with TestClient(app) as client:
        # `create_app` returns the body-size wrapper; the state is one level in.
        inner = client.app.app  # type: ignore[attr-defined]
        inner.state.credentials.store("admin.tests", ADMIN_SECRET)
        client.headers.update({"Authorization": f"Bearer {ADMIN_SECRET}"})
        inner.state.transparents = {"demo": _Built(registry=registry, spec=_Spec())}
        here = _World(client, upstream, registry)

        def capture(_request: Any, action: str, **facts: Any) -> None:
            if action == audit.ACTION_CREDENTIAL_SET:
                here.audits.append(facts)

        monkeypatch.setattr(audit, "record", capture)
        yield here


def test_replaying_the_same_key_does_not_repeat_the_refresh(world: _World) -> None:
    """The recorded defect: every replay was another network round trip.

    The replay still answers with the catalogue total — read from the snapshot
    the first write's refresh left — so its body is the original's, which is
    what idempotent means. The audit is where the difference is recorded.
    """
    first = world.put("sk-demo-one")
    again = world.put("sk-demo-one")

    assert world.upstream.fetches == 1
    assert first["catalogue_total"] == again["catalogue_total"] == 2
    assert [facts["catalogue_refreshed"] for facts in world.audits] == [True, False]


def test_a_changed_key_refreshes_at_once_inside_the_cooldown(world: _World) -> None:
    """The half a cooldown could get wrong.

    A different key can unlock a different catalogue. Making it wait out a
    timer would bring back, in a shorter form, the bug the refresh was added
    for: a provider keyed a moment ago that reports no models.
    """
    world.put("sk-demo-one")
    world.put("sk-demo-two")

    assert world.upstream.fetches == 2


def test_a_new_key_refreshes_a_catalogue_read_a_moment_ago(world: _World) -> None:
    """Startup warms every catalogue, so a fresh snapshot is the ordinary state
    when somebody types a key in — and the key is the reason to look again. A
    cooldown keyed on time alone would answer with the keyless listing."""
    asyncio.run(world.registry.refresh())

    world.put("sk-demo-one")

    assert world.upstream.fetches == 2


def test_the_same_key_refreshes_again_once_the_cooldown_has_passed(world: _World) -> None:
    """Re-saving an unchanged key is also how a person asks RAVIS to look again."""
    world.put("sk-demo-one")
    world.registry.snapshot.refreshed_at -= credential_api.REFRESH_COOLDOWN_SECONDS + 1

    world.put("sk-demo-one")

    assert world.upstream.fetches == 2


def test_a_failed_refresh_is_not_held_against_a_replay(world: _World) -> None:
    """A failed fetch keeps the old `refreshed_at` and records `last_error`.

    Reading the timestamp alone would call a catalogue fresh that the latest
    attempt could not reach — a failed read, cached, which no catalogue cache in
    RAVIS does.
    """
    world.put("sk-demo-one")
    world.upstream.failing = True
    asyncio.run(world.registry.refresh())
    world.upstream.failing = False

    world.put("sk-demo-one")

    assert world.upstream.fetches == 3


def test_forgetting_a_key_touches_no_catalogue(world: _World) -> None:
    """Delete has no network effect, so repeating it repeats nothing."""
    world.put("sk-demo-one")

    for _ in range(2):
        assert world.client.delete(PATH).status_code == 200

    assert world.upstream.fetches == 1
