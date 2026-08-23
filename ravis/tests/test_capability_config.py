"""Operator-declared capabilities, from a file as well as the environment.

§5.2 makes operator declaration the only source of capability truth until
probing (§8.7) or SIRVIS evidence (M13) exists. The file is for the case the
environment variable serves badly: a declaration whose *provenance* matters as
much as its content, and therefore needs to be readable and diffable.
"""

from __future__ import annotations

import json
from pathlib import Path

from ravis.config import Settings, inspect_configuration, resolved_capabilities


def _settings(**overrides: object) -> Settings:
    return Settings(
        database_path=":memory:",
        _env_file=None,  # type: ignore[call-arg]
        **overrides,  # type: ignore[arg-type]
    )


def _write(tmp_path: Path, payload: object) -> str:
    location = tmp_path / "capabilities.json"
    location.write_text(json.dumps(payload))
    return str(location)


def test_a_file_declares_capabilities(tmp_path: Path) -> None:
    path = _write(tmp_path, {"coder-a": {"tools": "SUPPORTED", "context_window": "32768"}})

    declared = resolved_capabilities(_settings(model_capabilities_path=path))

    assert declared == {"coder-a": {"tools": "SUPPORTED", "context_window": "32768"}}


def test_the_environment_overrides_the_file_per_model(tmp_path: Path) -> None:
    """The file is considered; the environment is what someone reaches for now.

    Merged per capability rather than per model, so overriding `tools` does not
    silently drop a `context_window` the file supplied.
    """
    path = _write(
        tmp_path,
        {
            "coder-a": {"tools": "SUPPORTED", "context_window": "32768"},
            "coder-b": {"tools": "SUPPORTED"},
        },
    )
    settings = _settings(
        model_capabilities_path=path,
        model_capabilities={"coder-a": {"tools": "UNSUPPORTED"}},
    )

    declared = resolved_capabilities(settings)

    assert declared["coder-a"] == {"tools": "UNSUPPORTED", "context_window": "32768"}
    assert declared["coder-b"] == {"tools": "SUPPORTED"}


def test_underscore_keys_are_notes_rather_than_models(tmp_path: Path) -> None:
    """JSON has no comments, and a capability record that cannot say where its
    claims came from is exactly what this file exists to avoid."""
    path = _write(
        tmp_path,
        {"_source": "benchmarks.md", "coder-a": {"tools": "SUPPORTED", "_measured": "8/8"}},
    )

    declared = resolved_capabilities(_settings(model_capabilities_path=path))

    assert "_source" not in declared
    # A note *inside* a model is left alone: the capability parser already
    # ignores names it does not recognise, and stripping it here would mean two
    # places deciding what a capability name is.
    assert declared["coder-a"]["tools"] == "SUPPORTED"


def test_a_named_file_that_is_missing_refuses_the_service(tmp_path: Path) -> None:
    """Fatal, because of the failure it replaces.

    Without the declarations every capability stays UNKNOWN, the agent pool
    fails closed (§5.2), and the operator sees "the agent is broken" rather than
    "you typed the path wrong".
    """
    settings = _settings(model_capabilities_path=str(tmp_path / "absent.json"))

    report = inspect_configuration(settings)

    assert report.is_startable() is False
    assert "does not exist" in report.fatal_findings[0].message


def test_a_malformed_file_refuses_the_service(tmp_path: Path) -> None:
    path = tmp_path / "capabilities.json"
    path.write_text("[not an object]")
    settings = _settings(model_capabilities_path=str(path))

    report = inspect_configuration(settings)

    assert report.is_startable() is False


def test_declaring_no_file_is_not_a_finding() -> None:
    """Absence is a state: no file means no declarations, not a misconfiguration."""
    report = inspect_configuration(_settings())

    assert resolved_capabilities(_settings()) == {}
    assert [f for f in report.findings if f.setting == "model_capabilities_path"] == []


def test_the_checked_in_measured_file_is_loadable_and_declares_tools() -> None:
    """The file this repository ships is not allowed to rot silently.

    It is derived from `clarvis/docs/benchmarks.md`, which is a document in
    another repository that can change without anything here noticing. This does
    not re-verify the measurements — nothing here can — but it does catch the
    file becoming unparseable or losing the claim that makes the agent pool
    routable at all.
    """
    path = Path(__file__).resolve().parent.parent / "measured-capabilities.json"
    settings = Settings(
        database_path=":memory:",
        model_capabilities_path=str(path),
        _env_file=None,  # type: ignore[call-arg]
    )

    declared = resolved_capabilities(settings)

    assert inspect_configuration(settings).is_startable()
    assert declared["qwen2.5-coder-7b-instruct"]["tools"] == "SUPPORTED"
    # The disagreement that is the whole point of the file: one model, two
    # packagings, opposite measured outcomes.
    assert declared["lmstudio-community/granite-4.0-h-tiny"]["tools"] == "SUPPORTED"
    assert declared["mlx-community/granite-4.0-h-tiny"]["tools"] == "UNSUPPORTED"
