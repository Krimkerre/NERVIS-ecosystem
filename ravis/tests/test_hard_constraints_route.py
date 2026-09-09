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


# ── A denied provider, reached anyway, because two halves disagreed ──────────
#
# Found by an external audit on 9 September 2026 and reproduced here before the
# fix. The application resolves a model to its provider in two places and they
# did not agree:
#
#   - **Execution** reads `request.state.translated_owners`, the map built when
#     a translated provider's catalogue is folded into the candidates. A bare
#     `claude-*` id resolves to `anthropic` there.
#   - **Policy** used `_provider_of`, which resolved an explicit
#     `ravis/anthropic/<model>` address and otherwise consulted only the
#     transparent upstreams — so the same bare id resolved to `default`.
#
# So a deny-list naming `anthropic` was evaluated against `default`, matched
# nothing, and the request went to Anthropic. The same resolver feeds provider
# health and session attribution, which is why the audit also saw a successful
# Anthropic call credited to `default`.


class _FakeTranslated:
    """A translated provider with a catalogue, a credential and a record."""

    name = "anthropic"
    has_credential = True

    def __init__(self) -> None:
        self.completed: list[str] = []

    async def models(self) -> list[str]:
        return ["claude-audit-1"]

    async def capabilities(self, model: str) -> Any:
        from ravis.core.capabilities import ModelCapabilities

        known = ModelCapabilities(model_id=model)
        known.context_window = 200_000
        return known

    async def complete(self, request: Any) -> Any:
        from ravis.core.responses import NormalizedResponse

        self.completed.append(request.requested_model)
        return NormalizedResponse(
            text="hello", provider=self.name, model=request.requested_model
        )


def _client_with_translated(denied: list[str] | None = None,
                            ) -> tuple[TestClient, _FakeTranslated]:
    """The app with one translated provider, and optionally a policy denying it.

    **The deny comes from the operator's policy file, because that is the only
    place it can come from.** A request may tighten privacy and nothing else —
    `effective_policy` takes `denied_providers` from the identity's configured
    policy alone, so a client cannot grant or revoke provider access by asking.
    This test client presents no credential, so its policy is the one configured
    for `anonymous`.
    """
    import json as _json
    import tempfile
    from pathlib import Path

    from ravis.policy import load_policies

    client, _ = _client()
    adapter = _FakeTranslated()
    client.app.app.state.translating = {"anthropic": adapter}  # type: ignore[attr-defined]
    if denied is not None:
        path = Path(tempfile.mkdtemp()) / "policies.json"
        path.write_text(_json.dumps(
            {"applications": {"anonymous": {"denied_providers": denied}}}
        ))
        client.app.app.state.policies = load_policies(path)  # type: ignore[attr-defined]
    return client, adapter


def test_a_translated_model_reaches_its_provider_without_a_policy() -> None:
    """The baseline. A refusal below is only evidence if this succeeds first."""
    client, adapter = _client_with_translated()
    with client:
        answered = _ask(client, "claude-audit-1")

    assert answered.status_code == 200, answered.text
    assert adapter.completed == ["claude-audit-1"], "the fake provider was not reached"


def test_a_denied_provider_is_refused_for_a_bare_model_id() -> None:
    """**The bypass itself.** `ravis/anthropic/claude-audit-1` was refused and
    the bare `claude-audit-1` was not, though both execute against the same
    adapter — because only the first form was one the policy's resolver could
    read. A deny-list that a client evades by dropping four characters from the
    model name is not a deny-list.
    """
    client, adapter = _client_with_translated(denied=["anthropic"])
    with client:
        answered = _ask(client, "claude-audit-1")

    assert answered.status_code == 422, answered.text
    assert adapter.completed == [], "the denied provider was called anyway"
    assert "denied" in json.dumps(answered.json())


def test_the_explicit_address_stays_refused() -> None:
    """The half that already worked, kept so a fix cannot trade one for the other."""
    client, adapter = _client_with_translated(denied=["anthropic"])
    with client:
        answered = _ask(client, "ravis/anthropic/claude-audit-1")

    assert answered.status_code == 422, answered.text
    assert adapter.completed == []
