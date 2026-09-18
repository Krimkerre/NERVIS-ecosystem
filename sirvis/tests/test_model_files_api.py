"""SIRVIS's Reveal and Delete endpoints (§8, SIRVIS 0.19.7).

The runtime's answer, LM Studio's two listings, the models folder and the Trash are all stated
here, so nothing touches this machine's models: `locate` works on a tree under `tmp_path`,
Delete moves into a Trash under `tmp_path`, and Reveal is recorded rather than run.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sirvis.api import routes
from sirvis.api.security import Scope, mint_token
from sirvis.app import create_app
from sirvis.config import Settings
from sirvis.core.inventory import build_inventory
from sirvis.model_files import to_trash
from sirvis.runtimes import LMStudioAdapter

MODEL = "mlx-community/tiny-4bit"


def _entry(state: str) -> dict[str, Any]:
    return {"id": "tiny", "runtime_key": "lmstudio", "compatibility_type": "mlx",
            "quantization": "4bit", "arch": "llama", "publisher": "mlx-community", "type": "llm",
            "max_context_length": 8192, "state": state}


def an_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, state: str = "not-loaded"
           ) -> tuple[TestClient, str, str, list[Path]]:
    models = tmp_path / "models"
    (models / MODEL).mkdir(parents=True)
    (models / MODEL / "model.safetensors").write_bytes(b"x" * 64)
    settings = Settings(database_path=":memory:", lmstudio_base_url="http://127.0.0.1:9",
                        lmstudio_cli_path="/nonexistent/lms", lmstudio_models_path=str(models),
                        _env_file=None)  # type: ignore[call-arg]
    app = create_app(settings)
    inventory = build_inventory([_entry(state)])

    async def stated(_request: Any) -> Any:
        return inventory

    shown: list[Path] = []
    monkeypatch.setattr(routes, "_inventory", stated)
    monkeypatch.setattr(LMStudioAdapter, "listings",
                        lambda _self: ([{"modelKey": "tiny", "path": MODEL}], []))
    monkeypatch.setattr(routes, "to_trash",
                        functools.partial(to_trash, system="Darwin", trash=tmp_path / ".Trash"))
    monkeypatch.setattr(routes, "reveal", lambda path: shown.append(path) or "Finder")
    model_id = next(iter(inventory.installed.values())).local_model_id
    token = mint_token(app.state.database, "operator", {Scope.ADMIN})
    return TestClient(app), token, model_id, shown


def _as(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def test_the_files_are_read_and_delete_moves_them_to_the_trash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, token, model_id, _ = an_api(monkeypatch, tmp_path)

    files = client.get(f"/api/v1/models/{model_id}/files").json()
    deleted = client.request("DELETE", f"/api/v1/models/{model_id}", json={}, headers=_as(token))

    assert files["targets"] == [str(tmp_path / "models" / MODEL)] and files["size_bytes"] == 64
    assert deleted.status_code == 200, deleted.text
    assert not (tmp_path / "models" / MODEL).exists()
    assert (tmp_path / ".Trash" / "tiny-4bit" / "model.safetensors").exists()


def test_both_need_admin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client, _, model_id, shown = an_api(monkeypatch, tmp_path)

    assert client.request("DELETE", f"/api/v1/models/{model_id}", json={}).status_code in (401, 403)
    assert client.post(f"/api/v1/models/{model_id}/reveal", json={}).status_code in (401, 403)
    assert (tmp_path / "models" / MODEL).exists() and shown == []


def test_a_loaded_model_is_not_moved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client, token, model_id, _ = an_api(monkeypatch, tmp_path, state="loaded")

    refused = client.request("DELETE", f"/api/v1/models/{model_id}", json={}, headers=_as(token))

    assert refused.status_code == 409
    assert "unload it before deleting it" in refused.json()["error"]["message"]
    assert (tmp_path / "models" / MODEL).exists()


def test_reveal_shows_the_model_s_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client, token, model_id, shown = an_api(monkeypatch, tmp_path)

    answered = client.post(f"/api/v1/models/{model_id}/reveal", json={}, headers=_as(token))

    assert answered.status_code == 200, answered.text
    assert shown == [tmp_path / "models" / MODEL] and answered.json()["in"] == "Finder"
