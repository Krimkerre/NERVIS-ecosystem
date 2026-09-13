"""What the launcher tells RAVIS about LM Studio's default load window.

A model LM Studio has not loaded yet is opened at LM Studio's *default* load length — a figure in
LM Studio's own settings that its API does not report — and RAVIS routes on that figure
(`RAVIS_LMSTUDIO_DEFAULT_CONTEXT`). The launcher does not start LM Studio, so it cannot set the
default the way it starts Ollama at a number. It reads it at every start instead, so a default
changed in LM Studio reaches RAVIS rather than silently disagreeing with it.

**No test here reads this machine's LM Studio.** LM Studio's folder keeps credentials beside its
settings, so each test moves LM Studio's home and its home pointer into its own directory before
anything runs, and every settings file below is written there. Each environment is still built by
the launcher's real `_services`, with the credentials and processes it would reach replaced.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path
from types import ModuleType

import pytest
from tests.test_launcher_lifecycle import RAVIS as RAVIS_SERVICE
from tests.test_launcher_lifecycle import FakeMachine, _as_ps_shows, _quiet_start, _record
from tests.test_launcher_lifecycle import _launcher as _sealed_launcher

REPOSITORY = Path(__file__).resolve().parents[2]
RUN_PY = REPOSITORY / "tools" / "run.py"

# Everything the decision reads from the environment, cleared before each test so that whatever
# the shell running the suite exports cannot change an answer.
DECIDING_VARIABLES = (
    "RAVIS_LMSTUDIO_DEFAULT_CONTEXT",
    "RAVIS_UPSTREAMS",
    "RAVIS_UPSTREAM_BASE_URL",
    "RAVIS_UPSTREAM_KIND",
)
LOCAL_LMSTUDIO = {"name": "lmstudio", "base_url": "http://127.0.0.1:1234", "kind": "lmstudio"}
LINE = "RAVIS's LM Studio context for models not yet loaded: "
UNREADABLE = "RAVIS's default: LM Studio's settings hold no default this launcher can read"
NOT_HERE = "RAVIS's default: the LM Studio RAVIS uses is not on this machine"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("launcher_lmstudio_context_under_test", RUN_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _move_lmstudio(monkeypatch: pytest.MonkeyPatch, run: ModuleType, tmp_path: Path) -> Path:
    """Put LM Studio's home and its home pointer inside `tmp_path`; return that home."""
    home = tmp_path / ".lmstudio"
    home.mkdir()
    monkeypatch.setattr(run, "LM_STUDIO_HOME", home)
    monkeypatch.setattr(run, "LM_STUDIO_HOME_POINTER", tmp_path / ".lmstudio-home-pointer")
    for variable in DECIDING_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    return home


def _laid_out(**held: object) -> str:
    """Settings text laid out as LM Studio writes it: two-space JSON, the key at the top level.

    The neighbouring keys are placeholders. They are there so the one key is found among others,
    as it is in the real file, without copying anything else LM Studio stores.
    """
    return json.dumps({"placeholderBefore": True, **held, "placeholderAfter": {"x": 1}}, indent=2)


def _custom(length: object) -> dict[str, object]:
    """The value LM Studio writes for a default length chosen in its settings."""
    return {"type": "custom", "value": length}


def _ravis_environment(
    monkeypatch: pytest.MonkeyPatch, run: ModuleType, tmp_path: Path
) -> dict[str, str]:
    """The environment `start` would launch RAVIS with, as the real `_services` builds it.

    The rest of what `_services` reaches is replaced: NERVIS's environment, built in the same
    call, is handed credentials that live in `.run/`; the default upstream list reads the names in
    the credential store; Ollama and code-server are other questions; and the workspace NERVIS is
    given is made beside the repository root, so the root is a folder inside `tmp_path` here and
    the workspace lands in `tmp_path` too.
    """
    root = tmp_path / "NERVIS-ecosystem"
    root.mkdir()
    monkeypatch.setattr(run, "ROOT", root)
    for minted in (
        "nervis_ravis_credential", "benchmark_token", "admin_token", "ravis_admin_credential"
    ):
        monkeypatch.setattr(run, minted, lambda: "(not a credential)")
    monkeypatch.setattr(run, "_stored_credential_names", set)
    monkeypatch.setattr(run, "_ollama", list)
    monkeypatch.setattr(run, "_code_server", list)
    return next(env for name, _, _, env, _ in run._services() if name == "RAVIS")


