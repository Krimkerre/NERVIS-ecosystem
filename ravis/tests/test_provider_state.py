"""Turning a provider off (M10), and what that has to actually mean.

A toggle that changes a badge on a screen and nothing else is worse than no
toggle, so most of these assert the *consequence*: not listed, not a candidate,
not reachable by direct address.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.test_plural_upstreams import _built

from ravis.app import create_app
from ravis.config import Settings
from ravis.credentials import CredentialFile, CredentialStore
from ravis.provider_state import ProviderState
from ravis.transparent import merged_candidates, merged_catalogue

# ── The file ─────────────────────────────────────────────────────────────────


def test_a_provider_nobody_has_touched_is_enabled(tmp_path: Path) -> None:
    """The file records decisions, not state, so nothing needs migrating when a
    provider is added."""
    assert ProviderState(tmp_path / "providers.json").is_enabled("google")


def test_a_decision_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "providers.json"
    ProviderState(path).set_enabled("google", False)

    assert not ProviderState(path).is_enabled("google")


def test_re_enabling_is_recorded_rather_than_erased(tmp_path: Path) -> None:
    """So the file distinguishes "someone turned this back on" from "nobody has
    ever touched this"."""
    path = tmp_path / "providers.json"
    state = ProviderState(path)
    state.set_enabled("google", False)
    state.set_enabled("google", True)

    assert '"google"' in path.read_text()
    assert state.is_enabled("google")


def test_a_corrupt_file_leaves_everything_enabled(tmp_path: Path) -> None:
    """The permissive direction, deliberately. This is not a security control,
    and an operator whose file got mangled should find their gateway working
    rather than silently serving nothing."""
    path = tmp_path / "providers.json"
    path.write_text("{ not json", encoding="utf-8")

    assert ProviderState(path).is_enabled("google")


def test_the_state_file_is_not_pretending_to_be_secret(tmp_path: Path) -> None:
    """Unlike the credential file. Nothing here is a secret and a 0600 file
    would be a small lie about what it holds."""
    import stat

    path = tmp_path / "providers.json"
    ProviderState(path).set_enabled("google", False)

    assert stat.S_IMODE(path.stat().st_mode) & stat.S_IRUSR


# ── The consequence, on the pure functions ───────────────────────────────────


def _pair() -> dict[str, Any]:
    return {
        "lmstudio": _built("lmstudio", ["only-lms"]),
        "ollama": _built("ollama", ["only-ollama"]),
    }


def test_a_disabled_upstream_is_not_advertised() -> None:
    """Listing a model a request would then be refused for is worse than not
    listing it — the client picks from this very response (§5.0.1)."""
    ids = [e["id"] for e in merged_catalogue(_pair(), frozenset({"ollama"}))["data"]]

    assert "only-lms" in ids
    assert "only-ollama" not in ids


async def test_a_disabled_upstream_supplies_no_candidates() -> None:
    candidates = await merged_candidates(_pair(), None, frozenset({"ollama"}))

    assert "only-lms" in candidates
    assert "only-ollama" not in candidates


# ── The consequence, through the API ─────────────────────────────────────────


@pytest.fixture()
def client(tmp_path: Path) -> Any:
    app = create_app(Settings(database_path=":memory:", _env_file=None))  # type: ignore[call-arg]
    inner = app
    while not hasattr(inner, "state"):
        inner = inner.app  # type: ignore[attr-defined]
    inner.state.provider_state = ProviderState(tmp_path / "providers.json")
    inner.state.credentials = CredentialStore(
        file=CredentialFile(tmp_path / "credentials.json"), environment={}, keychain=False
    )
    with TestClient(app) as ready:
        yield ready


def test_the_toggle_round_trips(client: Any) -> None:
    assert client.put(
        "/api/v1/providers/anthropic/enabled", json={"enabled": False}
    ).json() == {"name": "anthropic", "enabled": False}

    rows = {r["name"]: r for r in client.get("/api/v1/providers").json()["items"]}
    assert not rows["anthropic"]["enabled"] if "anthropic" in rows else True


def test_a_disabled_provider_is_not_probed(client: Any) -> None:
    """An operator who switched something off should not have RAVIS keep
    calling it — and a timeout would slow the screen that explains why."""
    client.put("/api/v1/providers/default/enabled", json={"enabled": False})

    rows = {r["name"]: r for r in client.get("/api/v1/providers").json()["items"]}
    assert rows["default"]["reachable"] is None
    assert "disabled" in rows["default"]["detail"]


def test_addressing_a_disabled_provider_is_refused(client: Any) -> None:
    """Not left to the router: without this the translated fork finds no
    adapter, falls through to Path A, and forwards an Anthropic model to
    whatever the transparent upstream happens to be."""
    client.put("/api/v1/providers/anthropic/enabled", json={"enabled": False})

    response = client.post(
        "/v1/chat/completions",
        json={"model": "ravis/anthropic/claude-3", "messages": [{"role": "user", "content": "x"}]},
    )

    assert response.status_code == 503
    assert "disabled" in response.text
