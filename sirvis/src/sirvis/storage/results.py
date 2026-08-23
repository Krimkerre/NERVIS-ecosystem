"""§11.9's raw result directory, so a rescoring never needs a rerun.

    results/<experiment-id>/
    ├── experiment.json
    ├── system.json
    ├── runtime.json
    ├── responses/
    ├── telemetry/
    └── logs/

The value is stated in one sentence in §11.9 — "raw responses enable rescoring
without rerunning inference" — and it is the difference between a grading bug
costing an afternoon of rescoring and costing a week of GPU time. It is also
what makes a result auditable: a number nobody can trace back to the text the
model actually produced is a claim rather than evidence.

**Telemetry is JSON Lines here, not Parquet.** §17 names Parquet for
high-frequency telemetry "where SQLite becomes unsuitable", and a single-model
run samples memory a few dozen times — a columnar format and a pyarrow
dependency would be answering a scale problem this does not have. The file is
one sample per line so the format can change to Parquet at M10, when concurrent
multi-model runs produce enough rows to justify it, without any reader here
having assumed a schema.

Written before the database row that points at it, deliberately. §11.10 requires
that no result becomes visible before its snapshot and provenance commit, and
the way to guarantee that is to make the files land first and the row that makes
them findable land last: a crash in between leaves an orphan directory, which is
inert, rather than a database row pointing at responses that were never written.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

# Kept out of the filename alphabet. A test ID arrives from a YAML file an
# operator wrote, and a specification with `id: ../../etc/passwd` must not be
# able to choose where this writes — the directory is derived from the ID, so
# the ID is constrained rather than trusted.
_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."


class ResultDirectory:
    """One experiment's raw output on disk.

    Created lazily: `sirvis doctor` deliberately reports the results directory
    without making it, so the engine that owns it makes it when it has something
    to put there.
    """

    def __init__(self, root: str | Path, experiment_id: str) -> None:
        self.root = Path(root).expanduser()
        self.experiment_id = experiment_id
        self.path = self.root / _safe(experiment_id)

    def prepare(self) -> Path:
        """Make the directory and its subdirectories, and return the root."""
        for child in ("responses", "telemetry", "logs"):
            (self.path / child).mkdir(parents=True, exist_ok=True)
        return self.path

    # ── The three documents that describe the run ────────────────────────────

    def write_experiment(self, payload: Mapping[str, Any]) -> Path:
        return self._write_json("experiment.json", payload)

    def write_system(self, payload: Mapping[str, Any]) -> Path:
        return self._write_json("system.json", payload)

    def write_runtime(self, payload: Mapping[str, Any]) -> Path:
        return self._write_json("runtime.json", payload)

    def write_result(self, payload: Mapping[str, Any]) -> Path:
        """The evidence envelope, beside the raw material it was derived from.

        Duplicated with the database row on purpose. The directory has to stand
        on its own — copied to another machine, attached to a bug report — and a
        set of responses with no summary is as hard to use as a summary with no
        responses.
        """
        return self._write_json("result.json", payload)

    # ── The raw material ─────────────────────────────────────────────────────

    def write_response(self, test_id: str, phase: str, index: int,
                       payload: Mapping[str, Any]) -> Path:
        """One generation, exactly as it came back.

        Warmups are written too. §11.7 excludes them from the statistics, not
        from the record: a warmup that returned an error explains a measured run
        that looks strange afterwards, and discarding it discards the
        explanation.
        """
        name = f"{_safe(test_id)}-{_safe(phase)}-{index:03d}.json"
        return self._write_json(Path("responses") / name, payload)

    def write_telemetry(self, samples: Iterable[Mapping[str, Any]]) -> Path:
        """Memory samples, one JSON object per line."""
        destination = self.path / "telemetry" / "measurements.jsonl"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8") as handle:
            for sample in samples:
                handle.write(json.dumps(sample, sort_keys=True) + "\n")
        return destination

    def append_log(self, line: str) -> None:
        """One line of the run's own narration.

        A benchmark that failed halfway is the case this exists for: the
        database row says `failed` and this says what it was doing at the time.
        """
        destination = self.path / "logs" / "run.log"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip("\n") + "\n")

    def _write_json(self, name: str | Path, payload: Mapping[str, Any]) -> Path:
        destination = self.path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return destination


def _safe(name: str) -> str:
    """A filename component derived from operator-supplied text.

    Every character outside the allow-list becomes an underscore, which makes
    path traversal impossible rather than merely unlikely: `..` survives as
    `..`, but a separator cannot, so the result is always one component inside
    the directory it was given.
    """
    cleaned = "".join(character if character in _SAFE else "_" for character in name)
    return cleaned.strip(".") or "unnamed"
