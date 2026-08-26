"""Virtual model pools — the things clients address instead of naming a model.

RAVIS.md §5. A pool is a *promise about capability*, not a list of models: it
declares what a member must satisfy, and membership is derived from that
declaration rather than typed out. Two consequences follow, and both matter.

Installing a model that meets the requirements adds it to the pool with nobody
editing anything. And a pool whose requirements nothing satisfies is
**unavailable** (§5.2) — never quietly downgraded to a model that almost fits.
The second is the load-bearing one: §5.1's hard invariant says every member of
`ravis/clarvis-agent` must satisfy the tool requirement, and the failure it
prevents is an agent routed to a model that cannot call tools, which surfaces as
a broken tool call far from its cause.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ravis.core.capabilities import Capability, ModelCapabilities

# Every pool ID carries this prefix. Clarvis derives an owner label from the
# vendor prefix, so these render as "by ravis" in its picker (§5).
POOL_PREFIX = "ravis/"


@dataclass(frozen=True)
class PoolRequirements:
    """What a model must satisfy to be a member.

    Required capabilities are a set rather than flags, so adding one is a data
    change. `minimum_context` is separate because it is a threshold rather than
    a yes/no — and an unknown context window fails it, since §12 forbids
    silently truncating context.
    """

    required: frozenset[Capability] = frozenset()
    minimum_context: int = 0
    # Where a member is allowed to run: `any`, `local`, or `remote`.
    #
    # **This existed only as prose until now.** `ravis/local` described itself as
    # "never leaves this machine" and `ravis/private` as "cloud providers are
    # excluded", and nothing enforced either — the routing engine receives a flat
    # table of model names with no record of which upstream they came from, so it
    # could not have enforced them. With four API providers configured, a request
    # to `ravis/local` was answered by an OpenRouter model. The prompt left the
    # machine, to a third party, from the one pool that promised it would not.
    locality: str = "any"

    def unmet_by(self, known: ModelCapabilities, *, remote: bool = False) -> list[str]:
        """Every reason this model cannot be a member, in readable form.

        Returns *all* reasons rather than the first, because §9.7 requires a
        route explanation to name what excluded a candidate — and "it failed
        something" is not an explanation a person can act on.
        """
        reasons = []
        for capability in sorted(self.required, key=lambda item: item.value):
            if not known.satisfies(capability):
                reasons.append(f"{capability.value} is {known.state_of(capability).value}")
        if self.minimum_context and not known.meets_context(self.minimum_context):
            found = known.context_window if known.context_window is not None else "unknown"
            reasons.append(f"context {found} < required {self.minimum_context}")
        if self.locality == "local" and remote:
            reasons.append("served by a remote provider, and this pool never leaves this machine")
        if self.locality == "remote" and not remote:
            reasons.append("runs on this machine, and this pool is cloud providers only")
        return reasons


@dataclass(frozen=True)
class VirtualModelPool:
    """One addressable pool.

    `prefer` is an ordered list of substrings. It is a weak, declared preference
    rather than a score — at M5 there is no evidence to score with, and inventing
    one would be the "opaque magic" §9.4 rules out. Real ranking arrives with
    SIRVIS evidence at M13; until then a route explanation says plainly that the
    choice was made on declared preference and stable ordering.
    """

    pool_id: str
    label: str
    description: str
    requirements: PoolRequirements = field(default_factory=PoolRequirements)
    prefer: tuple[str, ...] = ()
    # §9.2's soft preferences, the two of them that had no implementation.
    #
    # The spec's soft column reads "prefer local · prefer fast · prefer cheap ·
    # prefer already loaded". The last is residency and has worked since M5.
    # These two were prose: `ravis/cheap` described itself as "Least monetary
    # cost, preferring local models" and did neither — with an installed local
    # model and a paid cloud one both eligible, it selected whichever sorted
    # first alphabetically.
    #
    # **Opt-in per pool, never global.** An unconditional placement or price
    # term would become the entire ordering for every pool that declares no
    # preference, which is most of them — the same trap `size_rank` avoids by
    # applying only where a pool actually declared what it wants.
    prefer_local: bool = False
    prefer_cheap: bool = False
    # §9.2's "prefer fast", ranked on what RAVIS has actually timed.
    #
    # Only on models with enough samples to mean it — see `MINIMUM_SAMPLES`.
    # Everything else ranks *neutral*, deliberately, and this is the one place
    # the treatment differs from price. An unpriced model sorts last because a
    # price exists and the provider chose not to publish it. An unmeasured model
    # has simply never been called here, and sorting it last would be a trap
    # that closes: never chosen, so never measured, so never chosen.
    prefer_fast: bool = False
    # Which size tier this pool takes by default: `small`, `mid`, `large`.
    #
    # **A declared default, not a measurement, and the difference is the whole
    # justification.** `ravis/fast` with no default admits every model that
    # satisfies its invariants — six hundred once four API providers are
    # configured — which is technically correct and useless: the pool means
    # "answer quickly", and nothing in a bare `/v1/models` listing says how
    # quickly anything answers. SIRVIS measures that for local models and there
    # is no equivalent for a cloud one.
    #
    # What *is* knowable is size, and size is the axis these three pools differ
    # on: small ones answer sooner, large ones answer better. See `size_tier`
    # for how it is read. Empty means every eligible model, which is what every
    # other pool wants.
    default_tier: str = ""
    # Whether a picker should offer this pool.
    #
    # The ID stays addressable either way — §5 requires it and a client may name
    # it — so this hides a choice rather than removing a capability. Exactly one
    # pool uses it: `ravis/private` is indistinguishable from `ravis/local`
    # today, and offering two identical options invites somebody to reason about
    # a difference that is not there.
    listed: bool = True

    def eligible(
        self,
        candidates: dict[str, ModelCapabilities],
        remote: frozenset[str] = frozenset(),
    ) -> list[str]:
        """The models that satisfy every requirement, in declared-preference order.

        Ordering here considers the pool's own intent only. Runtime facts —
        which models are loaded, how much memory is free — belong to the routing
        engine, because a pool definition is configuration and should not change
        meaning with the weather.
        """
        members = [
            model for model, known in candidates.items()
            if not self.requirements.unmet_by(known, remote=model in remote)
        ]
        return sorted(members, key=lambda model: (self.preference_rank(model), *size_rank(model)))

    def default_membership(self, candidates: list[str]) -> tuple[str, ...]:
        """The curated members present in this catalogue, or everything.

        Returns all candidates when the pool declares no default, so a pool
        without one behaves exactly as it always has. Returns all of them again
        when the default matches nothing present — a curated list that happens
        to name no installed model must not empty the pool, because the operator
        did not choose that and an empty pool refuses every request.
        """
        if not self.default_tier:
            return tuple(candidates)
        matched = tuple(
            model for model in candidates if size_tier(model) == self.default_tier
        )
        return matched or tuple(candidates)

    def preference_rank(self, model: str) -> int:
        """How well a model matches this pool's declared preference, lowest best.

        An int rather than a full sort key, so the routing engine can combine it
        with runtime facts. Callers pair it with `size_rank` for the tiebreak
        that makes selection *predictable* — M5's acceptance criterion, since a
        route explanation describing a coin toss explains nothing.
        """
        for position, fragment in enumerate(self.prefer):
            if fragment in model:
                return position
        return len(self.prefer)


_TOOLS_REQUIRED = PoolRequirements(required=frozenset({Capability.TOOLS}), minimum_context=32768)

# A parameter count embedded in a model identifier: `7b`, `2.6b`, `8x7b`, `30b-a3b`.
# Case-insensitive, and anchored on a word boundary so a `b` inside a word never
# counts.
_PARAMETERS = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)


def parameter_scale(model: str) -> float | None:
    """Billions of parameters read from a model identifier, or None.

    A heuristic over a *name*, which is the only size signal available before
    SIRVIS measures anything (M13) — a generic OpenAI-compatible endpoint
    publishes an ID and nothing else. It is used only as a last-resort tiebreak,
    never as a constraint, so being wrong costs an ordering rather than a route.

    **The last match wins**, which matters for mixture-of-experts names: in
    `qwen3-30b-a3b` the 3B is the *active* parameter count and the 30B is the
    total, and it is the active count that decides how fast the thing answers.

    None means the name carries no size — `phi-4-mini-instruct` and
    `granite-4.0-h-tiny` both describe their size in words. That is an absence
    rather than a zero (runbook §14.4), and `size_rank` sorts it last rather
    than pretending it is small.
    """
    matches = _PARAMETERS.findall(model)
    return float(matches[-1]) if matches else None


# Where each tier ends, in billions of parameters.
#
# Round numbers rather than derived ones, because there is nothing to derive
# them from: these are the shoulders of the distribution as vendors actually
# ship it — 7-8B is the small tier everybody has, 70B+ is the flagship tier, and
# the teens-to-thirties sit between. A boundary being approximate is fine for a
# *default* somebody can overrule and would not be fine for a constraint.
SMALL_CEILING_B = 8.0
MID_CEILING_B = 34.0

# Each vendor's own word for a tier, for the models whose names carry no size.
#
# Vendors are reliable about this because they price on it. Anthropic's
# haiku/sonnet/opus is the clearest case and maps exactly onto the three pools;
# the rest follow the same shape. It is product tiering read as product
# tiering — a much weaker claim than measuring latency, and a much stronger one
# than guessing from a substring nobody chose deliberately.
#
# **Ordered, small first, because a modifier narrows a family.** `gpt-5-mini` is
# small even though `gpt-5` is large, and longest-match got that backwards.
TIER_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("small", ("haiku", "mini", "nano", "flash", "lite", "tiny", "instant", "small")),
    ("large", ("opus", "ultra", "max", "large", "pro", "gpt-4", "gpt-5", "o1", "o3")),
    ("mid", ("sonnet", "medium", "gpt-4o", "gpt-4.1")),
)

# Models that are not on this axis at all.
#
# `text-embedding-3-large` is not a large chat model; it is not a chat model.
# Sweeping it into `ravis/performance` on the strength of the word "large" would
# answer a conversation with an embedding endpoint — and the failure would look
# like the model being broken rather than like the pool being wrong.
NOT_CHAT = (
    "embedding", "embed", "whisper", "tts", "dall-e", "moderation",
    "rerank", "guard", "transcribe", "image", "video", "voice",
)


def _has_word(model: str, word: str) -> bool:
    """Whether `word` appears in `model` as its own token.

    Bounded on both sides, because plain substring matching finds `mini` inside
    **ge**mini* and files every Gemini model as small. Model ids separate their
    parts with hyphens, slashes, dots and underscores, so anything alphanumeric
    on either side means the match landed inside a longer word.
    """
    return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", model) is not None


def size_tier(model: str) -> str | None:
    """`small`, `mid`, `large`, or None when the name says none of them.

    **Parameter count first, vendor word second.** A count is the more specific
    signal and it is the one that decides how fast the thing answers — including
    for mixture-of-experts names, where `parameter_scale` deliberately reads the
    *active* count rather than the total, so a 30B-A3B lands in `small` because
    that is how it behaves.

    None rather than a guess when a name carries neither, and None outright for
    anything that is not a chat model.
    """
    lowered = model.lower()
    if any(marker in lowered for marker in NOT_CHAT):
        return None
    scale = parameter_scale(model)
    if scale is not None:
        if scale <= SMALL_CEILING_B:
            return "small"
        return "mid" if scale <= MID_CEILING_B else "large"
    for tier, words in TIER_WORDS:
        if any(_has_word(lowered, word) for word in words):
            return tier
    return None


def size_rank(model: str) -> tuple[int, float, str]:
    """The tiebreak between candidates a pool considers equal: smaller first.

    This replaces a purely alphabetical tiebreak, and the reason is that
    alphabetical order carries *no meaning at all* — it once put a 14B ahead of
    a 7B that scored identically at three times the rate, purely because "1"
    sorts before "7". §9.2 lists "prefer fast" and "prefer cheap" among the soft
    preferences, and among otherwise-equal candidates the smaller one is both.

    It stays a tiebreak rather than becoming a score. A pool's declared
    preference and residency both outrank it, and nothing here claims the
    smaller model is *better* — only that when RAVIS has no evidence either way,
    the cheaper one to run is the better default. Real ranking is M13.

    **Only consulted for a pool that declared a preference.** The engine applies
    that restriction, and it exists because the first version did not: with no
    preference to be equal *on*, every candidate ties and size becomes the whole
    ranking rather than the last word in it.

    The model name is retained as the final component so the order stays total
    and reproducible, which §9.7's determinism gate requires.
    """
    scale = parameter_scale(model)
    return (1, 0.0, model) if scale is None else (0, scale, model)

# The required defaults from §5. `ravis/clarvis-chat` and `ravis/clarvis-agent`
# are the two whose IDs must stay stable — Clarvis names them in configuration.
DEFAULT_POOLS: tuple[VirtualModelPool, ...] = (
    VirtualModelPool(
        pool_id="ravis/auto",
        label="Auto",
        description="Let RAVIS decide, with no constraint beyond what the request needs",
    ),
    VirtualModelPool(
        pool_id="ravis/balanced",
        label="Balanced",
        description="A reasonable middle between speed, cost and quality",
        default_tier="mid",
    ),
    VirtualModelPool(
        pool_id="ravis/fast",
        label="Speed",
        description="Lowest latency, accepting weaker answers",
        prefer=("1.7b", "2b", "3b", "mini", "tiny"),
        default_tier="small",
        prefer_fast=True,
    ),
    VirtualModelPool(
        pool_id="ravis/performance",
        label="Performance",
        description="Best available answer, accepting latency and cost",
        prefer=("70b", "32b", "30b", "27b", "14b"),
        default_tier="large",
    ),
    VirtualModelPool(
        pool_id="ravis/cheap",
        label="Cheap",
        description="Least monetary cost, preferring local models",
        # Both halves of its own description, which until now it implemented
        # neither of. Price first: a local model prices at 0.0 and wins outright
        # against anything paid, so "preferring local" mostly falls out of
        # "least cost" — `prefer_local` decides the case where a cloud model is
        # also free, and OpenRouter has hundreds of those.
        prefer_cheap=True,
        prefer_local=True,
    ),
    VirtualModelPool(
        pool_id="ravis/local",
        label="Local Only",
        description="Never leaves this machine",
        requirements=PoolRequirements(locality="local"),
    ),
    VirtualModelPool(
        pool_id="ravis/api",
        label="API Only",
        description="Cloud providers only",
        requirements=PoolRequirements(locality="remote"),
    ),
    VirtualModelPool(
        pool_id="ravis/private",
        label="Private",
        description="Strictest privacy level; cloud providers are excluded",
        # The same constraint as `ravis/local`, and that is worth stating rather
        # than hiding. §5 requires both pools and never says how they differ; the
        # two descriptions were written to fill that silence and ended up saying
        # nearly the same thing. They express different *intents* — `local` is
        # about placement, `private` about disclosure — which coincide exactly as
        # long as the only non-loopback option is a third party's API. They stop
        # coinciding the moment a self-hosted box on the LAN is an upstream:
        # that is not local, and whether it is private is the operator's call.
        # Until that exists, giving `private` a fabricated extra constraint would
        # be inventing a distinction rather than implementing one.
        requirements=PoolRequirements(locality="local"),
    ),
    VirtualModelPool(
        pool_id="ravis/coding",
        label="Coding",
        description="Optimized for writing and reasoning about code",
        prefer=("coder", "code", "qwen"),
    ),
    VirtualModelPool(
        pool_id="ravis/reasoning",
        label="Reasoning",
        description="Models that reason before answering",
        requirements=PoolRequirements(required=frozenset({Capability.REASONING})),
    ),
    VirtualModelPool(
        pool_id="ravis/long-context",
        label="Long Context",
        description="Large context windows",
        requirements=PoolRequirements(minimum_context=131072),
    ),
    VirtualModelPool(
        pool_id="ravis/clarvis-chat",
        label="Clarvis Chat",
        description=(
            "Conversation, planning and instruction following. Tool support is optional "
            "unless the request itself supplies tools (§5.1)."
        ),
        # §5.1 asks this pool for instruction following and reasonable latency,
        # and until now it declared no preference at all — which meant pure
        # alphabetical order, and against a real catalogue that selected the
        # slowest installed model by accident. An instruction-tuned build is
        # what the description literally names, so that is what is preferred.
        # It narrows the field to a defensible class without pretending to rank
        # inside it: which instruct model is *best* is evidence RAVIS does not
        # have until M13.
        prefer=("instruct", "chat"),
    ),
    VirtualModelPool(
        pool_id="ravis/clarvis-agent",
        label="Clarvis Agent",
        description=(
            "Coding, tool use and repository reasoning. Tools are REQUIRED: §5.1's hard "
            "invariant forbids admitting a non-tool-capable model however well it codes."
        ),
        requirements=_TOOLS_REQUIRED,
        prefer=("coder", "code"),
    ),
)

POOLS_BY_ID = {pool.pool_id: pool for pool in DEFAULT_POOLS}


def is_pool_id(model: str) -> bool:
    """Whether a client addressed a pool rather than naming a model."""
    return model in POOLS_BY_ID


def direct_provider(model: str) -> str | None:
    """The provider named by a direct address like `ravis/anthropic/claude-x`.

    The half `direct_target` discards. It is what decides which *path* runs: a
    provider whose upstream does not speak the external protocol needs its
    request translated (§6, Path B), and nothing else in a request says so.
    """
    if not model.startswith(POOL_PREFIX) or is_pool_id(model):
        return None
    remainder = model[len(POOL_PREFIX):]
    provider, separator, target = remainder.partition("/")
    return provider if separator and target and provider else None


def direct_target(model: str) -> str | None:
    """The model named by a direct address like `ravis/lmstudio/qwen3-4b`.

    Direct addressing bypasses selection but keeps everything else — error
    normalization, cost and health tracking, policy (§5). Returns `None` when
    this is not a direct address, which the caller distinguishes from a pool.
    """
    if not model.startswith(POOL_PREFIX) or is_pool_id(model):
        return None
    remainder = model[len(POOL_PREFIX):]
    _, separator, target = remainder.partition("/")
    return target if separator and target else None
