"""Building the ASGI application, in an order that is visible rather than implied.

The wiring order below is load-bearing (RAVIS.md §4.4), so it is written as
nesting rather than as registration:

    BodySizeLimiter          ← outermost, runs first, refuses before auth
      └─ FastAPI
           └─ correlation + identity + origin + rate limit middleware
                └─ routes

A framework's middleware registration order is a convention; nesting is a fact.
This is the shape the §4.4 gate asserts — "the body-size test proves refusal
occurs without authentication having run" — and the reason it is asserted is
that the router these limits came from got it backwards while its docstring
claimed otherwise.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

import httpx
from ecosystem_protocol import new_request_id
from ecosystem_protocol import router as ecosystem_router
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from ravis.admission import (
    BodySizeLimiter,
    RateLimiter,
    check_origin,
    client_address,
    cors_headers,
    is_preflight,
)
from ravis.api.management import management_router
from ravis.api.management.decisions import DecisionLog
from ravis.api.openai import chat_router, models_router
from ravis.config import Settings, resolved_capabilities
from ravis.ecosystem import ravis_surface
from ravis.errors import RavisError, to_response
from ravis.evidence import EvidenceStore
from ravis.identity import resolve_identity
from ravis.providers.anthropic import AnthropicAdapter
from ravis.providers.base import TranslatingAdapter
from ravis.providers.generic_openai import GenericOpenAiAdapter
from ravis.providers.lmstudio import LmStudioAdapter
from ravis.providers.ollama import OllamaAdapter
from ravis.registry import ModelRegistry, refresh_periodically
from ravis.reliability import HealthRegistry
from ravis.reliability.attempts import RetryBudget
from ravis.routing import RoutingEngine
from ravis.storage import prepare_database
from ravis.upstream import Upstream, create_client, upstream_from

NextCall = Callable[[Request], Awaitable[Any]]


def create_app(settings: Settings) -> Any:
    """Assemble the application from settings, and return the outermost layer.

    Construction is separated from use (runbook §14.2): everything the request
    path needs is built here and attached, so no handler constructs a database
    connection or a limiter of its own. That is also what makes the whole thing
    testable without a running service.
    """
    api = FastAPI(
        title="RAVIS",
        version="0.0.1",
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan(settings),
    )
    _attach_shared_state(api, settings)
    _register_middleware(api, settings)
    _register_error_handling(api)
    api.include_router(ecosystem_router)
    api.include_router(models_router)
    api.include_router(chat_router)
    api.include_router(management_router)
    # Wrapping last means this ends up outermost, which is the entire point.
    return BodySizeLimiter(api, settings.max_request_bytes)


def _lifespan(settings: Settings) -> Any:
    """Own the things that must be opened once and closed on the way out.

    The pooled upstream client and the catalogue refresher belong here rather
    than at import time: a connection pool created at import outlives nothing
    and is never closed, and a background task started at import runs before
    there is a loop to run it on.
    """

    @asynccontextmanager
    async def lifespan(api: FastAPI) -> Any:
        # Warm the catalogue before serving. A first request must not be the
        # thing that discovers the upstream is unreachable (§5.0.1).
        await api.state.model_registry.refresh()
        # Evidence is read after the catalogue, because it is asked *about* the
        # catalogue: SIRVIS resolves the runtime keys RAVIS holds, so there is
        # nothing to ask until RAVIS knows what it has. Out of band for the same
        # reason the catalogue is — a route decision must not wait on a second
        # service's latency, and §13.4 requires routing to continue without it.
        await _refresh_evidence(api)
        refresher = asyncio.create_task(
            refresh_periodically(api.state.model_registry, settings.models_cache_ttl_seconds)
        )
        evidence_refresher = asyncio.create_task(
            _refresh_evidence_periodically(api, settings.models_cache_ttl_seconds)
        )
        try:
            yield
        finally:
            refresher.cancel()
            evidence_refresher.cancel()
            await api.state.upstream_client.aclose()

    return lifespan


def _attach_shared_state(api: FastAPI, settings: Settings) -> None:
    """Build the things every request needs, once, at startup."""
    api.state.settings = settings
    api.state.database = prepare_database(settings.database_path)
    api.state.upstream = upstream_from(settings)
    # Built here rather than in the lifespan so nothing downstream has to cope
    # with a half-constructed application. The lifespan owns *closing* the
    # client and running the refresher; it does not own creating them, which
    # keeps every attribute on `state` real from the moment the app exists.
    api.state.upstream_client = create_client(settings)
    api.state.model_registry = ModelRegistry(
        upstream=api.state.upstream,
        client=api.state.upstream_client,
        ttl_seconds=settings.models_cache_ttl_seconds,
    )
    # The discovery surface M6's capability filtering will read. It answers
    # questions about the upstream; it does not carry traffic — the transparent
    # path forwards bytes directly (§6), so nothing routes through here.
    # Stateless and I/O-free: capabilities are passed in, so a decision is
    # reproducible and testable without a provider (§9.7's determinism gate).
    api.state.routing_engine = RoutingEngine()
    # Bounded and in memory: route decisions are diagnostic rather than business
    # state, and §17's storage model does not list them. Losing them on restart
    # costs a debugging session; persisting every one costs disk forever.
    api.state.decision_log = DecisionLog()
    # Providers whose upstream does not speak the external protocol, keyed by
    # the name a direct address uses: `ravis/<provider>/<model>`. Empty until
    # M4 registers the first one — and empty is the honest default, because a
    # provider with no adapter is not a provider RAVIS can reach.
    #
    # Registered here rather than discovered, for the reason §3 gives about
    # provider clients: which providers exist is configuration, and a table
    # assembled by probing would make startup depend on the network.
    api.state.translating = _translating_adapters(settings, api.state.upstream_client)
    # SIRVIS's evidence, cached with a staleness policy (§13.3). Absent until
    # a base URL is configured, and RAVIS routes without it — §13.4 makes the
    # source optional and the degradation visible rather than silent.
    api.state.evidence = EvidenceStore(
        base_url=settings.sirvis_base_url,
        role=settings.sirvis_evidence_role,
        max_age_seconds=settings.sirvis_evidence_max_age_seconds,
    )
    api.state.adapter = _transparent_adapter(
        settings, api.state.upstream, api.state.upstream_client
    )
    # Health and circuit breakers (§10). Process-wide and in memory, for the same
    # reason the decision log is: this is operational state about *now*, and a
    # breaker that survived a restart would keep a provider closed off on the
    # strength of failures that happened before the code changed.
    api.state.health = HealthRegistry(
        failure_threshold=settings.breaker_failure_threshold,
        cooldown_seconds=settings.breaker_cooldown_seconds,
    )
    api.state.retry_budget = RetryBudget(
        max_attempts=settings.retry_max_attempts,
        max_total_seconds=settings.retry_max_seconds,
    )
    api.state.rate_limiter = RateLimiter()
    # Identity of this installation. Opaque and locally generated — never derived
    # from hardware, a serial number or a username (runbook §4.1).
    installation = uuid.uuid5(uuid.NAMESPACE_DNS, settings.database_path).hex[:12]
    api.state.service_id = f"ravis-{installation}"
    api.state.machine_id = uuid.uuid5(uuid.NAMESPACE_DNS, "ravis-machine").hex
    # What the shared MEP router publishes on RAVIS's behalf. Attached under the
    # name that package looks for; everything service-specific in it — identity,
    # capability declarations, the readiness check — is supplied from here, and
    # nothing about RAVIS leaks into the protocol package.
    api.state.ecosystem = ravis_surface(
        service_id=api.state.service_id,
        machine_id=api.state.machine_id,
        database=api.state.database,
    )


def _register_middleware(api: FastAPI, settings: Settings) -> None:
    """Correlation, identity, origin and rate limiting, in that order.

    Order matters within this layer too: correlation first so that everything
    after it can be logged against a request ID, identity next because the rate
    limit is keyed to it, and origin before the limit because a rejected origin
    should not consume somebody's allowance.

    A CORS preflight is answered before all of it. It carries no payload and
    does no work, so charging it against a rate limit would halve the effective
    allowance of every browser client — each real request would cost two.
    """

    @api.middleware("http")
    async def apply_admission_control(request: Request, call_next: NextCall) -> Any:
        request.state.request_id = request.headers.get("x-request-id") or new_request_id()
        request.state.trace_id = request.headers.get("traceparent", "")
        headers = {key.lower(): value for key, value in request.headers.items()}
        allowed = cors_headers(headers.get("origin", ""), settings)

        if is_preflight(request.method, headers):
            # 204 with the headers when the origin is allow-listed, 403 with
            # none when it is not. The browser turns the second into a CORS
            # error, which is the correct outcome: an origin nobody listed
            # should not learn what this service would have permitted.
            return Response(status_code=204 if allowed else 403, headers=allowed)

        identity = resolve_identity(headers, settings)
        request.state.identity = identity

        try:
            check_origin(headers, request.method, settings)
            peer = request.client.host if request.client else "unknown"
            address = client_address(headers, peer, settings)
            api.state.rate_limiter.check(
                f"{identity.application_id}:{address}", identity.rate_limit_per_minute
            )
        except RavisError as refusal:
            return to_response(request, refusal)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        # Report the resolved identity back; never read it as an assertion
        # (RAVIS.md §9.6.0).
        response.headers["X-Ecosystem-Actor"] = identity.application_id
        response.headers.update(allowed)
        return response


def _register_error_handling(api: FastAPI) -> None:
    """Translate exceptions into whichever dialect the path owes.

    One handler, one translation point. A handler per exception type would let
    the wire shape drift apart per endpoint, which is the failure `errors.py`
    exists to prevent.
    """

    @api.exception_handler(RavisError)
    async def handle_ravis_error(request: Request, exc: RavisError) -> JSONResponse:
        return to_response(request, exc)


def _translating_adapters(
    settings: Settings, client: httpx.AsyncClient
) -> dict[str, TranslatingAdapter]:
    """The providers whose upstream does not speak the external protocol (§6).

    Keyed by the name a direct address uses — `ravis/<provider>/<model>` — which
    is what `chat.py` looks up to decide which of §6's two paths runs.

    Registered from configuration rather than discovered by probing, for the
    reason §3 gives about provider clients: which providers exist is a
    deployment fact, and assembling this table over the network would make
    startup depend on reaching every provider in it.
    """
    adapters: dict[str, TranslatingAdapter] = {}
    if settings.anthropic_api_key:
        adapters["anthropic"] = AnthropicAdapter(
            upstream=Upstream(
                base_url=settings.anthropic_base_url, api_key=settings.anthropic_api_key
            ),
            client=client,
            max_output_tokens=settings.anthropic_max_output_tokens,
            configured_capabilities=resolved_capabilities(settings),
        )
    return adapters


def _transparent_adapter(
    settings: Settings, upstream: Upstream, client: httpx.AsyncClient
) -> GenericOpenAiAdapter:
    """The adapter that discovers the single transparent upstream (M8).

    All three speak the OpenAI protocol, so this changes what RAVIS can *learn*
    about the upstream, never how it reaches it — the forwarding path in §6 is
    identical whichever comes back.

    An unrecognised `upstream_kind` yields the generic adapter rather than an
    error. A typo should cost the vendor metadata it would have read, which
    shows up as capabilities staying UNKNOWN and pools failing closed, rather
    than costing the ability to serve anything at all.
    """
    kinds: dict[str, type[GenericOpenAiAdapter]] = {
        "lmstudio": LmStudioAdapter,
        "ollama": OllamaAdapter,
        "generic": GenericOpenAiAdapter,
    }
    adapter = kinds.get(settings.upstream_kind.strip().lower(), GenericOpenAiAdapter)
    return adapter(
        upstream=upstream,
        client=client,
        configured_capabilities=resolved_capabilities(settings),
    )


async def _refresh_evidence(api: FastAPI) -> None:
    """Re-read SIRVIS for the models this RAVIS actually has.

    Failures are the store's business, not this function's: `refresh` records a
    degraded source and returns, because §13.4 makes SIRVIS optional and a
    router that would not start without it would have made it mandatory.
    """
    store: EvidenceStore = api.state.evidence
    if not store.is_configured:
        return
    await store.refresh(api.state.upstream_client, api.state.model_registry.model_ids())


async def _refresh_evidence_periodically(api: FastAPI, seconds: float) -> None:
    """Keep the evidence cache warm, tolerating everything.

    A refresh that raised would kill the task and freeze the cache at whatever
    it last held — which is the one state §13.3 forbids, because stale evidence
    served as current is indistinguishable from measured evidence.
    """
    while True:
        await asyncio.sleep(seconds)
        try:
            await _refresh_evidence(api)
        except Exception:  # noqa: BLE001 - a background task must not die
            continue
