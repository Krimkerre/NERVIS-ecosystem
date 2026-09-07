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

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256

from ravis.core.capabilities import Capability, CapabilityState, ModelCapabilities

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
    # The mirror of the above, and not the same as `locality="remote"`.
    #
    # `locality` is a hard constraint: it removes local models from the pool
    # entirely, so a pool that declares it has nowhere to go when every provider
    # is down. This is a *soft* preference — a hosted model is chosen first and
    # the machine's own remains eligible underneath it, which is what "prefer
    # the API, fall back to local" actually means. Conversation is the case that
    # wants it: the small model that wins on cheap-and-already-loaded is a poor
    # thing to talk to, and no model at all is worse.
    prefer_remote: bool = False
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
    # How close two measured models have to be before they count as equally
    # fast, in milliseconds. Zero means exact ordering.
    #
    # **This is what makes a pool balanced rather than merely fast.** Speed and
    # cost are both real here — one measured, one published — and combining them
    # needs an exchange rate between milliseconds and dollars that no
    # measurement supplies. Inventing a weight would be §9.4's opaque magic with
    # arithmetic on top.
    #
    # A bucket avoids the invention. Models within the window count as equally
    # quick, which is a claim the data does support at this resolution, and the
    # cheaper of them wins. Nothing is weighted against anything; two facts are
    # consulted in a stated order.
    speed_bucket_ms: float = 0.0
    # The most a model may cost per million tokens and still be a default
    # member, in USD. `None` means price is not a membership condition.
    #
    # Set to `0.0` on `ravis/cheap`, which is what "cheap" turned out to mean in
    # practice: the models that cost nothing per token. A local runtime bills
    # nothing, and OpenRouter publishes hundreds of `:free` variants at exactly
    # zero — both are facts somebody published, not estimates.
    #
    # **An unpriced model is excluded, and that has a consequence worth naming.**
    # Google, OpenAI and Anthropic ship catalogues with no pricing at all, so
    # none of their models can be *derived* as free. Google's free tier is real
    # but it is a quota on an account, not a property of a model, and whether it
    # applies depends on whether billing is attached — which is account state
    # RAVIS cannot see and must not guess at. Those models are one tick away in
    # the picker; they are simply not something this can conclude.
    max_price_per_million: float | None = None
    #: Whether the ceiling is a **promise** rather than a preference.
    #:
    #: Membership ends `tuple(matched) or tuple(candidates)` — a guard against a
    #: pool resolving to nothing. For a pool expressing taste that is right:
    #: `ravis/cheap` means "least cost", and a machine where nothing is free
    #: should still get the cheapest paid model rather than a refusal.
    #:
    #: For `ravis/free-api` it is exactly wrong. That pool's name is its contract,
    #: and falling back to everything means a caller who asked for free is
    #: billed — silently, because the fallback leaves no exclusion to explain.
    #: With this set the pool resolves to nothing instead, and the engine
    #: refuses with a reason, which is what every other promise in this file
    #: does (`ravis/local` refuses rather than relaxing).
    refuse_above_ceiling: bool = False
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
    # Curated membership: a model joins this pool only if its id contains one of
    # these fragments.
    #
    # **Why a pool needs this at all.** `ravis/chat` admitted every model RAVIS
    # knew — 549 of them, no requirement, no preference — and the engine's own
    # comment says what happens then: "pools that say nothing fall back to
    # alphabetical order, which is meaningless". So conversation on this machine
    # was served by `amazon/nova-2-lite-v1`, which won on the letter A, and
    # `size_rank` put every model whose *name* carries a parameter count ahead
    # of every frontier model whose name does not — a 1B instruct outranking
    # gpt-5 because "1b" parses and "gpt-5" does not.
    #
    # **This is a declared preference, not a measurement, and the distinction
    # is the whole point.** §9.2 forbids inventing a quality ranking, and this
    # does not claim one: it says which families are *general conversational
    # assistants*, which is a statement about what a model is for rather than
    # about how good it is. Ranking within the class stays where §13 leaves
    # it — unmeasured, and honest about that.
    #
    # Order matters: `preference_rank` reads position, so this same tuple gives
    # the pool its ordering. An operator who disagrees replaces the list, and a
    # per-pool selection saved through the management API overrides it entirely.
    # The SIRVIS role whose evidence is about *this pool's* work.
    #
    # **Membership derived rather than declared, where a measurement exists.**
    # `curated` above is a judgement written by hand against a catalogue that
    # turns over every few weeks — defensible, and exactly what §13's evidence
    # is meant to replace. A build measured under this role and passing joins
    # whether or not a family fragment matched it; one measured and failing is
    # excluded whether or not one did. A build nobody has measured falls back to
    # the families, which is where every pool was.
    #
    # Empty means no role measures this pool's work yet, and the families are
    # the whole answer. Saying so is the point: the pools that can be evidenced
    # are visibly different from the pools that cannot.
    evidence_role: str = ""
    curated: tuple[str, ...] = ()
    # Fragments that disqualify a model even when a curated family matched it.
    # `gpt-5-codex` matches `gpt-5` and is not a conversational assistant;
    # `qwen2.5-vl-72b-instruct` matches an instruct family and is a vision
    # model. Per pool, because the same marker that disqualifies a model here
    # is what qualifies it next door.
    excluded: tuple[str, ...] = ()
    # Whether a picker should offer this pool.
    #
    # The ID stays addressable either way — §5 requires it and a client may name
    # it — so this hides a choice rather than removing a capability. Exactly one
    # pool uses it: `ravis/private` is indistinguishable from `ravis/local`
    # today, and offering two identical options invites somebody to reason about
    # a difference that is not there.
    listed: bool = True

    # §5.4's version, bumped by hand when a pool's *meaning* changes — when
    # `ravis/cheap` starts meaning something a consumer would want to know
    # about, not when its wording is tidied.
    version: str = "1"

    @property
    def revision(self) -> str:
        """A stable fingerprint of this pool's behaviour (§5.4).

        **Derived rather than stored, so it cannot drift from what it names.** A
        hand-maintained revision is a number somebody forgets to increment, and
        a consumer pinning a stale one is worse off than a consumer who could
        not pin at all: they believe they are protected.

        `clarvis-chat` and `clarvis-agent` get independent revisions for free,
        which §5.4 requires explicitly — each hash covers only its own pool, so
        changing one cannot move the other.

        **Label and description are excluded on purpose.** A pin is a claim
        about how the pool selects, and fixing a typo in a description must not
        invalidate every consumer's pin. Everything that changes *which model
        comes back* is in here; nothing that only changes how it reads is.
        """
        return sha256(self._definition().encode()).hexdigest()[:12]

    def revision_with(self, narrowing: Sequence[str] | None) -> str:
        """This pool's revision including an operator's stored narrowing.

        **The narrowing is the change that most often alters which model comes
        back, and it was the one thing `revision` could not see.** It lives in
        `pools.json` rather than in the definition, so a consumer pinning a
        revision was told nothing when an operator ticked a model out of the
        pool -- the case §5.4's pin exists for, and the one the docstring above
        promised was covered ("everything that changes which model comes back is
        in here").

        The *catalogue* is still excluded, deliberately: installing a model
        changes what a derived pool can choose from, and a revision that moved
        on every install would be a version number for the machine rather than
        for the pool. A narrowing is configuration, like the definition.

        No narrowing returns the definition revision unchanged, so a deployment
        that has never touched a pool keeps the revision it already published.
        """
        if not narrowing:
            return self.revision
        stated = "|".join(sorted(narrowing))
        return sha256(f"{self._definition()}\nnarrowed:{stated}".encode()).hexdigest()[:12]

    def _definition(self) -> str:
        """The behavioural definition, rendered canonically.

        Sorted and explicit rather than `repr()` of the dataclass: `repr` would
        fold in the label and description, and would change shape if a field
        were reordered — turning a cosmetic edit into a revision bump and
        breaking every pin for no reason.
        """
        requirements = {
            "required": sorted(item.value for item in self.requirements.required),
            "minimum_context": self.requirements.minimum_context,
            "locality": self.requirements.locality,
        }
        return json.dumps(
            {
                "pool_id": self.pool_id,
                "version": self.version,
                "requirements": requirements,
                "prefer": list(self.prefer),
                "prefer_local": self.prefer_local,
                "prefer_remote": self.prefer_remote,
                "prefer_cheap": self.prefer_cheap,
                "prefer_fast": self.prefer_fast,
                "speed_bucket_ms": self.speed_bucket_ms,
                "max_price_per_million": self.max_price_per_million,
                "default_tier": self.default_tier,
            },
            sort_keys=True,
        )

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
            # **Routability first, and it belongs here rather than only in
            # `default_membership`.** That is where it was, so a pool the
            # operator had curated by hand skipped it — `ravis/free-api` held
            # two Google Lyria entries, which are free, remote, and generate
            # music. Whether a model can answer a chat completion at all is not
            # a matter of taste, so it is not something a tick can override; the
            # same reasoning `_is_routable`'s own docstring gives.
            if self._is_routable(model)
            and not self.requirements.unmet_by(known, remote=model in remote)
        ]
        return sorted(members, key=lambda model: (self.preference_rank(model), *size_rank(model)))

    def default_membership(
        self,
        candidates: list[str],
        prices: Mapping[str, float | None] | None = None,
        evidence: Mapping[str, str] | None = None,
    ) -> tuple[str, ...]:
        """The curated members present in this catalogue, or everything.

        Returns all candidates when the pool declares no default, so a pool
        without one behaves exactly as it always has. Returns all of them again
        when the default matches nothing present — a curated list that happens
        to name no installed model must not empty the pool, because the operator
        did not choose that and an empty pool refuses every request.

        A price ceiling is applied only when prices were supplied. Without them
        nothing is known about cost, and filtering on an absent fact would empty
        the pool for a reason nobody could read off the data.
        """
        # **The baseline, before anything a pool declares.** An embedding model
        # cannot answer a chat completion and neither can a moderation
        # classifier, an image model or a batch endpoint — yet `ravis/auto` held
        # 41 of them and `ravis/local` held `text-embedding-nomic-embed-text`.
        # No pool wants these and every pool had them, which makes it a property
        # of routing rather than of any one pool's taste.
        routable = [model for model in candidates if self._is_routable(model)]
        measured = evidence or {}
        if (not self.default_tier and self.max_price_per_million is None
                and not self.curated and not self.excluded and not measured):
            return tuple(routable)
        matched = [
            model for model in routable
            if (not self.default_tier or size_tier(model) == self.default_tier)
            and self._admits(model, measured)
        ]
        if self.max_price_per_million is not None and prices is not None:
            ceiling = self.max_price_per_million
            matched = [
                model for model in matched
                # `is not None` first: unpriced is excluded rather than treated
                # as free, or every catalogue that publishes nothing would land
                # in the cheap pool by saying least about itself.
                if prices.get(model) is not None and prices[model] <= ceiling  # type: ignore[operator]
            ]
            if self.refuse_above_ceiling:
                # No `or tuple(candidates)`. A pool whose ceiling is a promise
                # resolves to nothing rather than to everything, and the engine
                # refuses — the same shape as `ravis/local` declining to relax.
                return tuple(matched)
        return tuple(matched) or tuple(candidates)

    def _is_routable(self, model: str) -> bool:
        """Whether this model can answer a chat completion at all.

        Not a matter of taste and not per pool: an embedding model, a reranker,
        a moderation classifier, a speech model and a batch endpoint are all
        things that cannot serve the request RAVIS routes. `NOT_CHAT` already
        named most of them for `size_tier`, which meant the knowledge existed
        and membership did not consult it.

        `:batch` is here rather than in a pool's own list for the same reason:
        `anthropic/claude-opus-4:batch` is the same weights on an asynchronous
        queue measured in hours, which cannot answer anybody waiting on a
        reply — in any pool.

        **The one word that is per pool is `image`, and it had to become so.**
        The baseline's argument is "no pool wants these", and that stopped being
        true the day a pool required image *output*: `google/gemini-2.5-flash-image`
        answers an ordinary chat completion with a picture beside its text, so
        it is chat-shaped in the only sense this predicate is about. Measured
        7 September 2026 — the drawing pool held nothing but OpenRouter's two
        auto-routers, because every model that actually draws had the word
        `image` in its name. `dall-e` stays in the list unconditionally: it is
        a different endpoint, not a chat model that draws.
        """
        lowered = model.lower()
        if lowered.endswith(":batch"):
            return False
        words: tuple[str, ...] = NOT_CHAT
        if Capability.IMAGE_OUT in self.requirements.required:
            words = tuple(word for word in NOT_CHAT if word != "image")
        return not any(_has_word(lowered, word) for word in words)

    def _admits(self, model: str, measured: Mapping[str, str]) -> bool:
        """Whether this pool takes this build, measurement first.

        **A measurement outranks the declared families in both directions.** A
        build this pool's role measured and passed joins even if no family
        fragment names it — which is the point: the families are a stand-in for
        evidence, and a stand-in must lose to the thing it stands in for. A
        build measured and failing is excluded even if a fragment does name it,
        because a hand-written list cannot outvote a trial that ran.

        `UNKNOWN` and absence both fall through to the families. Neither is a
        failed measurement: one means the role was measured on some other axis,
        the other that nobody has measured it at all, and §9.1 fails closed on
        what is *not established* rather than treating silence as refusal.
        """
        verdict = measured.get(model, "")
        if verdict == CapabilityState.SUPPORTED.value:
            return True
        if verdict == CapabilityState.UNSUPPORTED.value:
            return False
        return self._is_curated(model)

    def _is_curated(self, model: str) -> bool:
        """Whether this pool wants this model.

        **The two halves are independent**, which the first version got wrong by
        returning early when `curated` was empty: that made `excluded` dead on
        every pool that declared exclusions and no family list — which was four
        of them, including the three whose whole point is to differ from
        `ravis/coding` on purpose.
        """
        lowered = model.lower()
        if any(marker in lowered for marker in self.excluded):
            return False
        return not self.curated or any(fragment in lowered for fragment in self.curated)

    def preference_rank(self, model: str) -> int:
        """How well a model matches this pool's declared preference, lowest best.

        An int rather than a full sort key, so the routing engine can combine it
        with runtime facts. Callers pair it with `size_rank` for the tiebreak
        that makes selection *predictable* — M5's acceptance criterion, since a
        route explanation describing a coin toss explains nothing.
        """
        lowered = model.lower()

        # **The curated order first, which several pools rely on and none got.**
        # `CHAT_FAMILIES` is written cheap-frontier first and expensive last,
        # and says so: *"`preference_rank` reads position, so this is enforced
        # by where these sit rather than by a rule somewhere else."* It was not
        # — this method read `prefer` alone, so that ordering ranked nothing.
        #
        # Seen live: `ravis/clarvis-chat` declared `prefer=("instruct", "chat")`,
        # fragments that match nearly every chat model, so seven candidates tied
        # and the size tiebreak picked the smallest. A 7B answered a greeting by
        # paraphrasing its own brevity instruction.
        #
        # Before `prefer` rather than after it. The membership list is written
        # for this exact decision — cheap frontier, capable middle, expensive,
        # open-weight — while `prefer` holds fragments general enough to match
        # most of the pool. A general fragment must not outrank a specific
        # position, or the ordering is inert again.
        #
        # `prefer` still decides everything the curated list does not name,
        # which is what §5.1's "instruction following" needs: against a
        # catalogue of builds nobody enumerated, `instruct` beating a 2B with no
        # such marker is the whole of the available signal.
        for position, fragment in enumerate(self.curated):
            if fragment in lowered:
                return position
        for position, fragment in enumerate(self.prefer):
            if fragment in lowered:
                return len(self.curated) + position
        return len(self.curated) + len(self.prefer)


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
    # **Music generation**, found in `ravis/free-api`: `google/lyria-3-pro-preview`
    # publishes a price of zero and runs remotely, so it satisfied a free pool
    # perfectly and cannot answer a chat completion at all. The list already
    # covered speech and images and had no word for music.
    #
    # Named families rather than the word `audio`, which is the trap here:
    # `gpt-4o-audio-preview` *is* a chat model that happens to hear, and
    # excluding it would drop a working model to catch a broken one.
    "lyria", "music", "musicgen", "audiogen", "suno", "bark",
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

