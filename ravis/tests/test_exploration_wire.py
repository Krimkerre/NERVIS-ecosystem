"""The switch reaching the router — the one link a unit test cannot see.

`test_exploration.py` proves the engine explores correctly when told to, and
`_exploration` decides whether to tell it. Between them sits a single keyword
argument on one `engine.select(...)` call, and that is exactly the shape of the
bug found in `test_price_refresh.py` earlier the same day: a correct function
with nothing calling it, and a suite that noticed nothing because every test
called the function directly.

So this drives a real request through the real application and asserts the
answer changes. The dice are the only thing replaced -- `random.random` is where
the impurity deliberately lives, so pinning it is pinning the one value that
would otherwise make this flaky.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.test_fallback import ScriptedUpstream, _app_with

# Two models `ravis/chat` will take, described identically so nothing but the
# ranking separates them.
PAIR: dict[str, dict[str, str]] = {
    "vendor/alpha": {"context_window": "32000"},
    "vendor/beta": {"context_window": "32000"},
}


def _answered(monkeypatch: pytest.MonkeyPatch, roll: float, **asked: Any) -> str:
    """Which model actually served the request, read from the upstream itself."""
    monkeypatch.setattr("ravis.api.openai.chat.random.random", lambda: roll)
    upstream = ScriptedUpstream(PAIR)
    with _app_with(upstream) as client:
        reply = client.post(
            "/v1/chat/completions",
            json={"model": "ravis/chat", "messages": [{"role": "user", "content": "hi"}],
                  **asked},
        )
        assert reply.status_code == 200, reply.text
    return upstream.served[-1]


def test_the_switch_actually_changes_which_model_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The wire.** A losing roll and a winning roll on the same request body
    must reach different models, or nothing between the switch and the router is
    connected."""
    ordinary = _answered(monkeypatch, 0.99, explore=True)
    exploring = _answered(monkeypatch, 0.0, explore=True)

    assert ordinary != exploring, (
        "an exploring request served the same model as an ordinary one — the "
        "switch is not reaching the routing engine"
    )


def test_a_request_that_did_not_ask_never_explores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even on a roll of zero. Opt-in has to survive the unluckiest dice throw,
    because the failure mode is silent: a person who never switched this on
    would simply get worse answers sometimes and never know why."""
    rolled_zero = _answered(monkeypatch, 0.0)
    ordinary = _answered(monkeypatch, 0.99, explore=True)

    assert rolled_zero == ordinary
