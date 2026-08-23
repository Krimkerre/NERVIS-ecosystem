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
import uuid
from contextlib import aclosing
from typing import Any, AsyncGenerator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ravis.api.management.decisions import RecordedDecision
from ravis.api.openai.serialize import DONE, completion, frame_for, opening_frame
from ravis.content import check_image_count
from ravis.core.pools import direct_provider
from ravis.core.requests import NormalizedRequest, normalize
from ravis.core.responses import NormalizedStreamEvent
from ravis.providers.base import ProviderAdapter, TranslatingAdapter
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
from ravis.routing.engine import RoutingEngine
from ravis.routing.explain import RouteDecision
from ravis.runtime.resources import read_memory
from ravis.upstream import forwardable_headers

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

# The health-tracking name of the one upstream this build talks to. A stable
# label rather than the URL, deliberately: health snapshots reach diagnostics
# and route explanations, and §9.7 keeps internal URLs out of both. M8 makes
# this plural, at which point the adapter supplies the name.
UPSTREAM_PROVIDER = "upstream"


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
    upstream = request.app.state.upstream
    if not upstream.is_configured:
        return _openai_error(
            "No upstream is configured. Set RAVIS_UPSTREAM_BASE_URL.",
            "upstream_not_configured",
            503,
        )

    body = await request.body()
    parsed = _inspect(body, request)
    if isinstance(parsed, JSONResponse):
        return parsed

    decision = await _route(request, parsed, body)
    if not decision.routed:
        return _no_route(decision)

    chain = _chain_for(request, decision)
    call = _Call(
        client=request.app.state.upstream_client,
        target=upstream.url_for("/v1/chat/completions"),
        headers=forwardable_headers(dict(request.headers), upstream),
        body=body,
        payload=parsed,
        chain=chain,
        recorded=getattr(request.state, "recorded_decision", None),
    )

    # §6's fork, and the only place it is decided. A provider whose upstream
    # does not speak the external protocol needs Path B; everything else is
    # forwarded untouched, because §6 forbids normalising an already-compatible
    # stream for architectural purity.
    translating = _translating_for(request, parsed.get("model", ""))
    _record_path(call, TRANSLATED if translating else TRANSPARENT)
    if translating is not None:
        return await _translated(call, translating, decision)

    if parsed.get("stream") is True:
        return _stream_from_upstream(call)
    return await _forward_and_return(call)


def _translating_for(request: Request, requested: str) -> TranslatingAdapter | None:
    """The adapter that must translate this request, or None for Path A.

    Keyed on the provider segment of a direct address — `ravis/anthropic/…` —
    because that is the only part of a request that names an upstream. A pool
    resolves to a model rather than to a provider until the M8 provider table
    exists, so a pooled request cannot reach a translating adapter yet and is
    forwarded transparently. That is a real limit and not a silent one: it is
    why `execution_path` is recorded on every request rather than only on the
    interesting ones.
    """
    provider = direct_provider(requested)
    if provider is None:
        return None
    adapters: dict[str, TranslatingAdapter] = getattr(request.app.state, "translating", {})
    return adapters.get(provider)


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

    if call.payload.get("stream") is not True:
        return await _translated_completion(call, adapter, request, model, completion_id)

    started = call.chain.begin(model)

    async def relay() -> AsyncGenerator[bytes, None]:
        committed = False
        try:
            async with aclosing(adapter.stream(request)) as events:
                async for frame in _translated_frames(events, model, completion_id):
                    if not committed:
                        committed = True
                        call.chain.succeeded(model, started, ttft=call.chain.now() - started)
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
                call.chain.failed(model, started, FailureClass.UNKNOWN, str(failure))
            yield _sse_error(json.dumps(_error_body(str(failure), "upstream_error")).encode())
        call.finish()

    return StreamingResponse(relay(), media_type="text/event-stream",
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
    except Exception as failure:  # noqa: BLE001 - an adapter is third-party code
        call.chain.failed(model, started, FailureClass.UNKNOWN, str(failure))
        call.finish()
        return _chain_exhausted(call.chain)
    call.chain.succeeded(model, started)
    call.finish()
    return JSONResponse(completion(answer, model=model, completion_id=completion_id))


async def _translated_frames(
    events: AsyncGenerator[NormalizedStreamEvent, None], model: str, completion_id: str
) -> AsyncGenerator[bytes, None]:
    """The serializer's pieces, driven one event at a time.

    Event by event rather than collect-then-render, because a slow provider's
    first token has to reach the client when it arrives — buffering the stream
    to serialize it whole would turn Path B into the thing §6 warns against and
    would make every translated response feel broken.
    """
    yield opening_frame(model=model, completion_id=completion_id)
    started: set[int] = set()
    async for event in events:
        frame = frame_for(event, started, model=model, completion_id=completion_id)
        if frame is not None:
            yield frame
    yield DONE


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
        target: str,
        headers: dict[str, str],
        body: bytes,
        payload: dict[str, Any],
        chain: AttemptChain,
        recorded: RecordedDecision | None,
    ) -> None:
        self.client = client
        self.target = target
        self.headers = headers
        self.body = body
        self.payload = payload
        self.chain = chain
        self.recorded = recorded

    def body_for(self, model: str) -> bytes:
        """The request body addressed to one particular model."""
        return _with_model(self.body, self.payload, model)

    def finish(self) -> None:
        """Attach the attempt history to the recorded decision (§9.7).

        Called on every exit path, success or failure. A route explanation that
        shows what was chosen but not what happened when it was called is the
        half of the story that matters least during an incident.
        """
        if self.recorded is not None:
            self.recorded.attempts = self.chain.summary()


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

    messages = parsed.get("messages", [])
    if isinstance(messages, list):
        check_image_count(messages, request.app.state.settings)
    return parsed


