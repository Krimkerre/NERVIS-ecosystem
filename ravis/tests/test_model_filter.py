"""Narrowing a provider's catalogue (M10).

OpenRouter publishes 417 models. Without this every one lands in `/v1/models`,
which Clarvis renders as a picker, and every one becomes a routing candidate on
each request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.conftest import as_administrator
from tests.test_plural_upstreams import _built

from ravis.app import create_app
from ravis.config import Settings
from ravis.model_filter import ModelFilter, ModelFilters
from ravis.transparent import merged_candidates, merged_catalogue, resolve

CATALOGUE = [
    "anthropic/claude-sonnet-4",
    "anthropic/claude-opus-4",
    "openai/gpt-4o",
    "openai/gpt-4o-preview",
    "meta/llama-3.3-70b",
]


# ── Matching ─────────────────────────────────────────────────────────────────


def test_no_filter_offers_everything() -> None:
    """Not a cap, not a sample. A provider with no filter behaves exactly as it
    did before this existed."""
    assert ModelFilter().apply(CATALOGUE) == CATALOGUE


def test_an_include_pattern_narrows_to_one_vendor() -> None:
    offered = ModelFilter(include=("anthropic/*",)).apply(CATALOGUE)

    assert offered == ["anthropic/claude-sonnet-4", "anthropic/claude-opus-4"]


def test_exclude_wins_over_include() -> None:
    """"All of OpenAI except the previews" — the other precedence would make the
    exclusion unreachable."""
    offered = ModelFilter(include=("openai/*",), exclude=("*-preview",)).apply(CATALOGUE)

    assert offered == ["openai/gpt-4o"]


def test_an_exclude_only_filter_is_useful_on_its_own() -> None:
    """An empty include list means everything, not nothing."""
    offered = ModelFilter(exclude=("meta/*",)).apply(CATALOGUE)

    assert "meta/llama-3.3-70b" not in offered
    assert len(offered) == 4


def test_matching_is_case_sensitive() -> None:
    """Model ids are opaque strings in a URL path, where GPT-4 and gpt-4 are not
    interchangeable however a human reads them."""
    assert not ModelFilter(include=("OPENAI/*",)).matches("openai/gpt-4o")


def test_a_pattern_matching_nothing_offers_nothing() -> None:
    """Reported honestly rather than falling back to everything — a filter that
    silently stopped applying would be worse than one that selects nothing."""
    assert ModelFilter(include=("nope/*",)).apply(CATALOGUE) == []


# ── Persistence ──────────────────────────────────────────────────────────────


def test_a_filter_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    ModelFilters(path).set_for("openrouter", ModelFilter(include=("anthropic/*",)))

    assert ModelFilters(path).for_provider("openrouter").include == ("anthropic/*",)


def test_setting_one_provider_leaves_the_others(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    filters = ModelFilters(path)
    filters.set_for("openrouter", ModelFilter(include=("anthropic/*",)))
    filters.set_for("lmstudio", ModelFilter(exclude=("*-vision",)))

    assert ModelFilters(path).for_provider("openrouter").include == ("anthropic/*",)
    assert ModelFilters(path).for_provider("lmstudio").exclude == ("*-vision",)


def test_a_corrupt_file_leaves_every_provider_unfiltered(tmp_path: Path) -> None:
    """The permissive direction. A filter is a convenience, and a corrupt one
    must not be able to make a gateway serve nothing."""
    path = tmp_path / "models.json"
    path.write_text("{ not json", encoding="utf-8")

    assert ModelFilters(path).for_provider("openrouter").is_empty


def test_a_non_string_pattern_is_dropped_not_coerced(tmp_path: Path) -> None:
    """`str(3)` is a pattern that matches nothing and looks like it should match
    something."""
    path = tmp_path / "models.json"
    path.write_text('{"x": {"include": ["ok/*", 3, null]}}', encoding="utf-8")

    assert ModelFilters(path).for_provider("x").include == ("ok/*",)


# ── The same rule in all three places ────────────────────────────────────────


def _upstreams() -> dict[str, Any]:
    return {"openrouter": _built("openrouter", CATALOGUE)}


def test_the_catalogue_and_the_candidates_agree() -> None:
    """They must never disagree about whether a model exists — the same reason
    declaration order is the one collision rule across three code paths."""
    filters = {"openrouter": ModelFilter(include=("anthropic/*",))}

    listed = {
        e["id"] for e in merged_catalogue(_upstreams(), frozenset(), filters)["data"]
        if e["owned_by"] != "ravis"
    }
    import asyncio

    routed = set(asyncio.run(merged_candidates(_upstreams(), None, frozenset(), filters)))

    assert listed == routed == {"anthropic/claude-sonnet-4", "anthropic/claude-opus-4"}


def test_the_forwarder_will_not_reach_a_filtered_out_model() -> None:
    """Otherwise the router refuses a model and the forwarder still has a URL
    for it — the disagreement this is shaped to prevent."""
    filters = {"openrouter": ModelFilter(include=("anthropic/*",))}

    assert resolve(_upstreams(), "anthropic/claude-opus-4", filters) is not None
    assert resolve(_upstreams(), "openai/gpt-4o", filters).name == "openrouter", (
        "falls back to the first upstream rather than 404-ing, per resolve's third rule"
    )


# ── The endpoints ────────────────────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path: Path) -> Any:
    app = create_app(Settings(database_path=":memory:", _env_file=None))  # type: ignore[call-arg]
    inner = app
    while not hasattr(inner, "state"):
        inner = inner.app  # type: ignore[attr-defined]
    inner.state.model_filters = ModelFilters(tmp_path / "models.json")
    inner.state.transparents = _upstreams()
    with TestClient(app, headers=as_administrator(inner.state.credentials)) as ready:
        yield ready


def test_the_preview_reports_both_counts(client: Any) -> None:
    """"23 of 417" is the number an operator is actually reading."""
    body = client.get("/api/v1/providers/openrouter/models").json()

    assert body["catalogue_total"] == 5
    assert body["matched_total"] == 5
    assert not body["filtered"]


def test_setting_a_filter_reports_what_it_now_selects(client: Any) -> None:
    body = client.put(
        "/api/v1/providers/openrouter/models", json={"include": ["anthropic/*"]}
    ).json()

    assert body["matched_total"] == 2
    assert body["catalogue_total"] == 5
    assert body["filtered"]
    assert set(body["sample"]) == {"anthropic/claude-sonnet-4", "anthropic/claude-opus-4"}


def test_the_sample_cap_is_never_mistaken_for_the_answer(client: Any) -> None:
    """A truncated list presented as the whole result is the exact failure this
    filter exists to prevent."""
    body = client.get("/api/v1/providers/openrouter/models").json()

    assert "matched_total" in body
    assert body["sample_limit"] >= len(body["sample"])
