"""SIRVIS answers to loopback names and nothing else (§16 item 5)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from sirvis.app import create_app
from sirvis.config import Settings


@pytest.fixture()
def client(tmp_path: Any) -> Any:
    settings = Settings(database_path=str(tmp_path / "sirvis.db"), _env_file=None)  # type: ignore[call-arg]
    with TestClient(create_app(settings)) as made:
        yield made


@pytest.mark.parametrize("host", ["127.0.0.1:8721", "localhost:8721", "127.0.0.1"])
def test_a_loopback_host_is_served(client: Any, host: str) -> None:
    assert client.get("/api/v1/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["attacker.example", "attacker.example:8721"])
def test_a_rebound_name_is_refused(client: Any, host: str) -> None:
    """A read, which is the case `require` never sees.

    `check_origin` and the token check guard mutations. The disclosure this
    defends is a *read* of every model, benchmark result and machine detail on
    the box — no mutation involved, and no `Origin` header sent.
    """
    assert client.get("/api/v1/health", headers={"Host": host}).status_code == 403
