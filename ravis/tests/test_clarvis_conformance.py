"""The Clarvis conformance suite, run as part of the ordinary test suite.

`ravis conformance clarvis` is the command a release gate runs (RAVIS.md §8.9),
but a gate that only runs at release time discovers regressions long after the
change that caused them. So the same suite runs here on every commit.
"""

from __future__ import annotations

from ravis.compatibility.clarvis.conformance import run_suite

# Every requirement the suite is supposed to cover, Stage 2 and Stage 3.
# Asserted by name so that deleting a check fails the tests rather than quietly
# shrinking the gate — a suite that passes because it stopped looking is worse
# than no suite. §8.8 forbids removing the Stage 3 scenarios to make the suite
# green, and this set is what makes that more than a request.
EXPECTED_CHECKS = {
    "/v1/models cached response",
    # The Codex engine's id refused before anything runs (design §3.2: the 24th check).
    "ravis/clarvis-codex is refused as a chat model",
    "chat stream",
    "[DONE] terminator",
    # Reads `ReadStream.malformed_frames`, which had been counted on every
    # stream since M2 and reported nowhere. Every other check compares what
    # survived the proxy against a direct read, so a proxy corrupting a frame
    # both sides skipped identically passed all of them.
    "frames parse",
    "fragmented tool arguments",
    "tool_call_id preserved",
    "multiple tool indexes",
    "reasoning_content preserved",
    "reasoning kept out of content",
    # §15 lists usage and errors among the contract tests and no fixture carried
    # either: a usage frame has an empty `choices` and an upstream error has
    # none, so a reader walking choices saw both as nothing, and a proxy that
    # dropped them looked correct to every other check here.
    "usage frame reaches the client",
    "usage counts are unchanged",
    "a mid-stream upstream error reaches the client",
    "the error is passed through unchanged",
    "text delivered before the failure survives it",
    "a failed stream is not reported as complete",
    "bytes preserved across re-chunking",
    "stream is not buffered",
    "cancellation propagated",
    "suite detects a missing [DONE]",
    # Stage 3 (§8.8): each needs routing, and fallback cannot be static
    # configuration by definition.
    "clarvis-chat and clarvis-agent route separately",
    "clarvis-agent refuses a non-tool model",
    "fallback reaches a compatible model",
    "fallback leaves the stream uncorrupted",
}


async def test_the_suite_passes_against_the_transparent_route() -> None:
    """M2's acceptance criterion, in one line."""
    result = await run_suite()

    assert result.passed, [check.name for check in result.checks if not check.passed]


async def test_every_expected_check_is_present() -> None:
    """Guards the gate against being narrowed by deletion."""
    result = await run_suite()

    assert {check.name for check in result.checks} == EXPECTED_CHECKS


async def test_a_failing_check_fails_the_whole_suite() -> None:
    """A suite that reports PASS with a failed check inside is worse than none."""
    result = await run_suite()
    result.record("deliberately failing check", passed=False)

    assert result.passed is False


def test_the_suite_ignores_this_machine_s_config_directory() -> None:
    """§8.9 makes this suite the release gate for claiming Clarvis
    compatibility, so its verdict must be about the build and not the machine.

    `_app_against` already pointed `PoolMembership` at an unreadable path for
    exactly this reason — and `models.json`, `prices.json`, `policies.json` and
    `observations.json` were still read from `~/.config/ravis`, because each
    store finds its own way there. One env var closes all of them, including the
    next store somebody adds.

    Asserted rather than trusted because the failure it prevents is silent: the
    suite went green for months while reading a real catalogue, and only turned
    red when a pool change altered which of those cached models sorted first.
    """
    from ravis.compatibility.clarvis.conformance import _no_operator_state
    from ravis.credentials import config_directory

    before = config_directory()
    with _no_operator_state():
        during = config_directory()
    after = config_directory()

    assert during != before, "the suite must not read the operator's config"
    assert not during.exists(), "and the path it uses must not exist"
    assert after == before, "and it must put the environment back"


async def test_the_suite_cannot_reach_a_real_provider() -> None:
    """**The gate must not spend the operator's money, or read their keys.**

    It did both. The application registers Anthropic and Google whether or not a
    key exists, so on a machine with keys configured the fixture's catalogue was
    joined by real models, `ravis/clarvis-chat` preferred `claude-haiku` over the
    fixture's own, and the suite posted a fixture-shaped request to Anthropic's
    live API — `400: messages: at least one message is required`. The check
    failed for a reason unrelated to the code it tests, the run took three and a
    half minutes, and `ECOSYSTEM_RUNBOOK.md` §14.5 says no test reaches a live
    model.

    Asserted on the app the suite actually builds, because the failure was
    invisible on a machine with no credentials — which is every machine the
    author of a change is likely to be using.
    """
    from ravis.compatibility.clarvis import fixtures
    from ravis.compatibility.clarvis.conformance import _app_against, _FixtureUpstream

    app = _app_against(_FixtureUpstream(fixtures.PLAIN_CHAT, ("chat-only-model",)))

    assert app.app.state.translating == {}, (
        "a translated provider is reachable from the conformance app, so a pool "
        "can prefer a real hosted model over the fixture's own"
    )
    store = app.app.state.credentials
    assert store.names() == [], "the suite can see credentials stored on this machine"
    for provider in ("anthropic", "google", "openai", "deepseek", "xai"):
        assert not store.resolve(provider), f"{provider}'s key is visible to the suite"
