"""§14's budgets, as many as the owner sets: overall, per application, per provider (`budgets.py`).

Three layers, each tested where it lives: the rules and their arithmetic against a real ledger;
what the budgets do to routing, through the real policy and engine; and the read and write the
dashboard uses, through the app. The files land under the suite's throwaway config directory.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.conftest import as_administrator
from tests.conftest_upstream import RecordingUpstream
from tests.test_transparent_proxy import _app_with

from ravis.budgets import (
    CONFIGURED_ID,
    BudgetBook,
    BudgetConfigurationError,
    BudgetRule,
    status_of,
)
from ravis.config import Settings
from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
)
from ravis.cost import BudgetBand, CostState, UsageLedger, UsageRecord
from ravis.credentials import config_directory
from ravis.policy import (
    RoutingPolicy,
    effective_policy,
    policy_refusals,
    with_budget_pressure,
)
from ravis.routing.engine import RoutingEngine

NOW = 2_000_000.0
DAY = 24 * 3600.0


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


def _ledger(*spent: tuple[str, str, float | None, str | None]) -> UsageLedger:
    """A ledger holding (application, provider, cost, currency) records, all just now."""
    ledger = UsageLedger(clock=Clock())
    for application, provider, cost, currency in spent:
        ledger.record(UsageRecord(
            model="m", provider=provider, application_id=application, cost=cost,
            cost_state=CostState.ESTIMATED if cost is not None else CostState.UNKNOWN,
            currency=currency,
        ))
    return ledger


def _book(*budgets: dict[str, Any], limit: float = 0.0, hard: bool = False) -> BudgetBook:
    """A book with the owner's `budgets`, and a configured one when `limit` is set."""
    book = BudgetBook.load(Settings(budget_limit=limit, budget_hard=hard, _env_file=None))  # type: ignore[call-arg]
    book.replace(list(budgets))
    return book


# ── The rules ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("entry", "said"),
    [
        ({"scope": "everyone", "limit": 5}, "scope must be one of"),
        ({"scope": "application", "limit": 5}, "names the application it covers"),
        ({"scope": "provider", "target": "Anthropic", "limit": 5}, "in lower case"),
        ({"limit": 0}, "more than zero"),
        ({"limit": "lots"}, "numeric 'limit'"),
        ({"limit": 5, "currency": "dollars"}, "three letters"),
        ({"limit": 5, "period": "yearly"}, "period must be one of"),
        ({"limit": 5, "hard": "yes"}, "true or false"),
        ({"limit": 5, "colour": "red"}, "unrecognised ['colour']"),
    ],
)
def test_a_budget_that_cannot_be_one_is_refused_with_the_reason(
    entry: dict[str, Any], said: str
) -> None:
    with pytest.raises(BudgetConfigurationError) as refused:
        _book(entry)
    assert said in str(refused.value)


def test_the_same_slice_and_period_twice_is_refused() -> None:
    with pytest.raises(BudgetConfigurationError) as refused:
        _book({"scope": "application", "target": "clarvis", "limit": 5, "period": "weekly"},
              {"scope": "application", "target": "clarvis", "limit": 9, "period": "weekly"})
    assert "clarvis's weekly budget is set twice" in str(refused.value)


def test_budgets_survive_a_restart_with_their_ids_and_a_client_cannot_choose_one() -> None:
    book = _book({"limit": 50, "currency": "eur"},
                 {"scope": "provider", "target": "anthropic", "limit": 20, "period": "weekly",
                  "hard": True, "budget_id": CONFIGURED_ID})
    first = book.rules()

    reloaded = BudgetBook.load(Settings(_env_file=None))  # type: ignore[call-arg]

    assert reloaded.rules() == first
    assert first[0].currency == "EUR"
    assert CONFIGURED_ID not in {rule.budget_id for rule in first}
    # An id RAVIS gave out is kept when the list is sent back; a made-up one is replaced.
    sent = [rule.stored() for rule in first] + [{"limit": 1, "period": "daily",
                                                  "budget_id": "bud_00000000"}]
    kept = reloaded.replace(sent)
    assert [rule.budget_id for rule in kept[:2]] == [rule.budget_id for rule in first]
    assert kept[2].budget_id != "bud_00000000"


def test_a_malformed_file_refuses_rather_than_reading_as_no_budgets() -> None:
    """The permissive reading — nothing limits spending — is the one that spends."""
    path = config_directory() / "budgets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")

    with pytest.raises(BudgetConfigurationError):
        BudgetBook.load(Settings(_env_file=None))  # type: ignore[call-arg]