# Specialists that a family fragment sweeps in by accident. `gpt-5-codex` is a
# `gpt-5`, `qwen2.5-vl-72b-instruct` is a `qwen-2.5-…-instruct`, and
# `deepseek-r1-distill-llama-70b` is a `llama-…`; each is a real model for a
# real job that is not conversation. Checked as substrings after the family
# match, because the alternative is spelling every good variant of every family
# by hand and missing the next one released.
#
# **Per pool, never global.** A code specialist is noise in `ravis/chat` and the
# entire point of `ravis/coding`, so this belongs to the pool that means it.
NOT_CONVERSATION: tuple[str, ...] = (
    "codex", "coder", "codestral", "devstral",   # code
    "-vl-", "vision", "-image",                  # vision
    "distill", "-deep", "thinking",              # reasoning-first
    "guard", "moderat",                          # safety classifiers
)

# The families `ravis/chat` draws from, in the order it prefers them.
#
# **What this list is.** General conversational assistants — instruction-tuned
# models built to be talked to. It is not a quality ranking and cannot be one:
# §9.2 forbids RAVIS inventing quality it has not measured, and nothing here
# has been measured for conversation. What it encodes is *what a model is for*,
# which is knowable from the model itself, and a rough order of capability
# within that — frontier assistants before small ones, because the failure this
# fixes is a conversation served by whatever tiny thing sorted first.
#
# **What it deliberately leaves out**, each for a reason a person can check:
#
#   - reasoning-first builds (`r1-distill`, `deep`, `thinking`) — they spend the
#     output budget thinking, which is why a 24-token title call on this machine
#     came back as "Okay, let's tackle this user query"
#   - code and vision specialists (`coder`, `codestral`, `-vl-`) — better served
#     by `ravis/coding`, and a pool that admits them dilutes what naming this
#     one means
#   - embeddings, rerankers, moderation, audio and image models, which `NOT_CHAT`
#     already excludes everywhere
#   - batch endpoints, excluded by `_is_curated`: same model, asynchronous queue
#
# It ends with the local families so the pool keeps its stated shape — "a hosted
# model first, this machine's own underneath it" — and still answers when no
# provider is reachable.
CHAT_FAMILIES: tuple[str, ...] = (
    # **The cheap frontier tier leads, and that is a cost decision.** Ordering
    # by capability alone put `claude-fable-5` on every unremarkable turn of
    # every conversation, which is the most expensive way to answer "how is
    # RAVIS doing". These are frontier-quality models at a fraction of the
    # price, and the pool's job is to be the sane default rather than the best
    # possible answer regardless of the bill.
    "claude-haiku", "gemini-2.5-flash", "gpt-5-mini", "gpt-4o-mini",
    "deepseek-chat", "qwen-plus", "ministral-14b",
    # The capable middle, reached when nothing above it is available.
    "claude-sonnet", "gpt-5", "gemini-2.5-pro", "gpt-4.1", "gpt-4o",
    "mistral-large", "command-r", "nova-pro",
    # **Last, and last is the whole specification.** An expensive model is a
    # manual pick — named directly, or ticked into this pool in the picker —
    # and never what a pool reaches for on an ordinary turn. Keeping them at
    # the end of the order rather than out of the list makes them exactly one
    # thing: the answer when everything above is unavailable, which is better
    # than refusing the request. `preference_rank` reads position, so this is
    # enforced by where these sit rather than by a rule somewhere else.
    "claude-opus", "claude-fable", "grok-4", "qwen-max", "nova-premier",
    # Open-weight assistants, hosted or local, largest families first.
    "llama-3.3-70b-instruct", "qwen-2.5-72b-instruct", "llama-3.1-70b-instruct",
    "mistral-small", "ministral-14b", "qwen3-4b", "gemma-4", "granite-4",
    "llama-3.1-8b-instruct", "qwen-2.5-7b-instruct",
)

