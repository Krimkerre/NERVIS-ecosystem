"""`sirvis doctor` — §18's checklist, and the drift that let it go stale.

§18 says doctor checks Apple Silicon, macOS, RAM, disk, LM Studio, the LM Studio
API, installed models, GGUF and MLX capability, the database and the results
directory. It reported configuration and migrations only: M1 and M2 landed and
this was never revisited.

Nothing counts CLI output, which is why the drift survived a status gate built
to catch exactly this kind of thing. These tests are that count.
"""

from __future__ import annotations

import pytest

from sirvis.cli import main

# A port nothing listens on, so the runtime probe takes the unreachable path.
# §14.5: no test reaches a live service, and doctor now contacts one by design.
CLOSED_PORT = "http://127.0.0.1:9"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.setenv("SIRVIS_DATABASE_PATH", f"{tmp_path}/doctor.db")
    monkeypatch.setenv("SIRVIS_RESULTS_PATH", f"{tmp_path}/results")
    monkeypatch.setenv("SIRVIS_LMSTUDIO_BASE_URL", CLOSED_PORT)


def test_doctor_reports_the_machine_it_will_measure_on(capsys: pytest.CaptureFixture[str]) -> None:
    """§18 asks for Apple Silicon, RAM and disk. It reported none of them."""
    assert main(["doctor"]) == 0

    printed = capsys.readouterr().out

    assert "machine" in printed
    assert "apple silicon" in printed
    assert "memory" in printed
    assert "disk" in printed


def test_doctor_succeeds_with_no_runtime_and_says_so(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """§15.4: SIRVIS works standalone, and doctor is most useful exactly when
    something is not answering. An absent runtime is a finding, never a failure.
    """
    assert main(["doctor"]) == 0

    printed = capsys.readouterr().out

    assert "unreachable" in printed or "stopped" in printed
    assert "none visible" in printed
    assert "configuration is serveable" in printed


def test_doctor_reports_the_results_directory_without_creating_it(
    capsys: pytest.CaptureFixture[str], tmp_path: object
) -> None:
    """A directory appearing as a side effect of a diagnostic is a surprise.

    The benchmark engine owns it and can make it when it needs it; doctor's job
    is to say whether it will work.
    """
    import pathlib

    assert main(["doctor"]) == 0
    printed = capsys.readouterr().out

    assert "results" in printed
    assert not pathlib.Path(f"{tmp_path}/results").exists()


def test_doctor_still_refuses_an_unsafe_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    """The check that was already there must survive the ones that were added."""
    monkeypatch.setenv("SIRVIS_HOST", "0.0.0.0")  # noqa: S104 - the unsafe case under test

    assert main(["doctor"]) == 2


def test_doctor_names_a_metric_it_could_not_read_rather_than_guessing(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """M1's rule reaching the operator's screen: `unknown`, never a plausible
    number. A snapshot that hides its gaps invites the reader to assume there
    were none."""
    from sirvis import cli
    from sirvis.telemetry import SystemSnapshot

    monkeypatch.setattr(
        cli, "detect_system",
        lambda: SystemSnapshot(platform_name="Linux", architecture="x86_64",
                               is_apple_silicon=False),
    )

    assert main(["doctor"]) == 0
    printed = capsys.readouterr().out

    assert "unknown" in printed
    assert "apple silicon  no" in printed
