"""Models reached through LM Studio's LM Link, told apart from this machine's (19 September 2026).

On the owner's ThinkPad, LM Link showed the Mac's models in LM Studio's REST listing with no mark
at all, so SIRVIS and the trays called a Mac-hosted model "loaded here". `lms ls --json` does say:
`deviceIdentifier` is null for this machine and the other device's id for a linked build. The rows
below are the ThinkPad's own, trimmed to the fields read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sirvis.api import routes
from sirvis.api.security import Scope, mint_token
from sirvis.app import create_app
from sirvis.config import Settings
from sirvis.core.inventory import build_inventory
from sirvis.runtimes import LMStudioAdapter, variants
from sirvis.runtimes.variants import link_device_names, linked_device, linked_devices

MAC = "93c2fceb2889bcc190ba21dc03828d12"
THINKPAD_LS = [
    {"modelKey": "ibm/granite-4-h-tiny", "format": "gguf", "deviceIdentifier": None},
    {"modelKey": "google/gemma-4-e2b", "format": "gguf", "deviceIdentifier": None},
    {"modelKey": "text-embedding-nomic-embed-text-v1.5", "format": "gguf",
     "deviceIdentifier": None},
    {"modelKey": "qwen/qwen3.5-9b", "format": "safetensors", "deviceIdentifier": MAC},
    {"modelKey": "google/gemma-4-e2b", "format": "safetensors", "deviceIdentifier": MAC},
    {"modelKey": "text-embedding-nomic-embed-text-v1.5", "format": "gguf", "deviceIdentifier": MAC},
]


def test_each_build_is_placed_on_the_device_that_holds_it() -> None:
    table = linked_devices(list(THINKPAD_LS))

    assert linked_device("qwen/qwen3.5-9b", "mlx", table) == MAC
    assert linked_device("qwen/qwen3.5-9b@4bit", "mlx", table) == MAC, "a qualified key too"
    assert linked_device("google/gemma-4-e2b", "mlx", table) == MAC, "the Mac's MLX build"
    assert linked_device("google/gemma-4-e2b", "gguf", table) is None, "the ThinkPad's own GGUF"
    assert linked_device("ibm/granite-4-h-tiny", "gguf", table) is None


def test_a_build_both_machines_hold_is_not_claimed_for_the_other_one() -> None:
    """LM Studio lists one of the two and says not which; SIRVIS doesn't guess."""
    table = linked_devices(list(THINKPAD_LS))
    assert linked_device("text-embedding-nomic-embed-text-v1.5", "gguf", table) is None
    assert linked_device("never-listed", "gguf", table) is None
    assert linked_devices([]) == {}


def _entry(runtime_id: str, compatibility: str) -> dict[str, Any]:
    return {"id": runtime_id, "compatibility_type": compatibility, "quantization": "4bit",
            "arch": "qwen3_5", "publisher": "qwen", "type": "llm", "max_context_length": 8192,
            "state": "loaded"}


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, str, str]:
    settings = Settings(database_path=":memory:", lmstudio_base_url="http://127.0.0.1:9",
                        lmstudio_cli_path="/nonexistent/lms",
                        lmstudio_models_path=str(tmp_path / "models"),
                        _env_file=None)  # type: ignore[call-arg]
    app = create_app(settings)
    inventory = build_inventory([_entry("qwen/qwen3.5-9b", "mlx")])

    async def stated(_request: Any) -> Any:
        return inventory

    monkeypatch.setattr(routes, "_inventory", stated)
    monkeypatch.setattr(LMStudioAdapter, "listings", lambda _self: (list(THINKPAD_LS), []))
    monkeypatch.setattr(LMStudioAdapter, "link_device_names", lambda _self: {MAC: "Govert.local"})
    model_id = next(iter(inventory.installed.values())).local_model_id
    token = mint_token(app.state.database, "operator", {Scope.ADMIN, Scope.BENCHMARK})
    return TestClient(app), model_id, token


def test_the_model_list_says_which_device_a_build_runs_on(
    api: tuple[TestClient, str, str]
) -> None:
    client, _, _ = api
    rows = client.get("/api/v1/models").json()["items"]
    assert rows[0]["runtime_key"] == "qwen/qwen3.5-9b" and rows[0]["linked_device"] == MAC
    assert rows[0]["linked_device_name"] == "Govert.local", "named as the owner named the Mac"


def test_the_other_devices_are_named_from_lm_link_s_own_status(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Mac's `lms link status --json`, 19 September 2026."""
    status = {"status": "online", "issues": [], "deviceIdentifier": MAC,
              "deviceName": "Govert.local",
              "peers": [{"deviceIdentifier": "a5c01f10f08575ddb8ac5bea2c0b52d4",
                         "deviceName": "ThinkPadX13G2", "status": "connected", "loadedModels": []}]}
    monkeypatch.setattr(variants, "_ask", lambda _binary, _arguments: status)
    assert link_device_names("lms") == {"a5c01f10f08575ddb8ac5bea2c0b52d4": "ThinkPadX13G2"}

    for unreadable in (None, [], {"status": "offline"}, {"peers": [{"deviceName": "x"}]}):
        monkeypatch.setattr(variants, "_ask", lambda _binary, _arguments, said=unreadable: said)
        assert link_device_names("lms") == {}, unreadable


def test_a_linked_build_s_files_are_left_to_its_own_machine(
    api: tuple[TestClient, str, str]
) -> None:
    client, model_id, token = api
    refused = client.get(f"/api/v1/models/{model_id}/files")
    deleted = client.request("DELETE", f"/api/v1/models/{model_id}", json={},
                             headers={"authorization": f"Bearer {token}"})

    for answer in (refused, deleted):
        assert answer.status_code == 409
        assert "runs on another device through LM Link" in answer.json()["error"]["message"]


def test_a_linked_build_is_not_benchmarked_here(api: tuple[TestClient, str, str]) -> None:
    client, _, token = api
    refused = client.post(
        "/api/v1/benchmark-jobs",
        json={"specification": {"target": {"model": "qwen/qwen3.5-9b"}}},
        headers={"authorization": f"Bearer {token}"},
    )
    assert refused.status_code >= 400
    assert "benchmark it on that machine" in refused.json()["error"]["message"]
