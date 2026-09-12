"""RAVIS trying a hosted model's tool support itself, when nothing else has said.

**Why this exists.** A pool that requires tools admits only models known to support
them, and the providers RAVIS reaches directly — Anthropic, OpenAI and Google — publish
no tool support in their catalogues. Found on 11 September 2026: every Claude model
bought from Anthropic sat at UNKNOWN and was refused by `ravis/clarvis-agent`, so every
Clarvis build went to a smaller model, and the operator had to declare Claude Sonnet 5
and Haiku tool-capable by hand. Nothing else closes that gap: SIRVIS measures local
runtimes only, and a hosted model is known by being used — which a pool that refuses it
never does.

So RAVIS tries: one small request per model, carrying one tool the model is required to
call. A call is recorded as SUPPORTED and a provider refusing tools as UNSUPPORTED;
anything else — a timeout, a rate limit, a rejected key — establishes nothing and is
tried again hours later. §13.3 names this provenance OBSERVED_BY_RAVIS: better evidence
than a catalogue flag, weaker than SIRVIS's controlled measurement, and below an
operator's own declaration, which still wins.

**Bounded on every side.** Hosted models only, never a local runtime — a trial there
would load a model, which RAVIS never does on its own account. Only models a
tool-requiring pool would admit if tools were supported. A few per pass, a ceiling per
day, and a result kept for a month. Every trial is recorded on the spend screen like any
other call, because it is one.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
)
from ravis.core.pools import DEFAULT_POOLS
from ravis.core.requests import normalize
from ravis.core.responses import Usage
from ravis.cost import PriceBook, UsageLedger, UsageRecord, estimate, price_from_book
from ravis.reliability.failures import FailureClass, classify_exception, classify_response
from ravis.storage.database import Database
from ravis.transparent import merged_candidates, remote_models, resolve, translated_candidates

logger = logging.getLogger("ravis.trials")

TRIAL_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "report_ready",
        "description": "Report that this model can call tools.",
        "parameters": {"type": "object", "properties": {}},
    },
}
TRIAL_PROMPT = (
    "This is an automated check of whether you can call tools. Call the report_ready tool now."
)
# How a trial appears on the spend screen: RAVIS's own call, not an application's.
TRIAL_APPLICATION = "ravis"
TRIAL_POOL = "capability-trial"
# The stored state for a trial that established nothing.
INCONCLUSIVE = "INCONCLUSIVE"
# The stored state for a trial the provider refused for a reason that is not about tools:
# a model that takes no chat request at all, a parameter it will not accept. Asking again in
# six hours would get the same answer and spend the day's trials getting it, so it waits
# as long as a conclusive result does.
REFUSED = "REFUSED"
# A trial that established nothing is tried again after this long, not on the next pass:
# a provider that is down or rate-limiting does not want a fresh request every five minutes.
RETRY_INCONCLUSIVE_SECONDS = 6 * 3600.0
TRIAL_TIMEOUT_SECONDS = 30.0
DAY_SECONDS = 86400.0


def trial_payload(model: str) -> dict[str, Any]:
    """The request a trial sends: one tool, which the model is required to call."""
    return {
        "model": model,
        "messages": [{"role": "user", "content": TRIAL_PROMPT}],
        "tools": [TRIAL_TOOL],
        # Required rather than left to the model. Anthropic's translation renders it as
        # `any`, Gemini's as `ANY`, and OpenAI-compatible providers take it as written.
        "tool_choice": "required",
        "max_tokens": 64,
        "stream": False,
    }


@dataclass(frozen=True)
class TrialOutcome:
    """What one trial established. `state` is None when it established nothing.

    `retry_soon` says whether a trial that established nothing is worth repeating in hours
    (a timeout, a rate limit) or only when a month is up (a refusal unrelated to tools).
    """

    state: CapabilityState | None
    detail: str
    retry_soon: bool = False


def outcome_from_calls(calls: int) -> TrialOutcome:
    """A model required to call a tool either did, or answered without it."""
    if calls > 0:
        return TrialOutcome(CapabilityState.SUPPORTED, "called the tool RAVIS required it to call")
    return TrialOutcome(
        CapabilityState.UNSUPPORTED, "answered without calling the tool RAVIS required it to call"
    )


def outcome_from_failure(failure: FailureClass, message: str) -> TrialOutcome:
    """Only a refusal of tools says anything about tools; every other failure is silence."""
    if failure is FailureClass.TOOL_INCOMPATIBILITY:
        return TrialOutcome(
            CapabilityState.UNSUPPORTED, f"the provider refused tools: {message[:160]}"
        )
    if failure in TRANSIENT_FAILURES:
        return TrialOutcome(
            None, f"inconclusive ({failure.value}): {message[:160]}", retry_soon=True
        )
    return TrialOutcome(
        None, f"refused for a reason that is not about tools ({failure.value}): {message[:160]}"
    )


# Failures that say nothing about the model and may well be gone in a few hours.
TRANSIENT_FAILURES = frozenset(
    {
        FailureClass.TIMEOUT,
        FailureClass.CONNECTION,
        FailureClass.RATE_LIMIT,
        FailureClass.OVERLOAD,
        FailureClass.INVALID_UPSTREAM_RESPONSE,
        FailureClass.AUTHENTICATION,
        FailureClass.UNKNOWN,
    }
)


def stored_state(outcome: TrialOutcome) -> str:
    """How an outcome is written down: the capability state, or why there is none."""
    if outcome.state is not None:
        return outcome.state.value
    return INCONCLUSIVE if outcome.retry_soon else REFUSED


def failure_class_of(failure: Exception) -> FailureClass:
    """A translated adapter's error by the status and words it carries, else by transport."""
    status = getattr(failure, "status", None)
    if isinstance(status, int):
        return classify_response(status, str(failure).encode()) or FailureClass.UNKNOWN
    return classify_exception(failure)


