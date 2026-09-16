"""What RAVIS is doing right now, and what providers say about their limits (§12.3, M20).

§12.3 asks RAVIS to **track** active requests, local generations, queue depth, memory pressure
and provider congestion, and provider quotas where possible. Until 16 September 2026 only memory
pressure was known — sampled on a timer for routing — and nothing counted a request between the
moment it was sent and the moment its stream closed.

**Tracking, not steering.** §12.3 says what to watch and not what to do about it, and M20 names no
exit that changes a route. So nothing here reaches the router: a figure that moved requests would
be a routing decision nobody has made, and every client on the machine would feel it. The owner
decides that separately, with these numbers in front of them.

**Unknown stays unknown.** RAVIS holds no queue of its own — it sends a request upstream as it
arrives — so its queue depth is a fact, zero, and said as one. LM Studio and Ollama publish no
queue depth at all, so theirs is unknown, and RAVIS's own count of what it has in flight to them
is the nearest honest figure. A provider that sends no rate-limit headers has no limits recorded,
which is not the same as having none.
"""

from __future__ import annotations

import contextlib
import re
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

# OpenAI's spelling, and the bare form some compatible services use:
# `x-ratelimit-remaining-requests`, `x-ratelimit-reset-tokens`, `x-ratelimit-limit`.
_OPENAI_STYLE = re.compile(r"^x-ratelimit-(limit|remaining|reset)(?:-(requests|tokens))?$")
# Anthropic's: `anthropic-ratelimit-input-tokens-remaining` and the rest.
_ANTHROPIC_STYLE = re.compile(
    r"^anthropic-ratelimit-(requests|tokens|input-tokens|output-tokens)-(limit|remaining|reset)$"
)


def read_limits(headers: Mapping[str, str]) -> dict[str, dict[str, str]]:
    """Every limit a response's headers state, by what it limits, values as sent.

    Values are kept as the provider wrote them. The reset is a duration for OpenAI
    (`6m0s`), a timestamp for Anthropic and something else again elsewhere, and turning
    them into one number here would be a guess presented as a reading. A bare
    `x-ratelimit-limit` says what it counts nowhere, so it is filed as `unnamed`.
    """
    found: dict[str, dict[str, str]] = {}
    for name, value in headers.items():
        key = name.lower()
        openai = _OPENAI_STYLE.match(key)
        anthropic = _ANTHROPIC_STYLE.match(key)
        if openai:
            kind, part = openai.group(2) or "unnamed", openai.group(1)
        elif anthropic:
            kind, part = anthropic.group(1), anthropic.group(2)
        else:
            continue
        found.setdefault(kind, {})[part] = value.strip()
    return found


@dataclass
class _Limit:
    """One kind of limit as a provider last stated it, and when."""

    values: dict[str, str]
    seen_at: float


@dataclass
class _Provider:
    """What one provider's responses have said about its limits."""

    limits: dict[str, _Limit] = field(default_factory=dict)
    retry_after: str = ""
    retry_after_at: float | None = None


class LoadTracker:
    """Requests in flight, and each provider's own statement of its limits.

    One per process, on the app, like the health registry — and like it, in memory: a
    count of what is running now means nothing after a restart.
    """

    def __init__(
        self,
        provider_of_host: Callable[[str], str],
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._provider_of_host = provider_of_host
        self._clock = clock
        # (provider, model, local) -> attempts running now.
        self._running: Counter[tuple[str, str, bool]] = Counter()
        self._started = 0
        self._peak = 0
        self._since = wall()
        self._providers: dict[str, _Provider] = {}

    # ── Commands ─────────────────────────────────────────────────────────────

    @contextlib.asynccontextmanager
    async def running(self, model: str, provider: str, local: bool) -> AsyncIterator[None]:
        """Count one upstream attempt for as long as it runs — its whole stream included.

        An (async) context manager rather than a pair of calls, because an attempt ends in more
        ways than anyone lists — an answer, a refusal, a timeout, a client that went away — and a
        count that missed one of them would drift upward for the rest of the process.
        """
        key = (provider, model, local)
        self._running[key] += 1
        self._started += 1
        self._peak = max(self._peak, self.active)
        try:
            yield
        finally:
            self._running[key] -= 1
            if self._running[key] <= 0:
                del self._running[key]

    async def observe(self, response: httpx.Response) -> None:
        """Read a provider response's rate-limit headers: an `httpx` response hook.

        On the one client every provider call goes through, so a translated provider's
        headers are read too, though its adapter never hands them over. A response from a
        host no provider is configured at, or carrying no such header, changes nothing.
        """
        limits = read_limits(response.headers)
        retry_after = response.headers.get("retry-after", "")
        if not limits and not retry_after:
            return
        provider = self._provider_of_host(response.request.url.netloc.decode())
        if not provider:
            return
        now = self._clock()
        known = self._providers.setdefault(provider, _Provider())
        for kind, values in limits.items():
            known.limits[kind] = _Limit(values, now)
        if retry_after:
            known.retry_after, known.retry_after_at = retry_after, now

    # ── Queries ──────────────────────────────────────────────────────────────

    @property
    def active(self) -> int:
        return sum(self._running.values())

    def snapshot(self) -> dict[str, Any]:
        """What is running, and what providers last said, for `/api/v1/health`."""
        now = self._clock()
        return {
            "active": self.active,
            "local_generations": sum(n for (_, _, local), n in self._running.items() if local),
            "peak": self._peak,
            "attempts_started": self._started,
            "since": self._since,
            "by_provider": self._by_provider(),
            "queue": {
                "held": 0,
                "reason": "RAVIS sends each request upstream as it arrives and keeps no queue",
            },
            "local_queues": {
                "state": "unknown",
                "reason": "LM Studio and Ollama publish no queue depth; local_generations is "
                          "what RAVIS itself has running on them",
            },
            "limits": [self._limits_of(name, known, now)
                       for name, known in sorted(self._providers.items())],
        }

    def _by_provider(self) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for (provider, model, local), count in sorted(self._running.items()):
            row = rows.setdefault(provider, {"provider": provider, "local": local,
                                             "active": 0, "models": {}})
            row["active"] += count
            row["models"][model] = count
        return list(rows.values())

    @staticmethod
    def _limits_of(name: str, known: _Provider, now: float) -> dict[str, Any]:
        entry: dict[str, Any] = {"provider": name}
        for kind, limit in sorted(known.limits.items()):
            entry[kind] = {**limit.values, "seconds_ago": round(now - limit.seen_at, 1)}
        if known.retry_after and known.retry_after_at is not None:
            entry["retry_after"] = {"value": known.retry_after,
                                    "seconds_ago": round(now - known.retry_after_at, 1)}
        return entry
