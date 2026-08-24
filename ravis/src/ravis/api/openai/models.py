"""`GET /v1/models` — a cache read, and nothing more.

The endpoint that must never be slow. See `ravis/registry.py` for why: Clarvis
probes this path with no headers and a two-second timeout, and reads anything
slower or any 401 as "provider offline".
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ravis.registry import ModelRegistry
from ravis.transparent import TransparentUpstream, merged_catalogue

router = APIRouter(prefix="/v1", tags=["openai"])


@router.get("/models")
async def list_models(request: Request) -> dict[str, Any]:
    """Return the cached catalogue.

    No authentication, no upstream call, no awaiting anything. If that ever
    stops being true, Clarvis reports RAVIS offline and the cause is invisible
    from the client side.
    """
    transparents: dict[str, TransparentUpstream] = getattr(
        request.app.state, "transparents", {}
    )
    if transparents:
        return merged_catalogue(transparents)
    registry: ModelRegistry = request.app.state.model_registry
    return registry.as_openai_list()
