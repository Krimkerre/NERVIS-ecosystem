"""The cached model catalogue behind `GET /v1/models`.

This endpoint looks trivial and is not. RAVIS.md §4.3 and §5.0.1 constrain it
harder than anything else at M1, for a reason worth stating plainly:

Clarvis's OpenAI-compatible provider probes availability by fetching the model
list with **no headers and a two-second timeout**. A 401 makes it report the
provider *offline* rather than unauthorized — an actively misleading failure —
and so does a slow answer. So this endpoint may never authenticate, never block
on an upstream, and never take two seconds.

The resolution is that reads are served from an in-memory snapshot and refresh
happens on a background task. A read is a dictionary lookup; a refresh failure
leaves the previous snapshot in place rather than emptying the catalogue.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ravis.core.pools import DEFAULT_POOLS
from ravis.upstream import Upstream

logger = logging.getLogger(__name__)


@dataclass
class ModelSnapshot:
    """A catalogue as of one moment, plus how it came to be.

    `refreshed_at` of zero means "never successfully refreshed", which is
    different from "refreshed and found nothing" — a distinction a diagnostic
    needs and a bare empty list destroys (runbook §14.4).
    """

    models: list[dict[str, Any]] = field(default_factory=list)
    refreshed_at: float = 0.0
    last_error: str = ""

    @property
    def has_been_refreshed(self) -> bool:
        return self.refreshed_at > 0.0


class ModelRegistry:
    """Serves the catalogue from memory, refreshes it out of band.

    Deliberately not a cache with a lock around a fetch: a lock means a slow
    upstream can make a read slow, which is the exact failure §5.0.1 forbids.
    Reads never wait for anything.
    """

    def __init__(self, upstream: Upstream, client: httpx.AsyncClient, ttl_seconds: float) -> None:
        self._upstream = upstream
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._snapshot = ModelSnapshot()

    def use_client(self, client: httpx.AsyncClient) -> None:
        """Swap the HTTP client this registry refreshes through.

        Exists so a test can point the registry at an in-process fake upstream
        without reaching into a private attribute. A command, not a query: it
        changes state and returns nothing (runbook §14.2).
        """
        self._client = client

    @property
    def snapshot(self) -> ModelSnapshot:
        """The current catalogue. Never blocks, never raises."""
        return self._snapshot

    def model_ids(self) -> list[str]:
        """The upstream model IDs, for routing to choose among."""
        return [entry["id"] for entry in self._snapshot.models if entry.get("id")]

    def is_due_for_refresh(self, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        return moment - self._snapshot.refreshed_at >= self._ttl_seconds

    async def refresh(self) -> None:
        """Fetch the catalogue and replace the snapshot, or leave it alone.

        A failed refresh is recorded and otherwise ignored. Serving a slightly
        stale list is better than serving an empty one: an empty catalogue reads
        to a client as "this provider has no models", which is a stronger and
        more wrong claim than "here is what was there a minute ago".
        """
        if not self._upstream.is_configured:
            self._snapshot = ModelSnapshot(models=[], refreshed_at=time.monotonic())
            return
        try:
            models = await self._fetch()
        except (httpx.HTTPError, ValueError) as failure:
            logger.warning("model refresh failed", extra={"detail": str(failure)})
            self._snapshot.last_error = str(failure)
            return
        self._snapshot = ModelSnapshot(models=models, refreshed_at=time.monotonic())

    async def _fetch(self) -> list[dict[str, Any]]:
        """Read the upstream catalogue and normalise only what must be."""
        headers = (
            {"authorization": f"Bearer {self._upstream.api_key}"} if self._upstream.api_key else {}
        )
        response = await self._client.get(self._upstream.url_for("/v1/models"), headers=headers)
        response.raise_for_status()
        payload = response.json()
        entries = payload.get("data", []) if isinstance(payload, dict) else []
        return [entry for entry in entries if isinstance(entry, dict)]

    def as_openai_list(self) -> dict[str, Any]:
        """The `GET /v1/models` body, in OpenAI's list shape.

        Pools are listed first, then the upstream's own models. Listing pools at
        all is what makes them reachable: Clarvis picks a model from this
        response, so `ravis/clarvis-agent` has to appear here or it cannot be
        selected in the UI (§5, §5.0.1).

        **`created` is omitted from every entry.** Clarvis sorts by timestamp
        only when *all* entries carry it and falls back to alphabetical
        otherwise — and pools have no meaningful creation time, so including it
        on upstream models alone would produce exactly the mixed list that
        scrambles the intended order (§5.0.1).
        """
        pools = [
            {"id": pool.pool_id, "object": "model", "owned_by": "ravis"}
            for pool in DEFAULT_POOLS
        ]
        upstream = [
            {
                "id": entry.get("id", ""),
                "object": "model",
                "owned_by": entry.get("owned_by", "organization_owner"),
            }
            for entry in self._snapshot.models
        ]
        return {"object": "list", "data": pools + upstream}


async def refresh_periodically(registry: ModelRegistry, interval_seconds: float) -> None:
    """Background task: keep the snapshot warm for as long as the process runs.

    Sleeps *before* the first refresh, not after. Startup already warms the
    catalogue, so refreshing immediately here would fetch twice in a second —
    which is harmless against a local runtime and exactly the wrong first
    impression to make on a rate-limited provider.

    Cancellation is the normal way this ends, at shutdown, so it is allowed to
    propagate rather than being caught and logged as a failure.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        await registry.refresh()
