"""Which models an operator has chosen for a pool, when they have chosen.

A pool is defined by invariants — capabilities, context, locality — and picks
from whatever satisfies them. That is the right default and it is what makes a
pool keep working as a catalogue changes. But it leaves no way to say "not that
one", and with four API providers publishing six hundred models between them,
"whatever satisfies the invariants" is a wider net than most people want
`ravis/fast` casting.

So membership is an **optional narrowing**, stored per pool, empty by default.
An empty selection means the pool behaves exactly as it always has.

**A narrowing, never a widening — and that distinction is the whole safety
argument.** An operator may remove a model from a pool. An operator may not add
one that fails the pool's invariants: `ravis/local` promises the request never
leaves this machine, and a promise that an operator can tick away in a picker is
not a promise. The engine applies invariants first and this second, so the
worst a bad selection can do is empty a pool — visible immediately, and a
refusal rather than a silent leak.

Beside `providers.json` and `models.json`, for the same reason: nothing here is
secret, so it is a file a person can read, diff and keep in a dotfiles repo.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from ravis.credentials import config_directory


@dataclass
class PoolMembership:
    """Every pool's chosen members, persisted as ordinary configuration."""

    path: Path
    _cache: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @staticmethod
    def default(environment: dict[str, str] | None = None) -> PoolMembership:
        return PoolMembership(config_directory(environment) / "pools.json")

    def for_pool(self, pool_id: str) -> tuple[str, ...]:
        """The models chosen for one pool, or empty for "whatever qualifies"."""
        return self.all().get(pool_id, ())

    def all(self) -> dict[str, tuple[str, ...]]:
        """Every stored selection.

        A malformed file yields none at all — every pool unnarrowed, which is
        the permissive direction the sibling stores take. A corrupt convenience
        file must not be able to make a gateway serve nothing.
        """
        try:
            with self.path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            str(pool_id): tuple(
                model for model in record if isinstance(model, str) and model
            )
            for pool_id, record in payload.items()
            if isinstance(record, list)
        }

    def set_for(self, pool_id: str, models: tuple[str, ...]) -> tuple[str, ...]:
        """Store one pool's selection, replacing any previous one.

        An empty tuple removes the entry rather than storing `[]`. The two would
        behave identically — both mean "no narrowing" — and keeping the file
        free of entries that say nothing is what lets somebody read it and see
        only the decisions actually made.
        """
        payload = {pool: list(stored) for pool, stored in self.all().items()}
        if models:
            payload[pool_id] = list(models)
        else:
            payload.pop(pool_id, None)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, self.path)
        return models
