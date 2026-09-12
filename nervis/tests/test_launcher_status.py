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
    # No PID file is read: which process this machine happens to have recorded is not the
    # question, and a real one would send `ps` after a real process number.
    monkeypatch.setattr(run, "_recorded", lambda: {})
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
    # Not answering is all that is wrong: no process is on record for RAVIS here.
    assert {service["problem"] for service in body["services"]} == {None}

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


def test_a_silent_process_older_than_the_start_wait_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The menu shows what `start` found: a service running as a process yet not answering.

    Until 13 September 2026 that sentence reached only the menu bar app's log, and the menu
    said "not running" — wrong, and the wrong remedy, since a running process has to be
    stopped before the stack can start it again. The process table, the ages and the PID
    file are all replaced; nothing here reaches `ps` or a real process.
    """
    run = _launcher()
    monkeypatch.setattr(run, "_services", lambda: [
        ("SIRVIS", [], "sirvis serve", {}, "http://127.0.0.1:8721/ecosystem/health"),
        ("RAVIS", [], "ravis serve", {}, "http://127.0.0.1:8731/ecosystem/health"),
        ("NERVIS", [], "nervis serve", {}, "http://127.0.0.1:8790/ecosystem/health"),
    ])
    monkeypatch.setattr(run, "EXTERNAL", [("LM Studio", "http://127.0.0.1:1234/v1/models")])
    monkeypatch.setattr(run, "responds", lambda url, _timeout=1.5: ":8790" in url)
    monkeypatch.setattr(run, "_system_reading", lambda: None)
    monkeypatch.setattr(run, "_unread_notifications", lambda: 0)
    monkeypatch.setattr(run, "_clarvis_bridges", lambda: 0)
    monkeypatch.setattr(run, "_recorded", lambda: {
        "SIRVIS": {"pid": 600, "marker": "sirvis serve"},
        "RAVIS": {"pid": 700, "marker": "ravis serve"},
    })
    alive = {600: "sirvis serve", 700: "ravis serve"}
    monkeypatch.setattr(run, "_alive", lambda pid, marker: alive.get(pid) == marker)
    ages: dict[int, float | None] = {600: 12.0, 700: 45.0}
    monkeypatch.setattr(run, "_process_age", lambda pid: ages.get(pid))

    def problems() -> dict[str, object]:
        return {service["name"]: service["problem"] for service in run.status_report()["services"]}

    assert problems() == {
        "SIRVIS": None,  # twelve seconds old may still be booting, and a start reads the menu
        "RAVIS": "running as process 700 but not answering",
        "NERVIS": None,  # answering
        "CLARVIS": None,
        "LM Studio": None,  # not the launcher's
    }
    # Gone, or a recycled number no longer ours: simply not running, with nothing to name.
    alive.clear()
    assert set(problems().values()) == {None}
    # An age that cannot be read — Windows has no `ps` — says nothing rather than guess.
    alive[700] = "ravis serve"
    ages[700] = None
    assert problems()["RAVIS"] is None


def test_ps_elapsed_time_is_read_in_each_shape_it_takes() -> None:
    run = _launcher()
    assert run._elapsed_seconds("05:03") == 303
    assert run._elapsed_seconds(" 02:03:04\n") == 7_384
    assert run._elapsed_seconds("1-02:03:04") == 86_400 + 7_384
    assert run._elapsed_seconds("") is None
    assert run._elapsed_seconds("not-a:time") is None
