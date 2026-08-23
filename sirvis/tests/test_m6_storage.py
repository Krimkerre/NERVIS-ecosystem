"""Where results live (§17), and the rule about when they become visible (§11.10).

§17 states a cardinality — an Experiment has many runs, a run has one result per
ExperimentTarget, a run never spans experiments — and the reason it is written
down is that RAVIS's `source_run_id` and NERVIS's drill-through cannot be built
without it. Here it is enforced rather than described.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from sirvis.storage import (
    ResultDirectory,
    RunState,
    StoredResult,
    create_experiment,
    finish_run,
    list_runs,
    prepare_database,
    read_run,
    reconcile_interrupted,
    start_run,
)


def _run(database: object) -> tuple[str, str]:
    experiment = create_experiment(
        database, {"suite": "perf"}, suite_id="perf", suite_version="1",
        environment_mode="shared",
    )
    run = start_run(
        database, experiment, runtime_key="lmstudio", runtime_snapshot={},
        machine_snapshot_id=None, results_path=None,
    )
    return experiment, run


def _result(target: str = "model-a") -> StoredResult:
    return StoredResult(target_key=target, evidence_id="ev_1", validity="VALID",
                        payload={"metrics": {}})


def test_a_run_holds_one_result_per_target() -> None:
    """§17's sentence, made mechanical. A retry that writes twice fails loudly
    rather than leaving two results that disagree about one target."""
    database = prepare_database(":memory:")
    _, run = _run(database)
    finish_run(database, run, state=RunState.SUCCEEDED, detail="done", results=[_result()])

    with pytest.raises(sqlite3.IntegrityError):
        finish_run(database, run, state=RunState.SUCCEEDED, detail="again",
                   results=[_result()])


def test_results_and_the_terminal_state_commit_together() -> None:
    """§11.10: no result is visible before its run's state commits. The proof is
    the rollback — a failed insert must leave the run exactly as it was, not
    finished with its results missing."""
    database = prepare_database(":memory:")
    _, run = _run(database)
    finish_run(database, run, state=RunState.SUCCEEDED, detail="done", results=[_result()])

    with pytest.raises(sqlite3.IntegrityError):
        finish_run(database, run, state=RunState.FAILED, detail="rolled back",
                   results=[_result()])

    stored = read_run(database, run)
    assert stored is not None
    assert stored["state"] == RunState.SUCCEEDED.value
    assert stored["detail"] == "done"
    assert len(stored["results"]) == 1


def test_a_run_starts_recorded_so_a_crash_leaves_evidence_of_it() -> None:
    """§11.10 asks for jobs that survive a restart or are truthfully marked
    unrecoverable. A run first written when it succeeds can be neither."""
    database = prepare_database(":memory:")
    _, run = _run(database)

    stored = read_run(database, run)
    assert stored is not None
    assert stored["state"] == RunState.RUNNING.value
    assert stored["finished_at"] is None


def test_two_identical_experiments_are_two_experiments() -> None:
    """The opposite of M3's rule for model identity, deliberately: a build is
    described by its attributes, an experiment is an event. Deriving this ID
    would make a rerun overwrite the run it was meant to be compared with."""
    database = prepare_database(":memory:")
    first, _ = _run(database)
    second, _ = _run(database)

    assert first != second


def test_the_newest_run_is_the_one_latest_shows() -> None:
    database = prepare_database(":memory:")
    _run(database)
    _, newest = _run(database)

    page, _ = list_runs(database, limit=1)
    assert page[0]["run_id"] == newest


def test_paging_never_skips_or_repeats_a_run() -> None:
    """Ordered by rowid rather than `started_at`, because two runs begun in the
    same second sort arbitrarily by timestamp — and a paging key that can tie
    eventually drops a row or serves it twice, rarely and unreproducibly."""
    database = prepare_database(":memory:")
    created = [_run(database)[1] for _ in range(5)]

    seen, cursor = [], None
    for _ in range(10):
        page, cursor = list_runs(database, limit=2, cursor=cursor)
        seen += [r["run_id"] for r in page]
        if cursor is None:
            break

    assert seen == list(reversed(created))
    assert len(seen) == len(set(seen))


def test_the_last_page_offers_no_cursor() -> None:
    """A cursor on the final page points at nothing and invites one more
    request that returns nothing."""
    database = prepare_database(":memory:")
    _run(database)

    page, cursor = list_runs(database, limit=5)

    assert len(page) == 1
    assert cursor is None


def test_the_raw_directory_has_the_shape_the_specification_names(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """§11.9's layout, so a rescoring never needs a rerun."""
    directory = ResultDirectory(tmp_path, "exp_1")
    directory.prepare()
    directory.write_experiment({"suite": "perf"})
    directory.write_system({"chip": "M-series"})
    directory.write_runtime({"runtime_key": "lmstudio"})
    directory.write_response("t1", "measured", 0, {"content": "hello"})
    directory.write_telemetry([{"point": "baseline", "available_bytes": 1}])
    directory.append_log("acquired")

    root = tmp_path / "exp_1"
    assert (root / "experiment.json").exists()
    assert (root / "system.json").exists()
    assert (root / "runtime.json").exists()
    assert (root / "logs/run.log").read_text().strip() == "acquired"
    written = json.loads((root / "responses/t1-measured-000.json").read_text())
    assert written["content"] == "hello"
    telemetry = (root / "telemetry/measurements.jsonl").read_text().splitlines()
    assert json.loads(telemetry[0])["point"] == "baseline"


