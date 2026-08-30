"""Pool resolution: predictable selection, and a hard invariant that holds.

M5's acceptance is two claims — profiles select predictably, and the agent pool
enforces tools — so these tests are mostly about *refusing* to route.
"""

from __future__ import annotations

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
)
from ravis.core.pools import POOLS_BY_ID, direct_target, is_pool_id
from ravis.routing.engine import RoutingEngine

# Comfortably above `ravis/clarvis-agent`'s bar, which these fixtures were
# written to be and stopped being when §5.1's "long context" was finally
# enforced as 128K rather than 32K. Named rather than repeated, so the next
# change to the bar is one edit here instead of four in the file.
CLARVIS_CONTEXT = 131072


def _model(name: str, tools: bool | None = None, context: int | None = None) -> ModelCapabilities:
    """A candidate. `tools=None` means nobody has said — the common real case.

    **A tool-capable model here is also structured-output-capable and long.**
    §5.1 asks `ravis/clarvis-agent` for coding, tool use, repository reasoning,
    structured calls and long context; the pool enforced only the first two,
    which made it identical to `ravis/agent`, and now enforces all of them. A
    helper that declared tools alone would describe a model that pool refuses,
    turning every test below into a test of the refusal rather than of the
    ranking it is about.

    They travel together because a real model that calls tools and cannot be
    asked for a shape is rare, while a fixture that tests ordering wants a
    candidate the pool admits.
    """
    known = ModelCapabilities(model_id=name, context_window=context or 131072)
    if tools is not None:
        state = CapabilityState.SUPPORTED if tools else CapabilityState.UNSUPPORTED
        for capability in (Capability.TOOLS, Capability.STRUCTURED_OUTPUT):
            known.record(
                CapabilityClaim(
                    capability=capability,
                    state=state,
                    provenance=Provenance.CONFIGURED,
                )
            )
    return known


def test_a_plain_model_name_is_passed_through_untouched() -> None:
    """§5.3: an explicit request outranks any inference RAVIS could make."""
    decision = RoutingEngine().select("qwen3-4b", {"qwen3-4b": _model("qwen3-4b")})

    assert decision.selected == "qwen3-4b"


def test_the_agent_pool_refuses_a_model_with_unknown_tool_support() -> None:
    """§5.1's hard invariant, and the reason capability discovery fails closed.

    Nobody has said whether this model calls tools. Admitting it would route an
    agent to a model that may not, and the failure would surface as a broken
    tool call far from this decision.
    """
    candidates = {"mystery": _model("mystery", tools=None, context=CLARVIS_CONTEXT)}

    decision = RoutingEngine().select("ravis/clarvis-agent", candidates)

    assert decision.routed is False
    assert "unavailable" in decision.reason


def test_the_agent_pool_refuses_a_model_that_cannot_use_tools() -> None:
    candidates = {"chatty": _model("chatty", tools=False, context=CLARVIS_CONTEXT)}

    decision = RoutingEngine().select("ravis/clarvis-agent", candidates)

    assert decision.routed is False


def test_the_agent_pool_accepts_a_tool_capable_model() -> None:
    candidates = {"coder": _model("coder", tools=True, context=CLARVIS_CONTEXT)}

    decision = RoutingEngine().select("ravis/clarvis-agent", candidates)

    assert decision.selected == "coder"


def test_a_tool_capable_model_with_too_little_context_is_still_refused() -> None:
    """Both invariants apply; satisfying one is not enough."""
    candidates = {"coder": _model("coder", tools=True, context=8192)}

    decision = RoutingEngine().select("ravis/clarvis-agent", candidates)

    assert decision.routed is False


def test_an_exclusion_names_every_reason_not_just_the_first() -> None:
    """A model failing two ways needs a different fix from one failing once."""
    # Fails every requirement the pool has, which is the point: the excluded
    # entry must name all of them rather than stopping at the first.
    candidates = {"weak": _model("weak", tools=False, context=1024)}

    decision = RoutingEngine().select("ravis/clarvis-agent", candidates)

    # Every requirement it fails, named. Asserted by content rather than by a
    # count, which is what made this test break when the pool gained a third
    # requirement — the subject is "names all of them", not "names two".
    reasons = " · ".join(decision.excluded[0].reasons)
    assert "tools is UNSUPPORTED" in reasons
    assert "structured_output is UNSUPPORTED" in reasons
    assert "context 1024" in reasons


