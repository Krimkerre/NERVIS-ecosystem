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
import contextlib
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

import httpx
from ecosystem_protocol import EventPublisher, carrying, new_request_id, trace_id_from
from ecosystem_protocol import router as ecosystem_router
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from ravis.admission import (
    BodySizeLimiter,
    RateLimiter,
    check_content_type,
    check_host,
    check_origin,
    client_address,
    cors_headers,
    is_preflight,
)
from ravis.agent.lock_routes import router as project_locks_router
from ravis.agent.routes import owner_router as agent_owner_router
from ravis.agent.routes import router as agent_router
from ravis.api.management import management_router
from ravis.api.management.codex import router as codex_router
from ravis.api.management.codex_calibration import router as calibration_router
from ravis.api.management.credentials import router as credentials_router
from ravis.api.management.decisions import DecisionLog
from ravis.api.management.skills import router as skills_router
from ravis.api.openai import chat_router, embeddings_router, models_router
from ravis.codex.service import CodexService
from ravis.config import Settings, resolved_capabilities
from ravis.cost import (
    PriceBook,
    PriceConfigurationError,
    UsageLedger,
    budget_from,
    load_prices,
)
from ravis.credentials import REFRESH_SECONDS, CredentialStore, credential_for
from ravis.ecosystem import ravis_surface
from ravis.errors import RavisError, to_response
from ravis.evidence import EvidenceStore
from ravis.identity import resolve_identity
from ravis.model_filter import ModelFilters
from ravis.observations import Observations
from ravis.policy import load_policies
from ravis.pool_membership import PoolMembership
from ravis.provider_state import ProviderState
from ravis.providers.anthropic import AnthropicAdapter
from ravis.providers.base import TranslatingAdapter
from ravis.providers.google import GoogleAdapter
from ravis.providers_map import shared_provider_names
from ravis.registry import ModelRegistry, refresh_periodically
from ravis.reliability import HealthRegistry
from ravis.reliability.attempts import RetryBudget
from ravis.routing import RoutingEngine
from ravis.runtime.resources import MemoryReading, read_memory
from ravis.sessions import SESSION_HEADER, SessionStore
from ravis.storage import prepare_database
from ravis.transparent import adapter_for, build_transparents
from ravis.trials import CombinedEvidence, TrialStore, run_trials_periodically
from ravis.upstream import Upstream, create_client, upstream_from
from ravis.upstreams import DEFAULT_NAME, UpstreamSpec

NextCall = Callable[[Request], Awaitable[Any]]


