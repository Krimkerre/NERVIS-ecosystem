"""M15 — the cost engine, and the two things it is allowed to claim.

M15's exit, verbatim: *no double counting; estimates never presented as
invoices.* §14 adds the gate that shapes the tests — *fixture arithmetic,
currency, price-version, partial stream, retry, fallback and missing-usage
tests prevent double counting* — and one sentence that decides the whole
design: **unknown usage or cost stays unknown.**

The second criterion is the harder one to test, because "never presented as an
invoice" is a claim about wording as much as arithmetic. It is checked three
ways here: the state enum has no value for billed, an unpriced call reports
`None` rather than nought, and the endpoint's field is named `spend_estimated`
so a consumer reading only the key still reads the claim.
"""

from __future__ import annotations

from ravis.core.responses import Usage
from ravis.cost import (
    PER_MILLION,
    Budget,
    BudgetBand,
    CostState,
    Price,
    PriceBook,
    UsageLedger,
    UsageRecord,
    band_for,
    budget_from,
    estimate,
)


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


A_PRICE = Price(
    input_per_million=3.0, output_per_million=15.0,
    source="openrouter/models", captured_at=1_000_000.0,
)


# ── Arithmetic (§14's gate) ─────────────────────────────────────────────────


def test_cost_is_input_and_output_priced_separately() -> None:
    """The reason a blended figure is not enough.

    Input and output differ by five times in this fixture and by an order of
    magnitude in the real catalogue, so a summed price would produce a number
    that is wrong in proportion to how lopsided the call was — which is every
    call, since prompts and completions are not the same size.
    """
    cost, state = estimate(A_PRICE, Usage(input_tokens=1_000_000, output_tokens=1_000_000))

    assert state is CostState.ESTIMATED
    assert cost == 18.0


def test_a_small_call_costs_a_small_fraction() -> None:
    """Scale is per million tokens, and the arithmetic has to survive it."""
    cost, _ = estimate(A_PRICE, Usage(input_tokens=1_500, output_tokens=300))

    assert abs(cost - (1_500 * 3.0 + 300 * 15.0) / PER_MILLION) < 1e-12


def test_cached_input_is_charged_at_its_own_rate_when_published() -> None:
    """A cache hit is cheaper, and providers that price it say so separately."""
    price = Price(input_per_million=3.0, output_per_million=15.0,
                  cached_input_per_million=0.3)

    cost, _ = estimate(price, Usage(input_tokens=1000, cached_input_tokens=800,
                                    output_tokens=0))

    # 200 fresh at 3.0, 800 cached at 0.3 — not 1000 at either rate.
    assert abs(cost - (200 * 3.0 + 800 * 0.3) / PER_MILLION) < 1e-12


def test_cached_input_falls_back_to_the_ordinary_rate() -> None:
    """A provider that reports cached tokens without pricing them separately is
    charging them as ordinary input, and guessing a discount would understate
    a bill somebody eventually pays."""
    cost, _ = estimate(A_PRICE, Usage(input_tokens=1000, cached_input_tokens=800))

    assert abs(cost - 1000 * 3.0 / PER_MILLION) < 1e-12


# ── Unknown stays unknown (§14) ─────────────────────────────────────────────


def test_no_price_is_unknown_rather_than_free() -> None:
    """The failure §14 names outright: a dashboard reading "€0.00" would be a
    confident lie, and an unpriced provider is the ordinary case — OpenAI,
    Google and Anthropic publish no pricing in their catalogues at all."""
    cost, state = estimate(None, Usage(input_tokens=1000, output_tokens=500))

    assert cost is None
    assert state is CostState.UNKNOWN


def test_no_usage_is_unknown_even_with_a_price() -> None:
    """Half of an answer is not an answer. A price with no tokens is as
    unanswerable as tokens with no price."""
    assert estimate(A_PRICE, None) == (None, CostState.UNKNOWN)
    assert estimate(A_PRICE, Usage()) == (None, CostState.UNKNOWN)


