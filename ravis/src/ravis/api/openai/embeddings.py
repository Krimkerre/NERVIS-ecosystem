"""`POST /v1/embeddings` — one configured local model, no routing yet.

RAVIS.md §4.2 lists this as work deliberately deferred past MVP ("Later:
`POST /v1/embeddings`. Do not delay core routing for secondary APIs.").
Pulled forward here because NERVIS chat's own knowledge lookup needs it, with
the scope that decision actually calls for: embeddings have no pool, no cost
tradeoff between candidates and no fallback chain to choose between today —
there is one configured local model, and this either reaches it or refuses
honestly. Multi-provider routing for embeddings is real future work, not
something to fake with a single-candidate "chain" that only ever has one
link.

Cloud embeddings are deliberately absent — every candidate is local, matching
the launcher's own reasoning for starting Ollama: a knowledge base that will
eventually hold an operator's own notes has no business leaving the machine
to be searched.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ravis.api.openai.agent_backends import agent_backend_refusal

router = APIRouter(prefix="/v1", tags=["openai"])


def _error_body(message: str, error_type: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": error_type, "param": None, "code": error_type}}


def _openai_error(message: str, error_type: str, status: int) -> JSONResponse:
    """An error in OpenAI's shape, because /v1 clients parse it (§4.5)."""
    return JSONResponse(status_code=status, content=_error_body(message, error_type))


@router.post("/embeddings")
async def create_embeddings(request: Request) -> JSONResponse:
    """Refuse the Codex engine by name, then embed with the configured local model.

    `ravis/clarvis-codex` gets the same 400 as on chat completions, before anything else —
    even before "no embedding model is configured", because what is true of that id
    does not depend on how this route is configured (runbook §2.2).
    """
    refusal = agent_backend_refusal(await _parsed_or_none(request))
    if refusal is not None:
        return refusal
    return await _embed(request)


async def _parsed_or_none(request: Request) -> Any:
    """The body parsed as JSON, or `None` when it isn't JSON.

    A body that isn't JSON is `_embed`'s to refuse, in its own words; this only needs
    to know whether a `model` in it names the Codex engine.
    """
    try:
        return await request.json()
    except ValueError:
        return None


async def _embed(request: Request) -> JSONResponse:
    """Forward to the configured local embedding model, or refuse honestly.

    A 503 with a named cause here, not a 500 and not an empty vector — the
    same reasoning §4.5 already applies to `upstream_not_configured` on the
    chat path.
    """
    settings = request.app.state.settings
    if not settings.embedding_base_url:
        return _openai_error(
            "No embedding model is configured. Set RAVIS_EMBEDDING_BASE_URL.",
            "embedding_not_configured",
            503,
        )
    try:
        body = await request.json()
    except ValueError:
        return _openai_error("The request body is not valid JSON.", "invalid_request_error", 400)
    if not isinstance(body, dict) or body.get("input") in (None, "", []):
        return _openai_error("input is required.", "invalid_request_error", 400)

    client: httpx.AsyncClient = request.app.state.upstream_client
    try:
        upstream_response = await client.post(
            f"{settings.embedding_base_url}/v1/embeddings",
            json={"model": settings.embedding_model, "input": body["input"]},
            timeout=30.0,
        )
    except httpx.HTTPError as failure:
        return _openai_error(
            f"The configured embedding runtime is unreachable: {failure}",
            "embedding_runtime_unreachable",
            503,
        )
    if upstream_response.status_code >= 400:
        return _openai_error(
            "The configured embedding runtime refused the request: "
            f"HTTP {upstream_response.status_code}",
            "embedding_runtime_refused",
            502,
        )
    try:
        payload = upstream_response.json()
    except ValueError:
        return _openai_error(
            "The configured embedding runtime answered with a body this route "
            "cannot parse.",
            "embedding_runtime_refused",
            502,
        )
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return _openai_error(
            "The configured embedding runtime answered without an embeddings list.",
            "embedding_runtime_refused",
            502,
        )
    return JSONResponse({"object": "list", "data": data, "model": settings.embedding_model})
