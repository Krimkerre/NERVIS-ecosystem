"""Which models a pool takes by default, and why that is not a measurement.

`ravis/fast` with no default admits every model satisfying its invariants — six
hundred once four API providers are configured. Technically correct and useless:
the pool means "answer quickly", and nothing in a bare `/v1/models` listing says
how quickly anything answers. SIRVIS measures that for local models; there is no
equivalent for a cloud one.

Size is the axis these three pools differ on, and size *is* knowable — from the
parameter count in a name where there is one, and from the vendor's own product
tier where there is not. Vendors are reliable about tiering because they price
on it. Anthropic's haiku/sonnet/opus is the clearest case and maps exactly onto
the three pools.

It stays a **default an operator can overrule**, never a claim that a model was
measured.
"""

from __future__ import annotations

import pytest

from ravis.core.pools import POOLS_BY_ID, size_tier


class TestParameterCountsWin:
    """A count is the more specific signal, and the one that decides speed."""

    @pytest.mark.parametrize(
        ("model", "tier"),
        [
            ("qwen2.5-coder-7b", "small"),
            ("llama-3.1-70b", "large"),
            ("mistral-24b-instruct", "mid"),
        ],
    )
    def test_a_named_size_decides_the_tier(self, model: str, tier: str) -> None:
        assert size_tier(model) == tier

    def test_a_mixture_of_experts_is_tiered_by_its_active_count(self) -> None:
        """`qwen3-30b-a3b` is a 30B model that runs like a 3B one, and it is the
        running that `ravis/fast` is about. `parameter_scale` already reads the
        last match for this reason."""
        assert size_tier("qwen3-30b-a3b") == "small"


class TestVendorTiers:
    """For the models whose names carry no size at all."""

    @pytest.mark.parametrize(
        ("model", "tier"),
        [
            ("claude-haiku-4-5", "small"),
            ("claude-sonnet-5", "mid"),
            ("claude-opus-5", "large"),
            ("models/gemini-3.6-flash", "small"),
            ("models/gemini-pro-latest", "large"),
        ],
    )
    def test_the_vendors_own_word_decides(self, model: str, tier: str) -> None:
        assert size_tier(model) == tier

    def test_a_modifier_narrows_its_family(self) -> None:
        """`gpt-5-mini` is small even though `gpt-5` is large.

        Longest-match got this backwards, which is why the words are ordered
        rather than scored.
        """
        assert size_tier("gpt-5") == "large"
        assert size_tier("gpt-5-mini") == "small"
        assert size_tier("o3-mini") == "small"

    def test_a_tier_word_inside_a_longer_word_does_not_count(self) -> None:
        """`gemini` contains `mini`.

        Plain substring matching filed every Gemini model as small, including
        `gemini-pro`. Model ids separate their parts with hyphens and slashes,
        so anything alphanumeric on either side means the match landed inside a
        longer word.
        """
        assert size_tier("models/gemini-2.5-pro") == "large"


class TestModelsNotOnThisAxis:
    """`text-embedding-3-large` is not a large chat model. It is not a chat model."""

    @pytest.mark.parametrize(
        "model",
        ["text-embedding-3-large", "whisper-1", "dall-e-3", "omni-moderation-latest"],
    )
    def test_a_non_chat_model_has_no_tier(self, model: str) -> None:
        assert size_tier(model) is None

    def test_an_unrecognised_name_has_no_tier(self) -> None:
        """None rather than a guess. A default that swept unknowns into `fast`
        would be answering "how quick is this" with "no idea, probably quick"."""
        assert size_tier("deepseek-v4") is None


class TestDefaultMembership:
    def test_the_three_pools_partition_by_size(self) -> None:
        catalogue = ["claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5"]

        assert POOLS_BY_ID["ravis/fast"].default_membership(catalogue) == (
            "claude-haiku-4-5",
        )
        assert POOLS_BY_ID["ravis/balanced"].default_membership(catalogue) == (
            "claude-sonnet-5",
        )
        assert POOLS_BY_ID["ravis/performance"].default_membership(catalogue) == (
            "claude-opus-5",
        )

    def test_a_pool_with_no_default_takes_everything(self) -> None:
        catalogue = ["a", "b"]

        assert POOLS_BY_ID["ravis/auto"].default_membership(catalogue) == ("a", "b")

    def test_a_default_that_matches_nothing_present_takes_everything(self) -> None:
        """A curated list naming no installed model must not empty the pool.

        The operator did not choose that, and an empty pool refuses every
        request — a far worse outcome than a default that turned out not to
        apply here.
        """
        catalogue = ["deepseek-v4", "some-unknown-model"]

        assert POOLS_BY_ID["ravis/fast"].default_membership(catalogue) == tuple(catalogue)