logger = logging.getLogger("ravis")


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
    api.include_router(embeddings_router)
    api.include_router(management_router)
    api.include_router(credentials_router)
    api.include_router(codex_router)
    # Skills for every engine (RAVIS 0.27.0): the Skills page's list and switches, and the reads
    # of the models that aren't Codex (`api/management/skills.py`).
    api.include_router(skills_router)
    # Codex tasks (M29's third increment): the owner's stop-only route on its own router, and
    # every other agent-session route behind the Clarvis client check (`agent/routes.py`).
    api.include_router(agent_owner_router)
    api.include_router(agent_router)
    # The project lock for both engines (M29's fourth increment), behind the same client check.
    api.include_router(project_locks_router)
    if settings.codex_calibration:
        # Dev-only, with the owner present: without the setting these paths don't exist at all.
        api.include_router(calibration_router)
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
        await _refresh_catalogues(api)
        # Evidence is read after the catalogue, because it is asked *about* the
        # catalogue: SIRVIS resolves the runtime keys RAVIS holds, so there is
        # nothing to ask until RAVIS knows what it has. Out of band for the same
        # reason the catalogue is — a route decision must not wait on a second
        # service's latency, and §13.4 requires routing to continue without it.
        await _refresh_evidence(api)
        refresher = asyncio.create_task(
            _refresh_catalogues_periodically(api, settings.models_cache_ttl_seconds)
        )
        evidence_refresher = asyncio.create_task(
            _refresh_evidence_periodically(api, settings.models_cache_ttl_seconds)
        )
        recorder = asyncio.create_task(_flush_observations_periodically(api))
        trials = asyncio.create_task(run_trials_periodically(api, settings))
        # Keychain lookups renewed in a worker thread, so a request that presents
        # a key finds its credential already looked up.
        credential_refresher = asyncio.create_task(_refresh_credentials_periodically(api))
        memory_sampler = asyncio.create_task(_sample_memory_periodically(api))
        # Runbook Stage 7. Borrows the upstream client rather than opening a
        # pool of its own, and is cancelled like the others — a publisher that
        # outlived the app would hold the process open on a queue nobody reads.
        publisher = asyncio.create_task(
            api.state.events.run(api.state.upstream_client)
        )
        # The optional Codex engine (runbook §2.2, M29): its runtime check, then its one
        # supervised process, sign-in and allowance (`codex/service.py`). Serving never waits
        # for it, and `/v1/models` reads what the check found.
        codex_start = asyncio.create_task(api.state.codex_service.start())
        try:
            yield
        finally:
            codex_start.cancel()
            # First, and bounded: Codex's process ends inside the launcher's six-second wait.
            await api.state.codex_service.stop()
            refresher.cancel()
            evidence_refresher.cancel()
            recorder.cancel()
            trials.cancel()
            credential_refresher.cancel()
            memory_sampler.cancel()
            publisher.cancel()
            # Bounded, on the way out. The last thing RAVIS publishes about a
            # request is the event that closes its span, and a fire-and-forget
            # POST issued as the loop is torn down dies with it — losing exactly
            # the event that turns a bar into a bar rather than a point.
            await api.state.events.drain(api.state.upstream_client)
            # Written on the way out as well as on the timer, so a clean restart
            # keeps the samples taken since the last flush rather than the ones
            # that happened to fall on a tick.
            api.state.observations.flush()
            await api.state.upstream_client.aclose()

    return lifespan


# How often observed timings are written to disk.
#
# Not per request: a gateway that fsyncs on the hot path has traded away the
# latency it is measuring for the record of it. Not per hour either — the point
# of persisting is surviving a restart, and a window that wide loses most of
# what it was collecting whenever RAVIS is restarted, which during development
# is constantly.
OBSERVATION_FLUSH_SECONDS = 30.0


async def _flush_observations_periodically(api: FastAPI) -> None:
    """Write the observation windows down, on a timer.

    Cancellation at shutdown is the normal end, and the `finally` in the
    lifespan does the last write — so this deliberately does not catch it.
    """
    while True:
        await asyncio.sleep(OBSERVATION_FLUSH_SECONDS)
        api.state.observations.prune(await _offered_models(api))
        api.state.observations.flush()


# How often free memory is read for routing. The reading answers "is memory
# tight", which changes over seconds rather than per request, and on macOS
# taking it is a `vm_stat` process — 6–8 ms that every routed request used to
# spend inside the event loop (RAVIS.md §9.8).
MEMORY_SAMPLE_SECONDS = 5.0


async def _sample_memory_periodically(api: FastAPI) -> None:
    """Read free memory in a worker thread, on a timer, for routing to read.

    §9.8 says routing reads cached snapshots and never looks anything up live;
    free memory was the one reading it took live, on every routed request. The
    first sample is taken at startup. Cancellation at shutdown is the normal end.
    """
    while True:
        api.state.memory = await asyncio.to_thread(read_memory)
        await asyncio.sleep(MEMORY_SAMPLE_SECONDS)