class TrialStore:
    """Trial outcomes, persisted, one row per model and capability.

    Persisted because the point is to try each model once a month, not once a restart.
    """

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], float] = time.time,
        max_age_seconds: float = 30 * DAY_SECONDS,
    ) -> None:
        self._database = database
        self._clock = clock
        self._max_age = max_age_seconds

    def record(self, model: str, outcome: TrialOutcome, provider: str) -> None:
        """Keep what a trial established, replacing any earlier trial of the same model."""
        state = stored_state(outcome)
        with self._database.connection as connection:
            connection.execute(
                "INSERT INTO capability_trial"
                " (model, capability, state, detail, provider, tried_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(model, capability) DO UPDATE SET state = excluded.state,"
                " detail = excluded.detail, provider = excluded.provider,"
                " tried_at = excluded.tried_at",
                (model, Capability.TOOLS.value, state, outcome.detail, provider, self._clock()),
            )

    def claims_for(self, model: str) -> list[CapabilityClaim]:
        """A fresh, conclusive trial as a claim; anything else as nothing."""
        row = self._row(model)
        if (
            row is None
            or row[0] in (INCONCLUSIVE, REFUSED)
            or self._clock() - row[2] > self._max_age
        ):
            return []
        return [
            CapabilityClaim(
                capability=Capability.TOOLS,
                state=CapabilityState(row[0]),
                provenance=Provenance.OBSERVED,
                detail=row[1],
            )
        ]

    def due(self, model: str) -> bool:
        """Whether this model may be tried now: never tried, stale, or retry time reached."""
        row = self._row(model)
        if row is None:
            return True
        wait = RETRY_INCONCLUSIVE_SECONDS if row[0] == INCONCLUSIVE else self._max_age
        return self._clock() - row[2] >= wait

    def tried_since(self, moment: float) -> int:
        """How many models have been tried since `moment`, for the daily ceiling."""
        found = self._database.connection.execute(
            "SELECT COUNT(*) FROM capability_trial WHERE tried_at >= ?", (moment,)
        ).fetchone()
        return int(found[0]) if found else 0

    def _row(self, model: str) -> tuple[str, str, float] | None:
        found = self._database.connection.execute(
            "SELECT state, detail, tried_at FROM capability_trial"
            " WHERE model = ? AND capability = ?",
            (model, Capability.TOOLS.value),
        ).fetchone()
        return (str(found[0]), str(found[1]), float(found[2])) if found else None


