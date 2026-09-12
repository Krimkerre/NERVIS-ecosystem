"""The launcher's machine-readable status — everything the menu bar app knows.

`tools/run.py status --json` is the only thing NERVIS.app reads about the stack:
which services answer, which group each belongs to, where the dashboard is, and a
few figures from NERVIS's machine reading. The app draws its whole menu from it, so
a renamed key or a lost group would leave the menu empty without an error anywhere.

Nothing here starts a process or opens a port. The service table, the probes and
the readings are all replaced, because the question is the shape of the answer and
not whether this machine's services happen to be up.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

RUN_PY = Path(__file__).resolve().parents[2] / "tools" / "run.py"


def _launcher() -> ModuleType:
    spec = importlib.util.spec_from_file_location("launcher_under_test", RUN_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_status_json_groups_every_service_and_reads_the_rest_only_through_nervis(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _launcher()
    monkeypatch.setattr(run, "_services", lambda: [
        ("SIRVIS", [], "", {}, "http://127.0.0.1:8721/ecosystem/health"),
        ("RAVIS", [], "", {}, "http://127.0.0.1:8731/ecosystem/health"),
        ("NERVIS", [], "", {}, "http://127.0.0.1:8790/ecosystem/health"),
        ("Ollama", [], "", {}, "http://127.0.0.1:11434/api/version"),
        ("code-server", [], "", {}, "http://127.0.0.1:8080/healthz"),
    ])
    monkeypatch.setattr(run, "EXTERNAL", [("LM Studio", "http://127.0.0.1:1234/v1/models")])
    monkeypatch.setattr(run, "responds", lambda url, _timeout=1.5: ":8731" not in url)
    monkeypatch.setattr(run, "_system_reading", lambda: {"memory_total_bytes": 24})
    monkeypatch.setattr(run, "_unread_notifications", lambda: 2)
    monkeypatch.setattr(run, "_clarvis_bridges", lambda: 2)
    monkeypatch.setattr(run.sys, "argv", ["run.py", "status", "--json"])

    assert run._run_status() == 0
    body = json.loads(capsys.readouterr().out)

    groups = {
        service["name"]: (service["group"], service["answering"]) for service in body["services"]
    }
    assert groups == {
        "SIRVIS": ("stack", True),
        "RAVIS": ("stack", False),
        "NERVIS": ("stack", True),
        "Ollama": ("runtime", True),
        "CLARVIS": ("editor", True),
        "code-server": ("stack", True),
        "LM Studio": ("runtime", True),
    }
    # The menu's Stack section is these, in this order: code-server stays at the bottom,
    # below the CLARVIS it hosts.
    section = [s["name"] for s in body["services"] if s["group"] in ("stack", "editor")]
    assert section == ["SIRVIS", "RAVIS", "NERVIS", "CLARVIS", "code-server"]
    assert next(s for s in body["services"] if s["name"] == "CLARVIS")["windows"] == 2
    # Each line of the stack opens something: a screen in the dashboard, or code-server itself.
    addresses = {s["name"]: s["address"] for s in body["services"]}
    assert addresses["SIRVIS"] == run.DASHBOARD + "#/sirvis/Dashboard"
    assert addresses["RAVIS"] == run.DASHBOARD + "#/ravis/Dashboard"
    assert addresses["NERVIS"] == run.DASHBOARD + "#/nervis/Overview"
    assert addresses["CLARVIS"] == run.DASHBOARD + "#/clarvis/Workspace"
    assert addresses["code-server"] == "http://127.0.0.1:8080/"
    assert addresses["Ollama"] is None and addresses["LM Studio"] is None

    assert body["dashboard"] == run.DASHBOARD
    assert body["system"] == {"memory_total_bytes": 24}
    # The count the icon blinks on, and the screen the menu's item opens.
    assert body["notifications"] == {"unread": 2, "screen": run.NOTIFICATIONS}
    assert run.NOTIFICATIONS.endswith("#/nervis/Notifications")

    # With NERVIS down there is nobody to ask for the figures, the count or the Bridges,
    # and none of them is invented.
    monkeypatch.setattr(run, "responds", lambda url, _timeout=1.5: ":8790" not in url)
    down = run.status_report()
    assert down["system"] is None
    assert down["notifications"] is None
    clarvis = next(s for s in down["services"] if s["name"] == "CLARVIS")
    assert clarvis["answering"] is False and clarvis["windows"] is None


def test_a_bridge_counts_only_while_its_lease_is_live(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _launcher()
    monkeypatch.setattr(run, "_from_nervis", lambda _path: {"items": [
        {"service": "clarvis", "live": True},
        {"service": "clarvis", "live": False},
        {"service": "something-else", "live": True},
    ]})
    assert run._clarvis_bridges() == 1
    monkeypatch.setattr(run, "_from_nervis", lambda _path: None)
    assert run._clarvis_bridges() is None