async def _refresh_credentials_periodically(api: FastAPI) -> None:
    """Look every stored credential up again in a worker thread, on a timer.

    Identifying a caller resolves each `client.` and `admin.` credential, and a
    Keychain lookup is a `security` process: 46 ms per request carrying a key,
    spent in the event loop so that every other request waited too, until the
    store began keeping what it found (RAVIS.md §9.8). The store reuses a lookup
    for `CACHE_SECONDS`; this renews it before then, so a request only reads.
    The first pass runs at startup, which makes the first keyed request fast
    as well. Cancellation at shutdown is the normal end, so it is not caught.
    """
    while True:
        await asyncio.to_thread(api.state.credentials.refresh)
        await asyncio.sleep(REFRESH_SECONDS)


async def _offered_models(api: FastAPI) -> set[str]:
    """Every model id any upstream currently lists, translated ones included.

    Transparent catalogues are read from the registries rather than fetched, so
    they cost nothing and cannot fail. A translated provider is asked, but its
    discovery is cached behind a TTL so this is a dictionary lookup in the
    ordinary case.

    **Both kinds or neither.** Walking only the transparent upstreams would make
    every Anthropic model look withdrawn the instant the set became non-empty,
    and prune the measurements for a provider that is working perfectly. An
    upstream whose refresh failed contributes nothing, and `prune` treats an
    empty set as no evidence at all — which is the difference between
    "OpenRouter withdrew this model" and "OpenRouter did not answer just now".
    """
    offered: set[str] = set()
    for built in getattr(api.state, "transparents", {}).values():
        offered.update(built.registry.model_ids())
    for adapter in getattr(api.state, "translating", {}).values():
        try:
            offered.update(await adapter.models())
        except Exception:  # noqa: BLE001 — a failed listing is not a withdrawal
            continue
    return offered