# What `ravis/coding` draws from, in the order it prefers them.
#
# **The pool declared `prefer=("coder", "code", "qwen")` and no membership**,
# which means it admitted every model RAVIS knew and merely sorted three
# fragments to the front — and `qwen` as a preference fragment promotes every
# Qwen build there is, including a 1.7B that has never written a line of code
# here. What a coding pool needs is the opposite: a membership that is about
# coding, and an order inside it.
#
# Frontier general models lead rather than the specialists. That is a real
# claim about this class of work and not a quality ranking: an assistant that
# reads a repository, follows an instruction and writes a patch is doing
# something a code-completion specialist is not built for, and the specialists
# are kept because they are cheap, local and good at the narrower job.
CODE_FAMILIES: tuple[str, ...] = (
    # The capable middle leads, on the same cost reasoning as `CHAT_FAMILIES`:
    # a pool that answers every "rename this variable" with the most expensive
    # model in the catalogue is not a coding pool, it is a bill.
    "claude-sonnet", "gpt-5-mini", "qwen3-coder", "qwen-2.5-coder",
    "qwen2.5-coder", "codestral", "devstral", "deepseek-coder",
    # Stronger general models next, reached when the above are unavailable.
    "gpt-5", "gemini-2.5-pro", "deepseek-chat", "codellama", "starcoder",
    "granite-code", "codegemma",
    # Last-ditch only, for the reason `CHAT_FAMILIES` states at the same place.
    "claude-opus", "grok-4", "claude-fable",
    # Catch-alls, last on purpose. A build nobody has heard of that calls itself
    # a coder belongs in this pool — the named families above are an ordering,
    # not a gate, and a membership that admitted only models on a hand-kept list
    # would go stale the week after it was written.
    "coder", "codex", "code",
)

