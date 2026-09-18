"""`run.py clarvis-settings`: Clarvis connected to RAVIS and NERVIS, with its theme, anywhere.

Found on the Ubuntu VM on 18 September 2026: the installer put Clarvis into code-server and wrote
none of the settings that make it part of the ecosystem, so the theme was missing and, less
visibly, Clarvis wasn't pointed at RAVIS or reporting to NERVIS. The owner's Mac only had them
because they were typed in long ago.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

RUN_PY = Path(__file__).resolve().parents[2] / "tools" / "run.py"


@pytest.fixture
def launcher() -> ModuleType:
    spec = importlib.util.spec_from_file_location("launcher_clarvis_settings_under_test", RUN_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_machine_with_no_settings_gets_all_of_clarvis_s(launcher: ModuleType,
                                                          tmp_path: Path) -> None:
    path = tmp_path / "User" / "settings.json"

    added, said = launcher.seed_clarvis_settings(path)

    stored = json.loads(path.read_text())
    assert stored["workbench.colorTheme"] == "clarvis-nervis"
    assert stored["clarvis.chat.baseUrl.custom"] == "http://127.0.0.1:8731"
    assert stored["clarvis.bridge.nervisUrl"] == "http://127.0.0.1:8790"
    assert stored["clarvis.bridge.enrollmentSecretPath"].endswith("nervis/nervis.enrollment")
    assert len(added) == len(launcher.clarvis_settings()) and "added" in said


def test_what_somebody_set_is_kept_and_only_the_blanks_are_filled(launcher: ModuleType,
                                                                   tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"workbench.colorTheme": "Solarized Light",
                                "clarvis.bridge.enabled": False, "editor.fontSize": 15}))

    added, _ = launcher.seed_clarvis_settings(path)

    stored = json.loads(path.read_text())
    assert (stored["workbench.colorTheme"], stored["clarvis.bridge.enabled"]) == (
        "Solarized Light", False)
    assert stored["editor.fontSize"] == 15
    assert "workbench.colorTheme" not in added and "clarvis.chat.model" in added
    # A second run finds nothing to do.
    assert launcher.seed_clarvis_settings(path) == (
        [], f"Clarvis's settings were already there, in {path}")


def test_a_file_with_comments_is_left_alone_and_the_settings_are_printed(
    launcher: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "settings.json"
    original = '{\n  // my font\n  "editor.fontSize": 15,\n}\n'
    path.write_text(original)

    added, said = launcher.seed_clarvis_settings(path)

    assert added == [] and path.read_text() == original
    assert "isn't plain JSON" in said and '"workbench.colorTheme": "clarvis-nervis",' in said
