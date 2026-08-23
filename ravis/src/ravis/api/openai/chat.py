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

import json
import logging
from contextlib import aclosing
from typing import Any, AsyncGenerator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ravis.api.management.decisions import RecordedDecision
from ravis.content import check_image_count
from ravis.core.requests import normalize
from ravis.providers.base import ProviderAdapter
from ravis.registry import ModelRegistry
from ravis.reliability import (
    AttemptChain,
    FailureClass,
    HealthRegistry,
    classify_exception,
    classify_response,
    error_body,
)
from ravis.routing.engine import RoutingEngine
from ravis.routing.explain import RouteDecision
from ravis.runtime.resources import read_memory
from ravis.upstream import forwardable_headers

logger = logging.getLogger(__name__)

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

    if parsed.get("stream") is True:
        return _stream_from_upstream(call)
    return await _forward_and_return(call)


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
    """
    return JSONResponse(
        status_code=422,
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
    return _passthrough(last) if last is not None else _chain_exhausted(call.chain)


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
    while (model := call.chain.next_target()) is not None:
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
                    committed = True
                    call.chain.succeeded(model, started, ttft=call.chain.now() - started)
                yield chunk
    except httpx.HTTPError as failure:
        yield _stream_failed(call, model, started, failure, committed)
        return
    if not committed:
        # A stream that ended without a single byte. Not an error at the
        # transport level, and not something to retry: the upstream answered,
        # it simply had nothing to say.
        call.chain.succeeded(model, started)


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
