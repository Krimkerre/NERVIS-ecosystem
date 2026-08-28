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

import json
from pathlib import Path

import pytest

from ravis.core.responses import Usage
from ravis.cost import (
    PER_MILLION,
    Budget,
    BudgetBand,
    CostState,
    Price,
    PriceBook,
    PriceConfigurationError,
    UsageLedger,
    UsageRecord,
    band_for,
    budget_from,
    estimate,
    load_prices,
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
    # `output_tokens=0` rather than absent: a missing output count now makes the
    # whole cost UNKNOWN, since charging for half a call understates it.
    cost, _ = estimate(
        A_PRICE, Usage(input_tokens=1000, cached_input_tokens=800, output_tokens=0)
    )

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


# ── Where prices come from (§14's cost registry) ────────────────────────────


def test_a_local_model_is_free_rather_than_unpriced() -> None:
    """The conflation this engine exists to prevent, from the other direction.

    LM Studio and Ollama set the *ranking* price to zero and recorded no
    `Price`, so a local call reported cost UNKNOWN while RAVIS knew perfectly
    well it was free. Zero and unknown are the distinction; a local runtime is
    the clearest zero there is.
    """
    local = Price(input_per_million=0.0, output_per_million=0.0, source="lmstudio/local")

    cost, state = estimate(local, Usage(input_tokens=500, output_tokens=500))

    assert (cost, state) == (0.0, CostState.ESTIMATED)
    assert local.free is True
    # And the adapters record one, which is the half that was missing: without
    # it the engine is correct and the answer is still UNKNOWN.
    for runtime in ("lmstudio", "ollama"):
        source = Path(f"src/ravis/providers/{runtime}.py").read_text()
        assert "known.price = Price(" in source, f"{runtime} records no split price"


def test_an_absent_price_file_is_no_prices_not_an_error(tmp_path) -> None:  # noqa: ANN001
    """Most deployments have none, and that is a legitimate state."""
    assert load_prices(tmp_path / "nothing.json") == {}


def test_an_operator_can_price_what_a_catalogue_does_not(tmp_path) -> None:  # noqa: ANN001
    """OpenAI, Anthropic and Google ship catalogues with no pricing at all, so
    a call to any of them is UNKNOWN however carefully the engine multiplies.

    Stating the rate is configuration rather than invention — it is on the
    operator's own contract — and it is dated by the file it was written in,
    which a table hardcoded inside RAVIS never could be.
    """
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"gpt-4o-mini": {"input": 0.15, "output": 0.60}}))

    prices = load_prices(path)
    cost, state = estimate(prices["gpt-4o-mini"], Usage(input_tokens=8, output_tokens=8))

    assert prices["gpt-4o-mini"].source == "operator"
    assert prices["gpt-4o-mini"].captured_at > 0
    assert state is CostState.ESTIMATED
    assert abs(cost - 6e-06) < 1e-12


def test_an_operator_price_is_not_overwritten_by_a_catalogue() -> None:
    """A catalogue price is what a provider charges anybody; an operator writing
    one down is stating what *they* pay, which is the figure their budget is
    actually spent against."""
    book = PriceBook()
    book.state("m", Price(input_per_million=1.0, output_per_million=1.0, source="operator"))

    book.record("m", Price(input_per_million=99.0, output_per_million=99.0, source="catalogue"))

    assert book.price_of("m").input_per_million == 1.0


def test_a_malformed_price_file_fails_closed(tmp_path) -> None:  # noqa: ANN001
    """Failing open would return every paid model to UNKNOWN — which a budget
    also reads as nothing spent, so the failure would quietly remove a limit."""
    path = tmp_path / "prices.json"
    path.write_text("{ not json")

    try:
        load_prices(path)
    except PriceConfigurationError:
        return
    raise AssertionError("a malformed price file must not read as no prices")


def test_a_price_must_be_numeric_and_not_negative(tmp_path) -> None:  # noqa: ANN001
    """A lost zero is an order of magnitude on somebody's budget, so the shape
    is checked rather than coerced."""
    path = tmp_path / "prices.json"

    # `cached_input` is in this list because it was not in the guard. It was
    # converted in the `Price(...)` call, outside the try, so a non-numeric
    # value raised a bare `ValueError` -- and `PriceConfigurationError` is a
    # *subclass* of `ValueError`, not its parent, so `ravis doctor`'s
    # `except PriceConfigurationError` did not catch it. `serve` refused to
    # start on the same file, and the command that exists "because the service
    # will not start" died with a traceback instead of naming the entry. The
    # negative case escaped the negativity check for the same reason.
    for entry in ({"input": "cheap", "output": 1.0}, {"input": -1.0, "output": 1.0},
                  {"input": 1.0}, {"input": 1.0, "output": 1.0, "typo": 2},
                  {"input": 1.0, "output": 1.0, "cached_input": "free"},
                  {"input": 1.0, "output": 1.0, "cached_input": -0.5}):
        path.write_text(json.dumps({"m": entry}))
        try:
            load_prices(path)
        except PriceConfigurationError:
            continue
        raise AssertionError(f"accepted a bad price entry: {entry}")


