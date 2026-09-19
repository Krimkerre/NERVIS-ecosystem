"""A hosted provider declared before its key exists (19 September 2026).

On the owner's Linux laptop, an OpenRouter key saved on the Credentials screen was stored and used
by nothing: the launcher declared a hosted upstream only when a key was already stored at start,
so OpenRouter stayed off the Providers screen until the stack was restarted. The launcher now
declares every hosted kind it knows, key or no key, and RAVIS holds one without a key as waiting
for it: no catalogue, no route, no probe — OpenRouter's catalogue is public, and fetching it
keyless would offer hundreds of models that refuse every request.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ravis.app import create_app
from ravis.config import Settings
from ravis.credentials import CredentialFile, CredentialStore
from ravis.providers.generic_openai import GenericOpenAiAdapter
from ravis.transparent import build_transparents

ADMIN_SECRET = "admin-secret-for-tests"
# What `tools/run.py`'s `_default_upstreams` declares, less the two local runtimes' own probes.
DECLARED = json.dumps([
    {"name": "openrouter", "kind": "openrouter"},
    {"name": "openai", "kind": "openai", "base_url": "http://127.0.0.1:9"},
])


class _Catalogue:
    """OpenRouter's `/models`, public as the real one is, counting who asks."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/v0/models"):
            # LM Studio's residency read, which a refresh tries after any catalogue fetch; a
            # service that isn't LM Studio answers 404 and it is not the catalogue.
            return httpx.Response(404)
        self.asked.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json={"object": "list", "data": [{"id": "openai/gpt-x"}]})


def test_a_hosted_kind_waits_for_its_key_and_a_local_one_never_does(tmp_path: Path) -> None:
    settings = Settings(database_path=":memory:", upstreams=DECLARED,  # type: ignore[call-arg]
                        _env_file=None)
    catalogue = _Catalogue()
    client = httpx.AsyncClient(transport=httpx.MockTransport(catalogue.handle))
    store = CredentialStore(keychain=False, allow_environment=False,
                            file=CredentialFile(tmp_path / "credentials.json"))
    built = build_transparents(settings, client, store)
    openrouter = built["openrouter"]

    # An `openai` kind pointed at this machine is somebody's local server, which may want no key.
    assert openrouter.upstream.requires_key and not built["openai"].upstream.requires_key
    asyncio.run(openrouter.registry.refresh())
    assert catalogue.asked == [], "nothing asked of a service with no key"
    assert openrouter.registry.model_ids() == [] and not openrouter.adapter.has_credential

    store.store("openrouter", "sk-or-typed-in-later")
    asyncio.run(openrouter.registry.refresh())
    assert openrouter.registry.model_ids() == ["openai/gpt-x"]
    assert catalogue.asked == ["Bearer sk-or-typed-in-later"]
    assert openrouter.adapter.has_credential


@pytest.fixture()
def ravis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, _Catalogue]]:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # never the operator's own store
    app = create_app(Settings(database_path=":memory:", upstreams=DECLARED,  # type: ignore[call-arg]
                              _env_file=None))
    catalogue = _Catalogue()
    with TestClient(app) as client:
        inner = client.app.app  # type: ignore[attr-defined]
        inner.state.credentials.store("admin.tests", ADMIN_SECRET)
        client.headers.update({"Authorization": f"Bearer {ADMIN_SECRET}"})
        inner.state.transparents["openrouter"].registry.use_client(
            httpx.AsyncClient(transport=httpx.MockTransport(catalogue.handle)))
        yield client, catalogue


def test_a_key_saved_after_start_brings_the_provider_in_without_a_restart(
    ravis: tuple[TestClient, _Catalogue], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, catalogue = ravis
    probed: list[str] = []

    async def probe(adapter: GenericOpenAiAdapter) -> object:
        probed.append(adapter.name)
        raise AssertionError("a keyless hosted provider was probed")

    monkeypatch.setattr(GenericOpenAiAdapter, "health", probe)
    rows = {row["name"]: row for row in client.get("/api/v1/providers").json()["items"]}

    assert rows["openrouter"]["detail"] == "no key yet — not probed"
    assert rows["openrouter"]["reachable"] is None and "openrouter" not in probed
    assert catalogue.asked == []

    saved = client.put("/api/v1/providers/credentials/openrouter", json={"secret": "sk-or-new"})

    assert saved.status_code == 200, saved.text
    assert saved.json()["catalogue_total"] == 1, "its models, on the save — not the next start"
    assert catalogue.asked == ["Bearer sk-or-new"]
