"""`POST /v1/chat/completions` — transparent forwarding (RAVIS.md §6, Path A).

The whole design in one sentence: **this moves bytes and does not interpret
them.** The request body reaches the upstream exactly as the client sent it, and
the response reaches the client exactly as the upstream sent it.

That is not laziness, it is the point. §6 says not to normalise an
already-compatible stream, because every transformation is somewhere a detail
can be lost, and the details at risk are the ones Clarvis depends on: tool-call
indexes and IDs, fragmented JSON arguments, reasoning fields, finish reasons,
usage chunks and `[DONE]`. A proxy that never parses a tool-call fragment cannot
reassemble one wrongly.

The body *is* parsed once — but only to inspect it for the §4.4 image cap, to
learn whether the client asked for a stream, and to substitute the resolved
model. What gets forwarded is the original bytes wherever nothing changed.

**M12 added a second attempt, and changed nothing about the first.** A request
may now be tried against the router's fallback candidates when the primary
fails (§10), and the rule that makes that safe is short enough to state here:
*a fallback is only ever a decision about which request to make next.* Once a
byte of the response has reached the client the chain is over — there is no
code path back into it — so a stream cannot be corrupted by a retry, and a
client disconnect cannot be mistaken for one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from contextlib import aclosing
from typing import Any, AsyncGenerator, Callable, Sequence

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ravis.api.management.decisions import RecordedDecision
from ravis.api.openai.serialize import DONE, completion, frame_for, opening_frame
from ravis.content import check_image_count
from ravis.core.capabilities import Capability, ModelCapabilities
from ravis.core.pools import POOL_PREFIX, POOLS_BY_ID, direct_provider, is_pool_id
from ravis.core.requests import NormalizedRequest, normalize
from ravis.core.responses import NormalizedStreamEvent, Usage
from ravis.cost import (
    BudgetBand,
    PriceBook,
    UsageLedger,
    UsageRecord,
    band_for,
    estimate,
    price_from_book,
)
from ravis.evidence import EvidenceStore  # noqa: F401 - state typing
from ravis.evidence.sirvis import candidates_with_evidence
from ravis.policy import (
    ApplicationPolicies,
    PrivacyLevel,
    RoutingPolicy,
    direct_owners,
    effective_policy,
    policy_refusals,
    resold_models,
)
from ravis.providers.base import ProviderAdapter, TranslatingAdapter, TranslationError
from ravis.registry import ModelRegistry
from ravis.reliability import (
    AttemptChain,
    FailureClass,
    HealthRegistry,
    classify_error_body,
    classify_exception,
    classify_response,
    error_body,
)
from ravis.reliability.failures import HealthScope
from ravis.routing.engine import DEFAULT_EXPLORATION_RATE, Exploration, RoutingEngine
from ravis.routing.explain import RouteDecision
from ravis.sessions import SESSION_HEADER, SessionStore
from ravis.transparent import (
    TransparentUpstream,
    merged_candidates,
    merged_residency,
    remote_models,
    resolve,
    translated_candidates,
)
from ravis.upstream import Upstream, forwardable_headers

logger = logging.getLogger(__name__)

# §6's two names for the two paths, as the diagnostics must report them.
TRANSPARENT = "TRANSPARENT_OPENAI"
TRANSLATED = "TRANSLATED_NATIVE"

# The SSE fields that can precede a payload. Anything starting with one of these
# — or with `:`, a comment — is framing rather than content.
_SSE_FIELDS = (b"event:", b"id:", b"retry:")

router = APIRouter(prefix="/v1", tags=["openai"])

# Response headers that describe *our* hop rather than the payload, and would be
# wrong if copied: httpx has already decoded the body, so a forwarded
# content-encoding would tell the client to decode it a second time.
SKIPPED_RESPONSE_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})

# The health-tracking name of a deployment with exactly one transparent
# upstream. A stable label rather than the URL, deliberately: health snapshots
# reach diagnostics and route explanations, and §9.7 keeps internal URLs out of
# both.
#
# **With plural upstreams this is resolved per model instead** — see
# `_provider_of`. Leaving it as one shared label after M8 was a routing bug, not
# a naming one: a provider circuit takes out every model behind that provider,
# so one failing runtime excluded the entire catalogue.
UPSTREAM_PROVIDER = "upstream"


async def _direct_providers(request: Request) -> frozenset[str]:
    """Vendors this machine can buy from at the source, right now.

    **The set the reseller rule is allowed to prefer**, and every word of the
    condition is load-bearing. A vendor belongs here only if it is configured,
    its circuit is closed, *and it actually lists models*. The rule refuses an
    aggregator's copy, so a vendor that cannot serve the request is not a reason
    to refuse the only route that can.

    That third condition is not hypothetical. On this machine `google` is
    configured and credentialed and publishes **no catalogue at all**, while
    OpenRouter offers 43 `google/*` models. Without the check the rule would
    have refused all 43 in favour of a provider with nothing behind it, and
    Gemini would have become unreachable — a price preference turned into an
    outage, which is exactly what the fail-open clause exists to prevent.

    Asking the adapters is cheap: their discovery sits behind a TTL, so this is
    a dictionary lookup in the ordinary case, and a listing that fails leaves
    the vendor out — the safe direction.
    """
    translating: dict[str, Any] = getattr(request.app.state, "translating", {})
    transparents: dict[str, TransparentUpstream] = getattr(
        request.app.state, "transparents", {}
    )
    health = request.app.state.health

    def closed(name: str) -> bool:
        known = health.known(HealthScope.PROVIDER, name)
        return known is None or known.allows()

    usable = set()
    for name, adapter in translating.items():
        if not closed(name):
            continue
        try:
            if await adapter.models():
                usable.add(name)
        except Exception:  # noqa: BLE001 — a failed listing is not a catalogue
            continue
    for built in transparents.values():
        if closed(built.name) and built.registry.model_ids():
            usable.add(built.name)
    return frozenset(usable)


def _provider_of(request: Request) -> Callable[[str], str]:
    """Resolve a model to the provider that will actually serve it.

    Used for policy, provider health and session attribution — three readers of
    one question, and the answer has to be the same one execution uses or each
    of them describes a request that did not happen.

    **The owner map is consulted, and its absence was a policy bypass.** A
    translated provider's models reach the candidate set as bare ids —
    `claude-audit-1`, not `ravis/anthropic/claude-audit-1` — and this resolved
    only the explicit form, falling through to the transparent upstreams for
    everything else. So the same model answered `anthropic` at execution
    (`_translating_for` reads `request.state.translated_owners`) and `default`
    here, and a deny-list naming `anthropic` was compared against `default`,
    matched nothing, and let the request through. Found by an external audit on
    9 September 2026; `test_hard_constraints_route.py` holds the reproduction.

    The same wrong answer credited a successful Anthropic call to `default` in
    the health record, and the accounting path had already been fixed for it
    separately — `owner_of` below reads the map exactly as this now does, which
    is the tell that one resolution should have served both.

    Read lazily: the map is put on the request after this closure is built.

    Falls back to the single label when nothing is declared, so a deployment
    using the singular settings keeps exactly the health record it had.
    """
    transparents: dict[str, TransparentUpstream] = getattr(
        request.app.state, "transparents", {}
    )
    filters = _filters(request)

    def provider(model: str) -> str:
        translating: dict[str, Any] = getattr(request.app.state, "translating", {})
        addressed = direct_provider(model)
        if addressed is not None and addressed in translating:
            return addressed
        # Before the transparent upstreams, matching `_translating_for`: a model
        # a translated provider owns is served by that provider whatever a
        # transparent catalogue happens to also list.
        owned = getattr(request.state, "translated_owners", {}).get(model)
        if owned is not None:
            return str(owned)
        if not transparents:
            return UPSTREAM_PROVIDER
        built = resolve(transparents, model, filters)
        return built.name if built else UPSTREAM_PROVIDER

    return provider


class _TryNext(Exception):  # noqa: N818 - a control signal, not an error condition
    """Internal signal: this attempt failed before any byte reached the client.

    Raised only from inside a streaming attempt, and only before its first
    yield. That precondition is the stream-integrity guarantee expressed as
    code: an attempt that has begun sending cannot ask for another one, because
    the only mechanism for asking is unreachable once it has yielded.

    It carries the upstream's own error body when there was one, so that if the
    chain then runs out of candidates the client receives what the upstream
    actually said rather than a summary RAVIS wrote (§8 — Clarvis must not be
    able to tell an intermediary was inserted).
    """

    def __init__(self, upstream_error: bytes | None = None) -> None:
        super().__init__("attempt failed before commit")
        self.upstream_error = upstream_error


@router.post("/chat/completions")
async def create_chat_completion(request: Request) -> Response:
    """Forward a completion request to the configured upstream.

    One upstream, but no longer only one attempt: the router returns a primary
    and its ranked fallbacks, and §10's chain walks them until one answers or
    the retry budget is spent.
    """
    body = await request.body()
    parsed = _inspect(body, request)
    if isinstance(parsed, JSONResponse):
        return parsed

    # `ravis/<provider>/<model>` naming a provider that is switched off (M10).
    # Refused before the upstream guard below, and before the router: a disabled
    # *translated* provider needs no transparent upstream, so checking the
    # general condition first would answer "no upstream configured" to a request
    # whose real problem is a toggle. And without this check at all, the
    # translated fork finds no adapter, falls through to Path A, and forwards an
    # Anthropic model to whatever the transparent upstream happens to be.
    addressed = direct_provider(parsed.get("model") or "")
    if addressed is not None and addressed in _disabled(request):
        return _openai_error(f"Provider {addressed!r} is disabled.", "provider_disabled", 503)

    upstream = request.app.state.upstream
    if not upstream.is_configured:
        return _openai_error(
            "No upstream is configured. Set RAVIS_UPSTREAM_BASE_URL.",
            "upstream_not_configured",
            503,
        )

    decision = await _route(request, parsed, body)
    if not decision.routed:
        _emit_refused(request, decision)
        return _no_route(decision)
    _emit_selected(request, decision)

    chain = _chain_for(request, decision)
    call = _Call(
        client=request.app.state.upstream_client,
        destination=_destination_for(request, upstream),
        ttl=_ttl_for(request),
        body=body,
        payload=parsed,
        chain=chain,
        recorded=getattr(request.state, "recorded_decision", None),
        note_usage=_usage_writer(request, decision),
        note_finished=_finished_writer(request, decision),
    )
    # So an exhausted chain can say "no upstream lists this" rather than relay
    # an upstream's description of its own state. See `_unlisted_body`.
    call.catalogue = frozenset(decision.considered)

    # §6's fork, and the only place it is decided. A provider whose upstream
    # does not speak the external protocol needs Path B; everything else is
    # forwarded untouched, because §6 forbids normalising an already-compatible
    # stream for architectural purity.
    translating = _translating_for(request, parsed.get("model", ""), decision.selected or "")
    _record_path(call, TRANSLATED if translating else TRANSPARENT)
    if translating is not None:
        return await _translated(call, translating, decision)

    if parsed.get("stream") is True:
        return _stream_from_upstream(call)
    return await _forward_and_return(call)


def _destination_for(
    request: Request, fallback: Upstream
) -> Callable[[str], tuple[str, dict[str, str]]]:
    """A resolver from model to (url, headers), closed over this request.

    A closure rather than a precomputed pair because the model is not known
    until the chain picks one, and the chain may pick more than one.
    """
    transparents: dict[str, TransparentUpstream] = getattr(
        request.app.state, "transparents", {}
    )
    filters = _filters(request)
    incoming = dict(request.headers)

    def destination(model: str) -> tuple[str, dict[str, str]]:
        built = resolve(transparents, model, filters) if transparents else None
        upstream = built.upstream if built else fallback
        return (
            upstream.api_url("/chat/completions"),
            forwardable_headers(incoming, upstream),
        )

    return destination


# The upstream kinds that hold a model in this machine's memory until something
# evicts it, and take a per-request TTL to say when. Ollama is deliberately not
# here: it keeps models warm too, but through `keep_alive` with different units
# and semantics, and guessing that they mean the same thing is how one runtime
# ends up configured with another's number.
_TTL_KINDS = frozenset({"lmstudio"})


def _ttl_for(request: Request) -> Callable[[str], int]:
    """How long the runtime behind a model should keep it, or 0 to say nothing.

    Resolved per model for the same reason the destination is: §10's chain may
    fall back onto a different upstream than the primary, and a TTL meant for
    the local runtime must not travel to a hosted one that would either reject
    the field or, worse, quietly accept it as something else.

    The single-upstream case reads `upstream_kind` rather than the `Upstream`
    itself, which carries a base URL and a credential but not what kind of
    thing is at the other end.
    """
    settings = request.app.state.settings
    seconds = int(getattr(settings, "local_model_idle_ttl_seconds", 0) or 0)
    transparents: dict[str, TransparentUpstream] = getattr(
        request.app.state, "transparents", {}
    )
    filters = _filters(request)

    def ttl(model: str) -> int:
        if seconds <= 0:
            return 0
        built = resolve(transparents, model, filters) if transparents else None
        kind = built.spec.kind if built else str(getattr(settings, "upstream_kind", ""))
        return seconds if kind.strip().lower() in _TTL_KINDS else 0

    return ttl


def _translating_for(
    request: Request, requested: str, selected: str = ""
) -> TranslatingAdapter | None:
    """The adapter that must translate this request, or None for Path A.

    Two ways to arrive here, and for a long time only the first worked.

    A **direct address** names its provider outright — `ravis/anthropic/…` —
    and that segment is the only part of a request that identifies an upstream.

    A **pool** resolves to a bare model id, and this used to stop there: the
    docstring said "a pooled request cannot reach a translating adapter yet and
    is forwarded transparently", which meant every Anthropic model was invisible
    to every pool. What was missing was not a lookup but a *map* — nothing knew
    which provider served a given translated model. `translated_candidates`
    returns one alongside the candidates it contributes, built from the same
    catalogue read, so the answer cannot disagree with the set the router chose
    from.

    Forking on the **selected** model rather than the requested one is the whole
    fix. What a client asked for is a pool; what has to be translated is what
    the router picked.
    """
    provider = direct_provider(requested)
    if provider is None:
        provider = getattr(request.state, "translated_owners", {}).get(selected)
    if provider is None:
        return None
    adapters: dict[str, TranslatingAdapter] = getattr(request.app.state, "translating", {})
    adapter = adapters.get(provider)
    # Registered but unusable is not routable. A provider is in that table
    # because RAVIS knows how to reach it, not because it can authenticate
    # today — checked here so a key saved on the Credentials screen takes effect
    # on the next request, and an unconfigured provider still serves nothing
    # rather than forwarding a request that can only come back 401.
    if adapter is None or not adapter.has_credential:
        return None
    return adapter


def _chosen(request: Request, requested: str) -> tuple[str, ...]:
    """The models an operator picked for this pool, or empty.

    Empty for anything that is not a pool: a direct address names its target and
    a bare model name is passed through, so neither has a membership list to
    consult.
    """
    membership = getattr(request.app.state, "pool_membership", None)
    if membership is None or not is_pool_id(requested):
        return ()
    return tuple(membership.for_pool(requested))


def _observed(request: Request) -> dict[str, float]:
    """Median TTFT per model, for the models measured often enough to mean it."""
    store = getattr(request.app.state, "observations", None)
    return store.ttft_for_ranking() if store is not None else {}


def _reasoning_shares(request: Request, models: Sequence[str]) -> dict[str, float]:
    """SIRVIS's measured reasoning share per candidate, for the ones it has one.

    Only the models this pass is actually considering, and only the shares that
    were counted rather than inferred — `EvidenceStore.reasoning_share` drops
    the rest, along with anything past the staleness window. A build with no
    entry here is not ranked down; see `_reasoning_rank`.
    """
    store = getattr(request.app.state, "evidence", None)
    if store is None:
        return {}
    shares = {model: store.reasoning_share(model) for model in models}
    return {model: share for model, share in shares.items() if share is not None}


def _role_evidence(request: Request, models: Sequence[str]) -> dict[str, dict[str, str]]:
    """Which roles SIRVIS has measured each candidate for, and how it did.

    The map a pool's membership is derived from: `{model: {role: state}}`. Only
    the models this pass is considering, and only those with a record — a build
    nobody has measured is absent rather than present with an UNKNOWN, because
    `_admits` reads absence and UNKNOWN the same way and an empty dict is
    cheaper to reason about than one full of nothings.
    """
    store = getattr(request.app.state, "evidence", None)
    if store is None or not hasattr(store, "roles_measured"):
        return {}
    fit = {model: store.roles_measured(model) for model in models}
    return {model: roles for model, roles in fit.items() if roles}


def _record_path(call: _Call, path: str) -> None:
    """Note which of §6's two paths ran, on the record a diagnostic reads."""
    if call.recorded is not None:
        call.recorded.execution_path = path


