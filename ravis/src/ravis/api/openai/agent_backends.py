"""`ravis/clarvis-codex` on the OpenAI-compatible surface: listed only for a client that asks,
never chat.

Runbook §2.2 gives the Codex engine one catalogue id, and RAVIS.md §4.3, §5.0.1 item 5 and
§15.1.2 say how `/v1` treats it. The exact shapes are the contract fixture
`tests/fixtures/relay-contract/openai-refusal.json`.

**Listing.** `{id, object, owned_by}` and nothing else, after the pools, in both catalogue
builders — and only while Codex is enabled *and* the request carries `X-Clarvis-Engines: codex`.
Clarvis 0.17.0 and later sends that header. An older Clarvis never sees an id it cannot run; it
would otherwise offer it in its chat picker too, since both of its pickers read one list (design
review M13). Deciding reads a kept value and one header, and starts nothing.

**Refusal.** A chat completion or an embeddings request naming `ravis/clarvis-codex`, or anything
under `ravis/clarvis-codex/`, gets one 400 — whether or not Codex is enabled — before anything else
reads the request: before the disabled-provider and upstream checks, routing, route events and
sessions. The `ravis/clarvis-codex/<x>` form matters because it would otherwise parse as a direct
address naming a provider called `clarvis-codex`, and go on to the router.

**Why 400.** Clarvis's tool probe keeps any 4xx that isn't about access as "this model cannot
use tools" — true here — and its agent then stops without retrying. A 404 would contradict the
listing, a 401 or 403 would blame the key, and a 429 or 5xx would make Clarvis retry forever. The
body keeps OpenAI's error shape because `/v1` clients parse it (§4.5).
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from ravis.codex.runtime import CodexRuntime

#: The Codex engine's catalogue id (runbook §2.2).
CODEX_BACKEND_ID = "ravis/clarvis-codex"
#: The header through which Clarvis 0.17.0 and later names the agent engines it can drive.
ENGINES_HEADER = "X-Clarvis-Engines"
#: The refusal's error type and code, spelled as the fixture spells them.
NOT_A_CHAT_MODEL = "agent_backend_not_a_chat_model"
REFUSAL_MESSAGE = (
    "ravis/clarvis-codex is the Codex coding engine, not a chat model. It runs inside Clarvis "
    "0.17.0 or later when chosen as the coding model. Nothing was run."
)


def agent_backend_refusal(parsed: Any) -> JSONResponse | None:
    """The 400 for a request body naming the Codex engine, or `None` for any other body.

    Takes whatever the body parsed to, so a route can call it straight after parsing. A body
    that isn't an object, or whose `model` isn't a string, is left to that route's own checks.
    """
    model = parsed.get("model") if isinstance(parsed, dict) else None
    if not isinstance(model, str):
        return None
    if model != CODEX_BACKEND_ID and not model.startswith(CODEX_BACKEND_ID + "/"):
        return None
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "message": REFUSAL_MESSAGE,
                "type": NOT_A_CHAT_MODEL,
                "param": None,
                "code": NOT_A_CHAT_MODEL,
            }
        },
    )


def listed_agent_backends(request: Request) -> tuple[str, ...]:
    """The agent-backend ids this `/v1/models` answer carries: `ravis/clarvis-codex`, or none.

    Reads the runtime check's kept `enabled` and the request's header, and nothing else: the
    catalogue path never runs a program, signs in or waits (RAVIS.md §4.3).
    """
    runtime: CodexRuntime | None = getattr(request.app.state, "codex", None)
    if runtime is None or not runtime.enabled or not _asks_for_codex(request):
        return ()
    return (CODEX_BACKEND_ID,)


def _asks_for_codex(request: Request) -> bool:
    """Whether `X-Clarvis-Engines` names `codex` — a comma-separated list, like any HTTP list."""
    return any(
        engine.strip() == "codex"
        for value in request.headers.getlist(ENGINES_HEADER)
        for engine in value.split(",")
    )
