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
    "chat stream",
    "[DONE] terminator",
    "fragmented tool arguments",
    "tool_call_id preserved",
    "multiple tool indexes",
    "reasoning_content preserved",
    "reasoning kept out of content",
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