async def _translated(
    call: _Call, adapter: TranslatingAdapter, decision: RouteDecision
) -> Response:
    """Path B: normalize in, native out, OpenAI-compatible back (§6).

    **No fallback from here yet, and that is deliberate rather than unfinished.**
    §10's chain assumes every candidate is reachable the same way; falling back
    from a translated provider to a transparent one means the next attempt runs
    a different path with a different failure vocabulary, and deciding that
    quietly inside an exception handler is how a fallback starts producing
    answers nobody can account for. An adapter failure terminates the stream
    with the error it raised, which is the same thing the transparent path does
    once bytes have been committed.
    """
    model = decision.selected or call.payload.get("model", "")
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    request = normalize(call.body, call.payload)
    # The address the client used — `ravis/anthropic/claude-x` — is not a model
    # any provider will accept. The router already resolved it to the bare name,
    # so it is substituted here, once, rather than in every adapter: an adapter
    # re-deriving it would be four copies of a rule the router owns.
    request.requested_model = model

    if call.payload.get("stream") is not True:
        return await _translated_completion(call, adapter, request, model, completion_id)

    started = call.chain.begin(model)

    return StreamingResponse(
        _translated_relay(call, adapter, request, model, completion_id, started),
        media_type="text/event-stream",
                             headers={"cache-control": "no-cache"})


async def _translated_completion(
    call: _Call, adapter: TranslatingAdapter, request: NormalizedRequest,
    model: str, completion_id: str,
) -> Response:
    """Path B without a stream: one call, one whole answer.

    Its own function because the streaming relay below already carries the two
    hard parts — cancellation and the commit boundary — and folding a second
    shape into it makes the branch that matters harder to read than the one
    that does not.
    """
    started = call.chain.begin(model)
    try:
        answer = await adapter.complete(request)
    except TranslationError as refusal:
        # Not a chain exhaustion, and reporting it as one would bury the only
        # thing worth saying. Nothing was attempted upstream, the request itself
        # is what cannot be sent, and the client can fix it — so it gets a 400
        # carrying the reason rather than a 502 saying every candidate failed.
        call.chain.failed(model, started, FailureClass.INVALID_REQUEST, str(refusal))
        call.finish()
        return _openai_error(str(refusal), "invalid_request_error", 400)
    except Exception as failure:  # noqa: BLE001 - an adapter is third-party code
        call.chain.failed(model, started, _adapter_failure(failure), str(failure))
        call.finish()
        return _chain_exhausted(call.chain)
    call.chain.succeeded(model, started)
    # §14, Path B. The transparent path reads a usage frame off the wire; here
    # the adapter has already normalised it, so the count is simply on the
    # answer — and forgetting to record it was how Anthropic and Google, the
    # two providers a price file most exists for, produced no usage at all.
    call.note_usage(model, answer.usage, (call.chain.now() - started) * 1000)
    call.finish()
    return JSONResponse(completion(answer, model=model, completion_id=completion_id))


def _adapter_failure(failure: Exception) -> FailureClass:
    """Whose fault an adapter error was, which decides what happens next.

    A `TranslationError` is RAVIS refusing a request it could not render — the
    client's problem, and `INVALID_REQUEST` says so: no retry, no fallback, and
    no mark against the provider's health. Everything else stays UNKNOWN, which
    permits neither retry nor fallback either, but does not claim to know that
    the request was the thing at fault.

    Without this distinction one client sending an untranslatable body would
    walk the provider's circuit breaker toward open, and take the provider
    offline for every other caller on the machine.
    """
    if isinstance(failure, TranslationError):
        return FailureClass.INVALID_REQUEST
    # **An upstream's own 4xx says the same thing in its own words.** The
    # argument above applied to only one of the two ways a request can be the
    # client's fault: RAVIS refusing to render it, and the upstream refusing to
    # accept it. The second reached here as an unclassifiable `RuntimeError` and
    # became `UNKNOWN`, so `400: max_tokens must be an integer` was reported to
    # the caller as `502 upstream_error` — blaming a provider that had worked.
    status = getattr(failure, "status", None)
    if isinstance(status, int):
        return classify_response(status, str(failure).encode()) or FailureClass.UNKNOWN
    return FailureClass.UNKNOWN


