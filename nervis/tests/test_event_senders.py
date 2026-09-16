"""A batch of events proves its sender (NERVIS 0.34.18; the security review's S7).

Until then anything able to reach NERVIS's port could post events, which NERVIS stores, shows
and turns into notifications. Now a batch presents a service's events secret — minted by the
launcher, one per service — or a registered editor window's own token, is refused whole without
one, and each event in it must name the sender that credential proves.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.test_m8b_status import a_bridge, register

from nervis.app import create_app
from nervis.config import Settings

# This file sets up its own senders (`conftest.page_control_token`).
CHECKS_EVENT_SENDERS = True

RAVIS_SECRET = "ravis-events-secret-for-this-test"
SIRVIS_SECRET = "sirvis-events-secret-for-this-test"


def event(service: str, instance: str = "", event_id: str = "e1") -> dict[str, Any]:
    return {
        "event_id": event_id,
        "event_type": f"{service}.something.happened",
        "event_version": "1.0.0",
        "occurred_at": "2026-09-16T21:00:00.000Z",
        "source": {"service_type": service, "service_id": f"{service}-1", "instance_id": instance},
        "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        "severity": "info",
        "data": {},
    }


def bearer(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


@pytest.fixture()
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv(
        "NERVIS_EVENT_PRODUCER_SECRETS",
        json.dumps({"ravis": RAVIS_SECRET, "sirvis": SIRVIS_SECRET}),
    )
    with TestClient(create_app(Settings(database_path=str(tmp_path / "nervis.db")))) as client:
        yield client


def stored(api: TestClient) -> list[str]:
    return [item["event_id"] for item in api.get("/api/v1/events?limit=100").json()["items"]
            if not str(item.get("event_type", "")).startswith("nervis.")]


@pytest.mark.parametrize(
    "headers", [{}, bearer(""), bearer("a guess"), {"Authorization": "Basic x"}]
)
def test_a_batch_without_a_senders_credential_is_refused_and_nothing_is_kept(
    api: TestClient, headers: dict[str, str]
) -> None:
    answered = api.post("/api/v1/events", json=[event("ravis")], headers=headers)
    assert answered.status_code == 401
    assert answered.json()["error"]["code"] == "UNAUTHORIZED"
    assert stored(api) == []


def test_a_services_secret_proves_that_service_and_no_other(api: TestClient) -> None:
    answered = api.post(
        "/api/v1/events",
        json=[
            event("ravis", "i1", "own"), event("sirvis", "", "other"), event("clarvis", "", "win"),
        ],
        headers=bearer(RAVIS_SECRET),
    ).json()
    assert answered["accepted"] == 1
    assert [one["reason"] for one in answered["rejected"]] == ["unproven_source"] * 2
    assert stored(api) == ["own"]
    assert api.post("/api/v1/events", json=[event("sirvis", "", "sirvis-own")],
                    headers=bearer(SIRVIS_SECRET)).json()["accepted"] == 1


def test_a_window_s_token_proves_that_window_only(api: TestClient) -> None:
    bridge = a_bridge(lambda _: (200, "{}"))
    try:
        instance_id, token = register(api, bridge.server_address[1])
        answered = api.post(
            "/api/v1/events",
            json=[
                event("clarvis", instance_id, "mine"),
                event("clarvis", "another-window", "theirs"),
                event("clarvis", "", "no-window"),
                event("ravis", "", "as-ravis"),
            ],
            headers=bearer(token),
        ).json()
    finally:
        bridge.shutdown()
    assert answered["accepted"] == 1
    assert len(answered["rejected"]) == 3
    assert stored(api) == ["mine"]


def test_a_service_secret_cannot_claim_a_registered_window(api: TestClient) -> None:
    """Even when the secret proves the right service type, a registered window's id needs that
    window's token — the check that was already there, still standing."""
    bridge = a_bridge(lambda _: (200, "{}"))
    try:
        instance_id, _ = register(api, bridge.server_address[1])
        secrets = {"clarvis": "a-clarvis-events-secret-for-this-test"}
        api.app.state.settings.event_producer_secrets = secrets
        answered = api.post("/api/v1/events", json=[event("clarvis", instance_id)],
                            headers=bearer(secrets["clarvis"])).json()
    finally:
        bridge.shutdown()
    assert answered["accepted"] == 0
    assert answered["rejected"][0]["reason"] == "unproven_instance"


def test_with_no_secrets_configured_every_service_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A NERVIS started without the launcher knows no service's secret, so no service's events
    get in — refused, not quietly accepted."""
    monkeypatch.delenv("NERVIS_EVENT_PRODUCER_SECRETS", raising=False)
    with TestClient(create_app(Settings(database_path=str(tmp_path / "nervis.db")))) as client:
        answered = client.post("/api/v1/events", json=[event("ravis")], headers=bearer(""))
        assert answered.status_code == 401


def test_the_refusal_names_no_secret(api: TestClient) -> None:
    answered = api.post("/api/v1/events", json=[event("ravis")], headers=bearer("x" * 40))
    assert RAVIS_SECRET not in answered.text and SIRVIS_SECRET not in answered.text


def test_a_known_service_refused_is_said_on_the_event_read_and_a_stranger_is_not(
    api: TestClient,
) -> None:
    assert api.get("/api/v1/events").json()["refused_senders"] == {}
    for _ in range(3):
        api.post("/api/v1/events", json=[event("ravis"), event("mallory")], headers={})
    refused = api.get("/api/v1/events").json()["refused_senders"]
    assert set(refused) == {"ravis"}
    assert refused["ravis"]["batches"] == 3
    assert refused["ravis"]["since"] <= refused["ravis"]["last"]
    api.post("/api/v1/events", json=[event("ravis")], headers=bearer(RAVIS_SECRET))
    assert api.get("/api/v1/events").json()["refused_senders"]["ravis"]["batches"] == 3
