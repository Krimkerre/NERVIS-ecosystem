"""`reasoning_effort: "none"` asks for the opposite, and used to demand it.

Any reasoning budget made the router require `Capability.REASONING`. That is
right for "high" — a request that wants deliberation needs a model that can
deliberate — and exactly backwards for "none", where the request is declining
the capability it was being narrowed to.

Found chasing chat slowness on 9 September 2026. `deepseek-v4-flash` thinks by
default, and chat's long prompt made it think hard: 1,215 reasoning frames over
ten seconds before the word "hi". Chat began sending `"none"` — and was routed
away from DeepSeek to a model selected for reasoning, which is the one thing the
request had asked not to happen.

The value still travels to the provider, which is the point: a thinking model
that receives it turns thinking off. It simply stops being a hard constraint on
who is allowed to answer.
"""

from __future__ import annotations

from ravis.core.capabilities import Capability
from ravis.core.requests import NormalizedRequest
from ravis.routing.requirements import analyse


def _needs(effort: str | None) -> bool:
    asked = analyse(NormalizedRequest(
        messages=[{"role": "user", "content": "hi"}], reasoning_effort=effort,
    ))
    return Capability.REASONING in asked.required


def test_asking_for_no_reasoning_does_not_require_a_reasoning_model() -> None:
    assert not _needs("none"), (
        "a request declining to reason was narrowed to models chosen for reasoning"
    )


def test_asking_for_reasoning_still_requires_it() -> None:
    """The guard the fix must not have loosened: "high" is a real requirement,
    and a model that cannot reason cannot serve it."""
    for effort in ("minimal", "low", "medium", "high", "max"):
        assert _needs(effort), f"{effort!r} stopped requiring a reasoning-capable model"


def test_naming_no_budget_at_all_requires_nothing() -> None:
    """Unchanged, and stated so the three states stay distinct: absent is not
    the same as "none", which is not the same as "high"."""
    assert not _needs(None)
    assert not _needs("")
