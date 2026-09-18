"""SIRVIS's Reveal and Delete on an installed model, through NERVIS (SIRVIS 0.19.7).

The SIRVIS Models screen's row buttons name `sirvis.model.reveal` and `sirvis.model.delete`;
NERVIS makes the call with its admin credential, which the page never holds, and a refusal —
a loaded model, a shared file — reaches the person in SIRVIS's words. Both are button-only:
nothing a sentence says can propose moving a model to the Trash.
"""

from __future__ import annotations

from typing import Any

import httpx
from tests.test_m4_chat import an_api

from nervis import commands
from nervis.registry import RegistryState


def _sirvis(client: Any, answer: httpx.Response, sent: list[httpx.Request],
            capability: str = "sirvis.model_files") -> None:
    client.app.state.settings.sirvis_admin_credential = "admin-scoped"
    entry = client.app.state.registry.get("sirvis")
    assert entry is not None
    entry.state = RegistryState.HEALTHY
    entry.capabilities = {capability: "available"}

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return answer

    client.app.state.probe_client = httpx.AsyncClient(transport=httpx.MockTransport(capture))


def test_delete_asks_sirvis_with_the_admin_credential_and_returns_what_moved() -> None:
    sent: list[httpx.Request] = []
    client = an_api()
    _sirvis(client, httpx.Response(200, json={
        "runtime_key": "google/gemma", "moved": [{"from": "/m/g", "to": "/t/g"}], "kept": [],
    }), sent)

    answered = client.post("/api/v1/commands/run",
                           json={"operation": "sirvis.model.delete", "target": "lm_1"})

    assert answered.status_code == 200, answered.text
    assert sent[0].method == "DELETE" and str(sent[0].url).endswith("/api/v1/models/lm_1")
    assert sent[0].headers["authorization"] == "Bearer admin-scoped"
    assert sent[0].headers["content-type"] == "application/json", "SIRVIS refuses anything else"
    assert answered.json()["delete"]["moved"][0]["to"] == "/t/g"


def test_reveal_is_a_post_to_the_model_s_reveal() -> None:
    sent: list[httpx.Request] = []
    client = an_api()
    _sirvis(client, httpx.Response(200, json={"revealed": "/m/g", "in": "Finder"}), sent)

    answered = client.post("/api/v1/commands/run",
                           json={"operation": "sirvis.model.reveal", "target": "lm_1"})

    assert answered.status_code == 200, answered.text
    assert sent[0].method == "POST" and str(sent[0].url).endswith("/api/v1/models/lm_1/reveal")
    assert answered.json()["reveal"]["in"] == "Finder"


def test_a_refusal_reaches_the_person_in_sirvis_s_words() -> None:
    sent: list[httpx.Request] = []
    client = an_api()
    _sirvis(client, httpx.Response(409, json={"error": {
        "code": "RESOURCE_BUSY", "message": "google/gemma is loaded; unload it before deleting it",
    }}), sent)

    answered = client.post("/api/v1/commands/run",
                           json={"operation": "sirvis.model.delete", "target": "lm_1"})

    assert answered.status_code >= 400
    assert "unload it before deleting it" in answered.json()["error"]["message"]


def test_a_sirvis_without_the_capability_is_not_asked() -> None:
    sent: list[httpx.Request] = []
    client = an_api()
    _sirvis(client, httpx.Response(200, json={}), sent, capability="sirvis.downloads")

    answered = client.post("/api/v1/commands/run",
                           json={"operation": "sirvis.model.delete", "target": "lm_1"})

    assert answered.status_code >= 400 and sent == []


def test_neither_is_ever_offered_from_a_sentence() -> None:
    assert {"sirvis.model.reveal", "sirvis.model.delete"} <= commands.BUTTON_ONLY
    for said in ("delete the model gemma", "move gemma to the trash", "show me gemma's files"):
        proposal = commands.propose(said, [])
        assert proposal is None or proposal.operation.id not in commands.BUTTON_ONLY
