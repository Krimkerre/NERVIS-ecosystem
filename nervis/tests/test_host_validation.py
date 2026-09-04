"""NERVIS answers to loopback names and nothing else (§16 item 5).

**Not in the audit's list, and included anyway.** §16 item 5 names RAVIS and
SIRVIS. NERVIS is the service actually opened in a browser, it proxies to the
other two, and it holds RAVIS's `admin.` credential — so a rebinding attacker
who found the other two shut would simply come here. Closing two of three doors
is not a defence.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis.app import create_app
from nervis.config import Settings


@pytest.fixture()
def client(tmp_path: Any) -> Any:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"), workspace_path=str(tmp_path),
        _env_file=None,
    )
    with TestClient(create_app(settings)) as made:
        yield made


@pytest.mark.parametrize("host", ["127.0.0.1:8790", "localhost:8790", "127.0.0.1", "[::1]:8790"])
def test_a_loopback_host_is_served(client: Any, host: str) -> None:
    assert client.get("/api/v1/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["attacker.example", "attacker.example:8790"])
def test_a_rebound_name_is_refused(client: Any, host: str) -> None:
    """The GET matters most here.

    A same-origin read carries no `Origin`, so nothing else in this service
    would notice — and what it would disclose is the whole registry, every
    peer's diagnostics, and which credentials are configured.
    """
    refused = client.get("/api/v1/health", headers={"Host": host})

    assert refused.status_code == 403
    assert "host" in refused.json()["error"]["message"].lower()


def test_the_refusal_lands_before_anything_reads_the_request(client: Any) -> None:
    """The check runs ahead of correlation, so a rebound request never reaches a
    handler, a database read, or the proxy that holds RAVIS's admin credential."""
    assert client.post(
        "/api/v1/ravis/pools/curate",
        headers={"Host": "attacker.example", "Content-Type": "application/json"},
        json={},
    ).status_code == 403
