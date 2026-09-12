"""The launcher's machine-readable status — everything the menu bar app knows.

`tools/run.py status --json` is the only thing NERVIS.app reads about the stack:
which services answer, which group each belongs to, where the dashboard is, and a
few figures from NERVIS's machine reading. The app draws its whole menu from it, so
a renamed key or a lost group would leave the menu empty without an error anywhere.

Nothing here starts a process or opens a port. The service table, the probes and
the reading are all replaced, because the question is the shape of the answer and
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


def test_status_json_groups_every_service_and_reads_the_machine_only_through_nervis(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _launcher()
    monkeypatch.setattr(run, "_services", lambda: [
        ("SIRVIS", [], "", {}, "http://127.0.0.1:8721/ecosystem/health"),
        ("RAVIS", [], "", {}, "http://127.0.0.1:8731/ecosystem/health"),
        ("NERVIS", [], "", {}, "http://127.0.0.1:8790/ecosystem/health"),
        ("Ollama", [], "", {}, "http://127.0.0.1:11434/api/version"),
    ])
    monkeypatch.setattr(run, "EXTERNAL", [
        ("LM Studio", "http://127.0.0.1:1234/v1/models"),
        ("Clarvis", "http://127.0.0.1:7071/"),
    ])
    monkeypatch.setattr(run, "responds", lambda url, _timeout=1.5: ":8731" not in url)
    monkeypatch.setattr(run, "_system_reading", lambda: {"memory_total_bytes": 24})
    monkeypatch.setattr(run, "_unread_notifications", lambda: 2)
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
        "LM Studio": ("runtime", True),
        "Clarvis": ("editor", True),
    }
    assert body["dashboard"] == run.DASHBOARD
    assert body["system"] == {"memory_total_bytes": 24}
    # The count the icon blinks on, and the screen the menu's item opens.
    assert body["notifications"] == {"unread": 2, "screen": run.NOTIFICATIONS}
    assert run.NOTIFICATIONS.endswith("#/nervis/Notifications")

    # With NERVIS down there is nobody to ask for the figures or the count, and
    # neither is invented.
    monkeypatch.setattr(run, "responds", lambda url, _timeout=1.5: ":8790" not in url)
    down = run.status_report()
    assert down["system"] is None
    assert down["notifications"] is None