def _refuse(*_args: object) -> None:
    raise AssertionError("LM Studio's settings were read for an LM Studio not on this machine")


def test_a_default_in_lm_studios_own_shape_reaches_ravis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The shape this machine's LM Studio writes, holding a number it does not hold.

    16,384 rather than 8,192, so the answer cannot be RAVIS's own default agreeing by accident.
    """
    run = _load()
    home = _move_lmstudio(monkeypatch, run, tmp_path)
    (home / "settings.json").write_text(_laid_out(defaultContextLength=_custom(16384)))

    environment = _ravis_environment(monkeypatch, run, tmp_path)

    assert environment["RAVIS_LMSTUDIO_DEFAULT_CONTEXT"] == "16384"
    assert run.lmstudio_context_line(os.environ) == f"{LINE}16384 (from LM Studio's settings)."


def test_an_lm_studio_folder_moved_elsewhere_is_found_through_its_home_pointer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """LM Studio follows `~/.lmstudio-home-pointer` to its folder, so the launcher does too."""
    run = _load()
    home = _move_lmstudio(monkeypatch, run, tmp_path)
    (home / "settings.json").write_text(_laid_out(defaultContextLength=_custom(16384)))
    moved = tmp_path / "elsewhere"
    moved.mkdir()
    (moved / "settings.json").write_text(_laid_out(defaultContextLength=_custom(24576)))
    run.LM_STUDIO_HOME_POINTER.write_text(f"{moved}\n")

    assert _ravis_environment(monkeypatch, run, tmp_path)["RAVIS_LMSTUDIO_DEFAULT_CONTEXT"] == (
        "24576"
    )


@pytest.mark.parametrize(
    "settings",
    [
        pytest.param(None, id="no settings file"),
        pytest.param(_laid_out(), id="no default in it"),
        pytest.param(_laid_out(defaultContextLength=16384), id="a bare number"),
        pytest.param(
            _laid_out(defaultContextLength={"type": "auto", "value": 16384}), id="another type"
        ),
        pytest.param(_laid_out(defaultContextLength=_custom("lots")), id="not a number"),
        pytest.param(_laid_out(defaultContextLength=_custom(0)), id="zero"),
        pytest.param(_laid_out(defaultContextLength=_custom(-4096)), id="negative"),
        pytest.param(_laid_out(defaultContextLength=_custom(True)), id="true"),
        pytest.param(_laid_out(defaultContextLength=_custom(16384.5)), id="a fraction"),
        pytest.param('{\n  "defaultContextLength": [not json', id="garbage after the key"),
        pytest.param('{\n  "defaultContextLength": {\n    "type": "cus', id="cut off mid-write"),
        pytest.param(
            _laid_out(defaultContextLength=_custom(16384), placeholder={
                "defaultContextLength": _custom(4096)
            }),
            id="the key twice",
        ),
    ],
)
def test_settings_holding_no_readable_default_leave_ravis_its_own(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, settings: str | None
) -> None:
    """Nothing is set, so RAVIS uses 8,192, and `start` says why."""
    run = _load()
    home = _move_lmstudio(monkeypatch, run, tmp_path)
    if settings is not None:
        (home / "settings.json").write_text(settings)

    environment = _ravis_environment(monkeypatch, run, tmp_path)

    assert "RAVIS_LMSTUDIO_DEFAULT_CONTEXT" not in environment
    assert run.lmstudio_context_line(os.environ) == f"{LINE}8192 ({UNREADABLE})."


def test_a_value_already_in_the_environment_wins_over_lm_studios_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An operator who set the variable chose; the launcher does not read over them."""
    run = _load()
    home = _move_lmstudio(monkeypatch, run, tmp_path)
    (home / "settings.json").write_text(_laid_out(defaultContextLength=_custom(16384)))
    monkeypatch.setenv("RAVIS_LMSTUDIO_DEFAULT_CONTEXT", "4096")

    environment = _ravis_environment(monkeypatch, run, tmp_path)

    assert environment["RAVIS_LMSTUDIO_DEFAULT_CONTEXT"] == "4096"
    assert run.lmstudio_context_line(os.environ) == (
        f"{LINE}4096 (set by you in RAVIS_LMSTUDIO_DEFAULT_CONTEXT)."
    )


