"""RAVIS's own tool trials of hosted models (§13.3's OBSERVED_BY_RAVIS).

Found on 11 September 2026: Anthropic's catalogue publishes no tool support, so every
Claude model bought directly was refused by `ravis/clarvis-agent` until an operator
declared one by hand. These tests cover the trial that lets RAVIS find out itself.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
)
from ravis.core.responses import NormalizedResponse, ToolCall
from ravis.reliability.failures import FailureClass
from ravis.storage.database import prepare_database
from ravis.trials import (
    CombinedEvidence,
    TrialOutcome,
    TrialStore,
    choose_trials,
    outcome_from_calls,
    outcome_from_failure,
    try_translated,
    try_transparent,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def _tools(provenance: Provenance) -> CapabilityClaim:
    return CapabilityClaim(
        capability=Capability.TOOLS, state=CapabilityState.SUPPORTED, provenance=provenance
    )


def _hosted(name: str) -> ModelCapabilities:
    """A direct build as Anthropic's catalogue describes one: long, structured, no tool claim."""
    known = ModelCapabilities(model_id=name, context_window=1_000_000)
    known.record(
        CapabilityClaim(
            capability=Capability.STRUCTURED_OUTPUT,
            state=CapabilityState.SUPPORTED,
            provenance=Provenance.ADVERTISED,
        )
    )
    return known


def test_a_trial_outranks_a_catalogue_flag_and_yields_to_measurement_and_the_operator() -> None:
    observed = _tools(Provenance.OBSERVED)

    assert observed.outranks(_tools(Provenance.ADVERTISED))
    assert _tools(Provenance.MEASURED).outranks(observed)
    assert _tools(Provenance.CONFIGURED).outranks(observed)


def test_a_conclusive_trial_is_a_claim_until_it_is_a_month_old() -> None:
    clock = Clock()
    store = TrialStore(prepare_database(":memory:"), clock=clock, max_age_seconds=30 * 86400.0)
    store.record("claude-opus-5", outcome_from_calls(1), "anthropic")

    [claim] = store.claims_for("claude-opus-5")
    assert claim.state is CapabilityState.SUPPORTED
    assert claim.provenance is Provenance.OBSERVED
    assert not store.due("claude-opus-5")

    clock.now += 31 * 86400.0
    assert store.claims_for("claude-opus-5") == []
    assert store.due("claude-opus-5")


def test_an_inconclusive_trial_claims_nothing_and_is_tried_again_hours_later() -> None:
    clock = Clock()
    store = TrialStore(prepare_database(":memory:"), clock=clock)
    store.record(
        "gpt-5-mini", TrialOutcome(None, "inconclusive (timeout)", retry_soon=True), "openai"
    )

    assert store.claims_for("gpt-5-mini") == []
    assert not store.due("gpt-5-mini")
    clock.now += 7 * 3600.0
    assert store.due("gpt-5-mini")
    assert store.tried_since(0.0) == 1


def test_combined_evidence_reads_sirvis_and_the_trials_together() -> None:
    class Sirvis:
        def claims_for(self, model: str) -> list[CapabilityClaim]:
            return [_tools(Provenance.MEASURED)] if model == "local-build" else []

        def context_window(self, model: str) -> int | None:
            return 32768 if model == "local-build" else None

    store = TrialStore(prepare_database(":memory:"), clock=Clock())
    store.record("claude-opus-5", outcome_from_calls(1), "anthropic")
    combined = CombinedEvidence(Sirvis(), store)

    assert [claim.provenance for claim in combined.claims_for("local-build")] == [
        Provenance.MEASURED
    ]
    assert [claim.provenance for claim in combined.claims_for("claude-opus-5")] == [
        Provenance.OBSERVED
    ]
    assert combined.context_window("local-build") == 32768
    assert CombinedEvidence(None, store).context_window("claude-opus-5") is None


def test_only_a_hosted_model_nobody_has_vouched_for_is_chosen() -> None:
    unknown = _hosted("claude-opus-5")
    advertised = _hosted("anthropic/claude-opus-5")
    advertised.record(_tools(Provenance.ADVERTISED))
    local = _hosted("qwen2.5-coder-7b-instruct")
    candidates = {known.model_id: known for known in (unknown, advertised, local)}
    remote = frozenset({"claude-opus-5", "anthropic/claude-opus-5"})

    assert choose_trials(candidates, remote, lambda _model: True, 5) == ["claude-opus-5"]
    assert choose_trials(candidates, remote, lambda _model: False, 5) == [], "not yet due"
    assert choose_trials(candidates, remote, lambda _model: True, 0) == []


