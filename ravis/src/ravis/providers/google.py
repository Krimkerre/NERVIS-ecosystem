"""The Gemini native adapter (§6, Path B) — M7's Google half.

**Why native, when Google publishes an OpenAI-compatible endpoint.** §6 is
emphatic that an already-compatible stream should not be normalized for
architectural purity, and RAVIS took that seriously: Google was reached through
`/v1beta/openai` on the transparent path, and for ordinary text it works.

It does not work for the surfaces §6 singles out. Measured against the same
request, through the same code path, the same afternoon:

    google via /v1beta/openai   finish_reason=stop        tool-call index absent
    openrouter (transparent)    finish_reason=tool_calls  tool-call index 0

A client that notices tool calls by reading `finish_reason` sees a finished text
answer, and one that assembles fragments by index has nothing to key on. Both
are what Clarvis's agent role does. So the compat endpoint was the shortcut
worth trying, the measurement is the reason it was abandoned, and Gemini goes
where §6 put it in the first place.

The mapping decisions live in `google_wire`, which is where the four that are
judgements rather than transcription are argued.
"""

from __future__ import annotations

import logging
import re
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
from ravis.core.responses import NormalizedResponse, NormalizedStreamEvent
from ravis.providers.base import (
    HEALTH_TIMEOUT_SECONDS,
    ProtocolMode,
    ProviderHealth,
    TranslationError,
    health_after,
)
from ravis.providers.google_wire import (
    StreamReader,
    dropped_parameters,
    read_response,
    render_request,
    sse_payloads,
)
from ravis.upstream import Upstream

logger = logging.getLogger(__name__)

# The API version, kept as one constant because it prefixes every path.
#
# Gemini model *names* already begin `models/` — the catalogue returns
# `models/gemini-3.6-flash`, not a bare id — so a path built from a name still
# needs the version in front of it. Building `/models/...:generateContent` and
# omitting this is a 404 that reads exactly like a deprecated model.
API_ROOT = "/v1beta"
MODELS_PATH = f"{API_ROOT}/models"

# Capabilities implied by speaking the Gemini API at all, at DEFAULT provenance
# so anything better replaces them without argument. Neither is safety- or
# capability-critical, which is what makes seeding them permissible under §7
# where seeding tool support would not be.
PROTOCOL_DEFAULTS = {
    Capability.TEXT: CapabilityState.SUPPORTED,
    Capability.STREAMING: CapabilityState.SUPPORTED,
}

# What the catalogue's `supportedGenerationMethods` is willing to tell us.
# A model that does not list `generateContent` is not a chat model at all —
# embedding and image endpoints share this catalogue — and saying so is the
# difference between a picker that offers it and a route that 404s.
GENERATE = "generateContent"

# How long a discovery read is believed. The same window the other adapters use:
# a catalogue changes daily rather than by the second, and this is about not
# fetching it once per routing pass rather than about freshness.
DISCOVERY_TTL_SECONDS = 300.0

# Gemini takes no `max_tokens` by default and will happily run to its own
# ceiling. Unlike Anthropic it does not *require* one, so this is not sent
# unless a client asked — a default cap here would silently truncate replies
# nobody asked to be short.
DEFAULT_MAX_OUTPUT_TOKENS: int | None = None


# The character shape a legitimate Gemini model name — or RAVIS's own
# `models/`-prefixed address for one — can take. The catalogue's own ids look
# like `gemini-3.6-flash` or `gemini-2.0-flash-001`; a direct address is the
# same string with `models/` in front. Nothing else is a model this adapter
# could ever have been asked for, so this is an allow-list of that shape
# rather than a blocklist of dangerous characters — a blocklist chasing `../`,
# an encoded slash, a stray `?` one pattern at a time is exactly the kind of
# check a variant nobody thought of slips past.
_MODEL_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-])*")


def _path(model: str) -> str:
    """The versioned path for one model, whichever form its name arrived in.

    Gemini names carry their own `models/` prefix and RAVIS addresses carry it
    too, so this tolerates both rather than assuming one — a direct address
    written `ravis/google/gemini-3.6-flash` should reach the same place as one
    written `ravis/google/models/gemini-3.6-flash`.

    `model` is `request.requested_model` — a string the caller chose, that the
    router does not check for a translated provider like this one (only the
    provider's own catalogue can say whether an address is real, and a foreign
    one is never checked against ours; see `RoutingEngine._direct`). It is
    spliced straight into the outbound URL below, so anything shaped outside
    `_MODEL_NAME` is refused here rather than allowed to alter which path gets
    requested on Google's own host.
    """
    bare = model.strip("/")
    if not bare.startswith("models/"):
        bare = f"models/{bare}"
    name = bare[len("models/") :]
    if not _MODEL_NAME.fullmatch(name):
        raise TranslationError(f"not a model address this adapter can route to: {model!r}")
    return f"{API_ROOT}/{bare}"