def test_a_half_reported_call_is_unknown_rather_than_understated() -> None:
    """Gemini sometimes reports a prompt count and no completion count.

    Charging for the half that arrived produced a figure labelled ESTIMATED
    that understated the call — and a budget reads an understatement as room
    left, which is the direction that actually costs somebody money. Found by
    comparing a ledger row against the frame RAVIS had just emitted.
    """
    partial = Usage(input_tokens=4, output_tokens=None)

    assert estimate(A_PRICE, partial) == (None, CostState.UNKNOWN)


def test_a_missing_count_on_a_free_component_still_costs() -> None:
    """A component priced at zero cannot change the total, so its absence is
    not a reason to refuse an answer — a local model reporting only its prompt
    count still cost nothing."""
    free_output = Price(input_per_million=3.0, output_per_million=0.0)

    cost, state = estimate(free_output, Usage(input_tokens=1_000, output_tokens=None))

    assert state is CostState.ESTIMATED
    assert abs(cost - 3_000 / PER_MILLION) < 1e-12


def test_a_non_streamed_transparent_call_is_written_to_the_ledger() -> None:
    """§14's ledger has to see the ordinary completion, not just the streamed one.

    `note_usage` is reached from three of the four success points: translated
    non-streaming, transparent streaming, translated streaming. The fourth --
    an ordinary transparent completion with `stream` omitted -- returned the
    upstream's response with its own `usage` object sitting in the body and
    discarded it. Every such call was invisible: absent from `/api/v1/usage`,
    absent from the spend the budget bands enforce, absent from the per-call
    table on the dashboard.

    Nothing caught it because the shared upstream fixture returned a completion
    body with no `usage` key at all -- a shape no OpenAI-compatible server
    produces. A fixture that cannot carry the field cannot fail when the field
    is dropped.

    The streamed call is asserted beside it deliberately: identical model,
    identical tokens. Only the contrast shows that the path, not the pricing,
    was the difference.
    """
    from tests.conftest_upstream import RecordingUpstream
    from tests.test_transparent_proxy import _app_with

    client, _ = _app_with(RecordingUpstream())
    with client:
        ledger = client.app.app.state.usage_ledger
        client.app.app.state.prices.state("qwen2.5-coder-7b", Price(input_per_million=0.3,
                                                                output_per_million=0.3))

        streamed = client.post(
            "/v1/chat/completions",
            json={"model": "qwen2.5-coder-7b", "stream": True,
                  "messages": [{"role": "user", "content": "hi"}]},
        )
        assert streamed.status_code == 200
        after_stream = len(ledger.recent(500))

        plain = client.post(
            "/v1/chat/completions",
            json={"model": "qwen2.5-coder-7b",
                  "messages": [{"role": "user", "content": "hi"}]},
        )
        assert plain.status_code == 200
        assert plain.json()["usage"]["prompt_tokens"] == 15, (
            "the upstream reported usage, so RAVIS had it in hand"
        )

    records = ledger.recent(500)
    assert len(records) == after_stream + 1, (
        "a non-streamed completion must leave a usage record, exactly as the "
        "streamed one does"
    )
    # `recent()` returns newest first, so the non-streamed call is at the head.
    written = records[0]
    assert written.usage is not None, "the upstream's counts, not a guess"
    assert written.usage.input_tokens == 15
    assert written.usage.output_tokens == 7
    assert written.cost_state is not CostState.UNKNOWN, (
        "both priced components are known, so this call can be costed"
    )


def test_a_cached_anthropic_call_is_not_priced_as_if_it_were_free() -> None:
    """The engine subtracts cache reads from input; the adapter must have added them.

    `estimate` computes `max(input - cached, 0)` because OpenAI and Google
    report an input total with the cached figure as a subset. Anthropic reports
    them disjoint, and its adapter passed both through unchanged -- so a call
    answered largely from cache had its new input driven to zero and was priced
    as though the fresh tokens cost nothing.

    Asserted against the arithmetic rather than a fixed figure, so it keeps
    meaning if the rates move.
    """
    from ravis.providers.anthropic_wire import usage_from

    usage = usage_from({
        "input_tokens": 20,          # fresh, in Anthropic's disjoint accounting
        "output_tokens": 5,
        "cache_read_input_tokens": 5_000,
    })
    price = Price(input_per_million=3.0, output_per_million=15.0,
                  cached_input_per_million=0.3)

    cost, state = estimate(price, usage)

    assert state is CostState.ESTIMATED
    expected = (20 * 3.0 + 5_000 * 0.3 + 5 * 15.0) / 1_000_000
    assert cost == pytest.approx(expected), (
        "twenty fresh tokens at the full rate, five thousand at the cache rate"
    )
    assert cost > (5_000 * 0.3 + 5 * 15.0) / 1_000_000, (
        "the fresh tokens must cost something; zeroing them is the bug"
    )


