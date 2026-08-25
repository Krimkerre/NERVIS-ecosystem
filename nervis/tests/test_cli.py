"""The command line, which has to work when nothing else does.

`doctor` is the command someone runs *because* the ecosystem is broken, so
every test here runs with no peer, no dashboard assumption and a fresh database.
"""

from __future__ import annotations

from typing import Any

import pytest

from nervis.cli import EXIT_FATAL_CONFIGURATION, EXIT_OK, main


def test_doctor_succeeds_with_nothing_else_running(monkeypatch, tmp_path, capsys) -> None:  # noqa: ANN001
    """M0's exit: `nervis doctor` works, and no external service is required."""
    monkeypatch.setenv("NERVIS_DATABASE_PATH", str(tmp_path / "nervis.db"))

    assert main(["doctor"]) == EXIT_OK
    assert "configuration is serveable" in capsys.readouterr().out


def test_doctor_migrates_rather_than_inspecting_a_path(monkeypatch, tmp_path, capsys) -> None:  # noqa: ANN001
    """"The database migrates cleanly" is an exit criterion, so `doctor` does it.

    Checking that a file exists proves nothing about the schema inside it, and
    the version printed here is read back from the bookkeeping table.
    """
    monkeypatch.setenv("NERVIS_DATABASE_PATH", str(tmp_path / "fresh.db"))

    main(["doctor"])

    assert "migrated to version 3" in capsys.readouterr().out


def test_doctor_prints_every_capability_with_its_reason(monkeypatch, tmp_path, capsys) -> None:  # noqa: ANN001
    """§4.1's argument — "not yet, because M6" beats a status light — applies to
    a person reading a terminal as much as to a peer negotiating."""
    monkeypatch.setenv("NERVIS_DATABASE_PATH", str(tmp_path / "nervis.db"))

    main(["doctor"])
    printed = capsys.readouterr().out

    assert "nervis.event_hub@1" in printed
    assert "the event hub" in printed or "M6" in printed


def test_doctor_names_its_peers_without_contacting_them(monkeypatch, tmp_path, capsys) -> None:  # noqa: ANN001
    """Probing is M2. The addresses are printed because "NERVIS cannot see
    RAVIS" is most often "NERVIS is pointed somewhere else"."""
    monkeypatch.setenv("NERVIS_DATABASE_PATH", str(tmp_path / "nervis.db"))
    monkeypatch.setenv("NERVIS_RAVIS_BASE_URL", "http://127.0.0.1:9")

    main(["doctor"])
    printed = capsys.readouterr().out

    assert "not contacted" in printed
    assert "http://127.0.0.1:9" in printed


def test_doctor_refuses_an_unsafe_bind(monkeypatch, tmp_path, capsys) -> None:  # noqa: ANN001
    monkeypatch.setenv("NERVIS_DATABASE_PATH", str(tmp_path / "nervis.db"))
    monkeypatch.setenv("NERVIS_HOST", "0.0.0.0")  # noqa: S104

    assert main(["doctor"]) == EXIT_FATAL_CONFIGURATION
    assert "FATAL" in capsys.readouterr().out


def test_serve_refuses_before_it_binds(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    """The check runs before the port is bound rather than after.

    A service that binds and then discovers it should not have is a service that
    was briefly reachable, and briefly is enough. `uvicorn.run` is replaced with
    something that fails loudly, so a regression here is an error rather than a
    test that hangs on a real socket.
    """
    monkeypatch.setenv("NERVIS_DATABASE_PATH", str(tmp_path / "nervis.db"))
    monkeypatch.setenv("NERVIS_HOST", "0.0.0.0")  # noqa: S104

    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("serve bound a port on a configuration it should have refused")

    monkeypatch.setattr("nervis.cli.uvicorn.run", refuse)

    assert main(["serve"]) == EXIT_FATAL_CONFIGURATION


def test_an_unknown_command_is_rejected() -> None:
    """argparse exits rather than returning, so the failure is loud."""
    with pytest.raises(SystemExit):
        main(["teleport"])