class CombinedEvidence:
    """SIRVIS's measurements and RAVIS's own trials, read as the one evidence store.

    The candidate builders already take a store with `claims_for` and `context_window`,
    so trials join them there — the one funnel routing and the management screens share.
    """

    def __init__(self, sirvis: Any, trials: TrialStore | None) -> None:
        self._sirvis = sirvis
        self._trials = trials

    def claims_for(self, model: str) -> list[CapabilityClaim]:
        measured = list(self._sirvis.claims_for(model)) if self._sirvis is not None else []
        observed = self._trials.claims_for(model) if self._trials is not None else []
        return [*measured, *observed]

    def context_window(self, model: str) -> int | None:
        if self._sirvis is None:
            return None
        window: int | None = self._sirvis.context_window(model)
        return window


def choose_trials(
    candidates: Mapping[str, ModelCapabilities],
    remote: frozenset[str],
    due: Callable[[str], bool],
    limit: int,
) -> list[str]:
    """The hosted models worth trying now, at most `limit` of them.

    A model qualifies when it is hosted, nothing has said anything about its tools, a
    pool that requires tools would admit it if tools were supported, and it is due.
    """
    if limit <= 0:
        return []
    names = sorted(candidates)
    prices = {model: known.price_per_million for model, known in candidates.items()}
    tool_pools = [pool for pool in DEFAULT_POOLS if Capability.TOOLS in pool.requirements.required]
    members = {
        pool.pool_id: set(pool.default_membership(names, prices, None)) for pool in tool_pools
    }
    chosen: list[str] = []
    for model in names:
        known = candidates[model]
        if model not in remote or known.state_of(Capability.TOOLS) is not CapabilityState.UNKNOWN:
            continue
        supposed = copy.deepcopy(known)
        supposed.record(
            CapabilityClaim(
                capability=Capability.TOOLS,
                state=CapabilityState.SUPPORTED,
                provenance=Provenance.CONFIGURED,
            )
        )
        wanted = any(
            model in members[pool.pool_id] and not pool.requirements.unmet_by(supposed, remote=True)
            for pool in tool_pools
        )
        if not wanted or not due(model):
            continue
        chosen.append(model)
        if len(chosen) >= limit:
            break
    return chosen


async def try_translated(adapter: Any, model: str) -> tuple[TrialOutcome, Usage | None]:
    """One trial through a translated provider's own adapter (Anthropic, Google)."""
    payload = trial_payload(model)
    request = normalize(json.dumps(payload).encode(), payload)
    request.requested_model = model
    try:
        response = await adapter.complete(request)
    except Exception as failure:  # noqa: BLE001 - a trial reports what happened, whatever it was
        return outcome_from_failure(failure_class_of(failure), str(failure)), None
    return outcome_from_calls(len(response.tool_calls)), response.usage