def test_the_chat_pool_does_not_require_tools() -> None:
    """§5.1: tool support is optional for clarvis-chat unless the request brings tools."""
    candidates = {"chatty": _model("chatty", tools=None)}

    decision = RoutingEngine().select("ravis/clarvis-chat", candidates)

    assert decision.selected == "chatty"


def test_selection_is_predictable_across_repeated_calls() -> None:
    """M5's acceptance criterion. A route explanation describing a coin toss is
    not an explanation."""
    candidates = {name: _model(name) for name in ("c", "a", "b")}
    engine = RoutingEngine()

    first = engine.select("ravis/clarvis-chat", candidates)
    second = engine.select("ravis/clarvis-chat", candidates)

    assert first.selected == second.selected == "a"


def test_a_declared_preference_beats_alphabetical_order() -> None:
    candidates = {name: _model(name, tools=True, context=CLARVIS_CONTEXT)
                  for name in ("alpha-model", "qwen-coder")}

    decision = RoutingEngine().select("ravis/clarvis-agent", candidates)

    assert decision.selected == "qwen-coder"


def test_the_explanation_separates_eligibility_from_ranking() -> None:
    """§9.7 separates facts from unknowns; §9.4 forbids an invented weighting.

    This asserted "No benchmark evidence" until M13 gave RAVIS some. The
    sentence was unconditional, so it went on appearing inside decisions that
    were only possible *because* evidence existed — a route explanation
    contradicting the route it explained.

    What survives is the narrower claim, which is the one §9.4 actually needs:
    evidence admits and excludes, and nothing ranks one admitted build above
    another on quality.

    **And then the identical mistake was made one milestone later.** The same
    sentence went on saying "No cost data is available yet" through M15 and
    past it, on every explanation RAVIS produced, while cost was both excluding
    candidates (a pool's per-million ceiling) and ordering them (a cheap pool,
    or a budget band leaning that way). This test asserted that exact string,
    so the check on the sentence kept the sentence wrong.

    The claim is now conditional on what actually applied, which is why this
    asserts the *unpriced* case here and the two applied cases below.

    **And then a third time, to the evidence half of the same sentence.** M16's
    reasoning tiebreak made "evidence decides eligibility rather than order"
    false — a measured reasoning share now orders candidates when the request
    caps its output — and this test asserted that string too, so once again the
    check on the sentence was holding the sentence wrong. What is asserted now
    is the claim that survived rather than the wording that did not: nothing
    ranks an admitted build above another *on quality*. The pool here sets no
    output ceiling, so this is the did-not-order case, and
    `test_reasoning_tiebreak.py` asserts the other one.
    """
    candidates = {"a": _model("a")}

    decision = RoutingEngine().select("ravis/clarvis-chat", candidates)

    assert "Evidence admits and excludes builds" in decision.reason
    assert "did not order these" in decision.reason
    assert "rather than on quality" in decision.reason
    assert "nothing ranks one admitted build above another" in decision.reason
    assert "did not order this pool" in decision.reason, (
        "clarvis-chat has no price ceiling and does not prefer cheap"
    )


def test_a_cheap_pool_says_that_prices_ordered_it() -> None:
    """The other side of the sentence above: when cost *does* apply, say so.

    `ravis/cheap` ranks on price by design, so an explanation claiming no cost
    data was available was contradicting the pool it was explaining -- the same
    shape as the evidence sentence before it.
    """
    decision = RoutingEngine().select(
        "ravis/cheap", {"a": _model("a"), "b": _model("b")}
    )

    if decision.routed:
        assert "Published prices" in decision.reason
        assert "did not order this pool" not in decision.reason


def test_an_empty_catalogue_is_a_no_route_with_a_distinct_reason() -> None:
    """"Nothing is available" and "nothing qualifies" need different fixes."""
    decision = RoutingEngine().select("ravis/clarvis-agent", {})

    assert decision.routed is False
    assert "no models are available" in decision.reason


