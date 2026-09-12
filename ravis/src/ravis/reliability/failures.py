"""Classifying what went wrong, and what that permits (RAVIS.md §10).

§10 lists eleven failure classes and three rules about them, and the rules are
what this module exists to encode:

- **Client cancellation is not a retry.** It is deliberately *absent* from the
  enum below. A cancelled request never reaches a classifier, because catching
  the cancellation in order to name it is the exact bug the rule warns about —
  see `attempts.py`, which lets `GeneratorExit` and `CancelledError` through
  untouched.
- **Do not route around a safety refusal** merely to find a more permissive
  provider. `CONTENT_REFUSAL` therefore permits no fallback at all.
- **Every fallback candidate must still satisfy the original hard constraints.**
  That is the router's job rather than this module's; here it only decides
  whether a next candidate may be *tried*.

The classification is not decoration. Each class carries a `FailurePolicy` that
answers three questions — retry the same target? try the next candidate? whose
health does this reflect? — and those three answers are the whole of the retry
and fallback behaviour. Adding a class means deciding its policy, which is why
the policy lives next to the class rather than in a table somewhere else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx


class HealthScope(Enum):
    """Whose fault a failure implies, and therefore whose circuit it opens.

    The distinction earns its place because the two have different blast
    radii. A refused connection means *nothing* on that provider will work, so
    every model behind it should be skipped. A model that has been unloaded, or
    that OOMs on this machine, says nothing about the model beside it.

    `NONE` is not "we could not tell" — it is "this was not the provider's
    fault". A malformed request fails identically on every provider in the
    world, and counting it against provider health would open a circuit because
    a client sent bad JSON (runbook §14.4: absence is a value, and here the
    value is *no health signal*).
    """

    PROVIDER = "provider"
    MODEL = "model"
    NONE = "none"


@dataclass(frozen=True)
class FailurePolicy:
    """What a failure class permits.

    `retry_same_target` is reserved for failures that prove the request never
    reached the upstream. Anything that *may* have been received is not retried
    against the same target: a second completion is wasted compute at best, and
    on a paid provider it is a second bill for an answer nobody reads.
    """

    retry_same_target: bool
    may_fall_back: bool
    scope: HealthScope


class FailureClass(Enum):
    """§10's failure taxonomy, plus one honest admission.

    `UNKNOWN` is the admission: an upstream error this code cannot place. It
    permits neither retry nor fallback, which is deliberate — re-issuing a
    request whose failure mode is not understood is how one unexplained error
    becomes three.
    """

    TIMEOUT = "timeout"
    CONNECTION = "connection_failure"
    RATE_LIMIT = "rate_limit"
    OVERLOAD = "provider_overload"
    MODEL_UNAVAILABLE = "model_unavailable"
    LOCAL_OOM = "local_oom"
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION = "authentication"
    TOOL_INCOMPATIBILITY = "tool_incompatibility"
    CONTEXT_OVERFLOW = "context_overflow"
    CONTENT_REFUSAL = "content_refusal"
    # A request this particular model cannot take as written — a parameter it
    # does not accept, a value it does not allow. Not an invalid request: OpenAI
    # refused `gpt-5.6-sol` with "Unsupported parameter: 'max_tokens' is not
    # supported with this model" for a body Claude Haiku and Gemini would have
    # served (11 September 2026). The next candidate is a different model, which
    # is the whole reason it may be tried.
    UNSUPPORTED_PARAMETER = "unsupported_parameter"
    # A success status carrying something that is not a usable response: a 200
    # with an error object in its body, or a stream that closes without a single
    # byte. §10's list does not name this, and it is added rather than folded
    # into an existing class because the alternative is a label that is a guess
    # — the configured upstream on this machine answers 200 for endpoints it
    # does not implement (STATUS.md), so "the status said fine" is not evidence
    # that anything worked.
    INVALID_UPSTREAM_RESPONSE = "invalid_upstream_response"
    UNKNOWN = "unknown"

    @property
    def policy(self) -> FailurePolicy:
        return _POLICIES[self]


_POLICIES: dict[FailureClass, FailurePolicy] = {
    # The provider is reachable but not answering usefully. Nothing about the
    # request is wrong, so another candidate is worth trying; the same one is
    # not, because the request may already be running there.
    FailureClass.TIMEOUT: FailurePolicy(False, True, HealthScope.PROVIDER),
    FailureClass.RATE_LIMIT: FailurePolicy(False, True, HealthScope.PROVIDER),
    FailureClass.OVERLOAD: FailurePolicy(False, True, HealthScope.PROVIDER),
    # The only class that retries the same target: a connection that was never
    # established cannot have delivered the request, so a second attempt is
    # provably not a duplicate. Everything else moves on instead.
    FailureClass.CONNECTION: FailurePolicy(True, True, HealthScope.PROVIDER),
    # The provider is fine; this model is not. Scoping the circuit to the model
    # is what lets the pool's next candidate — on the same provider — be tried.
    FailureClass.MODEL_UNAVAILABLE: FailurePolicy(False, True, HealthScope.MODEL),
    FailureClass.LOCAL_OOM: FailurePolicy(False, True, HealthScope.MODEL),
    # A bad credential fails identically on every model behind that provider,
    # so retrying and falling back are both pointless. It is scoped NONE rather
    # than PROVIDER on purpose: opening the circuit would convert a fixable 401
    # — which names the problem — into a 422 no-route, which does not. The
    # health counters still record it; only the breaker ignores it.
    FailureClass.AUTHENTICATION: FailurePolicy(False, False, HealthScope.NONE),
    # The request itself is the problem. It will fail the same way everywhere,
    # and a fallback would only spend a second model's time to say so again.
    FailureClass.INVALID_REQUEST: FailurePolicy(False, False, HealthScope.NONE),
    # A model refusing tools is a fact about that model, not about the request.
    # It used to sit with INVALID_REQUEST above and stop the chain, but every
    # candidate the router offers a tools-bearing request is tool-capable by
    # construction, so the pool's next model very likely answers. Not retried
    # against the same target — it would refuse again — and no circuit: a MODEL
    # circuit has no capability in its key and would take the model out of
    # plain chat too, which runbook §2.1 forbids. What stops the router
    # re-picking the refuser on every request is a separate, time-boxed
    # (model, tools) suppression — armed by `AttemptChain.failed`, applied at
    # routing, and kept in `HealthRegistry` beside the breakers.
    FailureClass.TOOL_INCOMPATIBILITY: FailurePolicy(False, True, HealthScope.NONE),
    # One model's objection to a parameter is evidence about that model only, so
    # another candidate is worth trying. Not retried against the same target —
    # it would object again — and no circuit: the same model serves requests
    # without that parameter perfectly well.
    FailureClass.UNSUPPORTED_PARAMETER: FailurePolicy(False, True, HealthScope.NONE),
    # §10 says context is handled by *routing* to a larger-context model, or by
    # rejecting — and explicitly not by silent truncation. Pre-flight filtering
    # (M6) is where the routing happens; an overflow that survives it means the
    # estimate was wrong, and the fallback chain is ordered by pool preference
    # rather than by context size, so the next candidate is not known to be
    # larger. Rejecting says something true. Falling back would be a guess.
    FailureClass.CONTEXT_OVERFLOW: FailurePolicy(False, False, HealthScope.NONE),
    # §10, verbatim: do not route around a safety refusal merely to find a more
    # permissive provider. This line is that sentence.
    FailureClass.CONTENT_REFUSAL: FailurePolicy(False, False, HealthScope.NONE),
    # The upstream said 200 and delivered nothing usable. Falling back is right:
    # nothing about the *request* has been shown to be wrong, and the next
    # candidate is a different model behind the same provider — which is also
    # why the circuit is scoped to the model. Not retried against the same
    # target: the request plainly arrived, since something came back.
    FailureClass.INVALID_UPSTREAM_RESPONSE: FailurePolicy(False, True, HealthScope.MODEL),
    FailureClass.UNKNOWN: FailurePolicy(False, False, HealthScope.NONE),
}

# Status codes whose meaning is unambiguous without reading the body.
#: Statuses whose meaning the body may not override. Both say the provider
#: read the request and refused the caller, which is a fact about the
#: credential rather than about the model, the prompt or the moment.
_CREDENTIAL_STATUSES = frozenset({401, 403})

_STATUS_CLASSES: dict[int, FailureClass] = {
    401: FailureClass.AUTHENTICATION,
    403: FailureClass.AUTHENTICATION,
    # An OpenAI-compatible server answers 404 for a model it does not serve,
    # which is a routing-relevant fact rather than a missing web page.
    404: FailureClass.MODEL_UNAVAILABLE,
    408: FailureClass.TIMEOUT,
    429: FailureClass.RATE_LIMIT,
    502: FailureClass.OVERLOAD,
    503: FailureClass.OVERLOAD,
    504: FailureClass.TIMEOUT,
}

# Phrases that identify a failure the status code alone cannot distinguish.
# Checked in order, because a body can match more than one: a refusal that
# mentions tokens must be read as a refusal. Substring matching is crude, but
# these strings come from real upstreams and no standard error code exists to
# use instead — a fact worth remembering before trusting the classification too
# far, which is why anything unmatched becomes INVALID_REQUEST rather than a
# more specific guess.
_BODY_MARKERS: tuple[tuple[FailureClass, tuple[str, ...]], ...] = (
    # Widened beyond the OpenAI/Azure `code` spellings, because the fallback
    # decision now depends on recognising a refusal: Azure's own prose, Gemini's
    # "blocked due to SAFETY" and any plain-language wording all missed the
    # three-substring version, and a refusal that is not recognised is a refusal
    # that gets routed around — which §10 forbids in terms.
    (
        FailureClass.CONTENT_REFUSAL,
        ("content_filter", "content policy", "content management", "content filtering",
         "safety", "blocked due to", "responsible ai", "violates"),
    ),
    (
        FailureClass.CONTEXT_OVERFLOW,
        ("context length", "context window", "maximum context", "too many tokens",
         "reduce the length"),
    ),
    (
        FailureClass.TOOL_INCOMPATIBILITY,
        # "support tool use" is OpenRouter's wording, found on RAVIS's first tool trial:
        # "No endpoints found that support tool use", sent as a 404. Unmarked, the status
        # read it as a missing model and opened that model's circuit for every request,
        # tools or not.
        ("does not support tools", "tools are not supported", "function calling is not",
         "unsupported parameter: 'tools'", "support tool use"),
    ),
    # After the tools entry, so "unsupported parameter: 'tools'" stays a tool
    # incompatibility. OpenAI's own codes first, then its wording.
    (
        FailureClass.UNSUPPORTED_PARAMETER,
        ("unsupported_parameter", "unsupported_value", "unsupported parameter",
         "unsupported value", "is not supported with this model"),
    ),
    (FailureClass.LOCAL_OOM, ("out of memory", "insufficient memory", "failed to allocate")),
    # "model unloaded or unavailable" is what the configured LM Studio answers
    # with — inside a 200 — for a model it is no longer holding, and it matched
    # none of the original three.
    (
        FailureClass.MODEL_UNAVAILABLE,
        ("model not found", "no model loaded", "model_not_found", "unloaded",
         "not loaded", "model is not available", "no models loaded"),
    ),
)


def classify_exception(failure: Exception) -> FailureClass:
    """Name a transport-level failure.

    Order matters: `httpx.ConnectTimeout` is both a timeout and a connection
    error, and it is the *timeout* reading that is useful — the connection was
    attempted rather than refused, so the target may well be alive and merely
    slow, and retrying the same one would wait all over again.

    **`CONNECTION` means the request never left, and only `ConnectError` proves
    that.** The policy table gives this class the one same-target retry in the
    system, justified as "a connection that was never established cannot have
    delivered the request, so a second attempt is provably not a duplicate."
    That reasoning is sound and it was being applied to failures it does not
    cover: every `httpx.TransportError` landed here, including `ReadError`,
    `WriteError`, `RemoteProtocolError` and `CloseError` — all of which happen
    *after* the request went out. Retrying those re-sends a completion the
    provider may have already run and billed, which is the duplicated charge
    §10 forbids.

    They become `INVALID_UPSTREAM_RESPONSE`, whose policy already says exactly
    what is true of them — no same-target retry because "the request plainly
    arrived, since something came back", but a fallback to the next candidate,
    because nothing about the *request* has been shown to be wrong. A stream
    that dies mid-flight is that class's own description of itself, arriving as
    an exception rather than as a body.
    """
    if isinstance(failure, httpx.TimeoutException):
        return FailureClass.TIMEOUT
    if isinstance(failure, httpx.ConnectError):
        return FailureClass.CONNECTION
    if isinstance(failure, httpx.TransportError):
        return FailureClass.INVALID_UPSTREAM_RESPONSE
    return FailureClass.UNKNOWN


def classify_response(status: int, body: bytes) -> FailureClass | None:
    """Name an HTTP-level failure, or return None when there is none.

    Returning None for a success is not a sentinel dressed as an error code
    (runbook §14.4): "this response did not fail" is a real answer to the
    question asked, and making it explicit is what keeps the caller's success
    path visible instead of implied.
    """
    if status < 400:
        return None
    # **A credential failure is not up for reinterpretation by the body.**
    # Body markers run first because a status is coarse and an upstream's own
    # words usually say more — but not here: a 401 or 403 means the provider
    # read the request and refused the caller, and no phrasing changes that.
    # Measured: a real 403 reading "Your project does not have access: model is
    # not available" matched the model-unavailable markers and classified as
    # `MODEL_UNAVAILABLE`, which *may fall back* — so an authentication failure
    # was shopped to the next provider, which §10 forbids in those words.
    if status in _CREDENTIAL_STATUSES:
        return FailureClass.AUTHENTICATION
    marked = _from_body(body)
    if marked is not None:
        return marked
    known = _STATUS_CLASSES.get(status)
    if known is not None:
        return known
    # A 4xx nobody recognised is the client's problem by definition; a 5xx
    # nobody recognised is not something to reason further about.
    return FailureClass.INVALID_REQUEST if status < 500 else FailureClass.UNKNOWN


def classify_error_body(body: bytes) -> FailureClass:
    """Name a failure from the upstream's own words, with no status to help.

    `classify_response` short-circuits on any status below 400, which is correct
    for the case it was written for and blind to the one that matters most on a
    local runtime: **a 200 carrying an error object**. LM Studio answers exactly
    that way for anything it will not serve, so a caller that has already
    established the body is an error — rather than inferring it from a status —
    needs the marker table without the status gate.

    **Fails closed, like `classify_response` does.** An unrecognised error body
    becomes `UNKNOWN`, which permits no fallback — and that is deliberate, after
    the alternative was tried and refuted. Defaulting to a fallback-eligible
    class means the *same* refusal produces opposite decisions depending on the
    status the upstream happened to attach, and the permissive branch would be
    the 2xx one: §10's "do not route around a safety refusal merely to find a
    more permissive provider" carries no status qualifier, and the marker table
    cannot be exhaustive over every provider's wording.

    Failing closed is also never a regression. Before this existed, a 200
    carrying an error was forwarded as a successful answer; now it is at worst
    forwarded as the failure it is, and at best — when its words are
    recognisable — it moves to the next candidate.
    """
    return _from_body(body) or FailureClass.UNKNOWN


def _from_body(body: bytes) -> FailureClass | None:
    """Read the upstream's own words, when they say more than the status does.

    Only the first 4 KB is read. An error body is small; anything larger is
    either not an error object or is carrying content that has no business
    being scanned for keywords.
    """
    try:
        text = body[:4096].decode("utf-8", errors="ignore").lower()
    except (UnicodeDecodeError, AttributeError):  # pragma: no cover - defensive
        return None
    for failure_class, markers in _BODY_MARKERS:
        if any(marker in text for marker in markers):
            return failure_class
    return None


def error_body(message: str, failure_class: FailureClass, decision: dict[str, Any]) -> bytes:
    """The OpenAI-shaped error a chain exhaustion returns, with its reasoning.

    The failure class travels in `code` so a client — or a person reading a log
    — can tell "every candidate was rate-limited" from "the request was
    malformed" without parsing prose. `route` carries the attempt history,
    which is the §9.7 explanation of a failure rather than of a success.
    """
    return json.dumps(
        {
            "error": {
                "message": message,
                "type": "upstream_error",
                "param": None,
                "code": failure_class.value,
                "route": decision,
            }
        }
    ).encode()
