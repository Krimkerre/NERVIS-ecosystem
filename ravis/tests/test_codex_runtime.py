"""The Codex runtime check and its version pin — RAVIS M29, increment R1.

Runbook §2.2, invariant 6: any Codex binary not recorded as tested or accepted pauses new tasks.
These tests hold the check to the rows of the Codex state table it owns — `not_installed`,
`not_available` and `untested_version` (`tests/fixtures/relay-contract/codex-state.json`) — and to
the promises around them: the executable comes from settings or Homebrew only, Codex only ever
runs in a throwaway home, and the answer is kept rather than looked up again.

The programs the check runs are fakes (`tests/codex_fakes.py`), run as real programs. Nothing here
runs the real Codex or `codesign`, and the tests that point a home at `~/.codex` give it a
temporary `HOME` first.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.codex_fakes import (
    EXPERIMENTAL_SCHEMA,
    PINNED_TEAM,
    STABLE_SCHEMA,
    FakeAppServer,
    FakeBrew,
    FakeCodesign,
    FakeCodex,
    homebrew_codex,
    pin_entry,
    point_homebrew_link,
    schema_tree_sha256,
    write_pin,
)

from ravis.app import create_app
from ravis.codex.runtime import (
    TESTED_RUNTIMES,
    CodexRuntime,
    CodexRuntimeError,
    locate_executable,
    tree_sha256,
)
from ravis.codex.service import CodexService
from ravis.config import ConfigurationFinding, Settings, data_directory, inspect_configuration
from ravis.credentials import config_directory

ASKED = {"X-Clarvis-Engines": "codex"}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway `HOME`, so the `~/.codex` a test names is a folder under `tmp_path`."""
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    return folder


@pytest.fixture
def codesign(tmp_path: Path) -> FakeCodesign:
    """A `codesign` satisfied by OpenAI's team, as the real one is by the installed Codex."""
    return FakeCodesign.install(tmp_path / "tools" / "codesign")


def _settings(**overrides: Any) -> Settings:
    """Codex left for the check to decide (`codex_enabled` unset), unless a test says otherwise."""
    return Settings(
        database_path=":memory:",
        _env_file=None,  # type: ignore[call-arg]
        **{"codex_enabled": None, **overrides},
    )


def _checked(settings: Settings, codesign: FakeCodesign, pin: Path) -> CodexRuntime:
    """Run the startup check to its end, and return the runtime keeping the answer."""
    runtime = CodexRuntime(settings, codesign=str(codesign.path), pin=pin)
    asyncio.run(runtime.check())
    return runtime


def _scratch() -> list[Path]:
    """Whatever is left in the folder the throwaway Codex homes are made in."""
    folder = data_directory() / "ravis-codex-scratch"
    return sorted(folder.iterdir()) if folder.exists() else []


# ── Where the executable is ─────────────────────────────────────────────────


def test_a_configured_executable_is_used_and_homebrew_is_never_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = FakeCodex.install(tmp_path / "elsewhere" / "codex")
    homebrew_codex(tmp_path / "homebrew")
    brew = FakeBrew.install(tmp_path / "brew-bin", prefix=tmp_path / "homebrew")
    monkeypatch.setenv("PATH", str(brew.path.parent))

    found = locate_executable(_settings(codex_executable=str(codex.path)))

    assert found == codex.path.resolve()
    assert not brew.asked.exists()


def test_with_no_setting_the_executable_is_the_homebrew_link_named_by_brew_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owner decision D4: `$(brew --prefix)/bin/codex`, followed to the cask's copy."""
    copy = homebrew_codex(tmp_path / "homebrew")
    brew = FakeBrew.install(tmp_path / "brew-bin", prefix=tmp_path / "homebrew")
    monkeypatch.setenv("PATH", str(brew.path.parent))

    found = locate_executable(_settings())

    assert found == copy.path.resolve()
    assert brew.asked.read_text().split() == ["--prefix"]


def test_with_no_setting_and_no_homebrew_codex_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nothing = tmp_path / "empty-bin"
    nothing.mkdir()
    monkeypatch.setenv("PATH", str(nothing))

    with pytest.raises(CodexRuntimeError) as refused:
        locate_executable(_settings())

    assert refused.value.state == "not_installed"
    assert "Homebrew is not on PATH" in refused.value.reason


def test_a_link_that_leads_nowhere_is_not_installed(tmp_path: Path) -> None:
    link = tmp_path / "bin" / "codex"
    link.parent.mkdir()
    link.symlink_to(tmp_path / "uninstalled" / "codex")

    with pytest.raises(CodexRuntimeError) as refused:
        locate_executable(_settings(codex_executable=str(link)))

    assert refused.value.state == "not_installed"


# ── Whether to trust it ─────────────────────────────────────────────────────