def test_a_direct_address_bypasses_selection() -> None:
    candidates = {"qwen3-4b": _model("qwen3-4b")}

    decision = RoutingEngine().select("ravis/lmstudio/qwen3-4b", candidates)

    assert decision.selected == "qwen3-4b"


def test_a_direct_address_to_a_missing_model_is_a_no_route() -> None:
    """Better than forwarding it to fail confusingly at the provider."""
    decision = RoutingEngine().select("ravis/lmstudio/absent", {"other": _model("other")})

    assert decision.routed is False


def test_pool_ids_are_recognised_and_direct_addresses_are_not_pools() -> None:
    assert is_pool_id("ravis/clarvis-agent") is True
    assert direct_target("ravis/clarvis-agent") is None
    assert direct_target("ravis/lmstudio/qwen3-4b") == "qwen3-4b"


def test_no_pool_id_contains_a_substring_clarvis_filters_out() -> None:
    """§5.0.1: Clarvis's catalogue filter is an unanchored substring match, so a
    pool named `ravis/image` would silently vanish from its picker."""
    forbidden = ("embed", "whisper", "transcribe", "image", "moderation", "audio",
                 "realtime", "rerank", "guard")
    offenders = [
        pool_id for pool_id in POOLS_BY_ID
        if any(word in pool_id for word in forbidden)
    ]

    assert offenders == []


# ── The size tiebreak (§9.2's "prefer fast" and "prefer cheap") ──────────────
#
# Every test below concerns the *last* thing consulted when ranking. It only
# ever separates candidates a pool already considers identical, and the reason
# it exists is a concrete regression: alphabetical order put a 14B ahead of a
# 7B that scored the same on every accuracy measure at three times the rate,
# purely because "1" sorts before "7".


def _tool_model(name: str) -> ModelCapabilities:
    """A candidate the agent pool will admit, so ranking is what is under test."""
    return _model(name, tools=True, context=CLARVIS_CONTEXT)


def test_size_breaks_a_tie_between_equally_preferred_candidates() -> None:
    """The regression, in one test. Both match 'coder'; the smaller one wins."""
    candidates = {
        name: _tool_model(name)
        for name in ("qwen2.5-coder-14b-instruct-mlx", "qwen2.5-coder-7b-instruct")
    }

    decision = RoutingEngine().select("ravis/clarvis-agent", candidates)

    assert decision.selected == "qwen2.5-coder-7b-instruct"
    assert decision.fallbacks == ["qwen2.5-coder-14b-instruct-mlx"]


def test_the_explanation_names_the_tiebreak_and_calls_it_weak() -> None:
    """§9.7: a weak reason must not be allowed to read as a strong one.

    A tiebreak that decides routes invisibly is policy nobody agreed to. This is
    the sentence that lets a reader disagree with it.
    """
    candidates = {
        name: _tool_model(name)
        for name in ("qwen2.5-coder-14b-instruct-mlx", "qwen2.5-coder-7b-instruct")
    }

    reason = RoutingEngine().select("ravis/clarvis-agent", candidates).reason

    assert "smallest at 7B" in reason
    assert "not on quality" in reason


def test_declared_preference_outranks_size() -> None:
    """Size is a tiebreak, never a score.

    A tiny model that does not match the pool's intent must not beat a large one
    that does — otherwise `ravis/clarvis-agent` would drift towards whatever is
    smallest rather than whatever codes.
    """
    candidates = {
        name: _tool_model(name) for name in ("qwen2.5-coder-14b-instruct-mlx", "tinyllama-1b")
    }

    decision = RoutingEngine().select("ravis/clarvis-agent", candidates)

    assert decision.selected == "qwen2.5-coder-14b-instruct-mlx"