def _attach_shared_state(api: FastAPI, settings: Settings) -> None:
    """Build the things every request needs, once, at startup."""
    api.state.settings = settings
    api.state.database = prepare_database(settings.database_path)
    # Built here rather than in the lifespan so nothing downstream has to cope
    # with a half-constructed application. The lifespan owns *closing* the
    # client and running the refresher; it does not own creating them, which
    # keeps every attribute on `state` real from the moment the app exists.
    api.state.upstream_client = create_client(settings)
    # Provider credentials (M10). Built here so every request sees the same
    # store, and so the file is resolved once rather than per lookup.
    api.state.credentials = CredentialStore(
        allow_environment=settings.credentials_allow_environment,
    )
    # §9.6's application policy. Loaded once at startup and deliberately *not*
    # re-read per request, unlike the provider toggles below: those fail open by
    # design, and this must not — a policy file that becomes unreadable while
    # RAVIS is running must not quietly downgrade every request already covered
    # by it. A malformed file raises here and stops startup, which is the
    # failure an operator can see and fix.
    api.state.policies = load_policies()

    # §12.1's sessions. Persisted rather than in memory, unlike the decision
    # log: the session gate names restart, and a session that forgot its model
    # on restart would swap the model under a conversation still in progress —
    # the churn stickiness exists to prevent.
    api.state.sessions = SessionStore(api.state.database)

    # §14's cost engine. The price book is filled from provider catalogues on
    # the refresh that already runs -- see `_restate_prices`, which is the
    # wiring that makes that true; until 9 September 2026 this comment
    # described an intention rather than the code, and every figure on the
    # spend screen was as old as the process. The ledger is persisted, since 12
    # September 2026: in memory, every restart emptied the spend screen and the
    # monthly budget with it. It is still nobody's accounting system, and §14 is
    # explicit that RAVIS never presents an estimate as an invoice.
    api.state.prices = PriceBook()
    # Operator-stated prices, loaded before any catalogue fills the book so the
    # `state`/`record` precedence is never a question of ordering. Three of the
    # four providers publish no pricing at all, so without this a call to
    # OpenAI, Anthropic or Google is permanently UNKNOWN — and a budget reads
    # unknown as unspent.
    for model, price in load_prices().items():
        api.state.prices.state(model, price)
    api.state.usage_ledger = UsageLedger(database=api.state.database)
    # §14's budget, when one is configured. `None` means unlimited, which is
    # not the same as a limit of zero and must not route as one.
    api.state.budget = budget_from(settings)

    # Which providers an operator has switched off (M10). Read on every routing
    # pass rather than cached, so a toggle takes effect on the next request
    # instead of the next restart — the file is small and local.
    api.state.provider_state = ProviderState()
    # Which of each provider's models are offered (M10). Read per request for
    # the same reason as the enable toggle: a narrowing that only took effect
    # after a restart is a narrowing nobody trusts.
    api.state.model_filters = ModelFilters.default()
    # Which models an operator chose for each pool (empty = whatever qualifies).
    # Read per request like the two above, so a change in a picker takes effect
    # on the next request rather than the next restart.
    api.state.pool_membership = PoolMembership.default()
    # What RAVIS has actually observed of each model, loaded from the last run.
    # The timings were always collected; `HealthRegistry` just held them in
    # memory, so every restart threw away the evidence and left the coverage
    # permanently too thin to route on.
    api.state.observations = Observations.default()
    # The latest free-memory reading, which routing reads rather than takes
    # (§9.8). Unknown until the first sample, which routes as RAVIS did before
    # it considered memory at all.
    api.state.memory = MemoryReading()
    # Every declared transparent upstream, in declaration order (M8). One
    # entry for a deployment using the singular settings, which is what every
    # deployment written before this is.
    api.state.transparents = build_transparents(
        settings, api.state.upstream_client, api.state.credentials
    )
    # The first declared upstream, still reachable under the names everything
    # written before plurality uses. Not a shim to be removed later: a single
    # upstream is the common deployment, and "the one to use when nothing more
    # specific applies" stays meaningful however many there are.
    primary = next(iter(api.state.transparents.values()), None)
    api.state.upstream = primary.upstream if primary else upstream_from(settings)
    api.state.model_registry = (
        primary.registry
        if primary
        else ModelRegistry(
            upstream=api.state.upstream,
            client=api.state.upstream_client,
            ttl_seconds=settings.models_cache_ttl_seconds,
        )
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
    api.state.translating = _translating_adapters(
        settings, api.state.upstream_client, api.state.credentials
    )
    # One name, two providers — said out loud rather than resolved in silence.
    # Logged at startup because this is a property of the configuration, so the
    # moment it becomes true is the moment somebody can still act on it.
    for name in shared_provider_names(api.state.transparents):
        logger.warning(
            "provider name %r is claimed by both a transparent upstream and a "
            "translated provider; `ravis/%s/<model>` reaches the translated one "
            "while it has a credential, and the upstream still contributes its "
            "catalogue to /v1/models",
            name,
            name,
        )

    # SIRVIS's evidence, cached with a staleness policy (§13.3). Absent until
    # a base URL is configured, and RAVIS routes without it — §13.4 makes the
    # source optional and the degradation visible rather than silent.
    api.state.evidence = EvidenceStore(
        base_url=settings.sirvis_base_url,
        role=settings.sirvis_evidence_role,
        max_age_seconds=settings.sirvis_evidence_max_age_seconds,
    )
    # RAVIS's own trials of hosted models' tool support, and the one store the candidate
    # builders read: SIRVIS's measurements with those trials beside them. See `trials.py`.
    api.state.trials = TrialStore(
        api.state.database, max_age_seconds=settings.capability_trial_max_age_days * 86400.0
    )
    api.state.capability_evidence = CombinedEvidence(api.state.evidence, api.state.trials)
    api.state.adapter = (
        primary.adapter
        if primary
        else adapter_for(
            UpstreamSpec(name=DEFAULT_NAME, base_url="", kind=settings.upstream_kind),
            api.state.upstream,
            api.state.upstream_client,
            settings,
        )
    )
    # Health and circuit breakers (§10). Process-wide and in memory, for the same
    # reason the decision log is: this is operational state about *now*, and a
    # breaker that survived a restart would keep a provider closed off on the
    # strength of failures that happened before the code changed.
    api.state.health = HealthRegistry(
        failure_threshold=settings.breaker_failure_threshold,
        cooldown_seconds=settings.breaker_cooldown_seconds,
        # The (model, tools) suppression a tool refusal arms. In memory for the
        # same reason as the breakers above; see `HealthRegistry.suppress`.
        suppression_seconds=settings.tool_refusal_suppression_seconds,
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
    # **Per installation, not per product.** This was
    # `uuid5(NAMESPACE_DNS, "ravis-machine")` — a constant, so every RAVIS in
    # existence reported the same machine. §4.1 asks for an id "stable per local
    # installation"; a constant satisfies "stable" and nothing else, and it
    # passed the test beside it because that test only asks whether the value is
    # hardware-derived. Harmless while it was published to a registry that also
    # knew the address it came from, and not harmless from M18b on: it goes into
    # `source.machine_id` on every event, where two machines' events would claim
    # to be one machine's.
    api.state.machine_id = uuid.uuid5(
        uuid.NAMESPACE_DNS, f"ravis-machine:{settings.database_path}"
    ).hex
    # What the shared MEP router publishes on RAVIS's behalf. Attached under the
    # name that package looks for; everything service-specific in it — identity,
    # capability declarations, the readiness check — is supplied from here, and
    # nothing about RAVIS leaks into the protocol package.
    # M18b. Disabled unless an operator points RAVIS at a hub; see
    # `nervis_base_url`. Constructed here so `app.state.events.emit(...)` is
    # always safe to call, whether or not anything is listening.
    api.state.events = EventPublisher(
        service_type="ravis",
        service_id=api.state.service_id,
        machine_id=api.state.machine_id,
        base_url=settings.nervis_base_url,
    )
    # Codex, the optional coding engine (runbook §2.2, M29). Building it runs nothing: the
    # lifespan starts it, and `/v1/models` reads the runtime check it keeps (`api.state.codex`).
    # After the publisher, because it publishes Codex's state changes and its audit; given the
    # database, because Clarvis's Codex tasks are kept there (migration 8).
    api.state.codex_service = CodexService(
        settings, emit=api.state.events.emit, database=api.state.database
    )
    api.state.codex = api.state.codex_service.runtime
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
        # The **trace id**, not the whole header. §11.2 joins events from
        # different services on this value, and `traceparent`'s third field is a
        # per-span parent id — so two spans in one trace carry two different
        # headers and matching on the string finds neither. Parsed in the shared
        # package, because all three services had the same line and all three
        # had it wrong.
        request.state.trace_id = trace_id_from(
            request.headers.get("traceparent", "")
        )
        headers = {key.lower(): value for key, value in request.headers.items()}
        allowed = cors_headers(headers.get("origin", ""), settings)

        if is_preflight(request.method, headers):
            # 204 with the headers when the origin is allow-listed, 403 with
            # none when it is not. The browser turns the second into a CORS
            # error, which is the correct outcome: an origin nobody listed
            # should not learn what this service would have permitted.
            return Response(status_code=204 if allowed else 403, headers=allowed)

        # The store is passed so a `client.<application>` credential resolves to
        # that application rather than to the one shared `configured` identity.
        # Read per request, like every other credential lookup here, so an
        # application enrolled on the Credentials screen works on the next
        # request instead of the next restart.
        identity = resolve_identity(headers, settings, api.state.credentials)
        request.state.identity = identity

        try:
            check_host(headers, settings)
            check_origin(headers, request.method, settings)
            check_content_type(headers, request.method)
            peer = request.client.host if request.client else "unknown"
            address = client_address(headers, peer, settings)
            api.state.rate_limiter.check(
                f"{identity.application_id}:{address}", identity.rate_limit_per_minute
            )
        except RavisError as refusal:
            return to_response(request, refusal)

        # The ids the response will carry, put where anything logging underneath
        # this request can reach them without being handed them. RAVIS is the
        # one service that also knows *who* is asking by this point, and §4.3
        # names `application_id` alongside the other two for exactly that.
        with carrying(request_id=request.state.request_id,
                      trace_id=request.state.trace_id,
                      application_id=identity.application_id,
                      # Read here rather than only where a session is resolved,
                      # because every event emitted under this request belongs
                      # to the same conversation whether or not the route
                      # bothered to look the session up.
                      session_id=request.headers.get(SESSION_HEADER, "")):
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

    @api.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """The refusals this service did not raise itself (§16 item 10).

        One translation point was one too few: the service's own error type was
        covered and everything the framework raises was not, so a 404 for a path
        that does not exist arrived as Starlette's `{"detail": …}` rather than
        §4.5's envelope. A client parsing the envelope found no `error` object at
        all on exactly the responses it most needs to read.
        """
        detail = exc.detail
        structured: dict[str, Any] = detail if isinstance(detail, dict) else {}
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": str(structured.get("code") or f"HTTP_{exc.status_code}"),
                    "message": str(structured.get("message") or detail),
                    "retryable": exc.status_code in (429, 503),
                    "details": {
                        key: value for key, value in structured.items()
                        if key not in ("code", "message")
                    },
                    "request_id": getattr(request.state, "request_id", ""),
                    "trace_id": getattr(request.state, "trace_id", ""),
                }
            },
            headers=getattr(exc, "headers", None),
        )