def test_homebrews_versioned_copy_is_not_available_though_it_is_there(
    tmp_path: Path, codesign: FakeCodesign
) -> None:
    """Design review N6: a path naming one version would read as missing after an upgrade."""
    copy = homebrew_codex(tmp_path / "homebrew")
    settings = _settings(codex_executable=str(copy.path))

    runtime = _checked(settings, codesign, write_pin(tmp_path / "pin.json"))

    assert runtime.report.state == "not_available"
    assert "versioned copy" in runtime.report.reason
    assert copy.runs() == []


def test_an_executable_other_users_can_change_is_not_available(
    tmp_path: Path, codesign: FakeCodesign
) -> None:
    codex = FakeCodex.install(tmp_path / "bin" / "codex")
    codex.path.chmod(0o775)

    runtime = _checked(
        _settings(codex_executable=str(codex.path)), codesign, write_pin(tmp_path / "pin.json")
    )

    assert runtime.report.state == "not_available"
    assert "other users" in runtime.report.reason
    assert codex.runs() == []


def test_a_binary_another_apple_team_signed_is_not_available(tmp_path: Path) -> None:
    codex = FakeCodex.install(tmp_path / "bin" / "codex")
    another_team = FakeCodesign.install(tmp_path / "tools" / "codesign", team="ZZZZZZZZZZ")

    runtime = _checked(
        _settings(codex_executable=str(codex.path)), another_team, write_pin(tmp_path / "pin.json")
    )

    assert runtime.report.state == "not_available"
    assert f"not signed by Apple team {PINNED_TEAM}" in runtime.report.reason
    assert codex.runs() == []


@pytest.mark.parametrize("inside", [".codex", ".codex/ravis"])
def test_a_codex_home_in_the_chatgpt_apps_own_folder_is_not_available(
    tmp_path: Path, home: Path, codesign: FakeCodesign, inside: str
) -> None:
    codex = FakeCodex.install(tmp_path / "bin" / "codex")
    settings = _settings(codex_executable=str(codex.path), codex_home=str(home / inside))

    runtime = _checked(settings, codesign, write_pin(tmp_path / "pin.json"))

    assert runtime.report.state == "not_available"
    assert "~/.codex" in runtime.report.reason
    assert codex.runs() == []


def test_a_codex_home_inside_ravis_configuration_folder_is_not_available(
    tmp_path: Path, codesign: FakeCodesign
) -> None:
    codex = FakeCodex.install(tmp_path / "bin" / "codex")
    settings = _settings(
        codex_executable=str(codex.path), codex_home=str(config_directory() / "codex")
    )

    runtime = _checked(settings, codesign, write_pin(tmp_path / "pin.json"))

    assert runtime.report.state == "not_available"
    assert "configuration folder" in runtime.report.reason
    assert codex.runs() == []


# ── Which build it is, and whether it is pinned ─────────────────────────────


def test_the_pinned_build_is_tested_and_still_paused_until_its_file_rules_are_proven(
    tmp_path: Path, codesign: FakeCodesign
) -> None:
    """Owner decision D2: file rules not yet proven pause new work, whatever the verdict."""
    codex = FakeCodex.install(tmp_path / "bin" / "codex")
    pin = write_pin(tmp_path / "pin.json", pin_entry(codex))

    report = _checked(_settings(codex_executable=str(codex.path)), codesign, pin).report

    assert (report.state, report.verdict, report.strict_rules) == (
        "untested_version",
        "tested",
        "unproven",
    )
    assert "file rules" in report.reason
    assert report.installed_sha256 == hashlib.sha256(codex.path.read_bytes()).hexdigest()
    assert (report.version, report.team_id, report.source) == ("0.154.0", PINNED_TEAM, "configured")


def test_a_pinned_build_with_proven_file_rules_leaves_no_runtime_row_standing(
    tmp_path: Path, codesign: FakeCodesign
) -> None:
    codex = FakeCodex.install(tmp_path / "bin" / "codex")
    pin = write_pin(tmp_path / "pin.json", pin_entry(codex, proven=True))

    report = _checked(_settings(codex_executable=str(codex.path)), codesign, pin).report

    assert (report.state, report.verdict, report.strict_rules) == (None, "tested", "proven")


def test_an_upgrade_that_re_points_the_link_is_an_untested_version_not_a_missing_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codesign: FakeCodesign
) -> None:
    prefix = tmp_path / "homebrew"
    pinned = homebrew_codex(prefix)
    pin = write_pin(tmp_path / "pin.json", pin_entry(pinned, proven=True))
    brew = FakeBrew.install(tmp_path / "brew-bin", prefix=prefix)
    monkeypatch.setenv("PATH", str(brew.path.parent))
    assert _checked(_settings(), codesign, pin).report.state is None

    upgraded = FakeCodex.install(
        prefix / "Caskroom" / "codex" / "0.155.0" / "bin" / "codex", build="two", version="0.155.0"
    )
    point_homebrew_link(prefix, upgraded)
    report = _checked(_settings(), codesign, pin).report

    assert (report.state, report.verdict, report.version, report.source) == (
        "untested_version",
        "untested",
        "0.155.0",
        "homebrew",
    )
    assert "not a build RAVIS has tested" in report.reason