@pytest.mark.parametrize(
    "declared",
    [
        pytest.param(
            {"RAVIS_UPSTREAMS": json.dumps([
                {**LOCAL_LMSTUDIO, "base_url": "http://192.168.1.50:1234"}
            ])},
            id="declared on the LAN",
        ),
        pytest.param(
            {"RAVIS_UPSTREAMS": json.dumps([
                LOCAL_LMSTUDIO,
                {"name": "studio", "base_url": "http://studio.example:1234", "kind": "LMStudio"},
            ])},
            id="one here and one elsewhere",
        ),
        pytest.param(
            {
                "RAVIS_UPSTREAM_BASE_URL": "http://studio.example:1234",
                "RAVIS_UPSTREAM_KIND": "lmstudio",
            },
            id="the single upstream, elsewhere",
        ),
        pytest.param({"RAVIS_UPSTREAMS": "[]"}, id="no LM Studio at all"),
    ],
)
def test_an_lm_studio_not_on_this_machine_never_has_this_machines_settings_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, declared: dict[str, str]
) -> None:
    """A remote LM Studio's default is in its own machine's settings, not this one's."""
    run = _load()
    home = _move_lmstudio(monkeypatch, run, tmp_path)
    (home / "settings.json").write_text(_laid_out(defaultContextLength=_custom(16384)))
    for variable, value in declared.items():
        monkeypatch.setenv(variable, value)
    monkeypatch.setattr(run, "_lmstudio_settings_context", _refuse)

    environment = _ravis_environment(monkeypatch, run, tmp_path)

    assert "RAVIS_LMSTUDIO_DEFAULT_CONTEXT" not in environment
    assert run.lmstudio_context_line(os.environ) == f"{LINE}8192 ({NOT_HERE})."


@pytest.mark.parametrize(
    "declared",
    [
        pytest.param(
            {"RAVIS_UPSTREAMS": json.dumps([
                {"name": "studio", "base_url": "http://localhost:1234", "kind": " LMStudio "}
            ])},
            id="named otherwise, at localhost",
        ),
        pytest.param(
            {"RAVIS_UPSTREAM_BASE_URL": "http://[::1]:1234", "RAVIS_UPSTREAM_KIND": "lmstudio"},
            id="the single upstream, at IPv6 loopback",
        ),
    ],
)
def test_an_lm_studio_declared_on_this_machine_is_read_however_it_is_declared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, declared: dict[str, str]
) -> None:
    run = _load()
    home = _move_lmstudio(monkeypatch, run, tmp_path)
    (home / "settings.json").write_text(_laid_out(defaultContextLength=_custom(16384)))
    for variable, value in declared.items():
        monkeypatch.setenv(variable, value)

    environment = _ravis_environment(monkeypatch, run, tmp_path)

    assert environment["RAVIS_LMSTUDIO_DEFAULT_CONTEXT"] == "16384"


def test_start_says_what_ravis_was_told_and_where_it_came_from(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One line of `start`'s output, sealed the way the lifecycle tests seal `start`.

    RAVIS is already on its way here, so nothing is launched and the run reaches the end of
    `start`, where the line is printed beside the Ollama and upstream lines.
    """
    machine = FakeMachine([RAVIS_SERVICE])
    machine.process(700, _as_ps_shows(RAVIS_SERVICE), 8731, answers_at=10.0)
    run = _sealed_launcher(monkeypatch, tmp_path, machine)
    home = _move_lmstudio(monkeypatch, run, tmp_path)
    _quiet_start(monkeypatch, run)
    _record(run, {"RAVIS": {"pid": 700, "marker": "ravis serve"}})
    (home / "settings.json").write_text(_laid_out(defaultContextLength=_custom(16384)))
    monkeypatch.setenv("RAVIS_UPSTREAMS", json.dumps([LOCAL_LMSTUDIO]))

    assert run.start() == 0

    assert f"\n{LINE}16384 (from LM Studio's settings).\n" in capsys.readouterr().out


def test_the_default_start_prints_is_the_one_ravis_holds() -> None:
    """`start` names RAVIS's default by number without asking RAVIS, so the two are pinned here."""
    config = (REPOSITORY / "ravis" / "src" / "ravis" / "config.py").read_text(encoding="utf-8")
    declared = re.search(r"^\s*lmstudio_default_context: int = (\d+)$", config, re.MULTILINE)

    assert declared is not None
    assert int(declared.group(1)) == _load().RAVIS_LMSTUDIO_DEFAULT