async def _translated_frames(
    events: AsyncGenerator[NormalizedStreamEvent, None], model: str, completion_id: str,
    note: Callable[[Usage | None], None] | None = None,
    answered: Callable[[], None] | None = None,
) -> AsyncGenerator[bytes, None]:
    """The serializer's pieces, driven one event at a time.

    Event by event rather than collect-then-render, because a slow provider's
    first token has to reach the client when it arrives — buffering the stream
    to serialize it whole would turn Path B into the thing §6 warns against and
    would make every translated response feel broken.

    **`answered` fires on the provider's first event, and that is not the first
    frame.** The opening frame below is RAVIS's own: it carries the role delta
    an OpenAI stream starts with and is constructed here, before this function
    has awaited the provider at all. A caller timing the stream from its first
    *frame* is therefore timing how long RAVIS took to build one — which is how
    a translated call to a hosted provider came to record 0.19 ms end to end
    while the client measured 6.1 seconds to first byte.
    """
    yield opening_frame(model=model, completion_id=completion_id)
    started: set[int] = set()
    reported: Usage | None = None
    upstream_answered = False
    async for event in events:
        if not upstream_answered:
            upstream_answered = True
            if answered is not None:
                answered()
        # **The latest reading wins, not the first.** Gemini reports
        # `usageMetadata` on every frame with the counts growing, so `or` kept
        # the earliest — which has a prompt count and no completion count yet,
        # and produced a record reading `out=None` and a cost that understated
        # the call. A provider that reports once at the end is unaffected.
        if event.usage is not None:
            reported = event.usage
        frame = frame_for(event, started, model=model, completion_id=completion_id)
        if frame is not None:
            yield frame
    yield DONE
    if note is not None:
        note(reported)


class _Call:
    """Everything one request needs to be attempted, possibly more than once.

    A small carrier rather than six parameters threaded through five functions.
    It exists because the streaming and non-streaming paths need exactly the
    same inputs and differ only in how they consume the response — and when
    those inputs were passed positionally, adding the chain meant editing every
    signature between the handler and the relay.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        destination: Callable[[str], tuple[str, dict[str, str]]],
        body: bytes,
        payload: dict[str, Any],
        chain: AttemptChain,
        recorded: RecordedDecision | None,
        note_usage: Callable[[str, Usage | None, float | None], None] | None = None,
        note_finished: Callable[[AttemptChain], None] | None = None,
        # Defaulted so every existing construction — the conformance fixtures
        # included — keeps working and simply sends no TTL, which is the
        # behaviour before this existed.
        ttl: Callable[[str], int] = lambda _model: 0,
    ) -> None:
        self.client = client
        self._destination = destination
        self._ttl = ttl
        self.body = body
        self.payload = payload
        # What every upstream lists, for the one purpose of telling "this model
        # is not offered anywhere" apart from "the upstream it was guessed onto
        # is unhappy". Set by the caller; empty means RAVIS has discovered
        # nothing yet, which is not evidence of absence.
        self.catalogue: frozenset[str] = frozenset()
        self.chain = chain
        self.recorded = recorded
        # §14's usage record, written once when a stream finishes. Injected
        # rather than reached for: this class performs no I/O and holds no app
        # state, and the ledger needs the identity and the price book, neither
        # of which a request carrier should know about.
        self._note_usage = note_usage
        # M18b's closing event, injected for the same reason as the usage
        # writer above: this class performs no I/O and holds no app state, and
        # publishing needs both. Called on every exit path because `finish` is.
        self._note_finished = note_finished

    def note_usage(self, model: str, usage: Usage | None, latency_ms: float | None) -> None:
        """Record what this call consumed, if anything is listening."""
        if self._note_usage is not None:
            self._note_usage(model, usage, latency_ms)

    def target_for(self, model: str) -> str:
        """The URL this attempt goes to.

        Resolved per attempt rather than fixed per request, because §10's chain
        walks candidates and a fallback may live on a different upstream than
        the primary did. A single URL for the whole chain would send that
        fallback to the wrong host — with the wrong credential attached — and
        the failure would look like the fallback model being broken.
        """
        return self._destination(model)[0]

    def headers_for(self, model: str) -> dict[str, str]:
        """The headers for this attempt, carrying that upstream's credential."""
        return self._destination(model)[1]

    def body_for(self, model: str) -> bytes:
        """The request body addressed to one particular model."""
        body = _with_model(self.body, self.payload, model, self._ttl(model))
        return _for_openai(body) if _is_openai(self.target_for(model)) else body

    def finish(self) -> None:
        """Attach the attempt history to the recorded decision (§9.7).

        Called on every exit path, success or failure. A route explanation that
        shows what was chosen but not what happened when it was called is the
        half of the story that matters least during an incident.
        """
        if self.recorded is not None:
            self.recorded.attempts = self.chain.summary()
        if self._note_finished is not None:
            self._note_finished(self.chain)


def _inspect(body: bytes, request: Request) -> dict[str, Any] | JSONResponse:
    """Parse the body for admission checks only, never for forwarding.

    Returns the parsed payload, or a ready-made error response when the request
    must be refused. The §4.4 URL refusal is deliberately *not* applied here:
    that rule says the field is forwarded untouched on the transparent path,
    because the upstream owns its own dereferencing policy and RAVIS is not the
    one making the fetch.
    """
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return _openai_error("Request body is not valid JSON", "invalid_request_error", 400)
    if not isinstance(parsed, dict):
        return _openai_error("Request body must be a JSON object", "invalid_request_error", 400)

    refusal = _shape_refusal(parsed)
    if refusal is not None:
        return refusal

    check_image_count(parsed.get("messages") or [], request.app.state.settings)
    return parsed


def _shape_refusal(parsed: dict[str, Any]) -> JSONResponse | None:
    """Refuse a payload RAVIS itself cannot read, and nothing more.

    **Only the two fields RAVIS dereferences.** This is a transparent proxy: the
    upstream owns its own schema, and validating `temperature` or `tools` here
    would invent a contract RAVIS has no business holding — the non-invention
    rule in the runbook's §1, applied to a request body. What RAVIS *does* read
    is `model`, to route on, and `messages`, to walk. Those two must be the shape
    it walks, or the walk crashes.

    It did. `messages` was checked only when it was already a list, so any other
    type went past the guard and failed further in — `'int' object has no
    attribute 'get'` inside `content.py`, surfacing as a bare 500 with no
    OpenAI-compatible body at all. A missing `model` was worse than a wrong
    number: it routed as the empty string, reached a real local runtime, failed
    to connect, and was reported to the caller as the *upstream's* failure.

    Found by sending bad payloads at a running RAVIS. Reading the guard would not
    have shown it, because the guard looks correct until you ask what happens
    when its condition is false.
    """
    model = parsed.get("model")
    if not isinstance(model, str) or not model.strip():
        return _openai_error(
            "'model' is required and must be a non-empty string",
            "invalid_request_error",
            400,
        )

    # **Absent is not malformed**, and this is the line the first version got
    # wrong. A transparent proxy forwards what it was given and lets the upstream
    # own its own schema (the runbook's non-invention rule); requiring `messages`
    # here refused requests the upstream would have answered, and broke fifty-six
    # tests that send a model and a parameter to check the forwarding path.
    #
    # Present-but-wrong-typed is different: RAVIS walks this list itself.
    messages = parsed.get("messages")
    if messages is not None and not isinstance(messages, list):
        return _openai_error(
            "'messages' must be a list", "invalid_request_error", 400
        )
    for index, message in enumerate(messages or []):
        if not isinstance(message, dict):
            return _openai_error(
                f"'messages[{index}]' must be an object",
                "invalid_request_error",
                400,
            )
    return None


def _disabled(request: Request) -> frozenset[str]:
    """Providers an operator has switched off (M10).

    Read per request rather than cached: the file is small and local, and a
    toggle that only took effect after a restart would be a toggle nobody
    trusts during an incident.
    """
    state = getattr(request.app.state, "provider_state", None)
    return frozenset(state.disabled()) if state else frozenset()


def _filters(request: Request) -> dict[str, Any] | None:
    """Each provider's model filter, or None when nothing is configured."""
    filters = getattr(request.app.state, "model_filters", None)
    return filters.all() if filters else None


