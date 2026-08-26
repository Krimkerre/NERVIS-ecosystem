"""M5 — what NERVIS reads from SIRVIS (§9), and the one thing it must not do.

M5's exit has five clauses. Three are met here: existing results appear,
provenance renders correctly, and **no benchmark business logic exists in
NERVIS**. Two are not, and cannot be: *"a benchmark launches through the SIRVIS
API"* and *"progress streams live"* need `sirvis.benchmarks.jobs@1`, which
SIRVIS advertises as **unavailable** because submit/poll/cancel lands with its
queue at M14 — and §1 forbids inventing another component's endpoint to get
there sooner. They are M5b.

The interesting tests here are the negative ones. §9's requirement is that
nothing is lost on the way through, and an absence is the one thing a reader
cannot see.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi.testclient import TestClient

from nervis.app import create_app
from nervis.config import Settings
from nervis.peers import sirvis as peer
from nervis.registry import RegistryState

# One evidence record shaped as SIRVIS publishes it, with every kind of
# provenance §9 names. Deliberately rich: the point of these tests is that all
# of it survives.
EVIDENCE = {
    "items": [
        {
            "evidence_id": "ev_1",
            "evidence_type": "MEASURED",
            "target": {
                "model_family": "qwen3-4b-2507", "format": "mlx",
                "quantization": "4bit", "runtime": "lmstudio", "variant": "var_a",
            },
            "role": "clarvis-chat",
            "metrics": {
                "generation_tokens_per_second": {
                    "median": 49.1, "mean": 49.2, "max": 49.9,
                    "samples": 5, "units": "tokens/second", "direction": "higher",
                },
                "tool_call_pass_rate": {"passed": 23, "total": 24, "phrasings": 8},
            },
            "runtime_configuration": {"context_length": 32768},
            "measured_at": "2026-08-20T11:02:00Z",
            "stale": False,
            "sirvis_version": "0.0.1",
            "machine_id": "mach_1",
        }
    ],
    "unresolved_candidates": ["a-build-nobody-installed"],
    "next_cursor": None,
}


def an_api(body: Any = None, *, status: int = 200,
           capabilities: dict[str, str] | None = None,
           state: RegistryState = RegistryState.HEALTHY) -> tuple[TestClient, list[httpx.Request]]:
    """NERVIS with a SIRVIS that answers exactly what the test says."""
    sent: list[httpx.Request] = []
    settings = Settings(
        database_path=":memory:",
        # Loopback literals: M2's SSRF guard refuses a hostname, because
        # resolving one would make the check depend on DNS at probe time.
        ravis_base_url="http://127.0.0.1:8731",
        sirvis_base_url="http://127.0.0.1:8721",
        clarvis_base_url="http://127.0.0.1:9",
        lmstudio_base_url="http://127.0.0.1:9",
        ollama_base_url="http://127.0.0.1:9",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if status >= 400:
            return httpx.Response(status, json={
                "error": {"code": "UNSUPPORTED_PARAMETER",
                          "message": "constraints are not implemented"}
            })
        return httpx.Response(200, json=EVIDENCE if body is None else body)

    app.state.probe_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    entry = app.state.registry.get("sirvis")
    assert entry is not None
    entry.state = state
    entry.capabilities = capabilities if capabilities is not None else {
        "sirvis.inventory.read": "available",
        "sirvis.benchmarks.results": "available",
        "sirvis.recommendations": "available",
        "sirvis.runtime_sets": "available",
        "sirvis.runtime.state.read": "available",
        "sirvis.benchmarks.jobs": "unavailable",
    }
    entry.checked_at = 1e12
    return TestClient(app), sent


# ── §9: nothing is lost on the way through ─────────────────────────────────


def test_an_evidence_record_arrives_byte_for_byte() -> None:
    """§9 lists what a view must preserve: MEASURED/ESTIMATED/UNKNOWN,
    timestamps, staleness, method, sample count, units and evidence links.

    Asserted as equality with what SIRVIS sent rather than field by field,
    because a field-by-field test only protects the fields somebody thought of.
    """
    client, _ = an_api()

    body = client.get("/api/v1/sirvis/evidence").json()

    assert body["available"] is True
    assert body["data"] == EVIDENCE


def test_nothing_rolls_up_or_ranks_on_the_way_through() -> None:
    """M5's exit: *no benchmark business logic exists in NERVIS.*

    §9 allows rollups in a *view*; it forbids NERVIS owning the arithmetic. The
    surest way to preserve provenance is to be in no position to drop it, so the
    reader adds no key and removes none.
    """
    client, _ = an_api()

    item = client.get("/api/v1/sirvis/evidence").json()["data"]["items"][0]

    assert set(item) == set(EVIDENCE["items"][0])
    metric = item["metrics"]["generation_tokens_per_second"]
    # A single number would be §9's forbidden "Model X: 93". Everything that
    # makes it readable is still here.
    assert {"median", "mean", "max", "samples", "units", "direction"} <= set(metric)


def test_a_refusal_carries_sirvis_s_own_code_and_message() -> None:
    """SIRVIS refuses `constraints` with UNSUPPORTED_PARAMETER, and that reason
    is more useful than "HTTP 422" — which is the whole point of §4.3's
    envelope."""
    client, _ = an_api(status=422)

    body = client.post("/api/v1/sirvis/recommendations", json={"roles": ["clarvis-chat"]}).json()

    assert body["available"] is False
    assert "UNSUPPORTED_PARAMETER" in body["reason"]
    assert "constraints are not implemented" in body["reason"]


# ── The one surface that refuses, and why it is listed at all ──────────────


def test_the_benchmark_job_surface_is_listed_and_refuses() -> None:
    """M5's other two exit clauses, stated as a test rather than left out.

    SIRVIS advertises `sirvis.benchmarks.jobs@1` as unavailable because its
    queue lands at M14. Listing the surface is how "planned and absent" shows,
    instead of leaving a reader to infer it from a gap — and §1 forbids
    inventing the endpoint to get there sooner.
    """
    client, sent = an_api()

    body = client.get("/api/v1/sirvis/jobs").json()

    assert sent == []  # §5.2: never calls a guessed endpoint
    assert body["available"] is False
    assert "sirvis.benchmarks.jobs" in body["reason"]


def test_the_index_lists_every_surface_with_its_verb() -> None:
    client, sent = an_api()

    surfaces = client.get("/api/v1/sirvis").json()["surfaces"]

    assert sent == []  # answering "which are readable" must not read them
    assert {s["key"] for s in surfaces} == set(peer.BY_KEY)
    verbs = {s["key"]: s["method"] for s in surfaces}
    # §14.3 makes the recommendation a POST: its inputs are a body and its
    # result is generated rather than stored.
    assert verbs["recommendations"] == "POST"
    assert verbs["evidence"] == "GET"


def test_a_post_surface_sends_a_body_and_a_get_surface_sends_a_query() -> None:
    client, sent = an_api(body={"profile": "clarvis", "ranked": {}})

    client.post("/api/v1/sirvis/recommendations", json={"roles": ["clarvis-chat"], "mode": "fast"})
    client.get("/api/v1/sirvis/evidence?limit=5")

    assert json.loads(sent[0].content)["roles"] == ["clarvis-chat"]
    assert sent[0].url.params.get("roles") is None
    assert sent[1].url.params["limit"] == "5"
    assert not sent[1].content


# ── The shared reader, which both peers now use ────────────────────────────


def test_both_peers_answer_with_the_same_envelope() -> None:
    """One shape however the read turned out, for either service.

    Two readers would drift, and a screen would then need to know which peer it
    was talking to in order to interpret a failure.
    """
    client, _ = an_api(state=RegistryState.UNREACHABLE, capabilities={})

    for path in ("/api/v1/sirvis/evidence", "/api/v1/ravis/providers"):
        body = client.get(path).json()
        assert set(body) == {"surface", "available", "availability", "reason", "data"}
        assert body["data"] is None
        assert body["reason"]


def test_an_unknown_sirvis_surface_lists_the_known_ones() -> None:
    client, _ = an_api()

    response = client.get("/api/v1/sirvis/telemetry")

    assert response.status_code == 404
    assert "evidence" in response.json()["error"]["details"]["known"]


def test_the_peer_routes_do_not_shadow_the_chat_router() -> None:
    """They did, and it took a failing M4 test to notice.

    `/api/v1/{service}` as a wildcard matches everything under `/api/v1`, so
    `/api/v1/chat/conversations` resolved to "peer `chat`, surface
    `conversations`" and 404'd. Two literal paths per peer cost two lines and
    cannot shadow a sibling that has not been written yet.
    """
    client, _ = an_api()

    assert client.get("/api/v1/chat/conversations").status_code == 200
    assert client.get("/api/v1/services").status_code == 200
    assert client.get("/api/v1/system").status_code == 200
