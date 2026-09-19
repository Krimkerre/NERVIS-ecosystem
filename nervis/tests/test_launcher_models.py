"""The launcher's model commands — what the menu bar app's LM Studio menu runs.

`tools/run.py models`, `load`, `unload` and `renew` go through SIRVIS, which owns
every load and unload (SIRVIS.md §9), and keep a small record of the sessions the
menu opened so it can renew them, release them on request, and release them before
the stack stops. The owner chose that a model loaded from the menu stays loaded
until it is unloaded there or NERVIS quits.

Nothing here reaches SIRVIS or loads a model: the one function that makes a request
is replaced by a script of SIRVIS's answers, and the session record lives in a
temporary directory.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

RUN_PY = Path(__file__).resolve().parents[2] / "tools" / "run.py"


def _launcher(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("launcher_models_under_test", RUN_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "RUN", tmp_path)
    monkeypatch.setattr(module, "MENU_SESSIONS", tmp_path / "menubar-sessions.json")
    return module


class FakeSirvis:
    """SIRVIS's answers, by (method, path), and a record of what was asked."""

    def __init__(self, answers: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self.answers = answers
        self.asked: list[tuple[str, str, Any]] = []

    def __call__(
        self, method: str, path: str, body: Any = None, timeout: float = 10.0
    ) -> tuple[int, Any]:
        del timeout
        self.asked.append((method, path, body))
        return self.answers.get((method, path), (404, {"error": {"message": "not found"}}))


def test_a_model_loaded_from_the_menu_is_held_renewed_and_released(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _launcher(monkeypatch, tmp_path)
    sirvis = FakeSirvis({
        ("POST", "/api/v1/runtime/sessions"): (200, {"session_id": "s-1"}),
        ("POST", "/api/v1/runtime/sessions/s-1/renew"): (200, {"session_id": "s-1"}),
        ("DELETE", "/api/v1/runtime/sessions/s-1"): (200, {"state": "released"}),
    })
    monkeypatch.setattr(run, "_sirvis_call", sirvis)

    assert run.load_model("qwen/qwen3.5-9b") == {"ok": True, "session_id": "s-1"}
    # Loaded through SIRVIS's sessions, with a policy that never looks queued.
    assert sirvis.asked[-1] == ("POST", "/api/v1/runtime/sessions",
                                {"models": [{"model_id": "qwen/qwen3.5-9b"}], "policy": "reject"})
    # A second click on a model the menu already holds does not open a second lease.
    assert run.load_model("qwen/qwen3.5-9b")["already"] is True
    opened = [asked for asked in sirvis.asked if asked[:2] == ("POST", "/api/v1/runtime/sessions")]
    assert len(opened) == 1

    assert run.renew_models() == {"ok": True, "renewed": 1}
    assert run.unload_model("qwen/qwen3.5-9b") == {"ok": True}
    assert run._menu_sessions() == {}


def test_the_menu_leaves_alone_what_it_did_not_load_and_forgets_lapsed_leases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _launcher(monkeypatch, tmp_path)
    run._save_menu_sessions({"a/held": "s-live", "b/lapsed": "s-gone"})
    sirvis = FakeSirvis({
        ("GET", "/api/v1/models"): (200, {"items": [
            {"runtime_key": "a/held", "installed_size_bytes": 5_000_000_000,
             "variant": {"runtime_format": "gguf", "quantization": "Q4_K_M"}},
            {"runtime_key": "b/lapsed", "installed_size_bytes": None, "variant": {}},
            {"runtime_key": "c/by-ravis"},
            {"runtime_key": "d/by-lm-studio"},
        ]}),
        ("GET", "/api/v1/runtime/residency"): (200, {
            "max_loaded": 2,
            "leases": [{"session_id": "s-live"}, {"session_id": "s-ravis"}],
            "holdings": [{"model_key": "a/held"}, {"model_key": "c/by-ravis"}],
            "foreign": ["d/by-lm-studio"],
        }),
    })
    monkeypatch.setattr(run, "_sirvis_call", sirvis)

    report = run.models_report()
    rows = {row["key"]: row for row in report["models"]}
    assert rows["a/held"] == {
        "key": "a/held", "name": "held", "format": "gguf", "quantization": "Q4_K_M",
        "size_bytes": 5_000_000_000, "loaded": True, "held_by_menu": True,
        "linked_device": None, "linked_device_name": None,
    }
    assert rows["b/lapsed"]["loaded"] is False and rows["b/lapsed"]["held_by_menu"] is False
    assert rows["c/by-ravis"]["loaded"] is True and rows["c/by-ravis"]["held_by_menu"] is False
    assert rows["d/by-lm-studio"]["loaded"] is True
    assert rows["d/by-lm-studio"]["held_by_menu"] is False
    assert report["max_loaded"] == 2
    # The lapsed lease is forgotten; RAVIS's is not the menu's to release.
    assert run._menu_sessions() == {"a/held": "s-live"}
    assert run.unload_model("c/by-ravis")["ok"] is False
    assert not [a for a in sirvis.asked if a[0] == "DELETE"]


def test_lm_link_models_come_after_this_machine_s_grouped_by_device(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The owner's ThinkPad with LM Link on, 19 September 2026: the Mac's models listed
    among its own, one of them loaded on the Mac and read as loaded here."""
    run = _launcher(monkeypatch, tmp_path)
    mac = {"linked_device": "93c2", "linked_device_name": "Govert.local"}
    monkeypatch.setattr(run, "_sirvis_call", FakeSirvis({
        ("GET", "/api/v1/models"): (200, {"items": [
            {"runtime_key": "qwen/qwen3.5-9b", "is_loaded": True, **mac},
            {"runtime_key": "google/gemma-4-e2b", "linked_device": None},
            {"runtime_key": "zeta/other-box", "linked_device": "a5c0", "linked_device_name": "Box"},
            {"runtime_key": "ibm/granite-4-h-tiny"},
            {"runtime_key": "google/gemma-4-e2b@mlx", **mac},
        ]}),
        ("GET", "/api/v1/runtime/residency"): (200, {"leases": [], "holdings": []}),
    }))

    rows = run.models_report()["models"]

    assert [(row["name"], row["linked_device_name"]) for row in rows] == [
        ("gemma-4-e2b", None), ("granite-4-h-tiny", None),
        ("other-box", "Box"),
        ("gemma-4-e2b@mlx", "Govert.local"), ("qwen3.5-9b", "Govert.local"),
    ]
    assert rows[-1]["loaded"] is True and rows[-1]["linked_device"] == "93c2"


def test_a_refusal_comes_back_in_sirvis_words_and_nothing_is_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _launcher(monkeypatch, tmp_path)
    monkeypatch.setattr(run, "_sirvis_call", FakeSirvis({
        ("POST", "/api/v1/runtime/sessions"): (
            409, {"error": {"code": "RESOURCE_BUSY", "message": "2 models are already loaded"}}),
    }))
    assert run.load_model("big/model") == {"ok": False, "error": "2 models are already loaded"}
    assert run._menu_sessions() == {}

    monkeypatch.setattr(run, "_sirvis_call", lambda *_args, **_kwargs: (0, None))
    assert run.load_model("big/model") == {"ok": False, "error": "SIRVIS is not answering"}
    assert run.models_report() == {"available": False, "models": [], "max_loaded": None}


def test_stopping_releases_only_the_menus_own_sessions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _launcher(monkeypatch, tmp_path)
    run._save_menu_sessions({"a/one": "s-1", "b/two": "s-2"})
    sirvis = FakeSirvis({
        ("DELETE", "/api/v1/runtime/sessions/s-1"): (200, {}),
        ("DELETE", "/api/v1/runtime/sessions/s-2"): (200, {}),
    })
    monkeypatch.setattr(run, "_sirvis_call", sirvis)

    run._release_menu_sessions()

    assert sorted(path for method, path, _ in sirvis.asked if method == "DELETE") == [
        "/api/v1/runtime/sessions/s-1", "/api/v1/runtime/sessions/s-2"]
    assert run._menu_sessions() == {}
    assert "released a/one for the menu bar" in capsys.readouterr().out
