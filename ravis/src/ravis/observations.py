"""What RAVIS has actually observed of a model, kept across restarts.

**The measurement was already happening.** Every request records its total
latency and every streamed one records time-to-first-token, into
`HealthRegistry`. §13.3 even names the resulting evidence kind —
`OBSERVED_BY_RAVIS`, "a measurement, but not one taken under controlled
conditions" — and nothing produced it, because the registry is in memory and a
restart discarded every sample. An audit of the routing path found the samples
and concluded coverage was too thin to rank on: four records against six hundred
models. It was thin because it kept starting over.

This is the same numbers, written down. No new instrumentation, no probe
traffic, no requests nobody asked for: a model gets measured by being used, and
the coverage that matters is the coverage of models this deployment actually
routes to.

**What it is not.** SIRVIS measures a local model under controlled conditions —
fixed prompt, warm runtime, repetitions, a recorded method. This is a rolling
window over whatever real traffic happened to look like, so a model that
answered three long prompts and one short one has a median that reflects the
prompts as much as the model. That is why the sample count travels with every
figure and why the two evidence kinds stay separate: one is a benchmark, the
other is experience.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ravis.credentials import config_directory

logger = logging.getLogger("ravis.observations")

# How many samples of each measure to keep per model.
#
# A window rather than a running mean, so a provider that got slower shows it
# within a bounded number of requests instead of being averaged against its own
# history forever. Sixty is roughly an afternoon of use for a model in regular
# rotation and small enough that the whole file stays a few hundred kilobytes.
WINDOW = 60

# Below this, a figure is reported but never ranked on.
#
# Two samples of a network call is not a measurement of anything; it is two
# numbers. The floor is deliberately low rather than statistically respectable
# because the alternative to a weak measurement here is *no* measurement, and
# five real observations of a model beat the substring heuristics they replace.
# `confident` is what routing asks; `samples` is what a screen shows, so nobody
# has to infer the difference.
MINIMUM_SAMPLES = 5

# Below this, a figure is not a slow model or a fast one — it is not a model at
# all. No completed inference, on any hardware, returns in under a millisecond,
# so a sample beneath this describes something other than the call it is filed
# against. Zero and negatives are covered by the same comparison.
IMPOSSIBLE_BELOW_MS = 1.0

# How long an unused model's samples are kept once nothing has exercised them.
#
# The backstop, not the main mechanism. A model withdrawn from a catalogue is
# pruned as soon as a healthy refresh proves it gone; this covers the case where
# no catalogue can be believed — a provider disabled, a key removed, an upstream
# unreachable for a week — where "absent" is not evidence of anything.
RETENTION_DAYS = 30.0


@dataclass
class ModelObservations:
    """One model's rolling windows."""

    latency_ms: list[float] = field(default_factory=list)
    ttft_ms: list[float] = field(default_factory=list)
    # Wall-clock seconds, not the monotonic clock the timings use.
    #
    # These two measure different things and only one of them may cross a
    # restart: a monotonic reading is meaningless in a file, because the epoch
    # it counts from is the process that wrote it. Retention is asked in days,
    # so wall clock is the right clock even though it can step backwards.
    last_seen: float = 0.0

    def record(self, latency_ms: float | None, ttft_ms: float | None,
               now: float | None = None) -> None:
        """Add a sample to each window, refusing ones that cannot be real.

        **The floor catches impossibility, not implausibility**, and the
        distinction is the point: a fast model and a slow one are both this
        store's business, but no completed inference — local or hosted — takes
        less than a millisecond end to end. A figure below that is not a
        measurement of a model, it is a measurement of something else that got
        recorded as one.

        Added after exactly that happened. A translated stream timed itself from
        RAVIS's own opening frame rather than the provider's first token, and
        filed 49 samples of ~0.2 ms against a hosted model, which then fed the
        median that routing ranks on. The defect was at the call site and is
        fixed there; this is the store declining to hold a number that cannot
        describe a model, so the next such bug shows up as missing data rather
        than as a model that looks impossibly fast.
        """
        for window, value in ((self.latency_ms, latency_ms), (self.ttft_ms, ttft_ms)):
            if value is None or value < IMPOSSIBLE_BELOW_MS:
                continue
            window.append(value)
            del window[:-WINDOW]
        self.last_seen = time.time() if now is None else now

    @property
    def median_latency_ms(self) -> float | None:
        return statistics.median(self.latency_ms) if self.latency_ms else None

    @property
    def median_ttft_ms(self) -> float | None:
        return statistics.median(self.ttft_ms) if self.ttft_ms else None

    @property
    def samples(self) -> int:
        """The count a reader should judge the medians by.

        The larger of the two windows rather than their sum: they measure the
        same requests, and adding them would double a number somebody is using
        to decide whether to believe the one beside it.
        """
        return max(len(self.latency_ms), len(self.ttft_ms))

    @property
    def confident(self) -> bool:
        return self.samples >= MINIMUM_SAMPLES

    def as_dict(self) -> dict[str, Any]:
        """Never a rounded figure without the count that earned it."""
        return {
            "median_latency_ms": self.median_latency_ms,
            "median_ttft_ms": self.median_ttft_ms,
            "samples": self.samples,
            "confident": self.confident,
            "last_seen": self.last_seen or None,
            # §13.3's name for exactly this: measured, but not under controlled
            # conditions. SIRVIS's benchmark evidence is the other kind.
            "provenance": "OBSERVED_BY_RAVIS",
        }


