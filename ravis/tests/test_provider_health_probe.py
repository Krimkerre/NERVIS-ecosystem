"""The Providers screen's health probe: bounded, and not re-run on every open.

Reported as "RAVIS often has a delay when checking pools or providers". Measured,
it was one live round-trip to OpenAI per screen open — `GET /models`, the cheapest
call the protocol offers and still ~830 ms across the Atlantic. Concurrent, so the
screen waits for whichever provider is slowest.

Two things were wrong underneath that, and only one of them is the delay.
"""

from __future__ import annotations

import httpx
import pytest

from ravis.providers.base import HEALTH_TIMEOUT_SECONDS
from ravis.providers.generic_openai import GenericOpenAiAdapter
from ravis.upstream import Upstream


def _upstream() -> Upstream:
    return Upstream(base_url="https://api.invalid", declared_key="k")


@pytest.mark.asyncio()
async def test_a_health_probe_carries_its_own_short_timeout() -> None:
    """The bug behind the delay, which is not the delay.

    The probe shares the upstream client, whose read timeout is
    `upstream_timeout_seconds` — 300 seconds, and correct for what it is for:
    streaming a long completion. A health check inherited it, so a provider that
    accepted a connection and then stalled would have held the Providers screen
    for five minutes. The connect timeout of 10 s does not cover that case; a
    server that answers slowly is not a server that fails to connect.

    Asserted on the request rather than by sleeping, and deliberately so. The
    first version stood up a handler that slept thirty seconds and asserted the
    probe gave up first — which never fired, because `MockTransport` runs the
    handler in-process and httpx's timeout governs socket I/O. It would have
    been testing httpx's timeout enforcement, which is httpx's to test. What is
    ours is that the right number reaches the request, and that is what this
    reads.
    """
    seen: list[object] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions.get("timeout"))
        return httpx.Response(200, json={"data": []})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(300.0, connect=10.0),
    )
    provider = GenericOpenAiAdapter(_upstream(), client)

    result = await provider.health()

    assert result.reachable
    assert seen, "the probe must actually have been sent"
    timeout = seen[0]
    assert isinstance(timeout, dict)
    assert timeout["read"] == HEALTH_TIMEOUT_SECONDS
    assert timeout["read"] < 300.0, "the inference timeout must not govern a health check"


@pytest.mark.asyncio()
async def test_a_repeat_open_within_the_window_does_not_re_probe() -> None:
    """The delay itself, as opposed to the bug behind it.

    Every open of the Providers screen cost one live round-trip per provider,
    concurrently — so the screen waited on whichever was slowest, measured at
    ~830 ms for OpenAI. Nothing was wrong with any of it; the answer simply
    comes from California.

    A reachability reading is worth about as much a few seconds later, so it is
    held briefly. The window is short on purpose: somebody who has just started
    LM Studio is watching this screen to see it appear, and a minute of
    stale "not answering" would read as the fix not working.
    """
    import asyncio

    from ravis.api.management.routes import (
        HEALTH_CACHE_SECONDS,
        ProviderHealthCache,
        _probe,
    )

    class _Adapter:
        name = "openai"

        def __init__(self) -> None:
            self.calls = 0

        async def health(self) -> object:
            self.calls += 1
            return type("_H", (), {"reachable": True, "detail": "", "latency_ms": 12.0})()

    adapter = _Adapter()
    cache = ProviderHealthCache()

    first = await _probe(adapter, cache, now=1000.0)
    second = await _probe(adapter, cache, now=1000.0 + HEALTH_CACHE_SECONDS / 2)

    assert adapter.calls == 1, "the second open must be answered from the reading just taken"
    assert first == second

    # Past the window the stale reading still answers — instantly — and the
    # refresh happens behind it. That is the whole of what makes the screen
    # feel fixed rather than merely faster on a fast retry.
    third = await _probe(adapter, cache, now=1000.0 + HEALTH_CACHE_SECONDS + 0.1)
    assert third == first, "an expired reading is served, not waited for"
    await asyncio.sleep(0)  # let the refresh task run
    assert adapter.calls == 2, "and it is refreshed behind the answer"


@pytest.mark.asyncio()
async def test_a_failing_probe_is_cached_too() -> None:
    """Otherwise the screen is slowest exactly when a provider is broken.

    An unreachable provider is the case where the probe costs the most — it runs
    to the timeout — and the case somebody reloads most often.
    """
    from ravis.api.management.routes import ProviderHealthCache, _probe

    class _Failing:
        name = "openai"

        def __init__(self) -> None:
            self.calls = 0

        async def health(self) -> object:
            self.calls += 1
            raise RuntimeError("nope")

    adapter = _Failing()
    cache = ProviderHealthCache()

    first = await _probe(adapter, cache, now=1000.0)
    await _probe(adapter, cache, now=1000.5)

    assert adapter.calls == 1
    assert first["reachable"] is False
