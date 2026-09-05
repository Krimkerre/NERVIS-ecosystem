"""`ravis doctor` — the M0 acceptance criteria, exercised through the CLI."""

from __future__ import annotations

import pytest

from ravis.cli import EXIT_FATAL_CONFIGURATION, EXIT_OK, main


def test_doctor_succeeds_on_a_default_configuration(monkeypatch, capsys) -> None:  # noqa: ANN001
    monkeypatch.setenv("RAVIS_DATABASE_PATH", ":memory:")

    assert main(["doctor"]) == EXIT_OK
    assert "configuration is serveable" in capsys.readouterr().out


def test_doctor_prints_the_resolved_provider_table(monkeypatch, tmp_path, capsys) -> None:  # noqa: ANN001
    """M0 acceptance: the table is printed, and nothing upstream is contacted.

    The base URL points at a port with nothing behind it, so a table that ever
    starts probing for real models fails this rather than passing slowly.
    `XDG_CONFIG_HOME` is redirected because the filters and the enable/disable
    state live there, and a test must not read whatever the developer running
    it happens to have configured.
    """
    monkeypatch.setenv("RAVIS_DATABASE_PATH", ":memory:")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("RAVIS_UPSTREAM_BASE_URL", "http://127.0.0.1:9/v1")

    main(["doctor"])

    printed = capsys.readouterr().out
    assert "resolved model → provider" in printed
    assert "first matching rule wins" in printed
    assert "ravis/default/*" in printed


def test_doctor_says_so_when_no_upstream_is_configured(monkeypatch, tmp_path, capsys) -> None:  # noqa: ANN001
    """An empty table names the settings that would fill it.

    "none configured" on its own sends the reader to the documentation; naming
    the two environment variables ends the question where it was asked.
    """
    monkeypatch.setenv("RAVIS_DATABASE_PATH", ":memory:")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("RAVIS_UPSTREAM_BASE_URL", raising=False)

    main(["doctor"])

    assert "RAVIS_UPSTREAM_BASE_URL or RAVIS_UPSTREAMS" in capsys.readouterr().out


def test_doctor_refuses_an_unsafe_bind(monkeypatch, capsys) -> None:  # noqa: ANN001
    monkeypatch.setenv("RAVIS_DATABASE_PATH", ":memory:")
    monkeypatch.setenv("RAVIS_HOST", "0.0.0.0")

    assert main(["doctor"]) == EXIT_FATAL_CONFIGURATION
    assert "FATAL" in capsys.readouterr().out


def test_an_unknown_command_is_rejected() -> None:
    """argparse exits rather than returning, so the failure is loud."""
    with pytest.raises(SystemExit):
        main(["teleport"])


def test_restoring_a_version_that_has_no_backup_says_so_rather_than_crashing(  # noqa: ANN001
    monkeypatch, tmp_path, capsys
) -> None:
    """The same defect NERVIS and SIRVIS had, in the same command.

    `restore_backup` refuses a missing version with a sentence naming the
    versions that exist, and no CLI caught it — so the command an operator
    reaches for while something is already wrong answered with a traceback.
    """
    import shutil

    from ravis.storage.database import prepare_database

    database = tmp_path / "ravis.db"
    monkeypatch.setenv("RAVIS_DATABASE_PATH", str(database))
    prepare_database(str(database))
    shutil.copyfile(database, database.with_name(f"{database.name}.v1.bak"))

    code = main(["restore-database", "--version", "9999"])

    assert code == EXIT_FATAL_CONFIGURATION
    said = capsys.readouterr().out
    assert "no backup at version 9999" in said
    assert "Traceback" not in said
