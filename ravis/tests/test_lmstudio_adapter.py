"""The LM Studio adapter: what a vendor endpoint may and may not establish.

LM Studio publishes more than a generic OpenAI-compatible endpoint does, so most
of this is about the *edges* of that extra knowledge — where an advertisement
stops being evidence. The last section is the one that matters: this machine's
own corpus contains a build LM Studio advertises as tool-capable and SIRVIS
measured failing, and the provenance ordering has to resolve that the right way.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from tests.test_sirvis_evidence import GGUF, MLX, payload, record, store_with

from ravis.core.capabilities import Capability, CapabilityState, Provenance
from ravis.providers.base import ProviderAdapter
from ravis.providers.lmstudio import LmStudioAdapter
from ravis.upstream import Upstream

# The two entries this machine actually reports for the granite pair, trimmed to
# the fields the adapter reads. Kept faithful to the live payload so a change in
# LM Studio's shape breaks a test rather than a deployment.
GGUF_ENTRY = {
    "id": GGUF,
    "type": "llm",
    "capabilities": ["tool_use"],
    "max_context_length": 1048576,
    "state": "not-loaded",
}
MLX_ENTRY = {
    "id": MLX,
    "type": "llm",
    "capabilities": ["tool_use"],
    "max_context_length": 131072,
    "state": "not-loaded",
}


def _adapter(
    entries: list[dict[str, Any]] | None = None,
    *,
    native_status: int = 200,
    configured: dict[str, dict[str, str]] | None = None,
) -> LmStudioAdapter:
    """An adapter over a canned LM Studio, or over something pretending to be one."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v0/models":
            if native_status != 200:
                return httpx.Response(native_status, json={"error": "not found"})
            return httpx.Response(200, json={"object": "list", "data": entries or []})
        return httpx.Response(
            200, json={"object": "list", "data": [{"id": e["id"]} for e in (entries or [])]}
        )

    return LmStudioAdapter(
        upstream=Upstream(base_url="http://lmstudio.invalid", api_key=""),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        configured_capabilities=configured,
    )


# ── 1. It is still an ordinary OpenAI-compatible adapter ─────────────────────


def test_it_satisfies_the_adapter_protocol() -> None:
    assert isinstance(_adapter(), ProviderAdapter)


def test_it_names_itself_lmstudio_without_being_told() -> None:
    """Route explanations name the provider; 'generic_openai' there would lie."""
    assert _adapter().name == "lmstudio"


async def test_it_inherits_model_listing_from_the_generic_adapter() -> None:
    """`/v1/models` is the protocol surface and needs no vendor handling."""
    assert await _adapter([GGUF_ENTRY, MLX_ENTRY]).models() == [GGUF, MLX]


# ── 2. What the catalogue establishes ────────────────────────────────────────


async def test_advertised_tool_use_is_recorded_as_advertised_not_measured() -> None:
    known = await _adapter([GGUF_ENTRY]).capabilities(GGUF)

    assert known.state_of(Capability.TOOLS) is CapabilityState.SUPPORTED
    assert known.claims[Capability.TOOLS].provenance is Provenance.ADVERTISED


async def test_the_declared_context_ceiling_is_carried_across() -> None:
    assert (await _adapter([GGUF_ENTRY]).capabilities(GGUF)).context_window == 1048576


async def test_a_vision_type_supports_vision() -> None:
    entry = {"id": "m", "type": "vlm", "max_context_length": 4096}
    known = await _adapter([entry]).capabilities("m")

    assert known.state_of(Capability.VISION) is CapabilityState.SUPPORTED


async def test_a_text_type_does_not_support_vision() -> None:
    """`type` is closed and always present, so both directions may be claimed."""
    known = await _adapter([GGUF_ENTRY]).capabilities(GGUF)

    assert known.state_of(Capability.VISION) is CapabilityState.UNSUPPORTED


async def test_an_embedding_model_is_not_a_text_model() -> None:
    """Otherwise the protocol default would put an embedding endpoint in a text pool."""
    entry = {"id": "e", "type": "embeddings", "max_context_length": 512}
    known = await _adapter([entry]).capabilities("e")

    assert known.state_of(Capability.EMBEDDINGS) is CapabilityState.SUPPORTED
    assert known.state_of(Capability.TEXT) is CapabilityState.UNSUPPORTED


# ── 3. Where the catalogue says nothing ──────────────────────────────────────


async def test_a_missing_capability_array_leaves_tools_unknown() -> None:
    """Absent for 8 of 20 models on the developer's machine. Silence is not a no."""
    entry = {"id": "quiet", "type": "llm", "max_context_length": 65536}
    known = await _adapter([entry]).capabilities("quiet")

    assert known.state_of(Capability.TOOLS) is CapabilityState.UNKNOWN
    assert not known.satisfies(Capability.TOOLS)


async def test_an_empty_capability_array_also_leaves_tools_unknown() -> None:
    """A field defaulted to empty and one populated with nothing are the same bytes."""
    entry = {"id": "empty", "type": "llm", "capabilities": [], "max_context_length": 4096}
    known = await _adapter([entry]).capabilities("empty")

    assert known.state_of(Capability.TOOLS) is CapabilityState.UNKNOWN


