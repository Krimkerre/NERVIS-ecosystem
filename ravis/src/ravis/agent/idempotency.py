"""A retried request answered once (design §3.5.1; `conventions.json` → `headers.Idempotency-Key`).

`Idempotency-Key` (1-128 printable characters) is required on create, `turns`, `steer`, `answer`,
`settle`, `reissue-token` and `owner-stop`: missing is 428 `IDEMPOTENCY_KEY_REQUIRED`. The first
**successful** answer is kept for 24 hours in `agent_idempotency`, per *scope* — the session or
workspace root, the window where the body names one, and the route — and per key, so two windows
never share one (review AM6). The same key and body get that answer again; the same key with
another body is 422 `IDEMPOTENCY_KEY_REUSED`. A refusal is decided afresh on a retry, as Clarvis's
fake relay does, since the contract leaves replaying errors unspecified.

**A retried settle returns the settled view**, not `409 CLAIM_INVALID`; a retried answer the same
`{resolved, decision_kind}` — but only for that window's own key, so another window gets `409
REQUEST_ALREADY_RESOLVED`, never someone else's 200 (`resolution_before_replay`).

**Tokens in a replay** (the contract's open point, decided here). A replayed create or
`reissue-token` must return the token it returned the first time, but RAVIS stores only a token's
sha256 and the table keeps ids and states. So the token is kept **in memory only**, beside the
table's row, for the same 24 hours: a replay in the same RAVIS run returns it. After a RAVIS
restart it is gone, and the replay is answered as a fresh `reissue-token` would be — a new token
if no window has been attached for 60 seconds, else 409 `WINDOW_ATTACHED` (`sessions.py`).
That grants nothing `reissue-token` doesn't already.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any

from fastapi import Request

from ravis.agent.store import ANSWERS_KEPT, AgentStore
from ravis.codex.idempotency import valid_key
from ravis.codex.refusals import idempotency_key_required, idempotency_key_reused

TOKEN_FIELD = "session_token"


def required_key(request: Request) -> str:
    key = request.headers.get("idempotency-key", "")
    if not valid_key(key):
        raise idempotency_key_required()
    return key


def body_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def scope(*parts: object) -> str:
    return "|".join("" if part is None else str(part) for part in parts)


class KeptResponses:
    """The relay's kept answers: the table, plus replayable tokens held in memory."""

    def __init__(self, store: AgentStore, clock: Callable[[], float] = time.monotonic) -> None:
        self._store = store
        self._clock = clock
        self._tokens: dict[tuple[str, str], tuple[str, float]] = {}

    def find(self, where: str, key: str, digest: str) -> tuple[int, dict[str, Any]] | None:
        """The kept (status, body) for this key, or None; another body raises the 422."""
        row = self._store.kept_answer(where, key)
        if row is None:
            return None
        if row["body_sha256"] != digest:
            raise idempotency_key_reused()
        return int(row["status"]), json.loads(row["response_json"])

    def keep(
        self, where: str, key: str, digest: str, status: int, body: dict[str, Any]
    ) -> None:
        stored = {name: value for name, value in body.items() if name != TOKEN_FIELD}
        self._store.keep_answer(where, key, digest, status, json.dumps(stored))
        token = body.get(TOKEN_FIELD)
        if isinstance(token, str):
            self.remember(where, key, token)

    def remember(self, where: str, key: str, token: str) -> None:
        """Hold a token a replay of this key must return, in memory only."""
        self._forget_old()
        self._tokens[(where, key)] = (token, self._clock())

    def token(self, where: str, key: str) -> str | None:
        self._forget_old()
        kept = self._tokens.get((where, key))
        return kept[0] if kept else None

    def _forget_old(self) -> None:
        horizon = self._clock() - ANSWERS_KEPT.total_seconds()
        for name in [name for name, (_, at) in self._tokens.items() if at < horizon]:
            del self._tokens[name]