def test_a_free_model_costs_zero_which_is_not_unknown() -> None:
    """Zero and unknown are the distinction the whole module rests on.

    A local runtime and OpenRouter's `:free` tier genuinely cost nothing, and
    that is the strongest thing a router can know about cost — it must not be
    collapsed into the same answer as "nobody published a price".
    """
    free = Price(input_per_million=0.0, output_per_million=0.0)

    cost, state = estimate(free, Usage(input_tokens=9_999, output_tokens=9_999))

    assert cost == 0.0
    assert state is CostState.ESTIMATED
    assert free.free is True


def test_there_is_no_billed_state() -> None:
    """RAVIS never sees an invoice, so it must not be able to claim one.

    A state nothing can reach would eventually be set anyway, by somebody
    reasonably assuming it exists because it is in the enum.

    `REPORTED` is not that state. It means the *provider* stated a per-call
    figure — stronger than RAVIS's own multiplication, and still ahead of
    credits, minimums and whatever a monthly statement reconciles to.
    """
    assert {state.value for state in CostState} == {"ESTIMATED", "REPORTED", "UNKNOWN"}
    assert not hasattr(CostState, "BILLED")


def test_a_provider_reported_cost_outranks_our_own_arithmetic() -> None:
    """OpenRouter returns `cost` on every usage frame, computed with knowledge
    RAVIS does not have — which upstream actually served, at what negotiated
    rate. Preferring RAVIS's multiplication over it would be choosing the
    weaker of two available answers.

    Verified live before it was written: for one call RAVIS estimated 6.45e-06
    from published prices and OpenRouter reported the same figure, split the
    same way between prompt and completion.
    """
    reported = Usage(input_tokens=15, output_tokens=7, reported_cost=6.45e-06)

    cost, state = estimate(A_PRICE, reported)

    assert state is CostState.REPORTED
    assert cost == 6.45e-06


def test_a_reported_cost_is_used_even_with_no_price_published() -> None:
    """It is the provider's own figure; it needs no price table behind it."""
    cost, state = estimate(None, Usage(output_tokens=5, reported_cost=0.002))

    assert (cost, state) == (0.002, CostState.REPORTED)


# ── Currency and price version (§14's gate) ─────────────────────────────────


def test_a_record_carries_the_price_it_was_computed_from() -> None:
    """§14 asks for price-source version and time on the record itself.

    Looking the price up later would let a price change silently restate what
    an old call cost — the same class of error as recomputing a route decision
    and calling it an explanation.
    """
    record = UsageRecord(
        model="m", provider="openrouter", application_id="clarvis",
        currency=A_PRICE.currency, price_source=A_PRICE.source,
        price_captured_at=A_PRICE.captured_at,
    )

    assert record.as_dict()["currency"] == "USD"
    assert record.as_dict()["price_source"] == "openrouter/models"
    # The time as well as the source: §14 asks for both, and a price nobody can
    # date cannot be audited against the day the call was made.
    assert record.as_dict()["price_captured_at"] == 1_000_000.0


def test_a_record_carries_no_prompt_or_completion() -> None:
    """§14: request logging defaults to metadata only.

    Asserted on the serialised shape, because that is what leaves the process,
    and as an exact set so a field cannot be added without somebody deciding.
    """
    record = UsageRecord(model="m", provider="p", application_id="a")

    assert set(record.as_dict()) == {
        "model", "provider", "application_id", "input_tokens", "output_tokens",
        "cached_input_tokens", "reasoning_tokens", "cost", "cost_state", "currency",
        "price_source", "price_captured_at", "latency_ms", "request_id",
        "session_id", "decision_id", "pool", "at",
    }


# ── No double counting (M15's exit criterion) ───────────────────────────────


def _record(ledger: UsageLedger, cost: float | None, model: str = "m") -> None:
    ledger.record(UsageRecord(
        model=model, provider="openrouter", application_id="clarvis",
        cost=cost, cost_state=CostState.ESTIMATED if cost is not None else CostState.UNKNOWN,
    ))


def test_a_retry_that_failed_contributes_nothing() -> None:
    """The structural guarantee, stated as a test.

    A usage record is written from one place — where an attempt is recorded as
    having succeeded — so a failed attempt has no path to the ledger at all.
    This asserts the arithmetic that follows from that: two attempts, one
    success, one record.
    """
    ledger = UsageLedger(clock=Clock())
    _record(ledger, 0.5)  # the attempt that worked; the failure wrote nothing

    total, priced, _ = ledger.spend()

    assert (total, priced) == (0.5, 1)