# What `ravis/reasoning` draws from. The pool already *requires* the `reasoning`
# capability, which is a real gate — but the capability is advertised by the
# provider, and "supports a reasoning parameter" is a different claim from
# "built to reason". These are the families built for it, and the requirement
# stays on top of them.
REASONING_FAMILIES: tuple[str, ...] = (
    "claude-sonnet", "gpt-5-mini", "o1", "o3", "o4", "deepseek-r1",
    "deepseek-v4", "qwq", "magistral", "thinking", "-deep", "reason",
    "r1-distill", "gpt-5", "gemini-2.5-pro",
    # Last-ditch only. Reasoning is where an expensive model is most tempting
    # and most costly, so it sits where it can still answer a request nothing
    # else can and nowhere earlier.
    "claude-opus", "grok-4",
)

# Specialists that a general-purpose pool should not reach for. `ravis/balanced`,
# `ravis/fast` and `ravis/performance` differ from each other on size and from
# `ravis/coding` on purpose — a "balanced" pool that answers with a code
# completion model is not balanced, it is miscategorised.
#
# Not applied to `ravis/auto`, which is the one pool whose description promises
# no constraint beyond what the request needs, and where a code request should
# be able to reach a code model.
GENERAL_PURPOSE_EXCLUSIONS: tuple[str, ...] = (
    "coder", "codex", "codestral", "devstral", "starcoder", "codellama",
    "-vl-", "vision",
)

