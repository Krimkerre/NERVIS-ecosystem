"""How the model probe reads an answer.

The script itself is a thin shell around one judgement — *did this model accept
a chat request* — and that judgement got it wrong twice before it was right.
Both mistakes are pinned here, because both were the kind that looks correct in
a terminal: one condemned working models, the other exonerated dead ones.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_SOURCE = Path(__file__).resolve().parents[2] / "tools" / "probe_models.py"
_SPEC = importlib.util.spec_from_file_location("probe_models", _SOURCE)
assert _SPEC and _SPEC.loader
probe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(probe)


def _refusal(detail: str) -> dict[str, Any]:
    """RAVIS's shape for a failed chain: a summary, and the reason underneath."""
    return {"error": {
        "message": "No upstream attempt succeeded. Tried: x (unknown).",
        "route": {"attempts": [{"model": "x", "outcome": "unknown", "detail": detail}]},
    }}


def test_a_retirement_is_read_from_the_upstreams_own_words() -> None:
    """RAVIS's summary says only "(unknown)". The sentence that distinguishes a
    retired model from an overloaded one is on the attempt, and reading the
    summary instead marked every dead model as merely unexplained."""
    verdict, why = probe._verdict(_refusal(
        "This model models/gemini-2.5-flash is no longer available to new users."
    ))

    assert verdict == probe.GONE
    assert "no longer available" in why


def test_a_model_that_serves_another_api_is_gone_for_this_one() -> None:
    """"Only supports Interactions API" is not a fault to wait out — it is the
    model saying it does not serve chat completions, ever."""
    verdict, _ = probe._verdict(_refusal("This model only supports Interactions API."))

    assert verdict == probe.GONE


def test_a_transient_failure_is_never_condemned() -> None:
    """A timeout or an overloaded region is a moment, not a fact. Excluding on
    one would quietly shrink the catalogue every time a provider had a bad
    afternoon."""
    for passing in ("503 Service Unavailable", "The model is overloaded",
                    "429 Too Many Requests", "ReadTimeout"):
        verdict, _ = probe._verdict(_refusal(passing))
        assert verdict == probe.BROKEN, passing


def test_answering_with_no_text_is_not_grounds_for_exclusion() -> None:
    """**The mistake that would have cost capability.** An earlier version gave
    each model eight tokens and condemned anything that produced no text —
    which caught `gemini-3.6-flash`, a reasoning build that spends a small
    budget thinking and had already been watched answering at a larger one.

    Excluding a working model is the expensive error: a useless model that
    answers merely ranks badly and is never chosen, while an excluded one is
    capability thrown away that nothing will rediscover.
    """
    verdict, why = probe._verdict(
        {"choices": [{"message": {"content": "", "role": "assistant"}}]}
    )

    assert verdict == probe.QUIET
    assert verdict != probe.GONE
    assert "not excluded" in why


def test_a_plain_answer_is_a_working_model() -> None:
    verdict, why = probe._verdict(
        {"choices": [{"message": {"content": "Ready.", "role": "assistant"}}]}
    )

    assert verdict == probe.WORKS
    assert why == ""


def test_a_reasoning_only_answer_still_counts_as_working() -> None:
    """Some builds return `reasoning_content` and no `content` at a small
    budget. They accepted the request, which is the whole question."""
    verdict, _ = probe._verdict(
        {"choices": [{"message": {"reasoning_content": "thinking", "role": "assistant"}}]}
    )

    assert verdict == probe.WORKS


def test_only_gone_reaches_the_exclude_list() -> None:
    """The record carries four verdicts and exactly one of them condemns."""
    assert probe.GONE != probe.QUIET != probe.BROKEN != probe.WORKS
