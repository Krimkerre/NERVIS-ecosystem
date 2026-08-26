"""The Anthropic native adapter — M4, and the first thing to drive Path B.

M3b built the translated path and proved it with a test double. This is the
first real provider on it: an upstream that does not speak the client's protocol
and never will, so every request is rendered into Anthropic's Messages shape and
every response is rendered back (§6, Path B).

**Raw HTTP rather than the `anthropic` SDK, and the reason is architectural.**
RAVIS already owns everything an SDK would bring: retries and the retry budget
(§10), circuit breakers, timeouts, and the cancellation contract that requires
closing a generator to stop an upstream mid-generation (§8.6). An SDK client
would have to be switched off on each of those axes to avoid retrying a request
the chain has already decided not to retry — and its typed stream events are a
re-serialisation of exactly the frames this adapter needs verbatim. There is
also a plural argument: M7 and M8 add Google, OpenRouter, LM Studio and Ollama,
and a gateway carrying five vendor SDKs to make the same POST is a gateway that
has stopped being one HTTP client. The translation itself lives next door in
`anthropic_wire`, tested without a socket.

**What this adapter refuses to claim is the part to read before changing it.**
`capabilities()` reports what the Models API advertises and nothing else. Tool
support is the one that matters: §5.1's hard invariant is that every member of
`ravis/clarvis-agent` must satisfy the tool requirement, so a guess here routes
an agent to a model on the strength of an assumption. Anthropic's catalogue does
not document a tool-support key, so the claim stays UNKNOWN and the pool fails
closed until an operator declares it or M13's evidence arrives. Direct
addressing — `ravis/anthropic/<model>` — is unaffected, and that is what M4's
acceptance criterion exercises.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncGenerator

import httpx

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
    apply_configured,
)
from ravis.core.requests import NormalizedRequest
from ravis.core.responses import FinishReason, NormalizedResponse, NormalizedStreamEvent
from ravis.providers.anthropic_wire import (
    ANTHROPIC_VERSION,
    StreamReader,
    dropped_parameters,
    read_response,
    render_request,
    sse_payloads,
)
from ravis.providers.base import ProtocolMode, ProviderHealth
from ravis.upstream import Upstream

logger = logging.getLogger(__name__)

MESSAGES_PATH = "/v1/messages"
MODELS_PATH = "/v1/models"

# Capabilities implied by speaking the Messages API at all, seeded at DEFAULT
# provenance so anything better replaces them without argument. Neither is
# safety- or capability-critical, which is what makes seeding them permissible
# under §7 where seeding tool support would not be.
PROTOCOL_DEFAULTS = {
    Capability.TEXT: CapabilityState.SUPPORTED,
    Capability.STREAMING: CapabilityState.SUPPORTED,
}

# Capability claims the Models API is willing to make, as
# (capability, key in the catalogue's `capabilities` tree).
#
# `tool_use` is in this table on purpose despite not being a documented key. The
# failure mode is what makes that safe rather than speculative: a key the
# catalogue does not carry records nothing, leaving the capability UNKNOWN and
# failing closed. So the table asks, and never asserts what it was not told.
ADVERTISED = (
    (Capability.VISION, "image_input"),
    (Capability.STRUCTURED_OUTPUT, "structured_outputs"),
    (Capability.REASONING, "thinking"),
    (Capability.TOOLS, "tool_use"),
)


class AnthropicUpstreamError(RuntimeError):
    """An error Anthropic returned, in Anthropic's own words.

    Built from the provider's error body rather than from httpx's exception
    string, which carries the request URL — §9.7 keeps internal URLs out of
    diagnostics, and this message reaches the client verbatim when a chain runs
    out of candidates.
    """


# How long a discovery read is believed. The same window the other adapters
# use: a catalogue changes daily rather than by the second, and this is about
# not fetching it once per routing pass rather than about freshness.
DISCOVERY_TTL_SECONDS = 300.0


class AnthropicAdapter:
    """A `TranslatingAdapter` for the Anthropic Messages API (§6, Path B)."""

    protocol_mode = ProtocolMode.TRANSLATED


    @property
    def has_credential(self) -> bool:
        """Whether a request sent now could authenticate.

        Read per call rather than captured, because the credential is resolved
        per call — a key saved on the Credentials screen has to take effect
        without a restart, and this is the check that decides whether the
        provider is offered at all.
        """
        return bool(self._upstream.key())

    def __init__(
        self,
        upstream: Upstream,
        client: httpx.AsyncClient,
        *,
        name: str = "anthropic",
        max_output_tokens: int = 16000,
        configured_capabilities: dict[str, dict[str, str]] | None = None,
        discovery_ttl_seconds: float = DISCOVERY_TTL_SECONDS,
        clock: Any = time.monotonic,
    ) -> None:
        self.name = name
        self._upstream = upstream
        self._client = client
        # Anthropic requires `max_tokens` on every request and OpenAI does not,
        # so a client that named no limit still needs a number. This is that
        # number — a default for the unstated case, never a cap on a stated one.
        self._max_output_tokens = max_output_tokens
        self._configured = configured_capabilities or {}
        self._ttl = discovery_ttl_seconds
        self._clock = clock
        # path -> (read at, payload). Only discovery reads land here.
        self._discovery: dict[str, tuple[float, dict[str, Any]]] = {}

    # ── Discovery ────────────────────────────────────────────────────────────

    async def health(self) -> ProviderHealth:
        """Reachability, measured by the cheapest authenticated call there is."""
        if not self._upstream.is_configured:
            return ProviderHealth(reachable=False, detail="no anthropic upstream configured")
        started = time.monotonic()
        try:
            response = await self._client.get(self._url(MODELS_PATH), headers=self._headers())
            response.raise_for_status()
        except httpx.HTTPError as failure:
            return ProviderHealth(reachable=False, detail=type(failure).__name__)
        return ProviderHealth(reachable=True, latency_ms=(time.monotonic() - started) * 1000)

    async def models(self) -> list[str]:
        """The model IDs this account can address, or an empty list."""
        payload = await self._get(MODELS_PATH, cache=True)
        entries = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return []
        return [entry["id"] for entry in entries if isinstance(entry, dict) and entry.get("id")]

    async def capabilities(self, model: str) -> ModelCapabilities:
        """What the catalogue advertises about one model, and nothing more.

        Protocol defaults first, the catalogue second, operator configuration
        last — so a declaration wins over an advertisement, which is what
        §9.5's provenance ordering asks for. A catalogue that cannot be read
        leaves the model at its defaults rather than failing: discovery being
        unavailable is not evidence about the model.
        """
        known = ModelCapabilities(model_id=model)
        for capability, state in PROTOCOL_DEFAULTS.items():
            known.record(
                CapabilityClaim(
                    capability=capability,
                    state=state,
                    provenance=Provenance.DEFAULT,
                    detail="implied by the Anthropic Messages API",
                )
            )
        _record_advertised(known, await self._get(f"{MODELS_PATH}/{model}", cache=True))
        apply_configured(known, self._configured.get(model, {}))
        return known

    async def estimate_cost(self, request: NormalizedRequest) -> float | None:
        """Unknown until the cost engine lands at M15.

        `None`, never `0.0`: Anthropic is a paid provider, and §14 forbids
        presenting an estimate as an invoice. A zero here would read as free.
        """
        del request  # No pricing table exists yet; the parameter is the contract.
        return None

    # ── Generation ───────────────────────────────────────────────────────────

    async def complete(self, request: NormalizedRequest) -> NormalizedResponse:
        """One non-streaming generation, translated both ways."""
        model = request.requested_model
        body = self._body_for(request, model, stream=False)
        started = time.monotonic()
        response = await self._client.post(
            self._url(MESSAGES_PATH), headers=self._headers(), json=body
        )
        _raise_for_status(response, response.content)
        answer = read_response(response.json(), provider=self.name, model=model)
        answer.latency_ms = (time.monotonic() - started) * 1000
        _log_refusal(answer)
        return answer

    def stream(self, request: NormalizedRequest) -> AsyncGenerator[NormalizedStreamEvent, None]:
        """One streaming generation, as normalized events.

        Not `async def`, per the `TranslatingAdapter` contract: this returns the
        generator rather than awaiting one, so that the relay closing it unwinds
        the `async with` below and the provider stops generating — and stops
        billing — the moment the client disconnects (§8.6).
        """
        return self._stream(request)

    async def _stream(
        self, request: NormalizedRequest
    ) -> AsyncGenerator[NormalizedStreamEvent, None]:
        model = request.requested_model
        body = self._body_for(request, model, stream=True)
        reader = StreamReader()
        async with self._client.stream(
            "POST", self._url(MESSAGES_PATH), headers=self._headers(), json=body
        ) as response:
            if response.status_code >= 400:
                # Read the body before raising. An error response is small and
                # not chunked, and raising without it would give the client a
                # status with none of the provider's explanation.
                _raise_for_status(response, await response.aread())
            async for line in response.aiter_lines():
                for payload in sse_payloads([line]):
                    for event in reader.events(payload):
                        yield event

    def _body_for(
        self, request: NormalizedRequest, model: str, *, stream: bool
    ) -> dict[str, Any]:
        """The Anthropic request body, with anything untranslatable logged.

        The logging is the §7 obligation being met rather than a nicety: a
        parameter that cannot cross the translation must not disappear without
        trace, and `temperature` — which every current Anthropic model rejects
        outright — is dropped on most requests an OpenAI client makes.
        """
        for parameter in dropped_parameters(request):
            logger.warning(
                "%s does not accept %s; the request was sent without it "
                "(current Anthropic models reject sampling parameters)",
                model,
                parameter,
            )
        body = render_request(
            request, model=model, max_output_tokens=self._max_output_tokens
        )
        body["stream"] = stream
        return body

    # ── Plumbing ─────────────────────────────────────────────────────────────

    async def _get(self, path: str, *, cache: bool = False) -> dict[str, Any]:
        """A discovery GET, or an empty mapping when it cannot be answered.

        `cache` is for the two reads a *route* makes — the catalogue and a
        model's entry. Those were uncached, which was fine while a translated
        provider could only be reached by direct address and is not now: a pool
        asks about every candidate on every request, so an uncached listing
        would put an Anthropic round trip on the routing path and blow §9.8's
        five-millisecond budget several times over. The generation calls below
        are deliberately not cached — those are the request.

        Discovery failures are absence rather than errors here: `models()` and
        `capabilities()` are called to *inform* a route, and a provider that
        cannot be listed right now should narrow the options rather than fail
        the request that was going somewhere else anyway.
        """
        if not self._upstream.is_configured:
            return {}
        if cache:
            hit = self._discovery.get(path)
            if hit is not None and self._clock() - hit[0] < self._ttl:
                return hit[1]
        try:
            response = await self._client.get(self._url(path), headers=self._headers())
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            # A failed read is not cached. Holding a blip for the whole window
            # would leave every Anthropic model capability-less — which fails
            # closed, so a moment's outage would empty the pools for minutes.
            return {}
        answer = payload if isinstance(payload, dict) else {}
        if cache:
            self._discovery[path] = (self._clock(), answer)
        return answer

    def _url(self, path: str) -> str:
        return self._upstream.url_for(path)

    def _headers(self) -> dict[str, str]:
        """RAVIS's own credential and the API version — never a caller's token."""
        headers = {"content-type": "application/json", "anthropic-version": ANTHROPIC_VERSION}
        # `key()`, never the declared field. Testing the field meant this
        # adapter sent no credential at all once credentials moved into the
        # store, and the failure surfaced as Anthropic's own
        # "x-api-key header is required" rather than as anything RAVIS said.
        key = self._upstream.key()
        if key:
            # `x-api-key`, not `authorization: Bearer`. The two are not
            # interchangeable on this API.
            headers["x-api-key"] = key
        return headers


def _record_advertised(known: ModelCapabilities, catalogue: dict[str, Any]) -> None:
    """Fold one model's catalogue entry into what is known about it.

    Only leaves that actually say `supported` are recorded. A key the catalogue
    does not carry — or carries without that flag — records nothing and leaves
    the capability UNKNOWN, which is the honest answer and the one that fails
    closed.
    """
    if not catalogue:
        return
    known.context_window = catalogue.get("max_input_tokens") or known.context_window
    known.max_output_tokens = catalogue.get("max_tokens") or known.max_output_tokens
    advertised = catalogue.get("capabilities")
    if not isinstance(advertised, dict):
        return
    for capability, key in ADVERTISED:
        leaf = advertised.get(key)
        if not isinstance(leaf, dict) or not isinstance(leaf.get("supported"), bool):
            continue
        known.record(
            CapabilityClaim(
                capability=capability,
                state=(
                    CapabilityState.SUPPORTED
                    if leaf["supported"]
                    else CapabilityState.UNSUPPORTED
                ),
                provenance=Provenance.ADVERTISED,
                detail=f"the Anthropic model catalogue reports {key}",
            )
        )


def _raise_for_status(response: httpx.Response, body: bytes) -> None:
    """Turn a non-2xx into an error carrying the provider's own message.

    Deliberately not `response.raise_for_status()`: that exception's string
    contains the request URL, and this message is what a client sees when the
    fallback chain is exhausted (§9.7 keeps internal addresses out of it).
    """
    if response.status_code < 400:
        return
    raise AnthropicUpstreamError(f"anthropic {response.status_code}: {_message_in(body)}")


def _message_in(body: bytes) -> str:
    """The `error.message` from an Anthropic error body, or a plain fallback."""
    try:
        payload = json.loads(body)
    except ValueError:
        return "the provider returned an error with no readable body"
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return "the provider returned an error with no message"


def _log_refusal(answer: NormalizedResponse) -> None:
    """Note a safety refusal, which the wire cannot fully express.

    `stop_reason: "refusal"` reaches the client as `content_filter` — the
    closest true statement OpenAI's vocabulary has — but the category that says
    *which* classifier declined has no equivalent at all. Logging it is what
    keeps that answerable after the fact.
    """
    if answer.finish_reason is FinishReason.CONTENT_FILTER:
        logger.info(
            "anthropic declined request %s on %s (reported as content_filter)",
            answer.provider_request_id or "?",
            answer.model,
        )