class GoogleUpstreamError(RuntimeError):
    """An error Gemini returned, in Google's own words.

    Built from the provider's error body rather than httpx's exception string,
    which carries the request URL — §9.7 keeps internal URLs out of diagnostics,
    and this message reaches the client verbatim when a chain runs out.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        """`status` is carried so the caller can tell whose fault this was.

        Without it every upstream refusal reached `_adapter_failure` as an
        unclassifiable `RuntimeError` and became `UNKNOWN` — so an upstream
        answering *400: max_tokens must be an integer* was reported to the client
        as `502 upstream_error`, blaming a provider that had worked correctly and
        said exactly what was wrong.
        """
        super().__init__(message)
        self.status = status


class GoogleAdapter:
    """A `TranslatingAdapter` for the Gemini API (§6, Path B)."""

    protocol_mode = ProtocolMode.TRANSLATED

    @property
    def has_credential(self) -> bool:
        """Whether a request sent now could authenticate.

        Read per call rather than captured, so a key saved on the Credentials
        screen takes effect without a restart.
        """
        return bool(self._upstream.key())

    def __init__(
        self,
        upstream: Upstream,
        client: httpx.AsyncClient,
        *,
        name: str = "google",
        max_output_tokens: int | None = DEFAULT_MAX_OUTPUT_TOKENS,
        configured_capabilities: dict[str, dict[str, str]] | None = None,
        discovery_ttl_seconds: float = DISCOVERY_TTL_SECONDS,
        clock: Any = time.monotonic,
    ) -> None:
        self.name = name
        self._upstream = upstream
        self._client = client
        self._max_output_tokens = max_output_tokens
        self._configured = configured_capabilities or {}
        self._ttl = discovery_ttl_seconds
        self._clock = clock
        self._discovery: dict[str, tuple[float, dict[str, Any]]] = {}

    # ── Discovery ────────────────────────────────────────────────────────────

    async def health(self) -> ProviderHealth:
        """Reachability, measured by the cheapest authenticated call there is."""
        if not self._upstream.is_configured:
            return ProviderHealth(reachable=False, detail="no google upstream configured")
        started = time.monotonic()
        try:
            response = await self._client.get(
                self._url(MODELS_PATH), headers=self._headers(),
                timeout=HEALTH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except httpx.HTTPError as failure:
            return health_after(failure, started, (time.monotonic() - started) * 1000)
        return ProviderHealth(reachable=True, latency_ms=(time.monotonic() - started) * 1000)

    async def models(self) -> list[str]:
        """Model names that can actually take a chat request.

        Filtered on `supportedGenerationMethods`, because this catalogue also
        lists embedding, image and TTS models — offering one as a chat
        candidate produces a 404 at the moment of use, which is the failure the
        deprecated-model hiding exists to clean up after.
        """
        payload = await self._get(MODELS_PATH, cache=True)
        entries = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return []
        return [
            entry["name"]
            for entry in entries
            if isinstance(entry, dict)
            and entry.get("name")
            and GENERATE in (entry.get("supportedGenerationMethods") or [])
        ]

    async def capabilities(self, model: str) -> ModelCapabilities:
        """What the catalogue advertises about one model, and nothing more.

        Protocol defaults first, the catalogue second, operator configuration
        last — §9.5's provenance ordering. A catalogue that cannot be read
        leaves the model at its defaults: discovery being unavailable is not
        evidence about the model.
        """
        known = ModelCapabilities(model_id=model)
        for capability, state in PROTOCOL_DEFAULTS.items():
            known.record(
                CapabilityClaim(
                    capability=capability,
                    state=state,
                    provenance=Provenance.DEFAULT,
                    detail="implied by the Gemini generateContent API",
                )
            )
        self._record_advertised(known, await self._get(_path(model), cache=True))
        apply_configured(known, self._configured.get(model, {}))
        return known

    def _record_advertised(self, known: ModelCapabilities, entry: dict[str, Any]) -> None:
        """Context window and thinking, where the catalogue states them.

        Tool support is deliberately *not* seeded from anything here. Gemini's
        model entry does not declare it, and §7 forbids inventing a capability
        claim — an UNKNOWN that fails closed is the correct answer, and the
        operator's own configuration or SIRVIS evidence is what resolves it.
        """
        if not entry:
            return
        window = entry.get("inputTokenLimit")
        if isinstance(window, int) and window > 0:
            known.context_window = window
        if entry.get("thinking"):
            known.record(
                CapabilityClaim(
                    capability=Capability.REASONING,
                    state=CapabilityState.SUPPORTED,
                    provenance=Provenance.ADVERTISED,
                    detail="the model catalogue reports thinking support",
                )
            )

    async def estimate_cost(self, request: NormalizedRequest) -> float | None:
        """Unknown here, deliberately: cost is priced centrally.

        `None`, never `0.0`. Google's free tier is a quota on an account rather
        than a property of a model, so a zero here would be a claim about
        somebody's billing that this code cannot make.
        """
        del request  # Priced centrally from the PriceBook; see `base.estimate_cost`.
        return None

    # ── Generation ───────────────────────────────────────────────────────────

    async def complete(self, request: NormalizedRequest) -> NormalizedResponse:
        """One non-streaming generation, translated both ways."""
        model = request.requested_model
        body = self._body_for(request)
        started = time.monotonic()
        response = await self._client.post(
            self._url(f"{_path(model)}:generateContent"),
            headers=self._headers(),
            json=body,
        )
        _raise_for_status(response, response.content)
        answer = read_response(response.json(), provider=self.name, model=model)
        answer.latency_ms = (time.monotonic() - started) * 1000
        return answer

    def stream(self, request: NormalizedRequest) -> AsyncGenerator[NormalizedStreamEvent, None]:
        """One streaming generation, as normalized events.

        Not `async def`, per the `TranslatingAdapter` contract: this returns the
        generator rather than awaiting one, so the relay closing it unwinds the
        `async with` below and Google stops generating — and stops billing — the
        moment the client disconnects (§8.6).
        """
        return self._stream(request)

    async def _stream(
        self, request: NormalizedRequest
    ) -> AsyncGenerator[NormalizedStreamEvent, None]:
        model = request.requested_model
        body = self._body_for(request)
        reader = StreamReader()
        # `alt=sse` is required. Without it `streamGenerateContent` answers with
        # a JSON *array* delivered in chunks, which is not an event stream and
        # cannot be read line by line.
        url = self._url(f"{_path(model)}:streamGenerateContent") + "?alt=sse"
        async with self._client.stream(
            "POST", url, headers=self._headers(), json=body
        ) as response:
            if response.status_code >= 400:
                # Read the body before raising: an error response is small and
                # not chunked, and raising without it hands the client a status
                # with none of the provider's explanation.
                _raise_for_status(response, await response.aread())
            async for line in response.aiter_lines():
                for payload in sse_payloads([line]):
                    for event in reader.events(payload):
                        yield event

    def _body_for(self, request: NormalizedRequest) -> dict[str, Any]:
        """The Gemini request body, with anything untranslatable logged.

        §9.4 again: a parameter that vanished without a word is
        indistinguishable from one that was honoured.
        """
        dropped = dropped_parameters(request)
        if dropped:
            logger.info(
                "google: parameters not translatable",
                extra={"provider": self.name, "dropped": dropped},
            )
        return render_request(request, max_output_tokens=self._max_output_tokens)

    # ── Transport ────────────────────────────────────────────────────────────

    async def _get(self, path: str, *, cache: bool = False) -> dict[str, Any]:
        """A discovery GET, or an empty mapping when it cannot be answered.

        Cached for the two reads a *route* makes, so a pool asking about every
        candidate does not put a Google round trip on the routing path and blow
        §9.8's budget. Generation calls are deliberately uncached — those are
        the request.

        A failed read is not cached: holding a blip for the whole window would
        leave every Gemini model capability-less, which fails closed and would
        empty the pools for minutes over a moment's outage.
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
            return {}
        answer = payload if isinstance(payload, dict) else {}
        if cache:
            self._discovery[path] = (self._clock(), answer)
        return answer

    def _url(self, path: str) -> str:
        return self._upstream.url_for(path)

    def _headers(self) -> dict[str, str]:
        """RAVIS's own credential — never a caller's token.

        `key()` rather than the declared field: testing the field is what made
        the Anthropic adapter send no credential at all once credentials moved
        into the store, four times over, and the mistake is cheap to repeat.
        """
        headers = {"content-type": "application/json"}
        key = self._upstream.key()
        if key:
            headers["x-goog-api-key"] = key
        return headers


def _raise_for_status(response: httpx.Response, body: bytes) -> None:
    """Turn an error response into Google's own words.

    Gemini nests its message under `error.message`; anything unparseable falls
    back to the status line, which is still more than an httpx repr would say
    and carries no URL.
    """
    if response.status_code < 400:
        return
    detail = ""
    try:
        payload = response.json() if body is None else _loads(body)
        if isinstance(payload, dict):
            detail = str((payload.get("error") or {}).get("message") or "")
    except ValueError:
        detail = ""
    raise GoogleUpstreamError(
        detail or f"google returned HTTP {response.status_code}",
        status=response.status_code,
    )


def _loads(body: bytes) -> Any:
    import json

    return json.loads(body.decode("utf-8", "replace"))