def test_a_matching_sha256_with_another_schema_tree_is_not_the_pinned_build(
    tmp_path: Path, codesign: FakeCodesign
) -> None:
    """Design review M1, M5: the pin holds the binary and both its trees, not the binary alone."""
    codex = FakeCodex.install(tmp_path / "bin" / "codex")
    entry = pin_entry(codex, proven=True, experimental_tree="0" * 64)

    report = _checked(
        _settings(codex_executable=str(codex.path)),
        codesign,
        write_pin(tmp_path / "pin.json", entry),
    ).report

    assert (report.state, report.verdict) == ("untested_version", "untested")


#: Names on which byte order and a folder-by-folder walk disagree ("." sorts before "/"), and on
#: which byte order and a case-blind sort disagree ("Z" sorts before "a").
TREE = {"a/z.json": '{"z": 1}\n', "a.b/c.json": "null\n", "Z.json": "[]\n", "a.json": "{}\n"}
#: What `find . -type f -print0 | sort -z | xargs -0 shasum -a 256 | shasum -a 256` printed inside
#: that tree on 13 September 2026, in the C locale and in this Mac's default locale alike.
TREE_SHA256 = "b21830a781e9627ff1d8070b8384a91501f96a5dc20073ccd7cf30da908ef497"


def test_a_schema_tree_hashes_to_what_the_recording_command_prints(tmp_path: Path) -> None:
    for name, content in TREE.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content)
    # `find -type f` leaves symbolic links out, so a link changes nothing.
    (tmp_path / "linked.json").symlink_to(tmp_path / "a.json")

    assert tree_sha256(tmp_path) == TREE_SHA256


def test_codex_runs_only_in_a_throwaway_home_that_is_gone_when_the_check_ends(
    tmp_path: Path, codesign: FakeCodesign
) -> None:
    codex = FakeCodex.install(tmp_path / "bin" / "codex")
    scratch = data_directory() / "ravis-codex-scratch"

    report = _checked(
        _settings(codex_executable=str(codex.path)), codesign, write_pin(tmp_path / "pin.json")
    ).report

    runs = codex.runs()
    assert [run.arguments.split(" --out ")[0] for run in runs] == [
        "--version",
        "app-server generate-json-schema",
        "app-server generate-json-schema",
    ]
    for run in runs:
        for place in (run.codex_home, run.home, run.tmpdir):
            assert Path(place).is_relative_to(scratch), place
    assert len({run.codex_home for run in runs}) == 1
    assert _scratch() == []
    assert (report.stable_tree, report.experimental_tree) == (
        schema_tree_sha256(STABLE_SCHEMA),
        schema_tree_sha256(EXPERIMENTAL_SCHEMA),
    )


def test_a_failed_schema_generation_is_not_available_and_leaves_no_home_behind(
    tmp_path: Path, codesign: FakeCodesign
) -> None:
    codex = FakeCodex.install(tmp_path / "bin" / "codex", fail_schema=True)

    report = _checked(
        _settings(codex_executable=str(codex.path)), codesign, write_pin(tmp_path / "pin.json")
    ).report

    assert report.state == "not_available"
    assert "generate-json-schema exited with status 7" in report.reason
    assert _scratch() == []


def test_codex_switched_off_is_not_available_and_nothing_is_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codesign: FakeCodesign
) -> None:
    prefix = tmp_path / "homebrew"
    copy = homebrew_codex(prefix)
    brew = FakeBrew.install(tmp_path / "brew-bin", prefix=prefix)
    monkeypatch.setenv("PATH", str(brew.path.parent))

    runtime = _checked(_settings(codex_enabled=False), codesign, write_pin(tmp_path / "pin.json"))

    assert runtime.report.state == "not_available"
    assert not runtime.enabled
    assert not brew.asked.exists()
    assert copy.runs() == []