def test_each_budget_counts_only_its_slice_and_only_its_currency() -> None:
    ledger = _ledger(("clarvis", "anthropic", 3.0, "USD"), ("nervis", "anthropic", 4.0, "USD"),
                     ("clarvis", "openai", 5.0, "USD"), ("clarvis", "openai", 7.0, "EUR"),
                     ("clarvis", "openai", None, None))
    records = ledger.since(0)

    clarvis = status_of(BudgetRule("b", "application", "clarvis", 10.0), records)
    anthropic = status_of(BudgetRule("b", "provider", "anthropic", 10.0), records)
    everything = status_of(BudgetRule("b", "all", "", 10.0), records)

    assert (clarvis.spent, clarvis.calls_priced) == (8.0, 2)
    # Seven euros are counted beside the dollars, not converted into them; the unpriced call too.
    assert (clarvis.calls_other_currency, clarvis.calls_unpriced) == (1, 1)
    assert (anthropic.spent, anthropic.band) == (7.0, BudgetBand.PREFER_CHEAPER)
    assert (everything.spent, everything.band) == (12.0, BudgetBand.EXHAUSTED)


def test_a_budget_only_counts_its_own_window() -> None:
    ledger = UsageLedger(clock=Clock())
    ledger.record(UsageRecord(model="m", provider="openai", application_id="clarvis",
                              cost=9.0, cost_state=CostState.ESTIMATED))
    book = _book({"limit": 10, "period": "daily"}, {"limit": 10, "period": "weekly"})

    daily, weekly = book.statuses(ledger, NOW + 2 * DAY)

    assert (daily.spent, weekly.spent) == (0.0, 9.0)
    assert weekly.band is BudgetBand.STRONG_PENALTY


# ── What they do to a request ───────────────────────────────────────────────


def test_an_application_budget_holds_that_application_and_no_other() -> None:
    ledger = _ledger(("clarvis", "openai", 9.5, "USD"))
    book = _book({"scope": "application", "target": "clarvis", "limit": 10, "hard": True})

    clarvis = book.for_request("clarvis", ledger, NOW)
    nervis = book.for_request("nervis", ledger, NOW)

    assert (clarvis.band, clarvis.hard) == (BudgetBand.STRONG_PENALTY, True)
    assert clarvis.labels == ("clarvis's monthly budget",)
    assert nervis.band is BudgetBand.NORMAL


def test_the_worst_band_wins_and_blocks_only_if_a_budget_there_is_hard() -> None:
    ledger = _ledger(("clarvis", "openai", 12.0, "USD"))
    soft_spent = _book({"limit": 10}, {"scope": "application", "target": "clarvis",
                                      "limit": 100, "hard": True})

    held = soft_spent.for_request("clarvis", ledger, NOW)

    # The overall budget is spent but soft; the hard one is at 12 %. Nothing is blocked.
    assert (held.band, held.hard) == (BudgetBand.EXHAUSTED, False)
    assert held.labels == ("the monthly budget",)


def test_the_configured_budget_still_counts_beside_the_owners() -> None:
    ledger = _ledger(("clarvis", "openai", 12.0, "USD"))
    book = _book({"scope": "provider", "target": "openai", "limit": 100}, limit=10.0, hard=True)

    held = book.for_request("clarvis", ledger, NOW)

    assert book.rules()[0].configured
    assert (held.band, held.hard) == (BudgetBand.EXHAUSTED, True)


def test_a_provider_budget_never_moves_the_request_band() -> None:
    """Anthropic's limit must not push Clarvis off OpenAI."""
    ledger = _ledger(("clarvis", "anthropic", 10.0, "USD"))
    book = _book({"scope": "provider", "target": "anthropic", "limit": 10, "hard": True})

    held = book.for_request("clarvis", ledger, NOW)

    assert held.band is BudgetBand.NORMAL
    assert held.providers == {
        "anthropic": (BudgetBand.EXHAUSTED, True, "anthropic's monthly budget")
    }


LOCAL, CLAUDE, GPT = "qwen2.5-7b-instruct", "claude-haiku", "gpt-4o-mini"
OWNERS = {LOCAL: "lmstudio", CLAUDE: "anthropic", GPT: "openai"}