# The required defaults from §5. `ravis/clarvis-chat` and `ravis/clarvis-agent`
# are the two whose IDs must stay stable — Clarvis names them in configuration.
DEFAULT_POOLS: tuple[VirtualModelPool, ...] = (
    VirtualModelPool(
        pool_id="ravis/auto",
        label="Auto",
        description="Let RAVIS decide, with no constraint beyond what the request needs",
    ),
    VirtualModelPool(
        pool_id="ravis/chat",
        evidence_role="chat",
        label="Chat",
        description="Conversation: a hosted model first, this machine's own underneath it",
        # Ordinary conversation is the one workload where §9.2's soft
        # preferences point the wrong way. "Prefer local, prefer cheap, prefer
        # already loaded" selects whatever small thing is resident — which is
        # the right answer for a classification call and a poor one for talking
        # to. Preferred rather than required, so a machine with no reachable
        # provider still answers instead of refusing.
        prefer_remote=True,
        # **And a membership, which is what it had none of.** See
        # `CHAT_FAMILIES`: without it this pool admitted every model RAVIS knew
        # and, declaring no preference, ordered them alphabetically.
        curated=CHAT_FAMILIES,
        excluded=NOT_CONVERSATION,
        # The same tuple as the ordering, so the pool prefers its members in the
        # order it declared them rather than by name. `prefer` was empty, and an
        # empty `prefer` is exactly what routes this pool alphabetically.
        prefer=CHAT_FAMILIES,
        # **`prefer_fast` was here and is deliberately gone.** It existed for
        # one reason, in its own words: "without this the pool ranks six hundred
        # remote models on nothing and settles them alphabetically". The curated
        # families rank them on something now, and that job is done.
        #
        # Leaving it would have inverted the new order, because
        # `_preference_terms` consults speed *before* the pool's declared
        # preference: any model RAVIS happened to have timed jumped ahead of the
        # families, and the first pick after curation was still the most
        # expensive model in the catalogue — measured once, and therefore
        # "fast", against a cheap tier nobody had called yet.
    ),
    VirtualModelPool(
        pool_id="ravis/balanced",
        label="Balanced",
        description="A reasonable middle between speed, cost and quality",
        default_tier="mid",
        # And not a code or vision specialist: this pool differs from
        # `ravis/coding` on purpose, and a balanced pool answering with a
        # completion model is miscategorised rather than balanced.
        excluded=GENERAL_PURPOSE_EXCLUSIONS,
        # Mid-sized models, ordered by measured speed at a quarter-second
        # resolution, and the cheaper one wherever that ordering ties. All three
        # words in the description end up meaning something: size is the only
        # available proxy for quality, the bucket is speed, price breaks the tie.
        prefer_fast=True,
        prefer_cheap=True,
        speed_bucket_ms=250.0,
    ),
    VirtualModelPool(
        pool_id="ravis/fast",
        label="Speed",
        description="Lowest latency, accepting weaker answers",
        prefer=("1.7b", "2b", "3b", "mini", "tiny"),
        default_tier="small",
        excluded=GENERAL_PURPOSE_EXCLUSIONS,
        prefer_fast=True,
    ),
    VirtualModelPool(
        pool_id="ravis/performance",
        label="Performance",
        description="Best available answer, accepting latency and cost",
        prefer=("70b", "32b", "30b", "27b", "14b"),
        default_tier="large",
        excluded=GENERAL_PURPOSE_EXCLUSIONS,
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
        max_price_per_million=0.0,
    ),
    # **Free and somebody else's hardware, which is not the same as cheap.**
    #
    # `ravis/cheap` prefers local, and on the machine this runs on "least cost"
    # resolves to a local model — correct for cheap and wrong for the caller
    # this exists for. Unattended work (NERVIS M25) must not load a local model:
    # loading one is exactly how work nobody is watching starts competing for
    # memory with the conversation somebody is having. So the constraint is
    # *free* **and** *remote*, and neither half is redundant.
    #
    # It is also why §9.6.1's background marker is not the answer. That marker
    # means "must be free" and lets a local model satisfy it, which is the one
    # outcome background work cannot afford.
    #
    # **Below `private` in the privacy ladder, and that is a boundary rather
    # than a preference.** A provider's free tier is free because the prompt is
    # worth something — OpenRouter's free variants are trained on. So this pool
    # is an egress path with logging, it may never be reached by a request that
    # asked for `private` or `local`, and a caller that wants free *and* private
    # is asking for something no provider sells.
    #
    # **Rate limits are the normal case here, not a fault.** Free tiers cap per
    # minute and per day, so a 429 from one candidate means "this one is spent,
    # try the next" rather than "the pool failed" — the fallback chain's
    # behaviour, not the breaker's.
    VirtualModelPool(
        pool_id="ravis/free-api",
        label="Free API",
        description=(
            "Costs nothing and runs on somebody else's hardware. Free tiers "
            "are logged and trained on, so this is never a private route"
        ),
        requirements=PoolRequirements(locality="remote"),
        max_price_per_million=0.0,
        # The ceiling is the contract, not a leaning. Without this a machine
        # with no free model routes unattended work to a paid one and says
        # nothing about it.
        refuse_above_ceiling=True,
        # Not `prefer_cheap`: every candidate here already prices at zero, so a
        # cheapness tiebreak would sort on a column where every row is 0.0 and
        # the order would fall to whatever came next. Speed is the useful
        # tiebreak among things that all cost nothing.
        prefer_fast=True,
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
        evidence_role="agent",
        label="Coding",
        description="Optimized for writing and reasoning about code",
        # See `CODE_FAMILIES`. The old value was `("coder", "code", "qwen")` with
        # no membership at all: every model RAVIS knew was a member, and `qwen`
        # promoted a 1.7B that has never written code here above every model
        # that has.
        curated=CODE_FAMILIES,
        prefer=CODE_FAMILIES,
        # Vision and audio variants of a coding family are not coding models,
        # and a batch endpoint is excluded for every curated pool.
        excluded=("-vl-", "vision", "-image", "guard", "moderat"),
    ),
    VirtualModelPool(
        pool_id="ravis/reasoning",
        # See `REASONING_FAMILIES`. The capability requirement below is the hard
        # gate and stays; this narrows what is left to the models actually built
        # to reason, because "supports a reasoning parameter" is a claim the
        # provider makes about an API and not about the model.
        curated=REASONING_FAMILIES,
        prefer=REASONING_FAMILIES,
        label="Reasoning",
        description="Models that reason before answering",
        requirements=PoolRequirements(required=frozenset({Capability.REASONING})),
    ),
    # **Emitting an image, which is not the same request as reading one.**
    # `ravis/vision` asks for a model that can look at a page; this asks for one
    # that can draw. Eleven of OpenRouter's six hundred models declare an image
    # among their output modalities where most declare one among their inputs,
    # so a pool that shared a capability with vision would offer `qwen2.5vl` as
    # an illustrator.
    #
    # No curated family list, for `ravis/vision`'s own reason and more sharply:
    # `gpt-image-1` and `gemini-3-pro-image` share no naming convention worth
    # trusting, and the vendors publish the fact outright — an advertised
    # output modality is evidence where a name pattern is a guess.
    # **Named `draw`, not `image`, and that is not a style choice.** §5.0.1:
    # Clarvis filters catalogues by unanchored substring, so a pool whose id
    # contains `image` disappears from its picker entirely. The invariant is a
    # test in `test_routing.py` whose docstring names this exact pool as the
    # example — written before this pool existed, and it caught it on the first
    # run.
    VirtualModelPool(
        pool_id="ravis/draw",
        label="Image generation",
        description="Models that emit an image",
        requirements=PoolRequirements(required=frozenset({Capability.IMAGE_OUT})),
        # **A router that advertises drawing does not draw.** OpenRouter's
        # `auto` publishes `image` among its output modalities because some
        # model behind it can, and then picks the model itself: asked for a red
        # circle on 7 September 2026 it chose `z-ai/glm-5.2` and answered in
        # words. It is the one candidate here whose capability is a claim about
        # somebody else's routing decision, which is exactly the claim this
        # pool cannot verify or fall back from.
        excluded=("openrouter/auto",),
    ),
    VirtualModelPool(
        pool_id="ravis/vision",
        label="Vision",
        description="Models that accept image input",
        # No curated family list, on purpose — unlike `ravis/coding`'s
        # `CODE_FAMILIES`, there is no name pattern for "built to see" worth
        # trusting yet, and a guessed one would just be `ravis/coding`'s own
        # exclusion list ("-vl-", "vision", "-image") pointed the other way,
        # which is a name heuristic doing a capability check's job. The
        # requirement below is real evidence — advertised or measured per
        # §9.5 — and stays the only gate until a caller's actual usage
        # justifies narrowing further, the same restraint `ravis/agent` shows.
        requirements=PoolRequirements(required=frozenset({Capability.VISION})),
    ),
    VirtualModelPool(
        pool_id="ravis/long-context",
        # A long context is what this pool promises; it says nothing about
        # answering with a code completion model, and the exclusions keep the
        # promise from quietly becoming one.
        excluded=GENERAL_PURPOSE_EXCLUSIONS,
        label="Long Context",
        description="Large context windows",
        requirements=PoolRequirements(minimum_context=131072),
    ),
    VirtualModelPool(
        pool_id="ravis/agent",
        evidence_role="agent",
        label="Agent",
        description=(
            "Tool-capable models for any agentic caller. Tools are REQUIRED, and nothing "
            "about this pool is tied to one product's role."
        ),
        # **The same hard invariant as `clarvis-agent`, and none of its opinions.**
        # That pool is Clarvis's: §5.1 gives it coding and repository reasoning, so
        # it prefers `coder` builds and is admitted on `clarvis-agent` evidence.
        # A caller that simply needs a model which can call tools — NERVIS chat if
        # it ever grows them, a script, a second consumer — was left choosing
        # between borrowing another product's role pool and having no pool at all.
        # Borrowing is the worse of the two: it silently inherits a preference
        # for code models and an evidence role that says nothing about the
        # caller's own workload.
        requirements=_TOOLS_REQUIRED,
        # Instruction-tuned builds follow a tool schema more reliably in
        # practice, and this is a *declared preference on a name* like every
        # other `prefer` in this file — not a measurement. Real ranking is
        # SIRVIS's job, and a route explanation says which of the two decided.
        prefer=("instruct",),
        # **Admitted on the same evidence as `clarvis-agent`, deliberately.**
        # RAVIS asks SIRVIS about one role because a pool's invariant is usually
        # role-specific — but the trial behind this requirement is not:
        # `tool_call_well_formed` counts whether a build emits a well-formed
        # call across eight phrasings of one request, which is a property of the
        # build rather than of Clarvis. Measured on this machine the two
        # `granite-4.0-h-tiny` builds score 24/24 and 3/24 while both advertise
        # `tool_use`, and the failing one is excluded from this pool as well as
        # from Clarvis's. Sharing the evidence is what makes that true; a second
        # role would measure the same thing twice and let the answers drift.
    ),
    VirtualModelPool(
        pool_id="ravis/clarvis-chat",
        evidence_role="clarvis-chat",
        # The same families as `ravis/chat`, for the same reason: §5.1 asks this
        # pool for conversation and instruction following, and `prefer` alone
        # left every other model in the pool as an equal member.
        curated=CHAT_FAMILIES,
        excluded=NOT_CONVERSATION,
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
        # **Kept, and no longer the primary signal.** These matched almost every
        # model in the pool, which left the ranking to the size tiebreak and put
        # the smallest build in front of a conversation. `CHAT_FAMILIES` now
        # ranks first and these decide only what it does not name — a build from
        # a family nobody enumerated, where `instruct` is the only evidence
        # there is.
        prefer=("instruct", "chat"),
    ),
    VirtualModelPool(
        pool_id="ravis/clarvis-agent",
        evidence_role="clarvis-agent",
        label="Clarvis Agent",
        description=(
            "Coding, tool use, repository reasoning, structured calls and long context. "
            "Tools are REQUIRED: §5.1's hard invariant forbids admitting a "
            "non-tool-capable model however well it codes."
        ),
        # **§5.1 names five things and this enforced two.** Its own sentence is
        # "optimized for coding, tool use, repository reasoning, structured
        # calls, long context and reliability", and the pool declared tools and
        # 32K — which made it identical to `ravis/agent`, the general
        # tool-capable pool, member for member. Two pools that select the same
        # 295 models are one pool with two names, and Clarvis names this one
        # because its needs are narrower.
        #
        # Structured output is a requirement rather than a preference for the
        # reason tools are: Clarvis parses what comes back, and a model that
        # cannot be asked for a shape fails the request rather than answering it
        # worse. 128K rather than 32K because "repository reasoning" is the
        # phrase — a file, its imports and a diff do not fit in 32K, and a pool
        # that admits a model which will truncate them is promising something it
        # cannot keep.
        requirements=PoolRequirements(
            required=frozenset({Capability.TOOLS, Capability.STRUCTURED_OUTPUT}),
            minimum_context=131072,
        ),
        # And coding, which is the first word of its description. `ravis/agent`
        # stays the general "anything that can call a tool" pool; this one is
        # for the plugin that reads and edits a repository.
        curated=CODE_FAMILIES,
        prefer=CODE_FAMILIES,
        excluded=("-vl-", "vision", "-image"),
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