def test_an_unparseable_size_sorts_last_rather_than_smallest() -> None:
    """A name that carries no size is an unknown, not a zero (runbook §14.4).

    `phi-4-mini-instruct` is genuinely small and says so in words. Treating the
    absence as "smallest" would let any model with an unconventional name win
    every tie on a size nobody measured.
    """
    candidates = {
        name: _tool_model(name) for name in ("phi-4-mini-instruct", "qwen-7b-instruct")
    }

    decision = RoutingEngine().select("ravis/clarvis-agent", candidates)

    assert decision.selected == "qwen-7b-instruct"


def test_a_mixture_of_experts_is_read_by_its_active_parameters() -> None:
    """`qwen3-30b-a3b` answers at 3B speed, not 30B speed, and the tiebreak is
    about how fast a thing answers."""
    candidates = {name: _tool_model(name) for name in ("qwen3-30b-a3b", "qwen3-8b")}

    decision = RoutingEngine().select("ravis/clarvis-agent", candidates)

    assert decision.selected == "qwen3-30b-a3b"


def test_the_chat_pool_prefers_an_instruction_tuned_build() -> None:
    """§5.1 names instruction following; until M12 the pool declared nothing.

    With no preference the selection was purely alphabetical, which against a
    real catalogue picked the slowest installed model by accident.
    """
    candidates = {name: _model(name) for name in ("aardvark-2b", "meta-llama-8b-instruct")}

    decision = RoutingEngine().select("ravis/clarvis-chat", candidates)

    assert decision.selected == "meta-llama-8b-instruct"


def test_the_pool_listing_agrees_with_what_the_router_would_pick() -> None:
    """`/api/v1/pools` and the router must not order members differently.

    They are two code paths over the same intent, and a dashboard that lists a
    pool's members in one order while the router picks from another is the kind
    of disagreement nobody notices until it is being debugged under pressure.
    """
    candidates = {
        name: _tool_model(name)
        for name in ("qwen2.5-coder-14b-instruct-mlx", "qwen2.5-coder-7b-instruct", "llama-8b")
    }
    pool = POOLS_BY_ID["ravis/clarvis-agent"]

    # `default_membership` over `eligible`, which is what the management API's
    # `_members` computes and therefore what the dashboard shows. Comparing the
    # raw eligible set stopped being the listing the moment a pool could curate:
    # `llama-8b` satisfies every requirement and is not one of the families
    # `ravis/clarvis-agent` is for, so the router does not choose from it and
    # the screen does not list it.
    listed = list(pool.default_membership(pool.eligible(candidates)))
    decision = RoutingEngine().select("ravis/clarvis-agent", candidates)

    assert listed[0] == decision.selected
    assert listed[1:3] == decision.fallbacks


# ── A name in RAVIS's own namespace that RAVIS does not recognise ────────────


def test_an_unrecognised_ravis_name_is_refused_rather_than_forwarded() -> None:
    """Found by fat-fingering a pool ID against a running gateway.

    `ravis/talk` is not a pool, and has no second slash so it is not a direct
    address either. It used to fall through to the plain-model-name path and get
    forwarded verbatim — and LM Studio answered with whatever happened to be
    loaded. The request *succeeded*, with no pool, no capability filtering, no
    tool invariant and no fallback, and looked entirely fine.

    §5.3's "an explicit request outranks inference" does not cover this, because
    `ravis/` is RAVIS's own namespace: no upstream serves a model called
    `ravis/talk`, so a name in it that RAVIS does not know is a typo.

    **The example used to be `ravis/chat`, until that became a real pool.** A
    test whose invalid input turns valid stops testing anything and starts
    failing, which is the good failure mode — the alternative is one that keeps
    passing against a name nobody checks.
    """
    decision = RoutingEngine().select("ravis/talk", {"qwen3-4b": _model("qwen3-4b")})

    assert decision.routed is False
    assert "ravis/clarvis-chat" in decision.reason


