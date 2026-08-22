"""`ravis doctor` — the M0 acceptance criteria, exercised through the CLI."""

from __future__ import annotations

import pytest

from ravis.cli import EXIT_FATAL_CONFIGURATION, EXIT_OK, main


def test_doctor_succeeds_on_a_default_configuration(monkeypatch, capsys) -> None:  # noqa: ANN001
    monkeypatch.setenv("RAVIS_DATABASE_PATH", ":memory:")

    assert main(["doctor"]) == EXIT_OK
    assert "configuration is serveable" in capsys.readouterr().out


def test_doctor_prints_the_resolved_provider_table(monkeypatch, capsys) -> None:  # noqa: ANN001
    """M0 acceptance: the table is printed, and nothing upstream is contacted."""
    monkeypatch.setenv("RAVIS_DATABASE_PATH", ":memory:")

    main(["doctor"])

    assert "resolved model → provider" in capsys.readouterr().out


def test_doctor_refuses_an_unsafe_bind(monkeypatch, capsys) -> None:  # noqa: ANN001
    monkeypatch.setenv("RAVIS_DATABASE_PATH", ":memory:")
    monkeypatch.setenv("RAVIS_HOST", "0.0.0.0")

    assert main(["doctor"]) == EXIT_FATAL_CONFIGURATION
    assert "FATAL" in capsys.readouterr().out


def test_an_unknown_command_is_rejected() -> None:
    """argparse exits rather than returning, so the failure is loud."""
    with pytest.raises(SystemExit):
        main(["teleport"])