def test_a_specification_cannot_choose_where_results_are_written(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Test IDs come from a YAML file an operator wrote. `id: ../../etc/passwd`
    must land inside the results directory, not outside it."""
    directory = ResultDirectory(tmp_path, "exp_1")
    directory.prepare()
    written = directory.write_response("../../escape", "measured", 0, {})

    # The property is containment, not the absence of dots: a name may keep a
    # `..` inside it harmlessly, but it can never carry a separator, so the file
    # always lands in this experiment's own responses directory.
    assert written.resolve().is_relative_to((tmp_path / "exp_1" / "responses").resolve())


# ── Runs that outlived their process (§11.10) ────────────────────────────────


def test_an_interrupted_run_is_marked_unrecoverable() -> None:
    """§11.10 asks that a job survive a restart *or* be truthfully marked
    unrecoverable. A run does neither on its own: `start_run` writes RUNNING
    before the work — right, because a run first recorded on success could not
    be recovered at all — and nothing puts the row right if the process dies.
    Two such rows sat in this machine's database for a day."""
    database = prepare_database(":memory:")
    _, interrupted = _run(database)

    abandoned = reconcile_interrupted(database)

    stored = read_run(database, interrupted)
    assert abandoned == [interrupted]
    assert stored is not None
    assert stored["state"] == RunState.FAILED.value
    assert "ended before the run finished" in stored["detail"]
    assert stored["finished_at"] is not None


def test_a_finished_run_is_left_alone() -> None:
    """Reconciliation reads *unfinished* as abandoned. A run that reached a
    terminal state reached it honestly and is nobody's to rewrite."""
    database = prepare_database(":memory:")
    _, done = _run(database)
    finish_run(database, done, state=RunState.SUCCEEDED, detail="completed",
               results=[_result()])

    assert reconcile_interrupted(database) == []
    stored = read_run(database, done)
    assert stored is not None
    assert stored["state"] == RunState.SUCCEEDED.value
    assert stored["detail"] == "completed"


def test_reconciling_twice_changes_nothing() -> None:
    """It runs at every startup, so it has to be a no-op on a clean database —
    and must not keep rewriting `finished_at` on runs it already settled."""
    database = prepare_database(":memory:")
    _run(database)
    first = reconcile_interrupted(database)

    assert len(first) == 1
    assert reconcile_interrupted(database) == []


def test_a_failed_run_is_not_called_cancelled() -> None:
    """Cancelled means a client asked to stop — a different fact about a
    different actor — and partial means some results were kept, which these have
    none of, because `finish_run` is the only writer of results."""
    database = prepare_database(":memory:")
    _run(database)
    reconcile_interrupted(database)

    states = {r["state"] for r in list_runs(database, limit=10)[0]}
    assert states == {RunState.FAILED.value}
