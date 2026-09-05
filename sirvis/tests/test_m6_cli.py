"""`sirvis benchmark run` and `sirvis results latest` — §18, and M6's exit.

The command is where the safety behaviour lives, and it is the reason this file
exists separately from the engine's tests. **A benchmark loads a model**: it is
slow, it spends the machine's memory, and it is invisible from the command that
triggered it. Four models were loaded onto the developer's machine during this
build without anyone intending it, so the command says what it is about to do
and waits — and refuses outright when there is nobody there to ask.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sirvis.cli import main

EXAMPLE = str(
    Path(__file__).resolve().parent.parent / "examples" / "basic.yaml"
)
"""The shipped example, addressed from this file rather than from the working
directory. `"examples/basic.yaml"` resolved only when pytest ran from `sirvis/`,
so from the repository root the CLI was handed a path that does not exist and
these tests asserted against its empty output (§16 item 11)."""


# A port nothing listens on. §14.5, and the reason the run below fails cleanly
# rather than measuring anything.
CLOSED_PORT = "http://127.0.0.1:9"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.setenv("SIRVIS_DATABASE_PATH", f"{tmp_path}/benchmark.db")
    monkeypatch.setenv("SIRVIS_RESULTS_PATH", f"{tmp_path}/results")
    monkeypatch.setenv("SIRVIS_LMSTUDIO_BASE_URL", CLOSED_PORT)


def test_it_refuses_to_load_a_model_with_nobody_to_ask(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Not politeness. An unattended script that meant to run this can pass
    `--yes`; one that did not should not discover the difference by finding a
    14 GB model resident an hour later."""
    code = main(["benchmark", "run", EXAMPLE])

    captured = capsys.readouterr()
    assert code == 2
    assert "about to load" in captured.out
    assert "--yes" in captured.err


def test_it_says_what_it_will_load_before_it_loads_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The model, the configuration and the amount of work — before any of it
    happens, not in a summary afterwards."""
    main(["benchmark", "run", EXAMPLE, "--yes"])

    printed = capsys.readouterr().out
    assert "qwen2.5-coder-7b-instruct" in printed
    assert "context_length" in printed
    assert "warmup" in printed


def test_an_absent_runtime_fails_the_run_rather_than_the_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """§15.4: SIRVIS works standalone. A closed LM Studio is the ordinary state
    of a laptop, and the message has to name that rather than a stack trace."""
    code = main(["benchmark", "run", EXAMPLE, "--yes"])

    assert code == 1
    assert "not answering" in capsys.readouterr().err


def test_the_model_can_be_overridden_so_the_example_stays_runnable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The shipped example names a model this machine may not have installed."""
    main(["benchmark", "run", EXAMPLE, "--yes", "--model", "something-else"])

    assert "something-else" in capsys.readouterr().out


def test_results_latest_says_so_when_nothing_has_been_measured(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An empty table is not an error, and the answer that helps is the command
    that would fill it."""
    assert main(["results", "latest"]) == 0

    assert "no benchmark runs" in capsys.readouterr().out


def test_restoring_a_version_that_has_no_backup_says_so_rather_than_crashing(  # noqa: ANN001
    monkeypatch, tmp_path, capsys
) -> None:
    """The same defect NERVIS and RAVIS had, in the same command.

    `restore_backup` refuses a missing version with a sentence naming the
    versions that exist, and no CLI caught it — so the command an operator
    reaches for while something is already wrong answered with a traceback.
    """
    import shutil

    from sirvis.cli import EXIT_FATAL_CONFIGURATION, main
    from sirvis.storage.database import prepare_database

    database = tmp_path / "sirvis.db"
    monkeypatch.setenv("SIRVIS_DATABASE_PATH", str(database))
    prepare_database(str(database))
    shutil.copyfile(database, database.with_name(f"{database.name}.v1.bak"))

    code = main(["restore-database", "--version", "9999"])

    assert code == EXIT_FATAL_CONFIGURATION
    said = capsys.readouterr().out
    assert "no backup at version 9999" in said
    assert "Traceback" not in said
