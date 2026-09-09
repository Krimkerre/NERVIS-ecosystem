"""Prices that go stale on their own, and the wiring that was never connected.

The operator asked a plain question -- *"do those prices get updated
periodically?"* -- and the honest answer was worse than "no". `PriceBook.record`,
whose docstring reads *"Take a catalogue price"*, had **no callers anywhere in
the tree**, while `app.py`'s own comment claimed the book "is filled from
provider catalogues on the refresh that already runs". Neither half happened:
every price came out of `prices.json` once, at process start, and OpenRouter's
published rates were parsed into `ModelCapabilities.price` on every refresh and
then dropped.

So a rate could only change by restarting the gateway, and nothing ever noticed
a vendor changing its pricing. These tests exist because a comment claiming an
update path is exactly as convincing as one, right up until somebody checks.

Note what is *not* claimed here. Only OpenRouter publishes machine-readable
pricing; OpenAI, Anthropic, Google, DeepSeek and xAI publish catalogues with no
rates in them at all, so their prices remain hand-written and are refreshed only
in the sense that the file is re-read. That limit is asserted below rather than
left for someone to discover.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from ravis.app import _refresh_catalogues, _restate_prices
from ravis.cost import PriceBook
from ravis.providers.openrouter import OpenRouterAdapter
from ravis.upstream import Upstream

# OpenRouter publishes dollars *per token*, as strings. A million-fold apart
# from every other number in this file, which is the point of the conversion.
PRICED = {
    "id": "vendor/priced",
    "pricing": {"prompt": "0.0000004", "completion": "0.0000016",
                "input_cache_read": "0.0000001"},
}
# "-1" is OpenRouter's "ask the provider", not a price -- and treating it as one
# would make this the cheapest model in the catalogue.
ASK_THE_PROVIDER = {"id": "vendor/unpriced", "pricing": {"prompt": "-1", "completion": "-1"}}


def an_adapter() -> OpenRouterAdapter:
    def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [PRICED, ASK_THE_PROVIDER]})

    return OpenRouterAdapter(
        upstream=Upstream(base_url="https://openrouter.ai/api", declared_key="k",
                          api_root="/v1"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )


def an_api(book: PriceBook, *, with_catalogue: bool = True) -> SimpleNamespace:
    """Enough of the app for the refresh hook, and nothing else."""
    transparents = {}
    if with_catalogue:
        transparents["openrouter"] = SimpleNamespace(adapter=an_adapter())
    return SimpleNamespace(state=SimpleNamespace(prices=book, transparents=transparents))


def written(tmp_path, monkeypatch, payload: dict) -> None:  # noqa: ANN001
    """An operator's `prices.json`, somewhere that is not the real home dir."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    directory = tmp_path / "ravis"
    directory.mkdir(exist_ok=True)
    (directory / "prices.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.asyncio
async def test_a_catalogue_price_reaches_the_book_at_all() -> None:
    """**The missing wire.** Before this, the assertion below was `is None` --
    not because anything decided prices should be ignored, but because nobody
    called the method that takes them."""
    book = PriceBook()

    await _restate_prices(an_api(book))

    held = book.price_of("vendor/priced")
    assert held is not None, "a published price still never reaches the price book"
    assert held.input_per_million == pytest.approx(0.4)
    assert held.output_per_million == pytest.approx(1.6)
    assert held.cached_input_per_million == pytest.approx(0.1)
    assert held.source == "openrouter/models"


@pytest.mark.asyncio
async def test_a_model_the_catalogue_will_not_price_stays_unpriced() -> None:
    """`-1` means "ask the provider". Recording it as a price would make the one
    model whose cost is unknown look like the cheapest thing available."""
    book = PriceBook()

    await _restate_prices(an_api(book))

    assert book.price_of("vendor/unpriced") is None


@pytest.mark.asyncio
async def test_the_operator_still_outranks_the_catalogue_after_a_refresh(
    tmp_path, monkeypatch  # noqa: ANN001
) -> None:
    """A catalogue price is what a vendor charges anybody; the operator's is what
    *they* pay. The refresh must not quietly promote the public rate -- their
    budget is spent against the contract they signed."""
    written(tmp_path, monkeypatch, {"vendor/priced": {"input": 99.0, "output": 99.0}})
    book = PriceBook()

    await _restate_prices(an_api(book))

    assert book.price_of("vendor/priced").input_per_million == 99.0


@pytest.mark.asyncio
async def test_editing_the_file_takes_effect_without_a_restart(
    tmp_path, monkeypatch  # noqa: ANN001
) -> None:
    """The whole point of the operator's question. A vendor changes its pricing;
    changing the number should not need the gateway bounced."""
    written(tmp_path, monkeypatch, {"some-model": {"input": 1.0, "output": 2.0}})
    book = PriceBook()
    await _restate_prices(an_api(book))
    assert book.price_of("some-model").input_per_million == 1.0

    written(tmp_path, monkeypatch, {"some-model": {"input": 5.0, "output": 9.0}})
    await _restate_prices(an_api(book))

    assert book.price_of("some-model").input_per_million == 5.0


@pytest.mark.asyncio
async def test_a_rate_deleted_from_the_file_stops_being_believed(
    tmp_path, monkeypatch  # noqa: ANN001
) -> None:
    """`state` only ever adds, so re-stating in a loop would leave a withdrawn
    price in force until the next restart -- the file and the spend screen
    disagreeing, with nothing to show which one was current."""
    written(tmp_path, monkeypatch, {"gone-tomorrow": {"input": 3.0, "output": 4.0}})
    book = PriceBook()
    await _restate_prices(an_api(book))
    assert book.price_of("gone-tomorrow") is not None

    written(tmp_path, monkeypatch, {})
    await _restate_prices(an_api(book))

    assert book.price_of("gone-tomorrow") is None, (
        "a price the operator deleted is still being charged against their budget"
    )


@pytest.mark.asyncio
async def test_a_typo_in_the_price_file_does_not_stop_the_refresh(
    tmp_path, monkeypatch  # noqa: ANN001
) -> None:
    """This hook shares a task with catalogue refreshes and the session-retention
    sweep. A malformed file raising here would stop the model lists updating --
    a far worse outcome than one unreadable rate. `serve` refuses to start on it
    and `ravis doctor` reports it, which are the two places an operator looks."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "ravis").mkdir(exist_ok=True)
    (tmp_path / "ravis" / "prices.json").write_text("{ not json", encoding="utf-8")
    book = PriceBook()

    await _restate_prices(an_api(book))

    assert book.price_of("vendor/priced") is not None, (
        "one bad line in prices.json stopped catalogue prices arriving"
    )


@pytest.mark.asyncio
async def test_an_upstream_that_publishes_nothing_is_skipped_not_failed() -> None:
    """Five of the six configured providers publish no rates at all. That is the
    permanent, ordinary state for them -- not an error, and not a reason for the
    refresh to stop before reaching the ones that do."""
    book = PriceBook()
    api = an_api(book)
    api.state.transparents["openai"] = SimpleNamespace(adapter=SimpleNamespace())

    await _restate_prices(api)

    assert book.price_of("vendor/priced") is not None


@pytest.mark.asyncio
async def test_the_catalogue_refresh_actually_calls_this() -> None:
    """**The wire, not just the thing it carries.**

    Every test above calls `_restate_prices` directly, so all of them keep
    passing if the one line hooking it into `_refresh_catalogues` is deleted --
    which is precisely the shape of the original bug: a correct function that
    nothing ever called. Probed by removing that line; without this test the
    suite noticed nothing.
    """
    book = PriceBook()
    api = an_api(book)
    refreshed: list[str] = []

    async def refresh() -> None:
        refreshed.append("yes")

    api.state.transparents["openrouter"].registry = SimpleNamespace(refresh=refresh)

    await _refresh_catalogues(api)

    assert refreshed == ["yes"], "the catalogue itself stopped refreshing"
    assert book.price_of("vendor/priced") is not None, (
        "the refresh no longer restates prices -- the exact gap this file exists for"
    )