async def test_an_upstream_that_is_not_lmstudio_degrades_to_generic_answers() -> None:
    """A 404 here is an ordinary configuration, not a fault."""
    known = await _adapter([GGUF_ENTRY], native_status=404).capabilities(GGUF)

    assert known.state_of(Capability.TOOLS) is CapabilityState.UNKNOWN
    assert known.state_of(Capability.TEXT) is CapabilityState.SUPPORTED
    assert known.claims[Capability.TEXT].provenance is Provenance.DEFAULT
    assert known.context_window is None


async def test_a_model_absent_from_the_catalogue_gets_generic_answers() -> None:
    known = await _adapter([GGUF_ENTRY]).capabilities("never/installed")

    assert known.state_of(Capability.TOOLS) is CapabilityState.UNKNOWN


# ── 4. The ordering the whole module exists for ──────────────────────────────


async def test_an_operator_override_outranks_the_catalogue() -> None:
    """§9.5: CONFIGURED sits above everything RAVIS can observe for itself."""
    adapter = _adapter([GGUF_ENTRY], configured={GGUF: {"tools": "UNSUPPORTED"}})
    known = await adapter.capabilities(GGUF)

    assert known.state_of(Capability.TOOLS) is CapabilityState.UNSUPPORTED
    assert known.claims[Capability.TOOLS].provenance is Provenance.CONFIGURED


def test_measured_evidence_overturns_the_catalogue_for_the_mlx_build() -> None:
    """The real disagreement, with the real numbers.

    LM Studio advertises `tool_use` for both packagings of granite-4.0-h-tiny.
    SIRVIS measured the GGUF build passing 24 of 24 tool trials and the MLX build
    passing 3. Advertisement loses; the pool admits one build and not the other.
    """
    store = store_with(
        payload(
            record(runtime_key=GGUF, passed=24, total=24),
            record(runtime_key=MLX, variant="var_mlx", passed=3, total=24, fmt="mlx"),
            variants={GGUF: "var_gguf", MLX: "var_mlx"},
        )
    )
    adapter = _adapter([GGUF_ENTRY, MLX_ENTRY])

    async def admit(build: str) -> Any:
        known = await adapter.capabilities(build)
        assert known.state_of(Capability.TOOLS) is CapabilityState.SUPPORTED, "advertised first"
        for claim in store.claims_for(build):
            known.record(claim)
        return known

    admitted = {build: asyncio.run(admit(build)) for build in (GGUF, MLX)}

    assert admitted[GGUF].satisfies(Capability.TOOLS)
    assert not admitted[MLX].satisfies(Capability.TOOLS)
    assert admitted[MLX].claims[Capability.TOOLS].provenance is Provenance.MEASURED


# ── 5. The catalogue is read once, not once per model ────────────────────────


class _Clock:
    """A hand-wound clock, so a TTL can be crossed without waiting for it."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _counting_adapter(clock: _Clock, *, fail: bool = False) -> tuple[LmStudioAdapter, list[int]]:
    calls = [0]

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v0/models":
            calls[0] += 1
            if fail:
                raise httpx.ConnectError("upstream is down")
            return httpx.Response(200, json={"object": "list", "data": [GGUF_ENTRY, MLX_ENTRY]})
        return httpx.Response(200, json={"object": "list", "data": []})

    adapter = LmStudioAdapter(
        upstream=Upstream(base_url="http://lmstudio.invalid", api_key=""),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        clock=clock,
    )
    return adapter, calls


async def test_one_catalogue_read_answers_for_every_model() -> None:
    """The routing path asks per candidate; without this that is a GET each.

    On this machine that is 20 round trips per chat completion, which is the
    cost `chat.py` warned about when it said capabilities are assembled per
    request and that the line would have to change once an adapter needed I/O.
    """
    adapter, calls = _counting_adapter(_Clock())

    for _ in range(3):
        await adapter.capabilities(GGUF)
        await adapter.capabilities(MLX)

    assert calls[0] == 1


async def test_the_catalogue_is_re_read_once_the_window_passes() -> None:
    """Installing a model should become visible without a restart."""
    clock = _Clock()
    adapter, calls = _counting_adapter(clock)

    await adapter.capabilities(GGUF)
    clock.now += 61.0
    await adapter.capabilities(GGUF)

    assert calls[0] == 2


async def test_a_failed_read_is_not_cached() -> None:
    """A cached failure would turn a blip into a minute of empty pools.

    Every model looks capability-less while the catalogue is unreadable, and
    capability-less fails closed under §9.1 — so holding a transient outage for
    the whole window would take the pools down for far longer than the outage.
    """
    adapter, calls = _counting_adapter(_Clock(), fail=True)

    known = await adapter.capabilities(GGUF)
    await adapter.capabilities(GGUF)

    assert known.state_of(Capability.TOOLS) is CapabilityState.UNKNOWN
    assert calls[0] == 2