def test_the_committed_pin_holds_homebrew_codex_0_154_0_with_its_file_rules_unproven() -> None:
    """The values measured for R1 on 13 September 2026 (the entry's `evidence` says how).

    The stable tree is also the reference the design record carries (§4.3) for the ChatGPT app's
    0.154.0-alpha.6.2, whose stable schema is byte-identical: a second source for that value.
    """
    document = json.loads(TESTED_RUNTIMES.read_text(encoding="utf-8"))

    [entry] = document["tested"]
    assert document["format"] == 3
    assert entry["codex_version"] == "0.154.0"
    assert entry["sha256"] == "4f85982624b3898c8991cb80c0981b2aa71070e3537046c9a95950318a95afcc"
    assert entry["stable_tree"] == (
        "b32fa164e4bedf854688c5e2dcee10b3da22c0306dd7eda148ee9eeed00dfe93"
    )
    assert entry["experimental_tree"] == (
        "d811e26c9f5b69cfb77cc7a6001a982394863b11f7a380ed243a0a77f1798172"
    )
    assert entry["strict_rules_proven"] is False


def _eventually(condition: Callable[[], bool], seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "the startup check did not finish"
        time.sleep(0.02)


def test_the_check_runs_once_at_startup_and_listing_reads_what_it_kept(
    tmp_path: Path, codesign: FakeCodesign
) -> None:
    server = FakeAppServer.create(tmp_path / "app-server")
    codex = FakeCodex.install(tmp_path / "bin" / "codex", app_server=server)
    settings = _settings(codex_executable=str(codex.path))
    app = create_app(settings)
    service = CodexService(
        settings,
        emit=app.app.state.events.emit,
        codesign=str(codesign.path),
        pin=write_pin(tmp_path / "pin.json", pin_entry(codex)),
    )
    app.app.state.codex_service = service
    app.app.state.codex = runtime = service.runtime

    with TestClient(app) as client:
        _eventually(lambda: runtime.report.state != "checking")
        listings = [client.get("/v1/models", headers=ASKED).json() for _ in range(3)]

    assert runtime.report.state == "untested_version"
    assert all("ravis/codex" in [entry["id"] for entry in body["data"]] for body in listings)
    assert [run.arguments for run in codex.runs()].count("--version") == 1


# ── What `ravis doctor` says ────────────────────────────────────────────────


def _codex_findings(settings: Settings) -> list[ConfigurationFinding]:
    """The Codex findings, having checked that none of them stops RAVIS serving."""
    report = inspect_configuration(settings)
    assert report.is_startable()
    return [finding for finding in report.findings if finding.setting.startswith("codex_")]


def test_doctor_notes_homebrews_versioned_copy(tmp_path: Path) -> None:
    copy = tmp_path / "Caskroom" / "codex" / "0.154.0" / "bin" / "codex"

    [finding] = _codex_findings(_settings(codex_executable=str(copy)))

    assert (finding.setting, finding.fatal) == ("codex_executable", False)
    assert "versioned copy" in finding.message


def test_doctor_notes_a_configured_executable_that_is_not_there(tmp_path: Path) -> None:
    [finding] = _codex_findings(_settings(codex_executable=str(tmp_path / "no-codex-here")))

    assert (finding.setting, finding.fatal) == ("codex_executable", False)
    assert "does not lead to a file" in finding.message


def test_doctor_notes_that_with_no_setting_and_no_homebrew_codex_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nothing = tmp_path / "empty-bin"
    nothing.mkdir()
    monkeypatch.setenv("PATH", str(nothing))

    [finding] = _codex_findings(_settings())

    assert (finding.setting, finding.fatal) == ("codex_executable", False)
    assert "Homebrew is not on PATH" in finding.message


@pytest.mark.usefixtures("home")
def test_doctor_notes_a_codex_home_in_the_chatgpt_apps_own_folder(tmp_path: Path) -> None:
    codex = FakeCodex.install(tmp_path / "bin" / "codex")

    [finding] = _codex_findings(_settings(codex_executable=str(codex.path), codex_home="~/.codex"))

    assert (finding.setting, finding.fatal) == ("codex_home", False)
    assert "~/.codex" in finding.message


def test_doctor_notes_a_codex_home_inside_ravis_configuration_folder(tmp_path: Path) -> None:
    codex = FakeCodex.install(tmp_path / "bin" / "codex")
    settings = _settings(
        codex_executable=str(codex.path), codex_home=str(config_directory() / "codex")
    )

    [finding] = _codex_findings(settings)

    assert (finding.setting, finding.fatal) == ("codex_home", False)
    assert "configuration folder" in finding.message


def test_doctor_has_nothing_to_say_about_a_sound_codex_or_one_switched_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nothing = tmp_path / "empty-bin"
    nothing.mkdir()
    monkeypatch.setenv("PATH", str(nothing))
    codex = FakeCodex.install(tmp_path / "bin" / "codex")
    caskroom_copy = tmp_path / "Caskroom" / "codex" / "0.154.0" / "bin" / "codex"

    assert _codex_findings(_settings(codex_executable=str(codex.path))) == []
    assert (
        _codex_findings(_settings(codex_enabled=False, codex_executable=str(caskroom_copy))) == []
    )