async def _route(request: Request, payload: dict[str, Any], body: bytes) -> RouteDecision:
    """Resolve what the client addressed into a model to call (§9).

    Capabilities are assembled per request rather than cached. That is cheap
    today because the generic adapter performs no I/O to answer — it merges
    protocol defaults with operator configuration — and the moment an adapter
    needs a network call to answer, this is the line that has to change.
    """
    engine: RoutingEngine = request.app.state.routing_engine
    registry: ModelRegistry = request.app.state.model_registry
    health: HealthRegistry = request.app.state.health
    # SIRVIS's measurements with RAVIS's own trials beside them (see `trials.py`).
    evidence = getattr(request.app.state, "capability_evidence", None) or getattr(
        request.app.state, "evidence", None
    )
    transparents: dict[str, TransparentUpstream] = getattr(
        request.app.state, "transparents", {}
    )
    # Every upstream's models, each asked through its own adapter. With one
    # upstream this is what it always was; with several it is the only way a
    # model on LM Studio gets LM Studio's catalogue read for it instead of
    # whichever adapter happened to be primary.
    if transparents:
        candidates = await merged_candidates(
            transparents, evidence, _disabled(request), _filters(request)
        )
        residency = merged_residency(transparents)
        # Translated providers join the candidate set. They were absent
        # entirely, so an Anthropic model could not be selected by any pool —
        # only addressed directly. The owner map travels with them so the fork
        # below can attribute a selection back to the adapter that serves it.
        translated, owners = await translated_candidates(
            {
                name: adapter
                for name, adapter in getattr(request.app.state, "translating", {}).items()
                if name not in _disabled(request)
            },
            evidence,
        )
        for model, known in translated.items():
            candidates.setdefault(model, known)
        request.state.translated_owners = owners
    else:
        adapter: ProviderAdapter = request.app.state.adapter
        candidates = await candidates_with_evidence(adapter, registry.model_ids(), evidence)
        residency = registry.residency
    # §14's price book, filled from the catalogue this pass already read. Done
    # here rather than on the refresh timer because the capability records are
    # what carry a price, and this is where they are assembled — harvesting it
    # anywhere else would mean reading the catalogue a second time to learn
    # something the first read already knew.
    _harvest_prices(request, candidates)
    # And the gaps the catalogue left, from the same book: operator-stated rates for the
    # providers that publish none, so a direct build ranks on its price rather than on
    # its name. See `price_from_book`.
    price_from_book(candidates, getattr(request.app.state, "prices", None))
    remote = remote_models(transparents) | frozenset(
        getattr(request.state, "translated_owners", {})
    )
    policy = _policy_for(request, payload)
    # §12.1's affinity, and §9.6.1's exemption from it. A background call is
    # explicitly *exempt* from session affinity — it is short, disposable and
    # latency-insensitive, so holding it on a conversation's model would drag a
    # frontier choice onto work that exists to be cheap.
    sticky = "" if policy.background else _sticky_model(request)
    # §12.2's expected session length, measured from this application's own
    # history rather than from the request in hand. `None` where there is not
    # enough history, which routes exactly as RAVIS did before the tradeoff
    # existed.
    expected = _expected_session_requests(request)
    # Vendors this machine can buy from at the source, resolved once: the
    # refusal path and the ranking path must agree about what is direct.
    direct = await _direct_providers(request)
    # Normalised once and kept: the engine reads its requirements, the
    # tool-refusal suppression below applies only when it carries tools, and
    # the attempt chain needs the same answer to decide whether a refusal may
    # arm one. Three readers of one fact should not each re-derive it.
    normalized = normalize(body, payload)
    request.state.carries_tools = normalized.carries_tools
    decision = engine.select(
        payload.get("model") or "",
        candidates,
        residency=residency,
        # Sampled on a timer by the app, never taken here (§9.8).
        memory=request.app.state.memory,
        # The request's own hard requirements (§9.5): a request carrying tools
        # or images demands a model that can handle them, whatever the pool's
        # static invariants say.
        request=normalized,
        # Providers whose catalogue this engine cannot see. Their own model
        # list is the authority, so a direct address to one is not checked
        # against the local upstream's — see `_direct`.
        foreign_providers=frozenset(getattr(request.app.state, "translating", {})),
        # Which candidates would leave this machine. `ravis/local` and
        # `ravis/private` are refusals rather than preferences, so this has to
        # reach the engine — it had no way to know, and answered `ravis/local`
        # with an OpenRouter model.
        # Every translated provider is hosted, so its models are remote — which
        # is what keeps them out of `ravis/local` now that they are candidates.
        remote_models=remote,
        # An operator's narrowing of this pool, if they made one. Read per
        # request for the same reason the provider toggles are.
        chosen=_chosen(request, payload.get("model") or ""),
        # What RAVIS has timed, for the pools that rank on speed. Only models
        # past the sample floor appear here — see `Observations`.
        observed_ttft_ms=_observed(request),
        # §11.4's reasoning share, which breaks a tie only when this request
        # capped its output — a build that spends the budget thinking returns
        # less answer, or none. Dormant otherwise; see `_reasoning_rank`.
        reasoning_share=_reasoning_shares(request, list(candidates)),
        role_evidence=_role_evidence(request, list(candidates)),
        # Whether this request is allowed to spend itself learning about a
        # model instead of using the best one. Off unless the caller asked.
        explore=_exploration(payload),
        # §10: do not keep routing to a failing provider. Models behind an open
        # circuit are excluded here, with the reason, rather than discovered
        # again by another request that pays another timeout to learn it — and,
        # for a request carrying tools, so are models that refused tools
        # recently. See `_unavailable_for`.
        unavailable=_unavailable_for(request, health, list(candidates), normalized),
        # §9.6's policy, resolved from the identity and what the request
        # declared. Computed here rather than in the engine because deciding
        # whether a model is forbidden needs its provider and whether that
        # provider is on this machine, and the engine is a pure function of a
        # capability table that knows neither.
        policy=policy,
        sticky=sticky,
        expected_session_requests=expected,
        policy_refusals=policy_refusals(
            policy,
            candidates,
            addressed=payload.get("model") or "",
            provider_of=_provider_of(request),
            remote=remote,
        ),
        # Ranked rather than refused inside a pool; see `_rank`.
        resold=resold_models(candidates, _provider_of(request), direct),
        # Who serves each directly bought candidate, so a resold copy is only
        # ranked behind a maker's copy that survived this request's constraints.
        direct_owners=direct_owners(candidates, _provider_of(request), direct),
    )
    # Recorded rather than recomputed. Re-running the router later would use a
    # different catalogue, residency and memory reading, and could reach a
    # different answer than the one being asked about — an explanation you
    # recompute is a guess about the past (§9.7).
    identity = getattr(request.state, "identity", None)
    recorded = request.app.state.decision_log.record(
        decision,
        application_id=identity.application_id if identity else "anonymous",
        request_id=getattr(request.state, "request_id", ""),
        trace_id=getattr(request.state, "trace_id", ""),
    )
    _record_session(request, decision, background=policy.background)
    request.state.route_decision = decision
    request.state.decision_id = recorded.decision_id
    request.state.recorded_decision = recorded
    return decision


def _unavailable_for(
    request: Request, health: HealthRegistry, models: list[str], normalized: NormalizedRequest
) -> dict[str, str]:
    """The models this request must not be routed to right now, each with why.

    Open circuits always. For a request carrying tools, also the models that
    refused tools recently — and only then, which is the whole of how a model
    that cannot do tools today keeps serving plain chat (runbook §2.1): a
    request without tools never asks about the suppression at all.

    **One reason per model, joined, never two.** Both kinds of exclusion lift
    on their own, and the router marks anything arriving here as resting; a
    no-route then answers 503 only when every excluded candidate carries exactly
    one reason (`RouteDecision.blocked_only_by_circuits`). A model with an open
    circuit *and* a suppression listed as two reasons would turn a resting pool
    into a 422 — the status Clarvis's §8.7 probe caches for the session.

    Passed in as `unavailable` rather than as a new engine argument, so the
    engine stays a pure function of what it is handed and needs no change.
    """
    blocked = health.unavailable(models, _provider_of(request))
    if not normalized.carries_tools:
        return blocked
    for model, reason in health.suppressed(models, Capability.TOOLS).items():
        blocked[model] = f"{blocked[model]}; {reason}" if model in blocked else reason
    return blocked


def _session_id(request: Request) -> str:
    """The session this request belongs to, as the client stated it (§4.3)."""
    return request.headers.get(SESSION_HEADER, "")


def _sticky_model(request: Request) -> str:
    """What this session last routed to, if it is still fresh enough to matter.

    Empty for a first request, an expired session, or a caller that sent no
    header — all of which mean "nothing to be consistent with", and none of
    which is an error.
    """
    store: SessionStore | None = getattr(request.app.state, "sessions", None)
    if store is None:
        return ""
    identity = getattr(request.state, "identity", None)
    session = store.affinity(
        identity.application_id if identity else "anonymous", _session_id(request)
    )
    return session.model if session else ""


def _expected_session_requests(request: Request) -> int | None:
    """How long this application's sessions usually run, or None.

    Read per request rather than cached: it changes as sessions accumulate, and
    a cached value would keep routing on last week's shape of usage. The query
    is one indexed read of a small table.
    """
    store: SessionStore | None = getattr(request.app.state, "sessions", None)
    if store is None:
        return None
    identity = getattr(request.state, "identity", None)
    return store.typical_length(identity.application_id if identity else "anonymous")


def _harvest_prices(request: Request, candidates: dict[str, ModelCapabilities]) -> None:
    """Record every published price this catalogue carried.

    Only models that actually publish one: a model with no price is left absent
    from the book rather than entered as free, which is the distinction the
    whole cost engine rests on.
    """
    prices: PriceBook | None = getattr(request.app.state, "prices", None)
    if prices is None:
        return
    for model, known in candidates.items():
        if known.price is not None:
            prices.record(model, known.price)


def _events(request: Request) -> Any:
    """RAVIS's publisher, or a disabled one. Never absent, so no caller branches."""
    return request.app.state.events


def _emit_selected(request: Request, decision: RouteDecision) -> None:
    """M18b: the moment that answers "why this model?", while it is still now.

    **A pointer plus a summary, not the explanation.** `RouteDecision.as_dict()`
    carries `considered` — every candidate id, which against an OpenRouter
    catalogue is hundreds of strings — and `excluded` with a reason for each.
    NERVIS already fetches the full explanation itself through the routes peer
    surface, so the event carries `decision_id` and the reader follows it.

    The summary rides along anyway, because the pointer dangles: the decision
    log is bounded at 200 entries in memory, so two hundred requests later the
    id resolves to nothing and a timeline with only an id on it says nothing at
    all.
    """
    _events(request).emit(
        "ravis.route.selected",
        trace_id=getattr(request.state, "trace_id", ""),
        data={
            "decision_id": getattr(request.state, "decision_id", ""),
            "request_id": getattr(request.state, "request_id", ""),
            "requested": decision.requested,
            "pool": decision.pool_id,
            "selected": decision.selected,
            "fallbacks": len(decision.fallbacks),
            "considered": len(decision.considered),
            "excluded": len(decision.excluded),
            "reason": decision.reason,
        },
    )


def _emit_refused(request: Request, decision: RouteDecision) -> None:
    """M18b: a no-route, which is an explainable outcome and not a failure.

    Its own type rather than `selected` with a null model: a type whose name
    asserts a selection that did not happen is one every consumer has to
    defensively re-check.

    `blocked_only_by_circuits` is the field worth crossing a service boundary
    for. A pool nothing satisfies and a pool whose candidates are all in
    cooldown look identical from outside and have different fixes, and this is
    the only place that distinction exists.
    """
    _events(request).emit(
        "ravis.route.refused",
        trace_id=getattr(request.state, "trace_id", ""),
        severity="warning",
        data={
            "decision_id": getattr(request.state, "decision_id", ""),
            "request_id": getattr(request.state, "request_id", ""),
            "requested": decision.requested,
            "pool": decision.pool_id,
            "reason": decision.reason,
            "blocked_only_by_circuits": decision.blocked_only_by_circuits,
            "excluded": len(decision.excluded),
        },
    )


def _finished_writer(
    request: Request, decision: RouteDecision
) -> Callable[[AttemptChain], None]:
    """M18b's closing event — the one funnel every exit path passes through.

    `_Call.finish` is called from nine places: both execution paths, the
    streaming relay, cancellation and every exhaustion. Emitting anywhere else
    means writing this event nine times or missing a path.

    It is also what gives RAVIS a *bar* rather than a dot. `traces.assemble`
    makes a service's span the interval its events cover and refuses to draw a
    duration from a single event, so without a second event RAVIS appears as a
    point at whatever moment it happened to finish.
    """

    def note(chain: AttemptChain) -> None:
        # `summary()` is a dict whose `attempts` key holds the list — the shape
        # §9.7 publishes. Treating the whole thing as the list is a mistake that
        # only shows up once a request is actually made, which is why every
        # proxy test caught it at once and no unit test did.
        recorded = getattr(request.state, "recorded_decision", None)
        summary = chain.summary()
        attempts = [one for one in summary.get("attempts", []) if isinstance(one, dict)]
        succeeded = any(one.get("outcome") == "succeeded" for one in attempts)
        _events(request).emit(
            "ravis.request.completed",
            trace_id=getattr(request.state, "trace_id", ""),
            severity="info" if succeeded else "error",
            data={
                "decision_id": getattr(request.state, "decision_id", ""),
                "request_id": getattr(request.state, "request_id", ""),
                "selected": decision.selected,
                # From the recorded decision, which is where `_record_path`
                # writes it — `request.state` never had it, so this published
                # an empty string for §6's execution path on every event. Found
                # by reading what actually arrived in the hub rather than by
                # any test, because "" is a plausible value for a field that is
                # genuinely unset early in a request.
                "execution_path": getattr(recorded, "execution_path", "") or "",
                "attempts": len(attempts),
                "provider": summary.get("provider", ""),
                "stopped_because": summary.get("stopped_because", ""),
                # The outcome of each attempt, not its body. `summary()` carries
                # the provider, the outcome and the timing; a response body
                # would carry the completion, which §9 forbids.
                "outcomes": [str(one.get("outcome") or "") for one in attempts],
                "succeeded": succeeded,
                # `AttemptChain.cancelled` is a *method*, so `bool(...)` on it
                # is always True — this reported every successful request as
                # cancelled, which is the one thing §9.7 says a route
                # explanation must never blur. The evidence is in the attempts.
                "cancelled": any(
                    one.get("outcome") == "cancelled" for one in attempts
                ),
            },
        )
        _emit_suppressions(request, chain)

    return note


