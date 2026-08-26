"""Measured metrics for API models, which turned out to already be measured.

Every request records its total latency and every streamed one records
time-to-first-token, into `HealthRegistry`. §13.3 even names the evidence kind —
`OBSERVED_BY_RAVIS`, "a measurement, but not one taken under controlled
conditions" — and nothing produced it, because the registry lives in memory and
a restart discarded every sample. An audit found four samples across six hundred
models and concluded coverage was too thin to rank on. It was thin because it
kept starting over.

No new instrumentation and no probe traffic: a model is measured by being used.
"""

from __future__ import annotations

import pytest

from ravis.core.pools import POOLS_BY_ID
from ravis.observations import MINIMUM_SAMPLES, WINDOW, Observations


@pytest.fixture()
def store(tmp_path) -> Observations:
    return Observations(tmp_path / "observations.json")


# ── Recording ──────────────────────────────────────────────────────────────


def test_a_median_needs_no_samples_to_be_absent(store: Observations) -> None:
    """None, not zero. A model nobody has called and a model that answers
    instantly are different, and reporting zero for the first is a lie a
    dashboard repeats."""
    assert store.of("never-called").median_latency_ms is None
    assert store.of("never-called").samples == 0


def test_timings_accumulate(store: Observations) -> None:
    for value in (100.0, 200.0, 300.0):
        store.record("m", value, value / 2)

    assert store.of("m").median_latency_ms == 200.0
    assert store.of("m").median_ttft_ms == 100.0
    assert store.of("m").samples == 3


def test_a_window_forgets_the_distant_past(store: Observations) -> None:
    """A rolling window rather than a running mean, so a provider that got
    slower shows it within a bounded number of requests instead of being
    averaged against its own history forever."""
    for _ in range(WINDOW + 40):
        store.record("m", 10.0, None)
    for _ in range(WINDOW):
        store.record("m", 900.0, None)

    assert store.of("m").samples == WINDOW
    assert store.of("m").median_latency_ms == 900.0


def test_a_missing_measure_is_skipped_not_zeroed(store: Observations) -> None:
    """A non-streaming request has no time-to-first-token. Recording zero would
    make it the fastest model in the catalogue."""
    store.record("m", 500.0, None)

    assert store.of("m").median_latency_ms == 500.0
    assert store.of("m").median_ttft_ms is None


# ── Confidence ─────────────────────────────────────────────────────────────


def test_a_figure_below_the_floor_is_shown_but_not_ranked_on(
    store: Observations,
) -> None:
    """A median of two network calls is two numbers.

    Reported so a reader can see it, absent from the ranking map so a caller
    cannot use it without deciding what to do about the other case.
    """
    store.record("m", 100.0, 50.0)
    store.record("m", 120.0, 60.0)

    assert store.of("m").median_ttft_ms == 55.0
    assert store.of("m").confident is False
    assert "m" not in store.ttft_for_ranking()


def test_enough_samples_makes_it_rankable(store: Observations) -> None:
    for _ in range(MINIMUM_SAMPLES):
        store.record("m", 100.0, 50.0)

    assert store.of("m").confident is True
    assert store.ttft_for_ranking()["m"] == 50.0


def test_the_count_travels_with_the_figure(store: Observations) -> None:
    """Never a median without the number of samples that earned it."""
    store.record("m", 100.0, 50.0)

    reported = store.of("m").as_dict()
    assert reported["samples"] == 1
    assert reported["provenance"] == "OBSERVED_BY_RAVIS"


# ── Surviving a restart, which is the entire point ─────────────────────────


def test_samples_outlive_the_process(store: Observations) -> None:
    for _ in range(MINIMUM_SAMPLES):
        store.record("m", 400.0, 120.0)
    store.flush()

    reopened = Observations(store.path)
    reopened.load()

    assert reopened.of("m").samples == MINIMUM_SAMPLES
    assert reopened.of("m").median_ttft_ms == 120.0
    assert reopened.of("m").confident is True


def test_a_corrupt_file_starts_empty_rather_than_refusing(
    store: Observations,
) -> None:
    """Losing a cache of timings is a mild inconvenience. Refusing to start a
    gateway over one is not a trade anybody would choose."""
    store.path.write_text("{not json", encoding="utf-8")
    store.load()

    assert store.all() == {}


def test_nonsense_in_the_file_is_dropped(store: Observations) -> None:
    store.path.write_text(
        '{"m": {"latency_ms": [1, "x", null, -5, 2], "ttft_ms": "nope"}}',
        encoding="utf-8",
    )
    store.load()

    assert store.of("m").latency_ms == [1.0, 2.0]
    assert store.of("m").ttft_ms == []


def test_flushing_nothing_writes_nothing(store: Observations) -> None:
    store.flush()

    assert not store.path.exists()


# ── The cold-start asymmetry ───────────────────────────────────────────────


def test_an_unmeasured_model_ranks_neutral_not_last() -> None:
    """The one place this differs from price, and it matters.

    An unpriced model sorts last because a price exists and the provider chose
    not to publish it. An unmeasured model has simply never been called here,
    and sorting it last would be a trap that closes: never chosen, so never
    measured, so never chosen.
    """
    from ravis.routing.engine import _UNMEASURED_MS, _speed_rank

    observed = {"timed-and-slow": 4_000.0, "timed-and-quick": 90.0}

    assert _speed_rank("never-timed", observed) == _UNMEASURED_MS
    assert _speed_rank("timed-and-quick", observed) < _speed_rank("never-timed", observed)
    assert _speed_rank("never-timed", observed) < _speed_rank("timed-and-slow", observed)


def test_only_the_speed_pool_ranks_on_it() -> None:
    """Opt-in, like the other soft preferences: an unconditional term would
    become the whole ordering for every pool that declares nothing."""
    assert POOLS_BY_ID["ravis/fast"].prefer_fast is True
    assert POOLS_BY_ID["ravis/auto"].prefer_fast is False