async def _route(request: Request, payload: dict[str, Any], body: bytes) -> RouteDecision:
    """Resolve what the client addressed into a model to call (§9).

    Capabilities are assembled per request rather than cached. That is cheap
    today because the generic adapter performs no I/O to answer — it merges
    protocol defaults with operator configuration — and the moment an adapter
    needs a network call to answer, this is the line that has to change.
    """
    engine: RoutingEngine = request.app.state.routing_engine
    adapter: ProviderAdapter = request.app.state.adapter
    registry: ModelRegistry = request.app.state.model_registry
    health: HealthRegistry = request.app.state.health
    candidates = {model: await adapter.capabilities(model) for model in registry.model_ids()}
    decision = engine.select(
        payload.get("model") or "",
        candidates,
        residency=registry.residency,
        memory=read_memory(),
        # The request's own hard requirements (§9.5): a request carrying tools
        # or images demands a model that can handle them, whatever the pool's
        # static invariants say.
        request=normalize(body, payload),
        # Providers whose catalogue this engine cannot see. Their own model
        # list is the authority, so a direct address to one is not checked
        # against the local upstream's — see `_direct`.
        foreign_providers=frozenset(getattr(request.app.state, "translating", {})),
        # §10: do not keep routing to a failing provider. Models behind an open
        # circuit are excluded here, with the reason, rather than discovered
        # again by another request that pays another timeout to learn it.
        unavailable=health.unavailable(list(candidates), UPSTREAM_PROVIDER),
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
    )
    request.state.route_decision = decision
    request.state.decision_id = recorded.decision_id
    request.state.recorded_decision = recorded
    return decision


def _chain_for(request: Request, decision: RouteDecision) -> AttemptChain:
    """Build the attempt chain this request will walk (§10).

    The candidates come from the router and are never widened here. That is
    what satisfies §10's requirement that every fallback still meet the original
    hard constraints and the pool invariants: this code cannot add a candidate
    the eligibility filter did not already pass.
    """
    chain = AttemptChain(
        health=request.app.state.health,
        provider=UPSTREAM_PROVIDER,
        budget=request.app.state.retry_budget,
    )
    chain.load(decision.selected or "", decision.fallbacks)
    return chain


def _with_model(body: bytes, payload: dict[str, Any], model: str) -> bytes:
    """Rewrite only the `model` field, leaving the request otherwise untouched.

    This is the one place the transparent path modifies what the client sent,
    and the asymmetry is deliberate. A pool ID is not a model any upstream
    knows, so resolving it means the substitution has to happen somewhere — and
    §6's "minimal safe forwarding" is satisfied by changing one field rather
    than by normalising and rebuilding the request. When the field already says
    what it should, the client's original bytes are forwarded verbatim.

    Note what is *not* symmetric: the response stream is never rewritten. §8.3's
    release-critical surfaces — tool-call indexes, fragmented arguments, finish
    reasons, `[DONE]` — are all downstream, and none of them are touched.
    """
    if model == payload.get("model"):
        return body
    rewritten = dict(payload)
    rewritten["model"] = model
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
                call.target, headers=call.headers, content=call.body_for(model)
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
            call.finish()
            return _passthrough(upstream_response)
        last = upstream_response
        call.chain.failed(
            model, started, failure_class, f"HTTP {upstream_response.status_code}"
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
    """
    if last is None:
        return _chain_exhausted(call.chain)
    if not misleading:
        return _passthrough(last)
    return Response(
        content=last.content,
        status_code=502,
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
    yield _sse_error(last_error or _exhausted_body(call.chain))


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
    try:
        async with call.client.stream(
            "POST", call.target, headers=call.headers, content=call.body_for(model)
        ) as upstream:
            if upstream.status_code >= 400:
                # An error before the stream begins is a normal response, not a
                # stream: read it whole, classify it, and either move on or pass
                # the upstream's own error object through — it is already in the
                # shape clients parse.
                _refuse(call, model, started, upstream.status_code, await upstream.aread())
            async for chunk in upstream.aiter_bytes():
                if not committed:
                    # The last moment a different model can still be chosen.
                    # Looked at rather than buffered: this chunk is already in
                    # hand and is forwarded immediately afterwards, so the
                    # inspection costs no latency and holds nothing back.
                    _commit_or_refuse(call, model, started, chunk)
                    committed = True
                yield chunk
    except httpx.HTTPError as failure:
        yield _stream_failed(call, model, started, failure, committed)
        return
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
    call.chain.failed(model, started, classify_error_body(refusal),
                      "HTTP 200 carrying an error")
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
    call.chain.failed(model, started, failure_class, f"HTTP {status}")
    raise _TryNext(detail)


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
    """
    return b"data: " + detail.strip() + b"\n\ndata: [DONE]\n\n"


def _exhausted_body(chain: AttemptChain) -> bytes:
    return error_body(chain.exhausted_message(), chain.last_class, chain.summary())


def _chain_exhausted(chain: AttemptChain) -> JSONResponse:
    """Nothing answered, and no upstream response exists to forward.

    503 rather than 502 when no attempt was even made: every candidate was
    behind an open circuit, which means RAVIS declined to try rather than tried
    and failed. The distinction is the difference between "your upstream is
    broken" and "your upstream was already known to be broken, come back in
    thirty seconds".
    """
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