def _emit_suppressions(request: Request, chain: AttemptChain) -> None:
    """Publish each tool-refusal suppression this request armed — once each.

    Through the closing funnel above, so no exit path can miss one, and once
    per *new* suppression rather than once per refusal: `HealthRegistry.suppress`
    reports whether it was new, and the chain keeps only those. The event is
    the diagnostic the fallback would otherwise swallow — once the next model
    answers, nothing else says that a model refused tools and is now resting.

    Identifiers, the upstream's words (already stripped of anything
    credential-shaped) and seconds. Never a prompt, never a body.
    """
    for model in chain.suppressed:
        held = chain.health.suppression(model, Capability.TOOLS)
        if held is None:
            continue
        logger.warning(
            "model suppressed for tool requests",
            extra={"detail": f"{model}: {held['reason']}"},
        )
        _events(request).emit(
            "ravis.capability.suppressed",
            trace_id=getattr(request.state, "trace_id", ""),
            severity="warning",
            data={
                "decision_id": getattr(request.state, "decision_id", ""),
                "request_id": getattr(request.state, "request_id", ""),
                **held,
            },
        )


def _usage_writer(
    request: Request, decision: RouteDecision
) -> Callable[[str, Usage | None, float | None], None]:
    """A sink that writes one §14 usage record for a completed call.

    Closed over the request rather than reading it later: by the time a stream
    finishes the handler has returned, and this is the only thing still holding
    the identity, the decision and the session the call belonged to.

    **Called from one place per attempt**, which is what makes double counting
    structural. A retry that failed never reaches it; a fallback that worked
    reaches it once, naming the model that actually answered rather than the
    one originally selected.
    """
    ledger: UsageLedger | None = getattr(request.app.state, "usage_ledger", None)
    identity = getattr(request.state, "identity", None)
    application = identity.application_id if identity else "anonymous"
    request_id = getattr(request.state, "request_id", "")
    session_id = _session_id(request)
    provider_of = _provider_of(request)
    prices = getattr(request.app.state, "prices", None)
    pool = decision.pool_id or ""
    # **Which provider actually served it**, which `_provider_of` cannot answer
    # for a translated call: it resolves the *selected* model, and a translated
    # provider's models never appear in a transparent upstream's catalogue — so
    # `claude-haiku-4-5` fell through to whichever transparent upstream was
    # declared first and every Path B call was attributed to `openai`. Found by
    # reading three records that named one provider for three providers.
    translating = getattr(request.app.state, "translating", {})
    addressed = direct_provider(decision.requested or "")

    def owner_of(model: str) -> str | None:
        """Which provider served this call, by the same rule §6's fork uses.

        **The fork's own comment is "forking on the selected model rather than
        the requested one is the whole fix", and that fix was applied there and
        not here.** `direct_provider` answers only for a directly addressed
        model: it returns None for a pool id (`core/pools.py`), so on a pooled
        request -- the normal way a client addresses RAVIS, and the reason
        `translated_candidates` exists at all -- `owner` was None and the record
        fell back to `provider_of`, which cannot resolve a translated model
        because its ids never appear in a transparent upstream's catalogue. The
        spend then landed under the `upstream` fallback label instead of
        Anthropic or Google.

        Read lazily: `translated_owners` is put on the request after this
        closure is built.
        """
        if addressed in translating:
            return addressed
        return getattr(request.state, "translated_owners", {}).get(model)

    def note(model: str, usage: Usage | None, latency_ms: float | None) -> None:
        if ledger is None:
            return
        owner = owner_of(model)
        price = prices.price_of(model) if prices is not None else None
        amount, state = estimate(price, usage)
        ledger.record(
            UsageRecord(
                model=model,
                provider=owner or provider_of(model),
                application_id=application,
                usage=usage,
                cost=amount,
                cost_state=state,
                currency=price.currency if price else None,
                price_source=price.source if price else "",
                price_captured_at=price.captured_at if price else 0.0,
                latency_ms=latency_ms,
                request_id=request_id,
                session_id=session_id,
                # Read here rather than captured above: the decision is recorded
                # after this closure is built, so reading it early would store
                # an empty string on every record.
                decision_id=getattr(request.state, "decision_id", ""),
                pool=pool,
            )
        )

    return note


#: How much of a partly-arrived SSE frame is kept while waiting for the rest.
#: A usage frame is a few hundred bytes; a content delta carrying an image can
#: be far larger, and it is a *frame* too, so the boundary can land inside one.
#: Generous enough that no real frame is cut, bounded so a provider that never
#: sends a frame terminator cannot grow this without limit.
USAGE_CARRY_BYTES = 32_768


def _carry_over(seen: bytes) -> bytes:
    """The tail of `seen` that is not yet a complete SSE frame.

    Frames end with a blank line, so everything before the last `\n\n` has
    already been offered to `_usage_in` in full and is dropped — which is what
    keeps a frame from being read twice, and keeps this buffer the size of one
    partial frame rather than the size of the stream.

    Truncating from the front when the cap is hit can leave a fragment that no
    longer parses, costing the usage figure for that one call. That is the
    deliberate trade against holding an unbounded buffer for a provider that
    never terminates a frame.
    """
    cut = seen.rfind(b"\n\n")
    tail = seen if cut < 0 else seen[cut + 2:]
    return tail[-USAGE_CARRY_BYTES:]


def _usage_in(chunk: bytes) -> Usage | None:
    """The token counts in an SSE chunk, if it carries a usage frame.

    Read from bytes forwarded unchanged either side of this call, so §6's
    byte-for-byte guarantee on the transparent path is untouched: nothing here
    rewrites, buffers or re-frames anything.

    Cheap on the common chunk — the substring test rejects a content delta
    before any JSON is parsed, and a stream carries one usage frame in hundreds.
    """
    if b'"usage"' not in chunk:
        return None
    for line in chunk.split(b"\n"):
        if not line.startswith(b"data:"):
            continue
        body = line[5:].strip()
        if not body or body == b"[DONE]":
            continue
        try:
            frame = json.loads(body)
        except ValueError:
            continue
        reported = frame.get("usage") if isinstance(frame, dict) else None
        if isinstance(reported, dict):
            return _usage_from(reported)
    return None


def _usage_in_body(content: bytes) -> Usage | None:
    """The token counts in a non-streamed completion body, if it carries any.

    The sibling of `_usage_in`, for the path that has no frames. Reads the bytes
    that are about to be forwarded unchanged and returns them to RAVIS's own
    shape; §6's byte-for-byte guarantee is untouched because nothing here
    rewrites or re-frames the response.
    """
    if b'"usage"' not in content:
        return None
    try:
        body = json.loads(content)
    except ValueError:
        return None
    reported = body.get("usage") if isinstance(body, dict) else None
    return _usage_from(reported) if isinstance(reported, dict) else None