@dataclass
class Observations:
    """Every model RAVIS has timed, persisted as ordinary configuration.

    Beside `providers.json` and `pools.json`: nothing here is secret, and a file
    a person can read and delete is the right shape for a cache of measurements
    they may want to reset after changing hardware or a provider plan.
    """

    path: Path
    _models: dict[str, ModelObservations] = field(default_factory=dict)
    _dirty: bool = False

    @staticmethod
    def default(environment: dict[str, str] | None = None) -> Observations:
        store = Observations(config_directory(environment) / "observations.json")
        store.load()
        return store

    def load(self) -> None:
        """Read the file, or start empty.

        A malformed file yields no observations rather than raising. Losing a
        cache of timings is a mild inconvenience; refusing to start a gateway
        over one is not a trade anybody would choose.
        """
        try:
            with self.path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        for model, record in payload.items():
            if isinstance(record, dict):
                self._models[str(model)] = ModelObservations(
                    latency_ms=_floats(record.get("latency_ms")),
                    ttft_ms=_floats(record.get("ttft_ms")),
                    last_seen=_seconds(record.get("last_seen")),
                )

    def record(self, model: str, latency_ms: float | None, ttft_ms: float | None,
               now: float | None = None) -> None:
        """Fold one request's timings in. Cheap and in memory; `flush` writes."""
        if latency_ms is None and ttft_ms is None:
            return
        self._models.setdefault(model, ModelObservations()).record(latency_ms, ttft_ms, now)
        self._dirty = True

    def prune(
        self,
        offered: set[str] | None = None,
        *,
        now: float | None = None,
        retention_days: float = RETENTION_DAYS,
    ) -> list[str]:
        """Drop samples that can no longer inform a route. Returns what went.

        Two ways a model becomes dead weight, and they need different tests.

        **It is gone from every catalogue.** Then it can never be selected
        again, and its samples are pure clutter — prune immediately. This is the
        precise mechanism and the one that matters for OpenRouter, which
        withdraws models regularly.

        **Nobody has used it in a month.** The backstop, for when no catalogue
        can be believed. `offered` is only honoured when it is non-empty, which
        is the guard that matters: a provider that is disabled, unreachable, or
        missing a credential contributes nothing to that set, and pruning
        against a set that failed to load would delete every measurement RAVIS
        has on the strength of one bad fetch. Absence is only evidence when
        something was actually present to compare against.

        An entry with no `last_seen` is one written before this existed. It is
        kept and stamped on next use rather than deleted, because "unknown age"
        and "thirty-one days old" are not the same claim.
        """
        moment = time.time() if now is None else now
        cutoff = moment - retention_days * 86_400
        # Only a *non-empty* set is evidence of absence. See the docstring.
        catalogue = offered or set()
        pruned = []
        for model, observed in list(self._models.items()):
            withdrawn = bool(catalogue) and model not in catalogue
            stale = observed.last_seen > 0 and observed.last_seen < cutoff
            if withdrawn or stale:
                del self._models[model]
                pruned.append(model)
        if pruned:
            self._dirty = True
            logger.info("pruned observations for %d model(s)", len(pruned))
        return pruned

    def of(self, model: str) -> ModelObservations:
        return self._models.get(model) or ModelObservations()

    def all(self) -> dict[str, ModelObservations]:
        return dict(self._models)

    def ttft_for_ranking(self) -> dict[str, float]:
        """Median TTFT per model, for the models with enough samples to mean it.

        Only the confident ones. A model with two samples is absent from this
        map rather than present with a shaky number, so a caller cannot use one
        without having decided what to do about the other case.
        """
        return {
            model: observed.median_ttft_ms
            for model, observed in self._models.items()
            if observed.confident and observed.median_ttft_ms is not None
        }

    def flush(self) -> None:
        """Write, if anything changed.

        Called on a timer and at shutdown rather than per request: a gateway
        that fsyncs on the hot path has traded the latency it is trying to
        measure for the record of it.
        """
        if not self._dirty:
            return
        payload = {
            model: {
                "latency_ms": observed.latency_ms,
                "ttft_ms": observed.ttft_ms,
                "last_seen": observed.last_seen,
            }
            for model, observed in self._models.items()
        }
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=1, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, self.path)
        self._dirty = False


def _seconds(value: object) -> float:
    """A timestamp out of the file, or zero for one written before this existed."""
    return float(value) if isinstance(value, (int, float)) and value > 0 else 0.0


def _floats(value: object) -> list[float]:
    """Samples out of whatever the file held, dropping anything that is not one."""
    if not isinstance(value, list):
        return []
    kept = [float(item) for item in value if isinstance(item, (int, float)) and item >= 0]
    return kept[-WINDOW:]
