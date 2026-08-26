"""`GET /v1/models` — cache-backed, fast, and never a 401.

The constraints here come from Clarvis's availability probe: no headers, a
two-second timeout, and a 401 read as *offline* rather than unauthorized
(RAVIS.md §5.0.1). So these tests are less about the catalogue's contents than
about what the endpoint refuses to do — authenticate, block, or empty itself.
"""

from __future__ import annotations

import contextlib

import httpx
from fastapi.testclient import TestClient
from tests.conftest_upstream import RecordingUpstream

from ravis.app import create_app
from ravis.config import Settings
from ravis.registry import ModelRegistry
from ravis.upstream import Upstream


def _registry(upstream_fake: RecordingUpstream, configured: bool = True) -> ModelRegistry:
    upstream = Upstream(base_url="http://upstream.invalid" if configured else "", declared_key="")
    return ModelRegistry(
        upstream=upstream,
        client=httpx.AsyncClient(transport=upstream_fake.transport()),
        ttl_seconds=300.0,
    )


async def test_a_refresh_populates_the_catalogue() -> None:
    fake = RecordingUpstream()
    registry = _registry(fake)

    await registry.refresh()

    assert len(registry.snapshot.models) == 2


async def test_reads_do_not_contact_the_upstream() -> None:
    """The endpoint is a dictionary lookup. Reading it a hundred times must not
    produce a hundred upstream calls, or a slow upstream becomes a slow probe."""
    fake = RecordingUpstream()
    registry = _registry(fake)
    await registry.refresh()

    for _ in range(100):
        registry.as_openai_list()

    assert fake.models_requests == 1


async def test_residency_is_probed_on_refresh_not_per_request() -> None:
    """§9.8 budgets routing at P50 under 5 ms, so residency is cached too."""
    fake = RecordingUpstream()
    registry = _registry(fake)
    await registry.refresh()

    for _ in range(100):
        registry.residency.state_of("anything")

    assert fake.residency_requests == 1


async def test_a_non_lmstudio_upstream_leaves_residency_unknown() -> None:
    """The ordinary case for any other server, and not a fault.

    Unknown rather than empty: empty would mean "nothing is loaded", which makes
    every model look equally cold and silently undoes the preference residency
    exists to provide.
    """
    registry = _registry(RecordingUpstream())
    await registry.refresh()

    assert registry.residency.known is False


async def test_a_failed_refresh_keeps_the_previous_catalogue() -> None:
    """Serving a slightly stale list beats serving an empty one.

    An empty catalogue tells a client "this provider has no models", which is a
    stronger and more wrong claim than "here is what was there a minute ago".
    """
    fake = RecordingUpstream()
    registry = _registry(fake)
    await registry.refresh()

    registry.use_client(httpx.AsyncClient(transport=httpx.MockTransport(_always_fails)))
    await registry.refresh()

    assert len(registry.snapshot.models) == 2
    assert registry.snapshot.last_error


def _always_fails(request: httpx.Request) -> httpx.Response:
    del request
    raise httpx.ConnectError("upstream is down")


async def test_an_unconfigured_upstream_yields_an_empty_catalogue_not_a_failure() -> None:
    """No upstream is a legitimate state, not a broken one (§5.0.1)."""
    registry = _registry(RecordingUpstream(), configured=False)

    await registry.refresh()

    assert registry.snapshot.models == []
    assert registry.snapshot.has_been_refreshed is True


async def test_created_is_present_on_every_entry_or_on_none() -> None:
    """Clarvis sorts by timestamp only when all entries carry `created`, so a
    partially-populated list scrambles whatever order was intended (§5.0.1)."""
    fake = RecordingUpstream()
    registry = _registry(fake)
    await registry.refresh()
    registry.snapshot.models[0]["created"] = 1

    entries = registry.as_openai_list()["data"]

    carrying = [entry for entry in entries if "created" in entry]
    assert len(carrying) in (0, len(entries))


