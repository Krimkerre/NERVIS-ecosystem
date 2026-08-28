"""`ravis preflight clarvis` — will Clarvis work if pointed here right now?

M9's acceptance is "point unmodified Clarvis at RAVIS, configuration only", and
its two failure modes are both silent from inside VS Code:

- **The base URL is written with `/v1` on the end.** Clarvis appends the path
  itself — `${baseUrl}/v1/models`, `${baseUrl}/v1/chat/completions`, verified in
  `clarvis/src/model/OpenAiCompatibleProvider.ts` — so a base of
  `http://127.0.0.1:8731/v1` produces `/v1/v1/models`, a 404, and a provider
  Clarvis reports as **offline**. Nothing in the message says the URL is wrong.
- **A pool has no eligible model.** `ravis/clarvis-agent` requires tools, and on
  this installation nothing yet *claims* them: provider catalogues are asked for
  a tool-support key and Anthropic's does not publish one, so the capability
  stays UNKNOWN and fails closed rather than being assumed. M13 consumes SIRVIS
  evidence, but SIRVIS measures local builds — it has nothing to say about an
  API model. So until an operator declares tool support (`configured_capabilities`)
  or a measurement exists, the pool is legitimately unavailable (§5.2). From
  Clarvis that looks like the agent being broken.

So this prints the settings to paste, and then actually resolves both pools
against the live catalogue and says what each one would select. Unlike `doctor`,
it *does* contact the upstream — that is the point, and it is why it is a
separate command rather than another section of a check that promises not to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from ravis.config import Settings, resolved_capabilities
from ravis.providers.generic_openai import GenericOpenAiAdapter
from ravis.registry import ModelRegistry
from ravis.routing import RoutingEngine
from ravis.routing.explain import RouteDecision
from ravis.upstream import create_client, upstream_from

# The two pools Clarvis addresses, and the setting that points at each.
CLARVIS_POOLS = (
    ("ravis/clarvis-chat", "clarvis.chat.model"),
    ("ravis/clarvis-agent", "clarvis.agent.model"),
)


@dataclass
class Preflight:
    """What Clarvis would get, and whether that is enough to work."""

    base_url: str
    models_listed: int
    decisions: dict[str, RouteDecision] = field(default_factory=dict)
    upstream_error: str = ""

    @property
    def ready(self) -> bool:
        """True when both pools resolve. Either failing means the agent or the
        chat side is dead on arrival, and both are worth failing the command."""
        return (
            not self.upstream_error
            and bool(self.decisions)
            and all(decision.routed for decision in self.decisions.values())
        )


def clarvis_settings(settings: Settings) -> dict[str, str]:
    """The VS Code settings that point Clarvis at this RAVIS.

    The base URL carries **no `/v1` suffix**, and that is the whole reason this
    function exists rather than a paragraph in a README: Clarvis builds the path
    itself, and the wrong value fails as "offline" rather than as "bad URL".

    `clarvis.chat.baseUrl.custom` is keyed by *provider*, not by role
    (`ModelService.baseUrl` reads `chat.baseUrl.${spec.id}`), so one URL serves
    both roles and only the two model settings differ. That is what makes the
    chat/agent split configuration rather than a code change.
    """
    host = "127.0.0.1" if settings.host == "0.0.0.0" else settings.host  # noqa: S104
    return {
        "clarvis.chat.provider": "custom",
        "clarvis.chat.baseUrl.custom": f"http://{host}:{settings.port}",
        "clarvis.chat.model": "ravis/clarvis-chat",
        "clarvis.agent.provider": "custom",
        "clarvis.agent.model": "ravis/clarvis-agent",
    }


async def run_preflight(
    settings: Settings, client: httpx.AsyncClient | None = None
) -> Preflight:
    """Read the live catalogue and resolve both Clarvis pools against it.

    `client` is injectable so the tests can run this against a recorded upstream
    rather than a socket (runbook §14.5). A caller-supplied client is *not*
    closed here — it belongs to whoever made it.
    """
    upstream = upstream_from(settings)
    owned = client is None
    connection = client or create_client(settings)
    registry = ModelRegistry(
        upstream=upstream, client=connection, ttl_seconds=settings.models_cache_ttl_seconds
    )
    adapter = GenericOpenAiAdapter(
        upstream=upstream,
        client=connection,
        configured_capabilities=resolved_capabilities(settings),
    )
    try:
        return await _resolve(settings, registry, adapter)
    finally:
        if owned:
            await connection.aclose()


async def _resolve(
    settings: Settings, registry: ModelRegistry, adapter: GenericOpenAiAdapter
) -> Preflight:
    """Refresh the catalogue and route each pool, or report why we cannot.

    Health and residency are deliberately absent from the `select` call. This
    reports what the *configuration* can do; folding in a circuit breaker's
    current state would make the answer depend on traffic that has not happened
    yet in a freshly started process.
    """
    base = f"http://{settings.host}:{settings.port}"
    await registry.refresh()
    models = registry.model_ids()
    if not models:
        return Preflight(
            base_url=base,
            models_listed=0,
            upstream_error=(
                f"the upstream at {settings.upstream_base_url or '(unset)'} listed no models"
                if settings.upstream_base_url
                else "no upstream is configured — set RAVIS_UPSTREAM_BASE_URL"
            ),
        )

    engine = RoutingEngine()
    candidates = {model: await adapter.capabilities(model) for model in models}
    return Preflight(
        base_url=base,
        models_listed=len(models),
        decisions={pool: engine.select(pool, candidates) for pool, _ in CLARVIS_POOLS},
    )


def render(result: Preflight, settings: Settings) -> str:
    """The operator-facing report: what to paste, and what it will do."""
    lines = ["Clarvis → RAVIS preflight", "", "Paste into VS Code settings.json:", ""]
    lines += [f'  "{key}": "{value}",' for key, value in clarvis_settings(settings).items()]
    lines += [
        "",
        "  The base URL has no /v1 on purpose — Clarvis appends the path itself,",
        "  and the doubled prefix reports as 'provider offline' rather than 404.",
        "",
    ]
    if result.upstream_error:
        lines += [f"  BLOCKED  {result.upstream_error}", "", "FAIL"]
        return "\n".join(lines)

    lines.append(
        f"{result.models_listed} model(s) in the catalogue; both pools resolved against it:"
    )
    lines.append("")
    for pool, setting in CLARVIS_POOLS:
        lines += _pool_lines(pool, setting, result.decisions[pool])
    lines.append("PASS" if result.ready else "FAIL")
    return "\n".join(lines)


def _pool_lines(pool: str, setting: str, decision: RouteDecision) -> list[str]:
    """One pool's verdict, with the fix when there is one to give."""
    if decision.routed:
        chain = " → ".join([decision.selected or "", *decision.fallbacks])
        return [f"  [PASS] {setting:<20} {pool}", f"         {chain}", ""]
    return [
        f"  [FAIL] {setting:<20} {pool}",
        f"         {decision.reason}",
        "         declare what the models can do in RAVIS_MODEL_CAPABILITIES —"
        " nothing probes yet (§8.7, M13)",
        "",
    ]


def as_dict(result: Preflight) -> dict[str, Any]:
    """The same verdict as data, for anything that would rather not parse text."""
    return {
        "ready": result.ready,
        "base_url": result.base_url,
        "models_listed": result.models_listed,
        "upstream_error": result.upstream_error,
        "pools": {pool: decision.as_dict() for pool, decision in result.decisions.items()},
    }