def _usage_from(reported: dict[str, Any]) -> Usage:
    """One provider's usage object in RAVIS's own shape.

    Absent counts stay `None` rather than becoming zero — §14's rule that
    unknown usage stays unknown, and the reason `estimate` can refuse to
    produce a figure at all rather than producing a confident nought.
    """
    details = reported.get("completion_tokens_details")
    cached = reported.get("prompt_tokens_details")
    return Usage(
        input_tokens=_count(reported.get("prompt_tokens")),
        output_tokens=_count(reported.get("completion_tokens")),
        cached_input_tokens=(
            _count(cached.get("cached_tokens")) if isinstance(cached, dict) else None
        ),
        reasoning_tokens=(
            _count(details.get("reasoning_tokens")) if isinstance(details, dict) else None
        ),
        reported_cost=_rate(reported.get("cost")),
    )


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _rate(value: Any) -> float | None:
    """A provider-reported cost, or None. Negative is not a cost."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value >= 0 else None


def _record_session(
    request: Request, decision: RouteDecision, background: bool = False
) -> None:
    """Write what this request routed to, so the next one can be consistent.

    Only a routed decision updates the model: a no-route means the session's
    last *successful* choice is still the thing to be consistent with, and
    overwriting it with nothing would make the next request start over. The
    session is still touched, so a conversation being actively refused does not
    quietly expire while somebody is trying to fix it.

    **A background call never writes the model, which is the other half of
    §9.6.1's exemption and was missed the first time.** Skipping affinity on the
    way in is not enough: a declared background call was still *recording* its
    own cheap selection, so generating one conversation title reset the
    session and the next real turn started over on a different model. Found by
    sending the four requests in order against a live gateway — the exemption
    read as working until the fifth one showed the conversation had moved.

    Exempt means exempt in both directions: it does not consume affinity and it
    does not get to redefine it.
    """
    store: SessionStore | None = getattr(request.app.state, "sessions", None)
    supplied = _session_id(request)
    if store is None or not supplied:
        return
    identity = getattr(request.state, "identity", None)
    application = identity.application_id if identity else "anonymous"
    if background or not decision.routed:
        store.touch(application, supplied)
        return
    pool = POOLS_BY_ID.get(decision.pool_id or "")
    store.record(
        application,
        supplied,
        pool=decision.pool_id or decision.requested,
        model=decision.selected or "",
        provider=_provider_of(request)(decision.selected or ""),
        # §5.4: the revision the session used, so a consumer can tell that a
        # pool's definition changed under a conversation already in progress.
        pool_revision=pool.revision if pool else "",
        profile=decision.pool_id or "",
    )


def _policy_for(request: Request, payload: dict[str, Any]) -> RoutingPolicy:
    """The policy governing this request (§9.6).

    Two sources, combined by `effective_policy`: what the operator configured
    for this application id, and what the request itself declared. The identity
    is read from request state rather than from the payload — §9.6.0 is explicit
    that a claimed identity is never accepted, and this is one of the places
    where reading `payload["application"]` would look natural and be a hole.

    Falls back to `anonymous`'s posture when no identity was resolved: absent is
    a domain value here, and the least-privileged answer is the safe one.
    """
    identity = getattr(request.state, "identity", None)
    policies: ApplicationPolicies = getattr(
        request.app.state, "policies", ApplicationPolicies()
    )
    configured = policies.for_application(
        identity.application_id if identity else "anonymous"
    )
    # §14's band, computed from what RAVIS has observed itself spending. It
    # rides on the policy because both are hard constraints applied in one
    # place — see `RoutingPolicy.budget_band`.
    budget = getattr(request.app.state, "budget", None)
    ledger = getattr(request.app.state, "usage_ledger", None)
    band = BudgetBand.NORMAL
    if budget is not None and ledger is not None:
        band, _spent, _unpriced = band_for(budget, ledger, time.time())
    return effective_policy(
        configured,
        metadata=payload.get("metadata") or {},
        may_declare_background=bool(identity and identity.may_declare_background_calls),
        ceiling=identity.max_privacy_level if identity else PrivacyLevel.NORMAL,
        budget_band=band,
        budget_hard=bool(budget and budget.hard),
    )


def _exploration(payload: dict[str, Any]) -> Exploration | None:
    """Whether this request may answer with something other than the best pick.

    **Opt-in, per request, and off by default.** Exploration costs the person
    asking a worse answer some of the time, so nothing here may switch it on by
    inference -- it is a choice somebody makes in chat's Model settings, and it
    travels on the request that choice applies to.

    This is also the one place the dice are thrown. The routing engine is a pure
    function of its arguments and §9.7 gates that; the API layer already does
    I/O and holds no such promise, so the randomness lives here and the engine
    receives a number. See `Exploration`.

    `rate` is accepted from the caller but clamped: a client asking to explore
    on every single request has almost certainly made a mistake, and half is
    already far more than anyone wants in a conversation.
    """
    if not payload.get("explore"):
        return None
    asked = payload.get("explore_rate")
    try:
        rate = float(asked) if asked is not None else DEFAULT_EXPLORATION_RATE
    except (TypeError, ValueError):
        rate = DEFAULT_EXPLORATION_RATE
    return Exploration(
        rate=min(max(rate, 0.0), 0.5),
        roll=random.random(),
        # Defaults true because it is the useful half: a model with no timing at
        # all is the one the ranking can never reach on merit. Explicitly false
        # means "spread the sampling evenly", which keeps existing figures
        # current instead.
        prefer_unmeasured=payload.get("explore_prefer_unmeasured") is not False,
    )


def _chain_for(request: Request, decision: RouteDecision) -> AttemptChain:
    """Build the attempt chain this request will walk (§10).

    The candidates come from the router and are never widened here. That is
    what satisfies §10's requirement that every fallback still meet the original
    hard constraints and the pool invariants: this code cannot add a candidate
    the eligibility filter did not already pass.
    """
    chain = AttemptChain(
        health=request.app.state.health,
        provider=_provider_of(request),
        budget=request.app.state.retry_budget,
        observations=getattr(request.app.state, "observations", None),
        from_pool=decision.pool_id is not None,
        # Set by `_route` from the normalised request. Only a request that
        # actually carried tools may arm a tool-refusal suppression.
        carries_tools=bool(getattr(request.state, "carries_tools", False)),
    )
    chain.load(decision.selected or "", decision.fallbacks)
    return chain


def _is_openai(url: str) -> bool:
    """Whether an attempt goes to OpenAI itself, not to an upstream speaking its protocol."""
    return httpx.URL(url).host == "api.openai.com"


def _for_openai(body: bytes) -> bytes:
    """`max_tokens`, spelled the way OpenAI's current models require.

    **OpenAI renamed the field, and its newer models refuse the old name.** Found
    live, 11 September 2026: `gpt-5.6-sol` answered a NERVIS chat turn with
    "Unsupported parameter: 'max_tokens' is not supported with this model. Use
    'max_completion_tokens' instead." OpenAI accepts the new name on its chat
    models, so for OpenAI the old one is renamed. DeepSeek, xAI, OpenRouter and
    the local runtimes speak this protocol too and are left alone — not all of
    them know the new name. A client that already sent it keeps what it sent.
    """
    try:
        payload = json.loads(body)
    except ValueError:
        return body
    if not isinstance(payload, dict) or "max_completion_tokens" in payload:
        return body
    if "max_tokens" not in payload:
        return body
    payload["max_completion_tokens"] = payload.pop("max_tokens")
    return json.dumps(payload).encode()


def _with_model(body: bytes, payload: dict[str, Any], model: str,
                ttl_seconds: int = 0) -> bytes:
    """Rewrite the `model` field — and, for a local runtime, how long it stays.

    This is the one place the transparent path modifies what the client sent,
    and the asymmetry is deliberate. A pool ID is not a model any upstream
    knows, so resolving it means the substitution has to happen somewhere — and
    §6's "minimal safe forwarding" is satisfied by changing one field rather
    than by normalising and rebuilding the request. When the field already says
    what it should, the client's original bytes are forwarded verbatim.

    **`ttl` is the second field, and only ever for a local runtime.** Left
    alone, LM Studio holds a model until something evicts it, so residency
    accumulates and then decides routing — a machine with 19% memory free
    prefers whatever is already warm, and the pool's order stops applying. The
    runtime has its own mechanism for this and takes `ttl` on the request, so
    RAVIS names a number rather than running an eviction loop of its own.

    **A client that set `ttl` itself keeps it.** This is a default for requests
    that expressed no opinion, not an override of one that did — the same rule
    the `model` field follows one line up.

    Note what is *not* symmetric: the response stream is never rewritten. §8.3's
    release-critical surfaces — tool-call indexes, fragmented arguments, finish
    reasons, `[DONE]` — are all downstream, and none of them are touched.
    """
    wants_ttl = ttl_seconds > 0 and "ttl" not in payload
    if model == payload.get("model") and not wants_ttl:
        return body
    rewritten = dict(payload)
    rewritten["model"] = model
    if wants_ttl:
        rewritten["ttl"] = ttl_seconds
    return json.dumps(rewritten).encode()


def _no_route(decision: RouteDecision) -> JSONResponse:
    """A no-route is a first-class, explainable outcome (§9.2, §9.7).

    Not a 500: nothing failed. The request was understood and refused, because
    no available model satisfies what was asked for — and §9.2 forbids relaxing
    a constraint to find something that fits. 422 says the request was
    well-formed but cannot be acted on, which is exactly the situation.

    **Except while the circuits are merely resting.** A pool whose every
    candidate is in cooldown is not a pool nothing satisfies — it is the same
    pool a minute from now, and 503 is what says so. The distinction is not
    cosmetic: Clarvis's §8.7 tool probe reads a 4xx as the model answering and
    caches that answer for the session, so returning 422 here teaches it that a
    perfectly capable model cannot call tools, and it keeps believing that long
    after the circuit closes.
    """
    resting = decision.blocked_only_by_circuits
    return JSONResponse(
        status_code=503 if resting else 422,
        content={
            "error": {
                "message": decision.reason,
                "type": "no_route",
                "param": "model",
                "code": "no_route",
                "route_decision": decision.as_dict(),
            }
        },
    )


async def _forward_and_return(call: _Call) -> Response:
    """The non-streaming path: try each candidate until one answers.

    The upstream's status code travels with the body. An upstream 429 must reach
    the client as a 429 — turning it into a 500 would tell the client to give up
    where it should have retried — and that stays true of the *last* attempt's
    response when the chain is exhausted, which is why the response is held
    rather than discarded when a fallback is about to be tried.
    """
    last: httpx.Response | None = None
    # Whether `last` is a success status that RAVIS has established is not a
    # success. Forwarding it verbatim is what made this gate a no-op for the
    # ordinary case: with one eligible candidate, or with every candidate
    # refusing the same way, the client still received the 200 — and Clarvis's
    # §8.7 probe still read it as "this model supports tools".
    misleading = False
    while (model := call.chain.next_target()) is not None:
        started = call.chain.begin(model)
        try:
            upstream_response = await call.client.post(
                call.target_for(model),
                headers=call.headers_for(model),
                content=call.body_for(model),
            )
        except httpx.HTTPError as failure:
            call.chain.failed(model, started, classify_exception(failure), str(failure))
            continue
        failure_class = classify_response(
            upstream_response.status_code, upstream_response.content
        )
        misleading = False
        if failure_class is None:
            # The same trap as on the streaming path, and the more dangerous
            # half: Clarvis's §8.7 tool probe comes through here, and it reads
            # any 2xx as "this model supports tools" — so a 200 carrying an
            # error object would teach it the opposite of the truth and be
            # cached for the session.
            failure_class = _refusal_class(upstream_response.content)
            misleading = failure_class is not None
        if failure_class is None:
            call.chain.succeeded(model, started)
            # **The fourth success point, and the one that recorded nothing.**
            # `note_usage` is called from the two streaming paths and from
            # translated non-streaming; transparent non-streaming -- an ordinary
            # completion with `stream` omitted -- returned here with the
            # upstream's own `usage` object sitting in the body it was about to
            # forward, and discarded it. Every such call was invisible to §14's
            # ledger: absent from `/api/v1/usage`, absent from the spend the
            # budget bands enforce, and absent from the per-call table. The
            # mirror image of the M15 Path B bug, on the path nothing covered.
            call.note_usage(
                model,
                _usage_in_body(upstream_response.content),
                (call.chain.now() - started) * 1000,
            )
            call.finish()
            return _passthrough(upstream_response)
        last = upstream_response
        # The upstream's own sentence, not just its status. Once a fallback
        # succeeds, this attempt record is the only place the refusal survives —
        # it used to say `HTTP 400` and nothing about why, which is what
        # `_refuse` already fixed on the streaming path. `AttemptChain.failed`
        # strips anything credential-shaped before keeping it.
        call.chain.failed(
            model, started, failure_class,
            f"HTTP {upstream_response.status_code}: {_message_of(upstream_response.content)}",
        )
    call.finish()
    _log_exhaustion(call.chain)
    return _exhausted_response(call, last, misleading)


def _exhausted_response(
    call: _Call, last: httpx.Response | None, misleading: bool
) -> Response:
    """The answer when no candidate worked, with a status that means something.

    An upstream 429 still arrives as a 429 — turning it into a 500 would tell
    the client to give up where it should have retried. But a **2xx carrying an
    error object** must not be forwarded as-is: RAVIS has already decided it is
    not an answer, and passing the status through would leave the record and the
    response contradicting each other. The upstream's own words are kept; only
    the status it chose is overruled.

    **A pool whose chain ended on a tool refusal is resting, not incapable.**
    Each refusal armed a suppression that lifts on its own, so every request
    after this one gets a 503 no-route until it does — and this request answers
    the same way rather than forwarding the last 4xx, so one pool state never
    produces two statuses. It matters because Clarvis's §8.7 probe caches any
    4xx for the whole session as "this pool cannot call tools" and never
    remembers a 5xx. The retry budget can also end the chain with a capable
    model still untried, and a 4xx would then be the pool appearing incapable
    when it is not — the one outcome §8.7 names. A directly named model is not
    a pool: its refusal is the answer to what was asked, and goes through as is.
    """
    if last is None:
        return _chain_exhausted(call.chain)
    if call.chain.from_pool and call.chain.last_class is FailureClass.TOOL_INCOMPATIBILITY:
        return _overruled(last, 503)
    # The same substitution the streaming path makes, and for the same reason:
    # an upstream describing its own state is not an answer to "why did this
    # model not work". Only when every attempt has failed, so no working route
    # is affected.
    explained = _unlisted_body(call, last.content)
    if explained != last.content:
        return Response(content=explained, status_code=404, media_type="application/json")
    if not misleading:
        return _passthrough(last)
    return _overruled(last, 502)


def _overruled(last: httpx.Response, status: int) -> Response:
    """The upstream's own words, under a status RAVIS has decided on instead."""
    return Response(
        content=last.content,
        status_code=status,
        headers=_forwardable_response_headers(last),
        media_type=last.headers.get("content-type"),
    )


