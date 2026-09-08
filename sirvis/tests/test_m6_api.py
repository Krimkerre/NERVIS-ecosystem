"""The read surface over benchmark runs and results (§4.2, §17).

§17 says these are stored *because* they are already served — they are the
provenance pointer RAVIS preserves and NERVIS dereferences to drill from a route
decision through to the evidence behind it. Until this existed the engine could
produce evidence and nothing could ask for it.

Reads are unauthenticated here, exactly like `/system` and `/models`. §4.5's gate
is about mutations — an unauthenticated or wrong-origin *change* is refused —
and CORS on this surface is deliberately read-only for the same reason.
"""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient
from tests.conftest_lmstudio import transport

from sirvis.app import create_app
from sirvis.config import Settings
from sirvis.runtimes import LMStudioAdapter
from sirvis.storage import RunState, StoredResult, create_experiment, finish_run, start_run


def _client() -> TestClient:
    """The real app with a recorded runtime under it (§14.5)."""
    settings = Settings(database_path=":memory:", lmstudio_base_url="http://127.0.0.1:9",
                        _env_file=None)  # type: ignore[call-arg]
    app = create_app(
        settings,
        runtime=LMStudioAdapter(
            "http://runtime.invalid", client=httpx.AsyncClient(transport=transport())
        ),
    )
    return TestClient(app)


def _seed(client: TestClient, target: str = "coder-7b") -> tuple[str, str]:
    """One finished run with one result, written the way the engine writes it."""
    database = client.app.state.database
    experiment = create_experiment(
        database, {"suite": "perf"}, suite_id="perf", suite_version="1",
        environment_mode="shared",
    )
    run = start_run(database, experiment, runtime_key="lmstudio", runtime_snapshot={},
                    machine_snapshot_id=None, results_path="results/exp_1")
    (result,) = finish_run(
        database, run, state=RunState.SUCCEEDED, detail="completed",
        results=[StoredResult(target_key=target, evidence_id="ev_abc", validity="VALID",
                              payload={"metrics": {"tok_s": {"median": 42.0}}})],
    )
    return run, result


def test_a_run_arrives_with_the_results_it_produced() -> None:
    """One request, not two. There is one result per ExperimentTarget and one
    target per run today, so splitting them would make a caller ask twice for
    one answer — and the drill from run to evidence is the path §17 exists for."""
    client = _client()
    run, result = _seed(client)

    body = client.get(f"/api/v1/benchmark-runs/{run}").json()

    assert body["run_id"] == run
    assert body["state"] == "succeeded"
    assert [r["result_id"] for r in body["results"]] == [result]
    assert body["results"][0]["metrics"]["tok_s"]["median"] == 42.0


def test_a_result_names_the_run_that_produced_it() -> None:
    """The end of the drill-through: a route decision keeps a `source_run_id`,
    and a result has to lead back the other way."""
    client = _client()
    run, result = _seed(client)

    body = client.get(f"/api/v1/benchmark-results/{result}").json()

    assert body["result_id"] == result
    assert body["run_id"] == run
    assert body["evidence_id"] == "ev_abc"
    assert body["validity"] == "VALID"


def test_an_unknown_run_is_a_404_that_says_which_one() -> None:
    """A run either happened or it did not — unlike a machine, which can exist
    and simply not have been captured yet."""
    response = _client().get("/api/v1/benchmark-runs/run_nothing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BENCHMARK_NOT_FOUND"
    assert "run_nothing" in response.json()["error"]["message"]


def test_an_unknown_result_is_a_404_too() -> None:
    response = _client().get("/api/v1/benchmark-results/res_nothing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BENCHMARK_NOT_FOUND"


def test_the_listing_is_newest_first_in_the_envelope_4_2_defines() -> None:
    client = _client()
    first, _ = _seed(client, "first")
    second, _ = _seed(client, "second")

    body = client.get("/api/v1/benchmark-runs").json()

    assert [r["run_id"] for r in body["items"]] == [second, first]
    assert body["next_cursor"] is None
    assert "snapshot_revision" in body


def test_paging_walks_every_run_exactly_once() -> None:
    """§4.2 promises `limit` and `cursor`. A list endpoint that accepted the
    limit and ignored the cursor would be half a contract, and the half nobody
    notices missing until a collection outgrows one page."""
    client = _client()
    created = [_seed(client, f"model-{i}")[0] for i in range(5)]

    seen, cursor, requests = [], None, 0
    while requests < 10:
        requests += 1
        query = f"?limit=2{'&cursor=' + cursor if cursor else ''}"
        body = client.get(f"/api/v1/benchmark-runs{query}").json()
        seen += [r["run_id"] for r in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert seen == list(reversed(created))
    assert len(seen) == len(set(seen))


def test_reading_a_run_needs_no_token() -> None:
    """Consistent with `/system` and `/models`: §4.5's gate is about mutations,
    and the browser dashboard reads this surface cross-origin."""
    client = _client()
    run, _ = _seed(client)

    assert client.get(f"/api/v1/benchmark-runs/{run}").status_code == 200


# ── §10, at the route: the service keeps answering under a bad disk ─────────


def _reading_app(results: object) -> object:
    """SIRVIS pointed at a results directory of the caller's choosing."""
    import httpx
    from fastapi.testclient import TestClient
    from tests.conftest_lmstudio import transport

    from sirvis.app import create_app
    from sirvis.config import Settings
    from sirvis.runtimes import LMStudioAdapter

    settings = Settings(database_path=":memory:", results_path=str(results),
                        lmstudio_base_url="http://127.0.0.1:9",
                        _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(
        settings,
        runtime=LMStudioAdapter("http://runtime.invalid",
                                client=httpx.AsyncClient(transport=transport())),
    ))


def test_a_read_only_results_directory_does_not_stop_the_service(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """**§15.4's standalone rule under §10's condition.** `doctor` reports an
    unwritable directory, which is the operator's warning; this is the other
    half — the service itself must keep answering rather than failing to start
    or 500-ing every read because writing would fail later.

    Refuses to run as root, where the permission bits do not apply and the test
    would pass for the wrong reason.
    """
    import os
    import pathlib

    if os.geteuid() == 0:
        raise AssertionError("permission bits do not apply as root; run as an ordinary user")
    locked = pathlib.Path(tmp_path) / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        with _reading_app(locked) as client:  # type: ignore[attr-defined]
            answered = client.get("/api/v1/runtimes")
            health = client.get("/ecosystem/health")
    finally:
        locked.chmod(0o755)

    assert answered.status_code == 200
    assert health.status_code == 200


def test_a_full_disk_does_not_stop_the_service(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The same claim for the other bad-disk condition. Every results write
    raises `ENOSPC`; the reads a person uses to find out *why* must still
    answer, because a diagnostic that dies with the disk is no diagnostic."""
    import errno

    from sirvis.storage.results import ResultDirectory

    def full(*_: object, **__: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(ResultDirectory, "prepare", full)
    monkeypatch.setattr(ResultDirectory, "write_experiment", full)

    with _reading_app(tmp_path) as client:  # type: ignore[attr-defined]
        answered = client.get("/api/v1/runtimes")
        runs = client.get("/api/v1/benchmark-runs")

    assert answered.status_code == 200
    assert runs.status_code == 200
