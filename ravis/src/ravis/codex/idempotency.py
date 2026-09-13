"""Answers kept by `Idempotency-Key`, so a retried request is answered once (conventions.json).

`POST /api/v1/codex/reprove` requires an `Idempotency-Key` (RAVIS.md §15.1.2): the launcher sends a
fresh one with each run of `run.py codex reprove`, and a request that reaches RAVIS twice — a retry
after a lost answer — must not start a second re-test on the owner's allowance. So the first
answer is kept for 24 hours and given again for the same key and the same body; the same key with
a different body is refused (422 `IDEMPOTENCY_KEY_REUSED`), and no key at all is 428.

Keys are kept per application, so two callers never share one. Only successful answers are kept —
a refusal is decided afresh on a retry, as C1's fake relay does, since the contract leaves replaying
errors unspecified. The store is in memory: agent sessions' durable idempotency table arrives with
migration 8 in M29's third increment, and a RAVIS restart ends any re-test anyway.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ravis.codex.refusals import idempotency_key_reused

KEPT_SECONDS = 24 * 3600.0
#: Plenty for a route the owner starts by hand; the oldest answer goes first beyond it.
CAPACITY = 256
#: "1-128 printable characters" (conventions.json, `headers.Idempotency-Key.format`).
KEY_LIMIT = 128


@dataclass(frozen=True)
class KeptAnswer:
    body_sha256: str
    status: int
    body: dict[str, Any]
    kept_at: float


def valid_key(key: str) -> bool:
    return 0 < len(key) <= KEY_LIMIT and key.isprintable()


class KeptAnswers:
    """The kept answers of one route, by (application, key)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._answers: OrderedDict[tuple[str, str], KeptAnswer] = OrderedDict()

    def find(self, application_id: str, key: str, body_sha256: str) -> KeptAnswer | None:
        """The answer this key already had, or None; a different body raises the 422 refusal."""
        self._forget_old()
        kept = self._answers.get((application_id, key))
        if kept is not None and kept.body_sha256 != body_sha256:
            raise idempotency_key_reused()
        return kept

    def keep(
        self, application_id: str, key: str, body_sha256: str, status: int, body: dict[str, Any]
    ) -> None:
        self._answers[(application_id, key)] = KeptAnswer(body_sha256, status, body, self._clock())
        while len(self._answers) > CAPACITY:
            self._answers.popitem(last=False)

    def _forget_old(self) -> None:
        horizon = self._clock() - KEPT_SECONDS
        for identity in [name for name, kept in self._answers.items() if kept.kept_at < horizon]:
            del self._answers[identity]