def _passthrough(upstream_response: httpx.Response) -> Response:
    """The upstream's answer, forwarded whole — status, headers and body."""
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=_forwardable_response_headers(upstream_response),
        media_type=upstream_response.headers.get("content-type"),
    )


def _stream_from_upstream(call: _Call) -> StreamingResponse:
    """The streaming path: raw bytes out as raw bytes arrive.

    Nothing here inspects a frame. `[DONE]` is correct because the upstream's
    `[DONE]` is forwarded verbatim, tool-call fragments survive because they are
    never touched, and there is no buffering because each chunk is yielded the
    moment it lands.
    """
    return StreamingResponse(
        _relay(call),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Without this a reverse proxy may buffer the whole stream and
            # deliver it at the end, which looks exactly like the model being
            # slow and is the bug this header exists to prevent.
            "X-Accel-Buffering": "no",
        },
    )


async def _relay(call: _Call) -> AsyncGenerator[bytes, None]:
    """Walk the fallback chain, yielding the first stream that starts.

    Typed as a generator rather than an iterator because `aclose()` is part of
    the contract, not an implementation detail: closing this is exactly how a
    client disconnect becomes an upstream cancellation (§8.6), and an
    `AsyncIterator` makes no promise that it can be closed.

    **Cancellation is not a retry (§10).** A disconnect raises `GeneratorExit`
    here and `CancelledError` in the task; neither is an `Exception`, so neither
    is caught by anything below, and both unwind straight out of this loop
    without ever reaching `chain.failed`. The loop cannot restart what it never
    learns about. That is deliberate, and it is why nothing in this module
    catches `BaseException`.
    """
    last_error: bytes | None = None
    attempted = ""
    while (model := call.chain.next_target()) is not None:
        attempted = model
        try:
            # `aclosing` rather than a bare `async for`, and the difference is
            # the whole of §8.6. When this generator is closed mid-stream, the
            # inner one must be closed *now* — that is what unwinds its
            # `async with client.stream(...)` and tears down the upstream
            # connection, so the model stops generating. Left to the garbage
            # collector, the upstream would keep producing tokens nobody reads
            # and, on a paid provider, nobody should be billed for.
            async with aclosing(_attempt_stream(call, model)) as attempt:
                async for chunk in attempt:
                    yield chunk
            call.finish()
            return
        except _TryNext as retry:
            last_error = retry.upstream_error or last_error
        except (GeneratorExit, asyncio.CancelledError):
            # The client went away. Recorded and re-raised, never handled: §10
            # says cancellation is not a retry, and swallowing it here would
            # both hide the disconnect and break the §8.6 propagation that
            # stops the upstream generating.
            #
            # Neither call awaits, which matters — an async generator that
            # awaits after catching `GeneratorExit` raises RuntimeError instead
            # of closing. Without this the decision stayed `execution: null`
            # forever, indistinguishable from a request still in flight, which
            # is the one thing a person watching a dashboard needs to tell
            # apart from "the user pressed Stop".
            call.chain.cancelled(attempted)
            call.finish()
            raise
    call.finish()
    _log_exhaustion(call.chain)
    yield _sse_error(_unlisted_body(call, last_error or _exhausted_body(call.chain)))


async def _attempt_stream(call: _Call, model: str) -> AsyncGenerator[bytes, None]:
    """One attempt against one model, as a stream of bytes.

    Two outcomes only, and the difference is the first yield:

    - **Nothing sent yet** — raise `_TryNext`, and the chain may try another
      candidate. Nothing has reached the client, so nothing can be corrupted.
    - **Already sending** — finish the stream with an error frame and a
      `[DONE]`, and record a stream interruption. §10's fallback is unavailable
      by then: a second model's answer appended to a first model's half-answer
      is not a recovery, it is a corrupted response that the client would parse
      as one message.

    Cancellation is a third outcome that this function deliberately does not
    handle. The `async with` closes the upstream connection as the exception
    unwinds, which is the propagation §8.6 requires.
    """
    started = call.chain.begin(model)
    committed = False
    # §14: the usage frame, if this provider sends one. None until it does, and
    # None afterwards for a provider that never does — which is a fact about the
    # provider rather than a zero-token call.
    reported: Usage | None = None
    try:
        async with call.client.stream(
            "POST",
            call.target_for(model),
            headers=call.headers_for(model),
            content=call.body_for(model),
        ) as upstream:
            if upstream.status_code >= 400:
                # An error before the stream begins is a normal response, not a
                # stream: read it whole, classify it, and either move on or pass
                # the upstream's own error object through — it is already in the
                # shape clients parse.
                _refuse(call, model, started, upstream.status_code, await upstream.aread())
            carried = b""
            async for chunk in upstream.aiter_bytes():
                if not committed:
                    # The last moment a different model can still be chosen.
                    # Looked at rather than buffered: this chunk is already in
                    # hand and is forwarded immediately afterwards, so the
                    # inspection costs no latency and holds nothing back.
                    _commit_or_refuse(call, model, started, chunk)
                    committed = True
                # §14's token counts, read on the way past. The chunk is yielded
                # unchanged on the next line, so §6's byte-for-byte guarantee is
                # untouched — this looks, and never rewrites.
                #
                # **Read across the chunk boundary, not within one chunk.** HTTP
                # chunking has nothing to do with SSE framing, so a usage frame
                # can arrive split in two. Each half then fails the `"usage"`
                # substring test, both are skipped, and the call is recorded
                # with no token counts at all — priced as UNKNOWN, which a
                # budget reads as nothing spent. Nothing announces it; the
                # figures are simply lower than the provider's own.
                #
                # `carried` holds only the bytes after the last complete frame,
                # so a frame is inspected exactly once: complete frames are
                # dropped from it in the same pass that reads them. It is an
                # inspection buffer and never reaches the client — `chunk` is
                # still yielded whole and unaltered below.
                #
                # Latest wins, for the reason the translated path documents: a
                # provider that reports usage on every frame reports it growing.
                combined = carried + chunk
                seen = _usage_in(combined)
                if seen is not None:
                    reported = seen
                carried = _carry_over(combined)
                yield chunk
    except httpx.HTTPError as failure:
        yield _stream_failed(call, model, started, failure, committed)
        return
    # **Written after the stream, and only for one that produced something.** A
    # stream that never committed is a non-answer (see below) and recording
    # usage for it would put a priced row against a call the client never got.
    if committed:
        call.note_usage(model, reported, (call.chain.now() - started) * 1000)
    if not committed:
        # A stream that ended without a single byte. This used to be recorded as
        # a *success*, on the reasoning that the upstream answered and simply
        # had nothing to say — but an SSE response with no frames is not an
        # empty answer, it is a non-answer: the client is left waiting for a
        # `[DONE]` that never comes, and the chain that could have tried another
        # model has already been told everything went well.
        #
        # Not to be confused with a model that generates no *content*. A
        # reasoning model can spend its whole budget thinking and legitimately
        # return empty content — but it still emits frames. Zero bytes is a
        # different thing, and it is never valid.
        call.chain.failed(
            model, started, FailureClass.INVALID_UPSTREAM_RESPONSE,
            "the upstream closed the stream without sending a byte",
        )
        raise _TryNext(json.dumps(_error_body(
            "the upstream closed the stream without sending a byte", "upstream_error"
        )).encode())


def _commit_or_refuse(call: _Call, model: str, started: float, chunk: bytes) -> None:
    """Credit the attempt, unless the first chunk shows it is not an answer.

    This is the entire pre-commit gate, and it sits at the only point where it
    can: `committed` closes the switching window one statement later, and after
    that the client holds bytes and a fallback would append a second answer to a
    partial first one.

    Two shapes are refused, both observed rather than imagined:

    - **A 200 whose body is an error object.** The configured upstream answers
      exactly this way for anything it will not serve, and RAVIS branched only
      on `status >= 400` — so a request the runtime refused was forwarded to the
      client as a successful stream and recorded as a working attempt.
    - **A 200 whose first SSE frame carries an error.** The same refusal arriving
      inside the protocol rather than instead of it.

    Anything that does not parse is left alone. A partial frame, an unusual
    keep-alive, a provider doing something unanticipated — none of those are
    evidence of a refusal, and guessing would abandon a model that was about to
    answer perfectly well.
    """
    refusal = _refusal_in(chunk)
    if refusal is None:
        call.chain.succeeded(model, started, ttft=call.chain.now() - started)
        return
    # The error's own words beside the status, for the reason the non-streaming
    # path keeps them: after a fallback succeeds, this record is the only place
    # the refusal is explained, and it becomes a suppression's reason.
    call.chain.failed(model, started, classify_error_body(refusal),
                      f"HTTP 200 carrying an error: {_message_of(refusal)}")
    raise _TryNext(refusal)


def _refusal_class(body: bytes) -> FailureClass | None:
    """The failure a success-status body admits to, or None if it admits none.

    Shared with the streaming gate deliberately: one definition of "this looks
    like a refusal", so the two paths cannot drift into disagreeing about the
    same upstream.
    """
    refusal = _refusal_in(body)
    return classify_error_body(refusal) if refusal is not None else None


