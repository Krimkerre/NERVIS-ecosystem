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
from ravis.runtime.lmstudio import probe_residency
from ravis.runtime.residency import ResidencySnapshot
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
        # Whether the last attempt failed to reach the upstream at all, as
        # opposed to reaching it and being refused. Drives the recovery
        # interval in `refresh_periodically`.
        self._unreachable = False
        self._snapshot = ModelSnapshot()
        # Refreshed alongside the catalogue rather than queried per request:
        # §9.8 budgets routing at P50 under 5 ms and requires it to read cached
        # state, so a live probe on the hot path would blow the budget outright.
        self._residency = ResidencySnapshot()

    @property
    def residency(self) -> ResidencySnapshot:
        """Which models the runtime reports as loaded, as of the last refresh."""
        return self._residency

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
        """Whether the snapshot has aged past its TTL.

        **Called from nowhere until now**, which is worth stating: it was
        written as a lazy-refresh hook, nothing ever used it, and its existence
        made the catalogue look self-healing when the only thing refreshing it
        was a 300-second timer. `refresh_periodically` reads it now.
        """
        moment = time.monotonic() if now is None else now
        return moment - self._snapshot.refreshed_at >= self._ttl_seconds

    @property
    def needs_recovery(self) -> bool:
        """Whether the last attempt failed to reach the upstream at all.

        The distinction that matters is **did not answer** versus **answered and
        said no**. An upstream that refused the connection may come back at any
        moment and is worth asking again soon; one that answered 429 or 403 is
        present and telling RAVIS something, and asking it more often is the one
        response guaranteed to make it worse.

        An empty catalogue from a *successful* fetch is not recovery either. LM
        Studio with its server up and nothing installed is legitimately empty,
        and retrying every fifteen seconds would not install anything.
        """
        return self._unreachable

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
        except httpx.HTTPStatusError as failure:
            # It answered, and said no. A 429 or a 403 is the upstream present
            # and telling RAVIS something; retrying sooner is the one response
            # guaranteed to make it worse.
            logger.warning("model refresh refused", extra={"detail": str(failure)})
            self._snapshot.last_error = str(failure)
            self._unreachable = False
            return
        except (httpx.HTTPError, ValueError) as failure:
            # It did not answer, or answered with something unparseable. This is
            # the case that recovers on its own, and the case worth asking about
            # again soon.
            logger.warning("model refresh failed", extra={"detail": str(failure)})
            self._snapshot.last_error = str(failure)
            self._unreachable = True
            return
        self._unreachable = False
        self._snapshot = ModelSnapshot(models=models, refreshed_at=time.monotonic())
        # Best-effort and never fatal: an upstream that is not LM Studio simply
        # 404s here, leaving residency UNKNOWN and routing exactly as it was.
        self._residency = await probe_residency(self._upstream.base_url, self._client)

    async def _fetch(self) -> list[dict[str, Any]]:
        """Read the upstream catalogue and normalise only what must be."""
        key = self._upstream.key()
        headers = {"authorization": f"Bearer {key}"} if key else {}
        response = await self._client.get(self._upstream.api_url("/models"), headers=headers)
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


# How soon to try again when the upstream did not answer at all. Short, because
# the thing being waited for is a local runtime being started by hand — and
# cheap, because a refused connection on loopback costs about a millisecond.
RECOVERY_INTERVAL_SECONDS = 15.0


async def refresh_periodically(
    registry: ModelRegistry,
    interval_seconds: float,
    recovery_seconds: float = RECOVERY_INTERVAL_SECONDS,
) -> None:
    """Background task: keep the snapshot warm for as long as the process runs.

    Sleeps *before* the first refresh, not after. Startup already warms the
    catalogue, so refreshing immediately here would fetch twice in a second —
    which is harmless against a local runtime and exactly the wrong first
    impression to make on a rate-limited provider.

    **The interval shortens while the upstream is unreachable**, and that is a
    fix rather than a refinement. With a flat 300-second TTL, a RAVIS that
    started while LM Studio was down served an empty catalogue for up to five
    minutes after LM Studio came back — every route refused with *"no models are
    available from the configured upstream"* and `considered: []` — and the only
    recovery was a restart. Observed 2026-08-25.

    Only for *unreachable*, never for refused. An upstream answering 429 is
    present and rate limiting, and the shorter interval would be NERVIS's
    self-inflicted outage again in a different service.

    Cancellation is the normal way this ends, at shutdown, so it is allowed to
    propagate rather than being caught and logged as a failure.
    """
    while True:
        await asyncio.sleep(recovery_seconds if registry.needs_recovery else interval_seconds)
        await registry.refresh()