def test_the_endpoint_answers_without_a_credential() -> None:
    """A 401 here makes Clarvis report RAVIS offline — a misleading failure."""
    settings = Settings(database_path=":memory:", _env_file=None)  # type: ignore[call-arg]
    client = TestClient(create_app(settings))

    response = client.get("/v1/models")

    assert response.status_code == 200
    assert response.json()["object"] == "list"


# ── Recovery when an upstream comes back ────────────────────────────────────


def _dead_registry(failure: Exception) -> ModelRegistry:
    """A registry whose upstream never answers."""

    def handle(request: httpx.Request) -> httpx.Response:
        del request
        raise failure

    return ModelRegistry(
        upstream=Upstream(base_url="http://upstream.invalid", declared_key=""),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        ttl_seconds=300.0,
    )


def _refusing_registry(status: int) -> ModelRegistry:
    """A registry whose upstream answers, and says no."""

    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status, json={"error": "no"})

    return ModelRegistry(
        upstream=Upstream(base_url="http://upstream.invalid", declared_key=""),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        ttl_seconds=300.0,
    )


async def test_an_unreachable_upstream_asks_for_a_shorter_interval() -> None:
    """The bug this exists for: a five-minute empty catalogue after recovery.

    A RAVIS that started while LM Studio was down served an empty candidate set
    for up to `models_cache_ttl_seconds` after LM Studio came back — every route
    refused with "no models are available from the configured upstream" and
    `considered: []`. The only recovery was a restart.
    """
    registry = _dead_registry(httpx.ConnectError("refused"))

    await registry.refresh()

    assert registry.needs_recovery is True


async def test_a_refused_upstream_does_not(status: int = 429) -> None:
    """It answered. It is present and telling RAVIS something.

    Asking a rate limiter more often is the one response guaranteed to make it
    worse — NERVIS proved that on itself the same day, by shortening its probe
    interval until it exceeded this service's anonymous limit.
    """
    registry = _refusing_registry(status)

    await registry.refresh()

    assert registry.needs_recovery is False


async def test_a_successful_but_empty_catalogue_is_not_recovery() -> None:
    """LM Studio with its server up and nothing installed is legitimately empty.

    Retrying every fifteen seconds would not install anything, and treating
    "this upstream has no models" as a fault would make an ordinary state look
    like an outage.
    """

    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"data": []})

    registry = ModelRegistry(
        upstream=Upstream(base_url="http://upstream.invalid", declared_key=""),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        ttl_seconds=300.0,
    )

    await registry.refresh()

    assert registry.snapshot.models == []
    assert registry.needs_recovery is False


async def test_recovery_clears_once_the_upstream_answers() -> None:
    """Otherwise the short interval would be permanent, which is the loop."""
    fake = RecordingUpstream()
    registry = _registry(fake)
    registry._unreachable = True  # noqa: SLF001 - simulating a prior failed pass

    await registry.refresh()

    assert registry.needs_recovery is False
    assert len(registry.snapshot.models) == 2


async def test_the_loop_sleeps_the_recovery_interval_while_unreachable() -> None:
    """The interval is chosen per pass rather than fixed when the task starts.

    A loop that read the interval once would keep the long one for the life of
    the process, which is the bug with extra steps.
    """
    import asyncio

    from ravis.registry import refresh_periodically

    registry = _dead_registry(httpx.ConnectError("refused"))
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def record(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) >= 3:
            raise asyncio.CancelledError
        await real_sleep(0)

    asyncio.sleep = record  # type: ignore[assignment]
    try:
        with contextlib.suppress(asyncio.CancelledError):
            await refresh_periodically(registry, interval_seconds=300.0, recovery_seconds=15.0)
    finally:
        asyncio.sleep = real_sleep  # type: ignore[assignment]

    # First sleep is the long one — nothing has failed yet. Every one after a
    # failed pass is the short one.
    assert slept[0] == 300.0
    assert slept[1:] == [15.0, 15.0]