def _refusal_in(chunk: bytes) -> bytes | None:
    """The error object inside a success, or None for an ordinary stream frame.

    Deliberately not an SSE parser. Only the first frame can matter — after it
    the window is closed — so this reads one payload and gives up on anything it
    cannot read, which is what keeps a false positive from costing a working
    model its turn.
    """
    for line in chunk.split(b"\n"):
        candidate = line.strip()
        # Blank separators, keep-alive comments (`: ping`) and the other SSE
        # fields legitimately precede the payload. Skipping them is what stops
        # the gate being defeated by punctuation a provider sent for its own
        # reasons — the first version read only the very first line and missed
        # a refusal that arrived one comment later.
        if not candidate or candidate.startswith(b":") or candidate.startswith(_SSE_FIELDS):
            continue
        if candidate.startswith(b"data:"):
            candidate = candidate[len(b"data:"):].strip()
        return _error_object(candidate)
    return None


def _error_object(text: bytes) -> bytes | None:
    """The bytes of an error object, or None for anything else.

    Returned verbatim rather than reshaped. It is the upstream's own words, and
    `_refuse` forwards a >=400 body the same way — normalising here would invent
    an error message the provider never produced.
    """
    if not text.startswith(b"{"):
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        # A partial frame, split at an arbitrary byte boundary, is not evidence
        # of anything. `UnicodeDecodeError` is a `ValueError`, so a chunk cut
        # mid-character lands here too.
        return None
    # Truthiness, not presence. Some proxies include `"error": null` on a
    # perfectly good response, and reading that as a refusal would cost a
    # working model its turn for saying nothing went wrong.
    if not isinstance(payload, dict) or not payload.get("error"):
        return None
    return text


def _refuse(call: _Call, model: str, started: float, status: int, detail: bytes) -> None:
    """Record a pre-stream HTTP error and hand control back to the chain.

    Always raises. Returning a value would leave the caller with a branch that
    has to remember to stop reading the response body, and forgetting it would
    mean yielding an error frame *and* a stream.
    """
    failure_class = classify_response(status, detail) or FailureClass.UNKNOWN
    call.chain.failed(model, started, failure_class, f"HTTP {status}: {_message_of(detail)}")
    raise _TryNext(detail)


def _message_of(body: bytes) -> str:
    """An upstream error's own sentence, short, for the attempt record.

    **The reason, not just the status.** The log said `gpt-5.6-sol
    (invalid_request)` and nothing else, so why OpenAI refused could only be
    found by spending a request to reproduce it. The error's message is neither
    a prompt nor a completion, which is what `Attempt` keeps out.
    """
    try:
        found = json.loads(body)
    except ValueError:
        found = None
    error = found.get("error") if isinstance(found, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    text = str(message) if message else body.decode("utf-8", "replace")
    return " ".join(text.split())[:200]


def _stream_failed(
    call: _Call, model: str, started: float, failure: httpx.HTTPError, committed: bool
) -> bytes:
    """Turn a transport failure into either a retry or a terminated stream."""
    if not committed:
        call.chain.failed(model, started, classify_exception(failure), str(failure))
        raise _TryNext
    logger.warning("upstream stream interrupted", extra={"detail": str(failure)})
    call.chain.interrupted(model)
    return _sse_error(json.dumps(_error_body(str(failure), "upstream_error")).encode())


def _sse_error(detail: bytes) -> bytes:
    """Wrap an error body as a single SSE frame, terminated properly.

    A stream that stops without `[DONE]` leaves a client waiting for more, so
    even a failure ends the stream the way the protocol says to end it.

    **One line, whatever the upstream sent.** An SSE `data:` field ends at a
    newline, and OpenAI pretty-prints its error bodies — eight newlines in a
    plain 401. Wrapped as they came, only `data: {` reached the client, no frame
    parsed, NERVIS showed "the model returned an empty message" and stored an
    empty reply, for a request OpenAI had refused in plain words (11 September
    2026). JSON is re-serialised compactly; anything else has its breaks folded.
    """
    text = detail.strip()
    try:
        text = json.dumps(json.loads(text)).encode()
    except ValueError:
        text = b" ".join(text.split())
    return b"data: " + text + b"\n\ndata: [DONE]\n\n"


def _exhausted_body(chain: AttemptChain) -> bytes:
    return error_body(chain.exhausted_message(), chain.last_class, chain.summary())


#: How RAVIS explains a model no upstream lists.
#:
#: **Only ever reached once every attempt has failed**, so this cannot take a
#: working route away from anybody. What it replaces is an upstream's truthful
#: description of *itself*, forwarded verbatim as the design says to and useless
#: as an answer to the question asked: a retired model id, or a typo, went to
#: whichever upstream happened to be the default — the local runtime, on this
#: machine — and came back *"No models loaded. Please load a model in the
#: developer page or use the `lms load` command."*
#:
#: Nothing was wrong with LM Studio and loading a model would not have helped.
_UNLISTED = (
    "No upstream lists {model!r}, so RAVIS had to guess where to send it and the "
    "guess failed. Check the id against /v1/models, or name the provider "
    "yourself with ravis/<provider>/{model}. The upstream that was tried "
    "answered: {detail}"
)


def _unlisted_body(call: _Call, fallback: bytes) -> bytes:
    """`fallback`, unless the model was absent from a catalogue that exists.

    An empty catalogue is not evidence of absence — at startup, or when every
    listing failed, RAVIS knows nothing yet — so the note is only added when
    there is a catalogue to be missing from.
    """
    requested = str(call.payload.get("model") or "")
    catalogue = getattr(call, "catalogue", None) or ()
    # A pool id and a `ravis/<provider>/<model>` address are never in the
    # catalogue and are not supposed to be — the first names a set to choose
    # from, the second names a provider. Only a bare model name can be missing
    # from it, and forgetting that turned every exhausted pool into "no upstream
    # lists 'ravis/clarvis-agent'", which is true and not the problem.
    if requested.startswith(POOL_PREFIX):
        return fallback
    if not requested or not catalogue or requested in catalogue:
        return fallback
    try:
        detail = json.loads(fallback).get("error", {}).get("message") or ""
    except (ValueError, AttributeError):
        detail = ""
    return error_body(
        _UNLISTED.format(model=requested, detail=str(detail)[:200]),
        FailureClass.MODEL_UNAVAILABLE,
        call.chain.summary(),
    )


def _chain_exhausted(chain: AttemptChain) -> JSONResponse:
    """Nothing answered, and no upstream response exists to forward.

    503 rather than 502 when no attempt was even made: every candidate was
    behind an open circuit, which means RAVIS declined to try rather than tried
    and failed. The distinction is the difference between "your upstream is
    broken" and "your upstream was already known to be broken, come back in
    thirty seconds".
    """
    # **A client error stays a client error, however many candidates saw it.**
    # `max_tokens: "lots"` is forwarded — a transparent proxy does not own the
    # upstream's schema — and Anthropic answers *400: max_tokens: Input should be
    # a valid integer*. Every candidate then refuses it for the same reason, the
    # chain exhausts, and reporting 502 tells the caller their upstream is broken
    # when it worked perfectly and said exactly what was wrong. 5xx here would
    # also invite a retry that cannot succeed.
    if chain.last_class is FailureClass.INVALID_REQUEST:
        return JSONResponse(
            status_code=400, content=json.loads(_exhausted_body(chain))
        )
    tried_something = any(attempt.outcome != "skipped" for attempt in chain.attempts)
    return JSONResponse(
        status_code=502 if tried_something else 503,
        content=json.loads(_exhausted_body(chain)),
    )


def _log_exhaustion(chain: AttemptChain) -> None:
    """Say once, in the log, that every candidate failed.

    Worth a line of its own because the client only ever sees the *last*
    failure, and "every model in the pool is down" and "this one model is down"
    look identical from there.
    """
    logger.warning(
        "no upstream attempt succeeded",
        extra={"detail": chain.exhausted_message()},
    )


def _forwardable_response_headers(response: httpx.Response) -> dict[str, str]:
    return {
        name: value
        for name, value in response.headers.items()
        if name.lower() not in SKIPPED_RESPONSE_HEADERS
    }


def _error_body(message: str, error_type: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": error_type, "param": None, "code": error_type}}


def _openai_error(message: str, error_type: str, status: int) -> JSONResponse:
    """An error in OpenAI's shape, because /v1 clients parse it (§4.5)."""
    return JSONResponse(status_code=status, content=_error_body(message, error_type))

async def _translated_relay(
    call: _Call,
    adapter: TranslatingAdapter,
    request: NormalizedRequest,
    model: str,
    completion_id: str,
    started: float,
) -> AsyncGenerator[bytes, None]:
    """Path B's stream, at module level rather than nested inside `_translated`.

    **Extracted because ruff charges a nested `def`'s whole complexity to its
    parent.** `_translated` measured 8 of 8 with only two points of its own; the
    other six were this closure. The trap that makes it worth fixing is not the
    number: editing *this* code failed the build with an error citing
    `_translated` at a line the developer had not touched, which is the least
    actionable form a complexity error can take.
    """
    committed = False
    try:
        async with aclosing(adapter.stream(request)) as events:
            # §14: recorded when the stream ends, and only from here, so a
            # translated call is counted exactly once like a transparent one.
            def _note(reported: Usage | None) -> None:
                call.note_usage(model, reported, (call.chain.now() - started) * 1000)

            # **Two boundaries, and conflating them was the bug.** `committed`
            # is about bytes: once one frame has left, no other model can be
            # chosen and a failure can no longer be retried. The *timing* is
            # about the provider: it is only known once the provider has sent
            # something. The first yielded frame satisfies the first and not the
            # second, because RAVIS builds that frame itself.
            #
            # Recording success there also credited a call that produced no
            # token at all: a provider failing after the opening frame was
            # logged as succeeded *and* failed, and the circuit breaker was
            # reading the first of those.
            def _answered() -> None:
                call.chain.succeeded(model, started, ttft=call.chain.now() - started)

            async for frame in _translated_frames(
                events, model, completion_id, _note, _answered
            ):
                committed = True
                yield frame
    except (GeneratorExit, asyncio.CancelledError):
        # §8.6 again, and for the same reason: a disconnect must reach the
        # adapter so the provider stops generating, and must never be
        # recorded as a failure.
        call.chain.cancelled(model)
        call.finish()
        raise
    except Exception as failure:  # noqa: BLE001 - an adapter is third-party code
        if committed:
            call.chain.interrupted(model)
        else:
            call.chain.failed(model, started, _adapter_failure(failure), str(failure))
        # A refused translation is the client's request being wrong, and
        # says so even here — the status line was committed the moment the
        # stream opened, so the error type in the frame is all that is left
        # to carry it.
        kind = (
            "invalid_request_error"
            if isinstance(failure, TranslationError)
            else "upstream_error"
        )
        yield _sse_error(json.dumps(_error_body(str(failure), kind)).encode())
    call.finish()