def test_the_refusal_suggests_the_pool_that_was_probably_meant() -> None:
    """Containment before edit distance — the mistake is a dropped qualifier.

    Edit distance alone proposed `ravis/cheap` for `chat` and `ravis/fast` for
    `agent`, because the shared `ravis/` prefix dominates the ratio. A confident
    wrong suggestion is worse than none: it sends the reader to fix something
    that was never broken.
    """
    engine = RoutingEngine()

    # `ravis/agent` used to be the example here — a dropped qualifier that meant
    # `clarvis-agent`. It is a real pool now (tool-capable models for any
    # caller), so the containment case needs a name that is still a near-miss.
    # `ravis/clarvis` contains two pools, and the first in stable order wins.
    assert "ravis/clarvis-chat" in engine.select("ravis/clarvis", {}).reason
    # And the new pool is itself suggestible, which is the same mechanism
    # working for it rather than against it.
    assert "ravis/agent" in engine.select("ravis/agents", {}).reason
    # A genuine misspelling has no containment match, so edit distance still
    # earns its place — at a raised cutoff.
    assert "ravis/clarvis-chat" in engine.select("ravis/clarvis-cat", {}).reason


def test_a_name_close_to_nothing_lists_the_pools_instead_of_guessing() -> None:
    reason = RoutingEngine().select("ravis/nonsense", {}).reason

    assert "did you mean" not in reason
    assert "ravis/clarvis-agent" in reason and "ravis/auto" in reason


def test_a_direct_address_is_still_a_direct_address() -> None:
    """The refusal must not swallow `ravis/<provider>/<model>`, which is §5's
    documented way to bypass selection."""
    candidates = {"qwen3-4b": _model("qwen3-4b")}

    decision = RoutingEngine().select("ravis/lmstudio/qwen3-4b", candidates)

    assert decision.selected == "qwen3-4b"


def test_a_plain_model_name_is_still_forwarded_untouched() -> None:
    """The refusal is scoped to RAVIS's namespace and must not widen (§5.3)."""
    decision = RoutingEngine().select("some-vendor/some-model", {})

    assert decision.selected == "some-vendor/some-model"


def test_size_does_not_rank_a_pool_that_declared_no_preference() -> None:
    """The regression a reader spotted: one model winning nearly every pool.

    The size tiebreak's justification is "among candidates a pool already
    considers identical, the smaller is cheaper" — and that assumes the pool
    expressed something for them to be equal *on*. A pool with an empty `prefer`
    considers everything equal, so size stopped being the last word and became
    the whole ranking: eight of thirteen pools resolved to the smallest
    installed model, a 1.7B nobody had benchmarked, and `ravis/balanced`
    selecting the tiniest thing available is self-evidently not balanced.

    Alphabetical order is meaningless here, and meaningless is the honest state
    until M13 — it is at least not systematically biased towards whatever
    happens to be smallest.
    """
    candidates = {name: _model(name) for name in ("zeta-70b", "alpha-1b")}

    decision = RoutingEngine().select("ravis/auto", candidates)

    assert decision.selected == "alpha-1b"  # alphabetical, not smallest-by-size


def test_size_still_breaks_ties_where_a_preference_was_declared() -> None:
    """The case the tiebreak was introduced for must keep working."""
    candidates = {
        name: _tool_model(name)
        for name in ("qwen2.5-coder-14b-instruct-mlx", "qwen2.5-coder-7b-instruct")
    }

    decision = RoutingEngine().select("ravis/clarvis-agent", candidates)

    assert decision.selected == "qwen2.5-coder-7b-instruct"


def test_the_agent_pool_is_not_clarvis_agent_wearing_a_shorter_name() -> None:
    """Two pools require tools, and the difference is whose opinions they carry.

    `clarvis-agent` is §5.1's: coding and repository reasoning, so it prefers
    `coder` builds and is admitted on `clarvis-agent` evidence. A caller that
    simply needs a model which can call tools was choosing between borrowing
    that role pool — inheriting a preference for code models it never asked for
    — and having no pool at all.
    """
    from ravis.core.pools import POOLS_BY_ID

    agent = POOLS_BY_ID["ravis/agent"]
    clarvis = POOLS_BY_ID["ravis/clarvis-agent"]

    # **Clarvis's is stricter, and that is the distinction.** These two were
    # identical — same requirements, same 295 members — which made one of them a
    # second name for the other. §5.1 asks this pool for "coding, tool use,
    # repository reasoning, structured calls, long context and reliability", so
    # it requires all of that; `ravis/agent` remains the pool for a caller who
    # simply needs a model that can call a tool.
    assert Capability.TOOLS in agent.requirements.required, "§5.1's hard invariant, both"
    assert Capability.TOOLS in clarvis.requirements.required
    assert clarvis.requirements.required > agent.requirements.required, "strictly more"
    assert clarvis.requirements.minimum_context > agent.requirements.minimum_context
    assert "coder" in clarvis.prefer and "coder" not in agent.prefer
    assert "role" not in agent.description.lower() or "one product" in agent.description


