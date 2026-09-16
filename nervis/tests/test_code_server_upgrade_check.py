"""The quick check after a code-server upgrade (`tools/code_server_upgrade_check.py`).

Its pure parts: reading `--version`, the engine range, which files changed, which of those
fail the check, the host suite's counts, and the record. The steps that run code-server,
npm and NERVIS are exercised by running the tool itself.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

TOOL = Path(__file__).resolve().parents[2] / "tools" / "code_server_upgrade_check.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("code_server_upgrade_check", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered first: a dataclass looks its module up while the class is made.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = load()


def test_the_version_line_is_read() -> None:
    line = "4.137.0 b11dabdaca0d3369986975be285db92c8795cea5 with Code 1.137.0\n"
    assert check.parse_version(line) == ("4.137.0", "1.137.0")
    assert check.parse_version("code-server: command not found") == ("", "")


def test_the_engine_range_is_a_caret() -> None:
    assert check.satisfies_caret("1.137.0", "^1.93.0")
    assert check.satisfies_caret("1.93.0", "^1.93.0")
    assert not check.satisfies_caret("1.92.9", "^1.93.0")
    assert not check.satisfies_caret("2.0.0", "^1.93.0")
    assert not check.satisfies_caret("1.137.0", ">=1.93.0"), "a range it cannot read is a failure"


def test_changed_files_are_found_both_ways_and_maps_are_skipped(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    for root in (old, new):
        (root / "node" / "routes").mkdir(parents=True)
        (root / "node" / "same.js").write_text("same")
    (old / "node" / "http.js").write_text("a")
    (new / "node" / "http.js").write_text("b")
    (old / "node" / "gone.js").write_text("x")
    (new / "node" / "routes" / "new.js").write_text("y")
    (old / "node" / "http.js.map").write_text("1")
    (new / "node" / "http.js.map").write_text("2")

    assert check.changed_files(old, new) == ["node/gone.js", "node/http.js", "node/routes/new.js"]
    assert check.changed_files(old, old) == []


def test_a_graded_or_webview_file_fails_and_names_what_to_re_run() -> None:
    failures, review = check.classify(
        ["out/node/proxy.js", "out/node/cli.js"], ["service-worker.js"]
    )
    assert len(failures) == 2
    assert "out/node/proxy.js changed" in failures[0] and "WebSocket upgrade" in failures[0]
    assert "service-worker.js changed" in failures[1] and "webview rendering" in failures[1]
    assert review == ["out/node/cli.js"], "an ungraded file is for review, not a failure"
    assert check.classify(["out/node/cli.js"], []) == ([], ["out/node/cli.js"])


def test_every_graded_file_names_a_matrix_cell() -> None:
    for name, cells in check.GRADED_FILES.items():
        assert name.startswith("out/node/") and name.endswith(".js"), name
        assert cells.strip(), name


def test_the_host_suite_counts_are_read() -> None:
    assert check.host_suite_counts("  31 passing (2s)\n") == (31, 0)
    assert check.host_suite_counts("  30 passing (3s)\n  1 failing\n") == (30, 1)
    assert check.host_suite_counts("Error: could not download") == (0, 0)


def test_a_record_replaces_its_version_and_keeps_the_rest() -> None:
    kept = {"4.136.0": {"checked_on": "2026-09-10"}}
    updated = check.recorded(
        {**kept, "4.137.0": {"checked_on": "old"}}, "4.137.0", {"checked_on": "new"}
    )
    assert updated == {**kept, "4.137.0": {"checked_on": "new"}}


def test_a_report_with_a_failure_does_not_pass() -> None:
    report = check.Report()
    report.add("PASS", "a", "")
    report.add("SKIP", "b", "")
    assert report.passed
    report.add("FAIL", "c", "")
    assert not report.passed