def test_a_pass_is_bounded_by_its_limit() -> None:
    candidates = {name: _hosted(name) for name in ("claude-opus-4-8", "claude-opus-5")}

    assert len(choose_trials(candidates, frozenset(candidates), lambda _model: True, 1)) == 1


def test_the_makers_own_copies_are_tried_before_an_aggregators_listings() -> None:
    """Alphabetically `anthropic/…` comes first; the first live pass went that way."""
    candidates = {name: _hosted(name) for name in ("anthropic/claude-opus-4-8", "claude-opus-5")}
    remote = frozenset(candidates)

    assert choose_trials(candidates, remote, lambda _model: True, 1) == ["claude-opus-5"]
    assert choose_trials(candidates, remote, lambda _model: True, 2) == [
        "claude-opus-5",
        "anthropic/claude-opus-4-8",
    ]


def test_only_a_call_or_a_refusal_of_tools_concludes_anything() -> None:
    assert outcome_from_calls(1).state is CapabilityState.SUPPORTED
    assert outcome_from_calls(0).state is CapabilityState.UNSUPPORTED
    refused = outcome_from_failure(FailureClass.TOOL_INCOMPATIBILITY, "does not support tools")
    assert refused.state is CapabilityState.UNSUPPORTED
    assert outcome_from_failure(FailureClass.RATE_LIMIT, "slow down").state is None
    assert outcome_from_failure(FailureClass.RATE_LIMIT, "slow down").retry_soon
    assert not outcome_from_failure(FailureClass.INVALID_REQUEST, "not a chat model").retry_soon


def test_a_refusal_unrelated_to_tools_claims_nothing_and_waits_the_month() -> None:
    """A model that takes no chat request at all would otherwise be asked again every six
    hours, spending the day's trials on the same refusal."""
    clock = Clock()
    store = TrialStore(prepare_database(":memory:"), clock=clock, max_age_seconds=30 * 86400.0)
    store.record(
        "gpt-realtime",
        outcome_from_failure(
            FailureClass.INVALID_REQUEST, "this model does not take chat requests"
        ),
        "openai",
    )

    assert store.claims_for("gpt-realtime") == []
    clock.now += 7 * 3600.0
    assert not store.due("gpt-realtime")
    clock.now += 30 * 86400.0
    assert store.due("gpt-realtime")


def test_a_translated_provider_is_tried_through_its_own_adapter() -> None:
    class Calling:
        def __init__(self) -> None:
            self.asked: list[tuple[str, Any]] = []

        async def complete(self, request: Any) -> NormalizedResponse:
            self.asked.append((request.requested_model, request.tool_choice))
            return NormalizedResponse(
                tool_calls=[ToolCall(index=0, name="report_ready", arguments="{}")]
            )

    class Refusing:
        async def complete(self, _request: Any) -> NormalizedResponse:
            error = RuntimeError("this model does not support tools")
            error.status = 400  # type: ignore[attr-defined]
            raise error

    adapter = Calling()
    outcome, _usage = asyncio.run(try_translated(adapter, "claude-opus-5"))
    refused, _usage = asyncio.run(try_translated(Refusing(), "claude-legacy"))

    assert outcome.state is CapabilityState.SUPPORTED
    assert adapter.asked == [("claude-opus-5", "required")]
    assert refused.state is CapabilityState.UNSUPPORTED


def test_an_openai_compatible_provider_is_tried_over_http_with_its_own_field_names() -> None:
    sent: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "type": "function",
                                    "function": {"name": "report_ready", "arguments": "{}"},
                                }
                            ]
                        }
                    }
                ],
                "usage": {"prompt_tokens": 40, "completion_tokens": 8},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    outcome, usage = asyncio.run(
        try_transparent(
            client, "https://api.openai.com/v1/chat/completions", "sk-test", "gpt-5-mini"
        )
    )

    assert outcome.state is CapabilityState.SUPPORTED
    assert "max_completion_tokens" in sent[0] and "max_tokens" not in sent[0]
    assert usage is not None


def test_a_provider_refusing_tools_over_http_is_recorded_as_unsupported() -> None:
    refusal = httpx.Response(400, json={"error": {"message": "This model does not support tools"}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: refusal))

    outcome, _usage = asyncio.run(
        try_transparent(client, "https://provider.test/v1/chat/completions", "k", "some-model")
    )

    assert outcome.state is CapabilityState.UNSUPPORTED