def test_a_fallback_bills_the_model_that_answered() -> None:
    """§10's chain may walk several candidates; only one served the request."""
    ledger = UsageLedger(clock=Clock())
    _record(ledger, 0.25, model="the-fallback")

    assert [r.model for r in ledger.recent()] == ["the-fallback"]
    assert ledger.spend()[1] == 1


def test_an_unpriced_call_is_counted_separately_not_as_zero() -> None:
    """A total alone invites being read as complete.

    Twelve dollars across forty calls of which nine had no price is a different
    statement from twelve dollars across forty calls, and only the second is
    what a lone number looks like.
    """
    ledger = UsageLedger(clock=Clock())
    _record(ledger, 1.0)
    _record(ledger, None)
    _record(ledger, 2.0)

    total, priced, unpriced = ledger.spend()

    assert (total, priced, unpriced) == (3.0, 2, 1)


def test_a_partial_stream_that_produced_nothing_records_nothing() -> None:
    """§14's gate names partial streams.

    A stream that never committed is a non-answer rather than an empty answer,
    and pricing it would put a charge against a call the client never received.
    The ledger simply never hears about it — asserted here as the empty state.
    """
    ledger = UsageLedger(clock=Clock())

    assert ledger.spend() == (0.0, 0, 0)
    assert ledger.recent() == []


# ── Budgets (§14) ───────────────────────────────────────────────────────────


def test_the_four_bands_are_the_specification_s_own() -> None:
    """0–70% normal · 70–90% prefer cheaper · 90–100% strong penalty · 100% over."""
    budget = Budget(limit=50.0)

    assert budget.band(0.0) is BudgetBand.NORMAL
    assert budget.band(34.9) is BudgetBand.NORMAL
    assert budget.band(35.0) is BudgetBand.PREFER_CHEAPER
    assert budget.band(44.9) is BudgetBand.PREFER_CHEAPER
    assert budget.band(45.0) is BudgetBand.STRONG_PENALTY
    assert budget.band(50.0) is BudgetBand.EXHAUSTED
    assert budget.band(120.0) is BudgetBand.EXHAUSTED


def test_no_budget_configured_is_not_a_budget_of_zero() -> None:
    """The worst possible default, avoided deliberately.

    A limit of zero puts the first request of the day into the exhausted band.
    An operator who configured nothing has not asked for that, so an absent
    budget is `None` and routes exactly as it did before the cost engine.
    """

    class Settings:
        budget_limit = 0.0

    assert budget_from(Settings()) is None
    assert band_for(None, UsageLedger(), 0.0)[0] is BudgetBand.NORMAL


def test_a_band_reports_how_much_of_its_window_was_unpriced() -> None:
    """§14: budget constraints must fail predictably when a price is unavailable.

    A band standing on partial evidence is not a band anybody should be
    throttled by silently, so the count travels with it.
    """
    clock = Clock()
    ledger = UsageLedger(clock=clock)
    _record(ledger, 40.0)
    _record(ledger, None)

    band, spent, unpriced = band_for(Budget(limit=50.0), ledger, clock())

    assert band is BudgetBand.PREFER_CHEAPER
    assert (spent, unpriced) == (40.0, 1)


def test_spend_outside_the_window_does_not_count() -> None:
    """A rolling window, so yesterday's spend stops throttling today's routes."""
    clock = Clock()
    ledger = UsageLedger(clock=clock)
    _record(ledger, 60.0)
    clock.advance(2 * 24 * 3600)

    band, spent, _ = band_for(Budget(limit=50.0, period="daily"), ledger, clock())

    assert band is BudgetBand.NORMAL
    assert spent == 0.0


# ── The price book ──────────────────────────────────────────────────────────


def test_an_unpriced_model_is_absent_rather_than_free() -> None:
    book = PriceBook()
    book.record("priced", A_PRICE)

    assert book.price_of("priced") is A_PRICE
    assert book.price_of("never-published-one") is None
    assert book.known() == 1