async def try_transparent(
    client: httpx.AsyncClient, url: str, key: str, model: str
) -> tuple[TrialOutcome, Usage | None]:
    """One trial against an OpenAI-compatible upstream, with that upstream's own key."""
    payload = trial_payload(model)
    if httpx.URL(url).host == "api.openai.com":
        # OpenAI's current models refuse `max_tokens`; the same rename as chat.py's
        # `_for_openai`, applied to a body RAVIS writes rather than one it forwards.
        payload["max_completion_tokens"] = payload.pop("max_tokens")
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    try:
        response = await client.post(
            url, json=payload, headers=headers, timeout=TRIAL_TIMEOUT_SECONDS
        )
    except Exception as failure:  # noqa: BLE001 - a trial reports what happened, whatever it was
        return outcome_from_failure(classify_exception(failure), str(failure)), None
    failed = classify_response(response.status_code, response.content)
    if failed is not None:
        return outcome_from_failure(failed, response.text), None
    try:
        body = response.json()
    except ValueError:
        return TrialOutcome(None, "inconclusive: the reply was not JSON", retry_soon=True), None
    choices = body.get("choices") if isinstance(body, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else {}
    message = first.get("message") if isinstance(first, dict) else None
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    # Imported here: the chat module imports half the package, this one is imported by it.
    from ravis.api.openai.chat import _usage_in_body

    return outcome_from_calls(len(calls) if isinstance(calls, list) else 0), _usage_in_body(
        response.content
    )


def note_trial_spend(
    ledger: UsageLedger | None,
    prices: PriceBook | None,
    model: str,
    provider: str,
    usage: Usage | None,
) -> None:
    """A trial on the spend screen, like any other call — its cost is real."""
    if ledger is None:
        return
    price = prices.price_of(model) if prices is not None else None
    amount, state = estimate(price, usage)
    ledger.record(
        UsageRecord(
            model=model,
            provider=provider,
            application_id=TRIAL_APPLICATION,
            usage=usage,
            cost=amount,
            cost_state=state,
            currency=price.currency if price else None,
            price_source=price.source if price else "",
            price_captured_at=price.captured_at if price else 0.0,
            pool=TRIAL_POOL,
        )
    )


async def run_trial_pass(
    api: Any, settings: Any, now: float | None = None
) -> list[tuple[str, TrialOutcome]]:
    """Choose, try and record, within the pass and daily limits."""
    store: TrialStore | None = getattr(api.state, "trials", None)
    if store is None or not settings.capability_trials:
        return []
    moment = time.time() if now is None else now
    budget = min(
        settings.capability_trials_per_pass,
        settings.capability_trials_per_day - store.tried_since(moment - DAY_SECONDS),
    )
    if budget <= 0:
        return []
    transparents = getattr(api.state, "transparents", {})
    provider_state = getattr(api.state, "provider_state", None)
    disabled = frozenset(provider_state.disabled()) if provider_state else frozenset()
    filter_holder = getattr(api.state, "model_filters", None)
    filters = filter_holder.all() if filter_holder else None
    evidence = getattr(api.state, "capability_evidence", None)
    translating = {
        name: adapter
        for name, adapter in getattr(api.state, "translating", {}).items()
        if name not in disabled and adapter.has_credential
    }
    candidates = (
        await merged_candidates(transparents, evidence, disabled, filters) if transparents else {}
    )
    translated, owners = await translated_candidates(translating, evidence)
    for model, known in translated.items():
        candidates.setdefault(model, known)
    price_from_book(candidates, getattr(api.state, "prices", None))
    remote = remote_models(transparents) | frozenset(owners)
    outcomes: list[tuple[str, TrialOutcome]] = []
    for model in choose_trials(candidates, remote, store.due, budget):
        owner = owners.get(model)
        if owner is not None:
            provider = owner
            outcome, usage = await try_translated(translating[owner], model)
        else:
            served = resolve(transparents, model, filters)
            if served is None or not served.upstream.key():
                continue
            provider = served.name
            outcome, usage = await try_transparent(
                api.state.upstream_client,
                served.upstream.api_url("/chat/completions"),
                served.upstream.key(),
                model,
            )
        store.record(model, outcome, provider)
        note_trial_spend(
            getattr(api.state, "usage_ledger", None),
            getattr(api.state, "prices", None),
            model,
            provider,
            usage,
        )
        logger.info(
            "capability trial: %s via %s -> %s (%s)",
            model,
            provider,
            stored_state(outcome),
            outcome.detail,
        )
        outcomes.append((model, outcome))
    return outcomes


async def run_trials_periodically(api: Any, settings: Any) -> None:
    """A trial pass on the catalogue timer, once the service has had time to warm."""
    await asyncio.sleep(settings.capability_trial_start_delay_seconds)
    while True:
        try:
            await run_trial_pass(api, settings)
        except Exception:  # noqa: BLE001 - a failed pass must not end the loop
            logger.exception("capability trial pass failed")
        await asyncio.sleep(settings.models_cache_ttl_seconds)
