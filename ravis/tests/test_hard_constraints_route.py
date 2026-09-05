"""A hard constraint refuses at the route, not only in the engine (§15).

**§15: "RAVIS enforces hard constraints before preferences, and explains routes
and rejections."** The engine's half is covered thoroughly — `test_policy.py`
checks every refusal family and `test_pool_locality.py` proves `ravis/local`
never selects a remote model. Both call the engine directly. Nothing drove a
*request* into `/v1/chat/completions` and asserted that a policy stopped it.

That gap matters more than it sounds, because between the request and the engine
sit the pieces where a constraint is most easily lost: `_policy_for` reads the
identity from request state rather than the payload (§9.6.0 — a claimed identity
is never accepted), `effective_policy` merges the operator's posture with what
the request declared, and `policy_refusals` runs a second pass for the addressed
model because a direct address names something no pool contains. Every one of
those is a place where a policy could be computed correctly and applied to
nothing — which is the defect shape this repository has found five times.

**The order is the claim, so the baseline is part of the test.** "Constraints
before preferences" cannot be shown by a refusal alone: a pool that refuses
everything refuses correctly by accident. So the first test proves the same
request succeeds and reaches a remote model when no policy forbids it, and the
rest prove that exact model is then excluded *by name and by reason* once one
does. A refusal is only evidence of ordering when the thing refused would
otherwise have won.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi.testclient import TestClient
from tests.conftest_upstream import RecordingUpstream

from ravis.app import create_app
from ravis.config import Settings

MESSAGES = [{"role": "user", "content": "hi"}]


def _client() -> tuple[TestClient, RecordingUpstream]:
    """The real app, with only the transport under its upstream client swapped.

    `upstream.invalid` is not a loopback address, so everything this upstream
    serves is remote — which is what makes it the right subject for a privacy
    constraint. Nothing above the transport is modified: admission, identity,
    policy and the router are the code that runs in production.
    """
    upstream = RecordingUpstream()
    settings = Settings(
        database_path=":memory:",
        upstream_base_url="http://upstream.invalid",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    client = httpx.AsyncClient(transport=upstream.transport())
    app.app.state.upstream_client = client
    app.app.state.model_registry.use_client(client)
    return TestClient(app), upstream


def _forwarded_model(upstream: RecordingUpstream) -> str:
    """Which model the router actually sent upstream.

    Read off the wire rather than out of the reply, because the reply is the
    fixture's and the request is the router's. It is also the stronger reading
    of "which model won": what was *asked for*, not what a stub chose to echo.
    """
    completions = [r for r in upstream.requests if r.url.path.endswith("/chat/completions")]
    assert completions, "nothing reached the upstream"
    return str(json.loads(completions[-1].content)["model"])


def _ask(client: TestClient, model: str, **metadata: Any) -> httpx.Response:
    body: dict[str, Any] = {"model": model, "messages": MESSAGES, "max_tokens": 8}
    if metadata:
        body["metadata"] = metadata
    return client.post("/v1/chat/completions", json=body)


def test_without_a_policy_the_request_reaches_a_remote_model() -> None:
    """The baseline that makes every refusal below mean something.

    Without this, a suite could pass while the pool was empty, the upstream
    unreachable or the router broken — all of which refuse, none of which is a
    policy being enforced.
    """
    client, upstream = _client()
    with client:
        answered = _ask(client, "ravis/auto")

    assert answered.status_code == 200, answered.text
    assert _forwarded_model(upstream)


def test_a_privacy_constraint_refuses_at_the_route() -> None:
    """The same request, with the one thing changed that policy governs."""
    client, _ = _client()
    with client:
        answered = _ask(client, "ravis/auto", privacy="local_only")

    assert answered.status_code == 422
    error = answered.json()["error"]
    assert error["code"] == "no_route"
    # §4.5's exception: the OpenAI-compatible routes answer in OpenAI's shape,
    # deliberately, because clients parse them. The decision rides alongside.
    assert error["route_decision"]["selected"] is None


def test_the_model_that_would_have_won_is_excluded_by_name_and_reason() -> None:
    """Constraints *before* preferences, which is the part §15 words carefully.

    The model the unconstrained request actually reached is the one preferences
    chose. Under `LOCAL_ONLY` it must appear among the exclusions with a policy
    reason — not merely rank lower, and not vanish silently. A router that
    dropped it without saying why would satisfy "refuses" and fail "explains".
    """
    client, upstream = _client()
    with client:
        _ask(client, "ravis/auto")
        preferred = _forwarded_model(upstream)
        refused = _ask(client, "ravis/auto", privacy="local_only")

    excluded = refused.json()["error"]["route_decision"]["excluded"]
    against = {row["model"]: row["reasons"] for row in excluded}
    assert preferred in against, f"{preferred} won on preferences and is not among the exclusions"
    assert any("LOCAL_ONLY" in reason for reason in against[preferred]), against[preferred]


def test_every_exclusion_carries_a_reason() -> None:
    """§15's second clause. An unexplained rejection is the failure this
    repository has hit in its other form — a value computed and applied nowhere,
    here inverted into a decision applied and never explained."""
    client, _ = _client()
    with client:
        refused = _ask(client, "ravis/auto", privacy="local_only")

    excluded = refused.json()["error"]["route_decision"]["excluded"]
    assert excluded, "a refusal that excluded nothing explains nothing"
    assert all(row["reasons"] for row in excluded)


def test_a_direct_address_does_not_bypass_the_constraint() -> None:
    """The shape somebody reaching around a policy would use.

    `policy_refusals` runs a second pass for the addressed model precisely
    because a direct address names something no pool contains — so without it
    `ravis/<provider>/<model>` would have been the one request policy could not
    see. §5.3 lets an explicit address outrank *inference*; a hard constraint is
    not inference.
    """
    client, upstream = _client()
    with client:
        _ask(client, "ravis/auto")
        named = _forwarded_model(upstream)
        refused = _ask(client, f"ravis/upstream/{named}", privacy="local_only")

    assert refused.status_code == 422
    assert "policy" in refused.json()["error"]["message"].lower()


def test_the_upstream_is_never_contacted_for_a_refused_request() -> None:
    """A refusal that still sends the prompt is not a refusal.

    `LOCAL_ONLY` is a promise about where the text goes, so the assertion has to
    be about the wire and not about the status code. This is the failure that
    would matter most and show least: a 422 arriving after the prompt already
    left the machine.
    """
    client, upstream = _client()
    with client:
        _ask(client, "ravis/auto", privacy="local_only")

    completions = [r for r in upstream.requests if r.url.path.endswith("/chat/completions")]
    assert completions == [], "a policy-refused request still reached the upstream"