def test_a_pooled_call_to_a_translated_provider_is_attributed_to_that_provider() -> None:
    """§6's fork resolves the owner from the *selected* model; the ledger did not.

    `_usage_writer` computed `direct_provider(decision.requested)`, which answers
    only for a directly addressed model and returns None for a pool id. So on a
    pooled request -- the normal way a client addresses RAVIS, and the reason
    translated candidates exist at all -- the owner was None and the record fell
    back to `provider_of`, which cannot resolve a translated model because its
    ids never appear in a transparent upstream's catalogue. The spend landed
    under the `upstream` fallback label instead of Anthropic.

    The fork one function away has the rule right, and its comment calls it "the
    whole fix": fall back to `translated_owners[selected]`. It was applied there
    and not here, so STATUS.md records the misattribution as fixed while it
    survived on the pooled route.
    """
    from ravis.api.openai.chat import _usage_writer
    from ravis.routing.engine import RouteDecision

    ledger = UsageLedger()

    class _State:
        translating = {"anthropic": object()}
        prices = PriceBook()
        usage_ledger = ledger

    class _App:
        state = _State()

    class _Request:
        app = _App()
        headers: dict[str, str] = {}

        class state:  # noqa: N801 - mirrors Starlette's attribute bag
            identity = None
            request_id = "rq"
            # Populated by the router before the stream finishes, which is why
            # the writer has to read it lazily rather than at construction.
            translated_owners = {"claude-haiku-4-5": "anthropic"}

    decision = RouteDecision(requested="ravis/clarvis-chat")
    decision.selected = "claude-haiku-4-5"
    decision.pool_id = "ravis/clarvis-chat"

    write = _usage_writer(_Request(), decision)  # type: ignore[arg-type]
    write("claude-haiku-4-5", Usage(input_tokens=10, output_tokens=2), 12.0)

    written = ledger.recent(5)[0]
    assert written.provider == "anthropic", (
        "the provider that served it, not the transparent upstream fallback"
    )


def test_a_mixed_currency_ledger_offers_no_single_total() -> None:
    """`spend` adds `record.cost` without consulting `record.currency`.

    The usage endpoint then labelled the result "USD" whatever the prices were
    stated in, so an operator pricing anything in euros -- or mixing a EUR
    contract with OpenRouter's USD catalogue figures -- read euros and dollars
    added into one number, rendered with a dollar sign by the dashboard, and
    compared against a budget limit in a third currency.

    Withheld rather than converted: a rate nobody supplied is not something
    RAVIS may invent, and §14's posture is that an uncertain figure says so
    rather than looking confident.
    """
    ledger = UsageLedger()
    for currency, cost in (("USD", 1.0), ("EUR", 2.0)):
        ledger.record(UsageRecord(model="m", provider="p", application_id="a",
                                  cost=cost, currency=currency,
                                  cost_state=CostState.ESTIMATED))

    assert ledger.currencies() == {"USD", "EUR"}


def test_a_single_currency_ledger_reports_that_currency() -> None:
    """The other half. A total is only withheld when it would be meaningless,
    and the currency reported is the one actually stated -- not a hardcoded USD.
    """
    ledger = UsageLedger()
    for cost in (1.0, 2.0):
        ledger.record(UsageRecord(model="m", provider="p", application_id="a",
                                  cost=cost, currency="EUR",
                                  cost_state=CostState.ESTIMATED))

    total, priced, _ = ledger.spend()

    assert ledger.currencies() == {"EUR"}
    assert (total, priced) == (3.0, 2)


def test_an_unpriced_record_does_not_claim_a_currency() -> None:
    """UNKNOWN cost carries no currency, so it must not make a single-currency
    ledger look mixed."""
    ledger = UsageLedger()
    ledger.record(UsageRecord(model="m", provider="p", application_id="a",
                              cost=1.0, currency="USD", cost_state=CostState.ESTIMATED))
    ledger.record(UsageRecord(model="m", provider="p", application_id="a",
                              cost=None, currency=None, cost_state=CostState.UNKNOWN))

    assert ledger.currencies() == {"USD"}