def _translating_adapters(
    settings: Settings, client: httpx.AsyncClient, credentials: CredentialStore
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
    # From the store, falling back to the settings field that used to be the
    # only source. This read `settings.anthropic_api_key` alone, so a key typed
    # into the Credentials screen was written to a 0600 file, reported as
    # configured, and never reached a request — the screen was not wrong about
    # having stored it, only about what storing it would do.
    # **Registered whether or not a key exists.** This was gated on the key, so
    # a provider RAVIS knows perfectly well how to reach reported itself
    # `unroutable` until one was saved *and* the service restarted — which reads
    # as "do not bother" at exactly the moment somebody is about to fix it.
    # Whether a request is actually routed here is decided per request, by
    # `has_credential`, so an unconfigured provider still serves nothing.
    adapters["anthropic"] = AnthropicAdapter(
        upstream=Upstream(
            base_url=settings.anthropic_base_url,
            declared_key=settings.anthropic_api_key,
            # Re-read per request, like every transparent upstream, so a key
            # saved on the Credentials screen takes effect on the next request
            # rather than the next restart.
            credential=lambda: credential_for(
                credentials, "anthropic", settings.anthropic_api_key
            ),
        ),
        client=client,
        max_output_tokens=settings.anthropic_max_output_tokens,
        configured_capabilities=resolved_capabilities(settings),
    )
    # Gemini, for the reasons argued in `providers/google.py`: its
    # OpenAI-compatible endpoint drops the tool-call index and reports `stop`
    # for a streamed tool call, both of which Clarvis's agent role reads.
    adapters["google"] = GoogleAdapter(
        upstream=Upstream(
            base_url=settings.google_base_url,
            declared_key=settings.google_api_key,
            credential=lambda: credential_for(credentials, "google", settings.google_api_key),
        ),
        client=client,
        configured_capabilities=resolved_capabilities(settings),
    )
    return adapters


async def _refresh_catalogues(api: FastAPI) -> None:
    """Warm every upstream's catalogue before serving.

    Concurrently, because they are independent and a deployment with a slow
    remote upstream should not have its local one wait behind it. Failures are
    the registry's business — it keeps its previous snapshot and stays
    serveable, which is §5.0.1's requirement that a first request must not be
    the thing that discovers an upstream is unreachable.
    """
    registries = _registries(api)
    if not registries:
        return
    await asyncio.gather(*(registry.refresh() for registry in registries))
    await _restate_prices(api)


async def _restate_prices(api: FastAPI) -> None:
    """Re-read what a call costs, on the refresh that already runs.

    Two sources, in the order §14's precedence needs them.

    The operator's `prices.json` is re-read from disk, so **editing a rate now
    takes effect on the next refresh rather than at the next restart**. Vendors
    change their pricing without asking, and needing the gateway bounced to
    record that made the file feel like a build-time constant instead of
    configuration.

    Then every catalogue that publishes per-token figures re-states its own,
    through `record`, which refuses to apply over anything the operator wrote
    down. Only OpenRouter publishes any; the rest are silently skipped, which is
    not a failure but the state five of the six providers are permanently in.

    Tolerant of everything, like the retention sweep it runs beside. A typo in
    `prices.json` must not kill the task that also refreshes catalogues -- it is
    reported by `ravis doctor` and refused outright by `serve`, which are the
    two places an operator actually looks. Nothing here is allowed to make the
    model lists stop updating.
    """
    book: PriceBook | None = getattr(api.state, "prices", None)
    if book is None:
        return
    with contextlib.suppress(PriceConfigurationError, OSError):
        book.restate(load_prices())
    for built in getattr(api.state, "transparents", {}).values():
        published = getattr(built.adapter, "catalogue_prices", None)
        if published is None:
            continue
        with contextlib.suppress(Exception):
            for model, price in (await published()).items():
                book.record(model, price)


async def _refresh_catalogues_periodically(api: FastAPI, interval: float) -> None:
    """The background half of the same thing, plus §12.1's retention sweep."""
    await asyncio.gather(
        *(refresh_periodically(registry, interval) for registry in _registries(api)),
        _expire_sessions_periodically(api, interval),
    )


async def _expire_sessions_periodically(api: FastAPI, interval: float) -> None:
    """Delete sessions past their retention window, on the catalogue timer.

    On an existing timer rather than its own: it is one indexed DELETE, and a
    second scheduler for a millisecond of work is machinery nobody has to
    maintain if it does not exist.

    Tolerates everything. A sweep that raised would kill the task it shares
    with catalogue refreshes, so a failure to prune old rows would stop the
    model lists updating — a much worse outcome than a late deletion.
    """
    sessions: Any = getattr(api.state, "sessions", None)
    if sessions is None:
        return
    while True:
        with contextlib.suppress(Exception):
            sessions.enforce_retention()
        await asyncio.sleep(interval)


def _registries(api: FastAPI) -> list[ModelRegistry]:
    """Every catalogue that needs refreshing.

    Falls back to the lone registry when nothing is declared, so an
    unconfigured RAVIS still refreshes the empty catalogue it serves rather
    than skipping the path entirely and leaving it untested.
    """
    transparents: dict[str, Any] = getattr(api.state, "transparents", {})
    if transparents:
        return [built.registry for built in transparents.values()]
    return [api.state.model_registry]


async def _refresh_evidence(api: FastAPI) -> None:
    """Re-read SIRVIS for the models this RAVIS actually has.

    Failures are the store's business, not this function's: `refresh` records a
    degraded source and returns, because §13.4 makes SIRVIS optional and a
    router that would not start without it would have made it mandatory.
    """
    store: EvidenceStore = api.state.evidence
    if not store.is_configured:
        return
    # **Every declared upstream's models, not the first one's.**
    # `api.state.model_registry` is `primary.registry` -- the first upstream
    # declared -- so with the plural upstreams M8 shipped, SIRVIS was never
    # asked about anything served by the second, third or fourth. Their builds
    # then routed on advertised capability alone while RAVIS held measured
    # evidence for their neighbours, and the Evidence screen reported the source
    # healthy: the gap looked like SIRVIS having nothing to say.
    #
    # `_registries` already knows the answer; it is what the refresh loop next
    # to this uses. Sorted so the request is stable between calls, which makes
    # a diff of two refreshes mean something.
    wanted = {model for registry in _registries(api) for model in registry.model_ids()}
    await store.refresh(api.state.upstream_client, sorted(wanted))


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