# ── Membership derived from role evidence, not from a hand-written list ──────
#
# The declared families in `pools.py` are a judgement written against a
# catalogue that turns over every few weeks. They are a stand-in for evidence,
# and a stand-in has to lose to the thing it stands in for.


def _measured(**verdicts: str) -> dict[str, dict[str, str]]:
    """Role verdicts as `EvidenceStore.roles_measured` produces them."""
    return {model: {"agent": state} for model, state in verdicts.items()}


def test_a_measured_build_joins_a_pool_no_family_named() -> None:
    """`some-obscure-7b` matches no fragment in `CODE_FAMILIES` and would never
    have been a member. It was benchmarked for the role and passed, which is a
    stronger claim than any list of names."""
    candidates = {name: _tool_model(name)
                  for name in ("some-obscure-7b", "qwen3-coder-30b")}

    decision = RoutingEngine().select(
        "ravis/coding", candidates,
        role_evidence=_measured(**{"some-obscure-7b": "SUPPORTED"}),
    )

    assert "some-obscure-7b" in [decision.selected, *decision.fallbacks]


def test_a_build_measured_and_failing_is_excluded_though_a_family_names_it() -> None:
    """`qwen3-coder-30b` leads the coding families. Measured for the role and
    failing, it is out — a hand-written list cannot outvote a trial that ran,
    and the exclusion says which."""
    candidates = {name: _tool_model(name)
                  for name in ("qwen3-coder-30b", "codestral-22b")}

    decision = RoutingEngine().select(
        "ravis/coding", candidates,
        role_evidence=_measured(**{"qwen3-coder-30b": "UNSUPPORTED"}),
    )

    assert decision.selected == "codestral-22b"
    refused = [e for e in decision.excluded if e.model == "qwen3-coder-30b"]
    assert refused, "the failing build is reported, not silently dropped"
    assert any("measured for agent and did not qualify" in reason
               for reason in refused[0].reasons)


def test_an_unmeasured_build_still_falls_back_to_the_families() -> None:
    """Absence of a measurement is not a failed one. §9.1 fails closed on what is
    not *established*, and treating silence as refusal would empty every pool on
    a machine that has never run a benchmark."""
    candidates = {name: _tool_model(name)
                  for name in ("qwen3-coder-30b", "some-obscure-7b")}

    decision = RoutingEngine().select("ravis/coding", candidates, role_evidence={})

    assert decision.selected == "qwen3-coder-30b"
    assert "some-obscure-7b" not in [decision.selected, *decision.fallbacks]


def test_a_role_measured_on_another_axis_does_not_count_as_a_verdict() -> None:
    """A throughput run under a role establishes nothing about fitness for it.
    `UNKNOWN` falls through to the families exactly as absence does."""
    candidates = {name: _tool_model(name)
                  for name in ("qwen3-coder-30b", "some-obscure-7b")}

    decision = RoutingEngine().select(
        "ravis/coding", candidates,
        role_evidence=_measured(**{"some-obscure-7b": "UNKNOWN"}),
    )

    assert decision.selected == "qwen3-coder-30b"
    assert "some-obscure-7b" not in [decision.selected, *decision.fallbacks]


def test_evidence_for_another_role_does_not_admit_this_pool() -> None:
    """A build measured for `chat` is not thereby a coding model. The pool names
    the role whose evidence is about *its* work, and evidence about a different
    question is not an answer to this one."""
    candidates = {name: _tool_model(name)
                  for name in ("qwen3-coder-30b", "some-obscure-7b")}

    decision = RoutingEngine().select(
        "ravis/coding", candidates,
        role_evidence={"some-obscure-7b": {"chat": "SUPPORTED"}},
    )

    assert "some-obscure-7b" not in [decision.selected, *decision.fallbacks]


