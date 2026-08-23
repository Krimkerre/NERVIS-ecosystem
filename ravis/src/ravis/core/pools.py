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

    def unmet_by(self, known: ModelCapabilities) -> list[str]:
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

    def eligible(self, candidates: dict[str, ModelCapabilities]) -> list[str]:
        """The models that satisfy every requirement, in preference order."""
        members = [
            model for model, known in candidates.items()
            if not self.requirements.unmet_by(known)
        ]
        return sorted(members, key=self._preference_rank)

    def _preference_rank(self, model: str) -> tuple[int, str]:
        """Rank by declared preference, then alphabetically.

        The alphabetical tiebreak is what makes selection *predictable*, which is
        M5's acceptance criterion: the same catalogue must produce the same route
        every time, or a route explanation is describing a coin toss.
        """
        for position, fragment in enumerate(self.prefer):
            if fragment in model:
                return (position, model)
        return (len(self.prefer), model)


_TOOLS_REQUIRED = PoolRequirements(required=frozenset({Capability.TOOLS}), minimum_context=32768)

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
    ),
    VirtualModelPool(
        pool_id="ravis/fast",
        label="Speed",
        description="Lowest latency, accepting weaker answers",
        prefer=("1.7b", "2b", "3b", "mini", "tiny"),
    ),
    VirtualModelPool(
        pool_id="ravis/performance",
        label="Performance",
        description="Best available answer, accepting latency and cost",
        prefer=("70b", "32b", "30b", "27b", "14b"),
    ),
    VirtualModelPool(
        pool_id="ravis/cheap",
        label="Cheap",
        description="Least monetary cost, preferring local models",
    ),
    VirtualModelPool(
        pool_id="ravis/local",
        label="Local Only",
        description="Never leaves this machine",
    ),
    VirtualModelPool(
        pool_id="ravis/api",
        label="API Only",
        description="Cloud providers only",
    ),
    VirtualModelPool(
        pool_id="ravis/private",
        label="Private",
        description="Strictest privacy level; cloud providers are excluded",
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