def _route(provider_budgets: dict[str, tuple[BudgetBand, bool, str]]) -> Any:
    """Route `ravis/auto` over one local and two hosted models, with these provider budgets."""
    claims = {
        capability: CapabilityClaim(capability=capability, state=CapabilityState.SUPPORTED,
                                    provenance=Provenance.ADVERTISED)
        for capability in (Capability.TOOLS, Capability.STREAMING)
    }
    candidates = {
        LOCAL: ModelCapabilities(model_id=LOCAL, claims=dict(claims), context_window=32_000,
                                 price_per_million=0.0),
        # Claude the cheaper of the two, so a price lean alone would pick it.
        CLAUDE: ModelCapabilities(model_id=CLAUDE, claims=dict(claims), context_window=200_000,
                                  price_per_million=1.0),
        GPT: ModelCapabilities(model_id=GPT, claims=dict(claims), context_window=128_000,
                               price_per_million=2.0),
    }
    remote = frozenset({CLAUDE, GPT})
    policy = with_budget_pressure(
        effective_policy(RoutingPolicy(), metadata={}, may_declare_background=False,
                         ceiling=RoutingPolicy().privacy, provider_budgets=provider_budgets),
        candidates, provider_of=OWNERS.__getitem__, remote=remote,
    )
    return RoutingEngine().select(
        "ravis/api", candidates, remote_models=remote, policy=policy,
        policy_refusals=policy_refusals(policy, candidates, addressed="ravis/api",
                                        provider_of=OWNERS.__getitem__, remote=remote),
    )


def test_a_spent_hard_provider_budget_refuses_that_providers_models_and_says_why() -> None:
    decision = _route({"anthropic": (BudgetBand.EXHAUSTED, True, "anthropic's weekly budget")})

    assert decision.selected == GPT
    refused = {entry.model: entry.reasons for entry in decision.excluded}
    assert any("anthropic's weekly budget is spent" in reason for reason in refused[CLAUDE])
    assert GPT not in refused
    assert any("anthropic's paid models refused" in line for line in decision.requirements)


def test_a_provider_budget_past_seventy_percent_ranks_its_models_lower() -> None:
    before = _route({})
    after = _route({"anthropic": (BudgetBand.PREFER_CHEAPER, False, "anthropic's weekly budget")})

    assert before.selected == CLAUDE, "the fixture must prefer Claude without a budget"
    assert after.selected == GPT
    assert CLAUDE in after.fallbacks, "ranked lower, never refused"


# ── The API the dashboard uses ──────────────────────────────────────────────


def _client() -> TestClient:
    client, _ = _app_with(RecordingUpstream())
    client.headers.update(as_administrator(client.app.app.state.credentials))  # type: ignore[attr-defined]
    return client


def test_the_owner_sets_budgets_and_reads_them_back_with_their_standing() -> None:
    client = _client()
    with client:
        empty = client.get("/api/v1/budgets").json()
        assert empty["budgets"] == []
        assert "anthropic" in empty["providers"]

        saved = client.put(
            "/api/v1/budgets",
            json={"budgets": [{"scope": "provider", "target": "anthropic", "limit": 20,
                               "period": "weekly", "hard": True}]},
            headers={"If-Match": empty["revision"]},
        )
        assert saved.status_code == 200, saved.text
        [budget] = saved.json()["budgets"]
        assert (budget["label"], budget["band"], budget["spent_estimated"]) == (
            "anthropic's weekly budget", "NORMAL", 0.0)

        usage = client.get("/api/v1/usage").json()
        assert [entry["budget_id"] for entry in usage["budgets"]] == [budget["budget_id"]]
        assert usage["budget"] is None, "no budget covering everything, so the old field is empty"

    stored = json.loads((config_directory() / "budgets.json").read_text())
    assert stored[0]["target"] == "anthropic"


def test_a_stale_editor_is_refused_rather_than_overwriting() -> None:
    client = _client()
    with client:
        revision = client.get("/api/v1/budgets").json()["revision"]
        assert client.put("/api/v1/budgets", json={"budgets": [{"limit": 5}]}).status_code == 200

        stale = client.put("/api/v1/budgets", json={"budgets": []},
                           headers={"If-Match": revision})

    assert stale.status_code == 412
    assert "the budgets changed since you read it" in stale.json()["error"]["message"]


def test_an_invalid_budget_is_refused_and_nothing_is_stored() -> None:
    client = _client()
    with client:
        refused = client.put("/api/v1/budgets", json={"budgets": [{"limit": -1}]})
        after = client.get("/api/v1/budgets").json()

    assert refused.status_code == 422
    assert "more than zero" in refused.json()["error"]["message"]
    assert after["budgets"] == []


def test_without_an_admin_credential_budgets_cannot_be_changed() -> None:
    client, _ = _app_with(RecordingUpstream())
    with client:
        refused = client.put("/api/v1/budgets", json={"budgets": [{"limit": 5}]})

    assert refused.status_code in (401, 403)
    assert not (config_directory() / "budgets.json").exists()