def test_the_curated_order_outranks_a_general_preference() -> None:
    """Reported from use: *"hello again"* was answered *"Read my lips: short."*

    A 7B had been handed a long system prompt and paraphrased its own brevity
    instruction back. It won because `ravis/clarvis-chat` declared
    `prefer=("instruct", "chat")` — fragments matching nearly every model in the
    pool — so seven candidates tied and the size tiebreak took the smallest.

    `CHAT_FAMILIES` was already written for this decision, cheap frontier first
    and expensive last, and its own comment claimed `preference_rank` enforced
    that ordering. It did not: the method read `prefer` alone, so the ordering
    ranked nothing at all.
    """
    pool = POOLS_BY_ID["ravis/clarvis-chat"]

    assert pool.preference_rank("anthropic/claude-haiku-4.5") < pool.preference_rank(
        "qwen/qwen-2.5-7b-instruct"
    )
    # And the expensive tier stays late, which is the whole reason the list is
    # ordered rather than merely populated.
    assert pool.preference_rank("anthropic/claude-haiku-4.5") < pool.preference_rank(
        "claude-fable-5"
    )


def test_a_general_preference_still_decides_what_the_list_does_not_name() -> None:
    """The falsifier for putting `curated` first. §5.1 asks this pool for
    instruction following, and against builds nobody enumerated `instruct` is
    the only signal there is — so it must still rank them, just not ahead of a
    family the list names explicitly."""
    pool = POOLS_BY_ID["ravis/clarvis-chat"]

    assert pool.preference_rank("meta-llama-8b-instruct") < pool.preference_rank("aardvark-2b")
    # Both lose to anything the curated list actually names.
    assert pool.preference_rank("anthropic/claude-haiku-4.5") < pool.preference_rank(
        "meta-llama-8b-instruct"
    )


def _priced(name: str, price: float | None) -> ModelCapabilities:
    """A candidate with a published price, or none at all."""
    return ModelCapabilities(model_id=name, price_per_million=price)


def test_a_hosted_tiebreak_is_on_price_not_parameter_count() -> None:
    """Reported: *"model size is only of importance when using local models —
    price/performance is more important when we have api at our disposal."*

    Exactly right, and the reason string admitted it: the tiebreak called
    itself "a tiebreak on cost to run" while measuring parameter count for a
    model whose parameters are somebody else's. A hosted 7B is not cheaper than
    a hosted frontier build by virtue of being small; the bill is per token.
    """
    candidates = {
        "vendor/small-7b": _priced("vendor/small-7b", 9.0),
        "vendor/large-200b": _priced("vendor/large-200b", 1.0),
    }
    remote = frozenset(candidates)

    decision = RoutingEngine().select(
        "ravis/clarvis-chat", candidates, remote_models=remote
    )

    assert decision.selected == "vendor/large-200b"


def test_a_local_tiebreak_is_still_on_size() -> None:
    """The falsifier. On this machine the parameters *are* the cost — memory
    and a load — so nothing about the hosted case should reach a local one."""
    candidates = {
        "local-small-7b": _priced("local-small-7b", None),
        "local-large-70b": _priced("local-large-70b", None),
    }

    decision = RoutingEngine().select("ravis/clarvis-chat", candidates)

    assert decision.selected == "local-small-7b"


def test_an_unpriced_hosted_model_does_not_win_by_saying_nothing() -> None:
    """`None` means nobody published a figure, not that it is free — the same
    rule the cheap pool's ceiling already applies. Otherwise every catalogue
    that publishes least wins most."""
    candidates = {
        "vendor/priced": _priced("vendor/priced", 5.0),
        "vendor/silent": _priced("vendor/silent", None),
    }
    remote = frozenset(candidates)

    decision = RoutingEngine().select(
        "ravis/clarvis-chat", candidates, remote_models=remote
    )

    assert decision.selected == "vendor/priced"
