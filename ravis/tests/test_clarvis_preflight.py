"""`ravis preflight clarvis` — M9's two silent failure modes, made loud.

Both are things that look like Clarvis being broken from inside VS Code, and
neither produces a message naming the real cause. That is the entire reason this
command exists, so these tests assert the *diagnosis*, not just the exit code.
"""

from __future__ import annotations

import httpx

from ravis.compatibility.clarvis.preflight import clarvis_settings, render, run_preflight
from ravis.config import Settings

# Every requirement `ravis/clarvis-agent` declares, which is now more than
# tools: §5.1 asks it for structured calls and long context too, and a fixture
# short of that resolves to an unavailable pool rather than to a route.
TOOL_CAPABLE = {
    "tools": "SUPPORTED",
    "structured_output": "SUPPORTED",
    "context_window": "131072",
}


def _settings(**overrides: object) -> Settings:
    return Settings(
        database_path=":memory:",
        upstream_base_url="http://upstream.invalid",
        _env_file=None,  # type: ignore[call-arg]
        **overrides,  # type: ignore[arg-type]
    )


def _upstream(models: list[str]) -> httpx.AsyncClient:
    """A catalogue-only upstream: the preflight never sends a completion."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200, json={"object": "list", "data": [{"id": m} for m in models]}
            )
        return httpx.Response(404, json={"error": "not used by preflight"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def test_the_base_url_never_carries_a_v1_suffix() -> None:
    """Verified against `clarvis/src/model/OpenAiCompatibleProvider.ts`.

    Clarvis builds `${baseUrl}/v1/models` itself. A base ending in `/v1` yields
    `/v1/v1/models`, a 404, and a provider Clarvis reports as **offline** — a
    message that names nothing. Getting this one string right is most of what
    M9's configuration step is.
    """
    written = clarvis_settings(_settings())["clarvis.chat.baseUrl.custom"]

    assert written == "http://127.0.0.1:8731"
    assert not written.endswith("/v1")


def test_one_base_url_serves_both_roles() -> None:
    """`ModelService.baseUrl` keys on provider, not role — so there is only one.

    This is why the chat/agent split is configuration rather than a code change:
    two model settings, one endpoint.
    """
    written = clarvis_settings(_settings())

    assert written["clarvis.chat.provider"] == written["clarvis.agent.provider"] == "custom"
    assert [key for key in written if key.endswith("baseUrl.custom")] == [
        "clarvis.chat.baseUrl.custom"
    ]
    assert written["clarvis.chat.model"] != written["clarvis.agent.model"]


async def test_both_pools_resolve_when_capabilities_are_declared() -> None:
    settings = _settings(model_capabilities={"coder-a": TOOL_CAPABLE})
    client = _upstream(["coder-a"])

    result = await run_preflight(settings, client)
    await client.aclose()

    assert result.ready is True
    assert result.decisions["ravis/clarvis-agent"].selected == "coder-a"
    assert "PASS" in render(result, settings)


async def test_an_undeclared_catalogue_fails_with_the_fix_in_the_message() -> None:
    """§5.2 working as written, and the surprise STATUS.md already flags.

    Nothing probes capabilities yet, so a model that can call tools is still
    UNKNOWN until an operator says so — and the agent pool fails closed. Correct,
    and completely opaque from VS Code, which is why the report names the
    setting to change.
    """
    settings = _settings()
    client = _upstream(["coder-a"])

    result = await run_preflight(settings, client)
    await client.aclose()
    report = render(result, settings)

    assert result.ready is False
    assert result.decisions["ravis/clarvis-agent"].routed is False
    # Chat has no tool invariant, so it still resolves — the report must show
    # one pool working and one not, rather than a single verdict for both.
    assert result.decisions["ravis/clarvis-chat"].routed is True
    assert "RAVIS_MODEL_CAPABILITIES" in report


async def test_an_unreachable_upstream_is_reported_as_such() -> None:
    """Distinct from "the pool has no eligible model" — different fix entirely."""
    settings = _settings()
    client = _upstream([])

    result = await run_preflight(settings, client)
    await client.aclose()

    assert result.ready is False
    assert "listed no models" in result.upstream_error


async def test_an_unconfigured_upstream_says_which_variable_to_set() -> None:
    settings = Settings(database_path=":memory:", _env_file=None)  # type: ignore[call-arg]
    client = _upstream([])

    result = await run_preflight(settings, client)
    await client.aclose()

    assert "RAVIS_UPSTREAM_BASE_URL" in result.upstream_error
