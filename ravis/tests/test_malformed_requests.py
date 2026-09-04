"""Malformed input answers 400, and does not crash or blame an upstream.

Found by sending deliberately bad payloads at a running RAVIS rather than by
reading, which is what made the shape of it clear. Six distinct 5xx answers came
back, in two families:

  {"model":"ravis/chat","messages":"hello"}   →  500  Internal Server Error
  {"messages":[{"role":"user","content":"x"}]} →  502  "No upstream attempt
                                                       succeeded. Tried:
                                                       (connection_failure)"

The first is an unhandled exception: `_inspect` checked `messages` *only when it
was already a list*, so any other type walked past the guard and crashed further
in. The second is worse than a wrong number — a request with no `model` was
routed as the empty string, which reached a real local runtime, failed to
connect, and was reported as the upstream's fault. §4.5 exempts `/v1` from the
MEP envelope in favour of OpenAI-compatible errors; it does not exempt it from
telling the truth about whose fault a failure was.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from ravis.app import create_app
from ravis.config import Settings


@pytest.fixture()
def client() -> Any:
    app = create_app(Settings(database_path=":memory:", _env_file=None))  # type: ignore[call-arg]
    with TestClient(app) as ready:
        yield ready


def _post(client: Any, payload: str) -> Any:
    return client.post(
        "/v1/chat/completions",
        headers={"Content-Type": "application/json"},
        content=payload,
    )


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("messages as a string", '{"model":"ravis/chat","messages":"hello"}'),
        ("messages as an object", '{"model":"ravis/chat","messages":{"role":"user"}}'),
        ("a message that is not an object", '{"model":"ravis/chat","messages":[42]}'),
        ("model is a number", '{"model":99,"messages":[{"role":"user","content":"x"}]}'),
        ("no model at all", '{"messages":[{"role":"user","content":"x"}]}'),
        ("model is empty", '{"model":"","messages":[{"role":"user","content":"x"}]}'),
        ("nothing at all", "{}"),
    ],
)
def test_a_malformed_request_is_refused_as_the_clients_fault(
    client: Any, name: str, payload: str
) -> None:
    """400, and never a 5xx — the caller broke this, not the service."""
    refused = _post(client, payload)

    assert refused.status_code == 400, name
    body = refused.json()
    assert body["error"]["type"] == "invalid_request_error", name
    # OpenAI-compatible shape, because §4.5 exempts `/v1` in favour of the shape
    # clients already parse. A bare "Internal Server Error" is neither.
    assert body["error"]["message"], name


def test_the_refusal_names_the_field(client: Any) -> None:
    """§4.5: "validation errors identify the rejected fields".

    "Request is invalid" sends somebody to read their whole payload; naming
    `messages` sends them to the line.
    """
    body = _post(client, '{"model":"ravis/chat","messages":"hello"}').json()

    assert "messages" in body["error"]["message"]


def test_no_upstream_is_contacted_for_a_request_that_cannot_route(client: Any) -> None:
    """The half that is not about status codes.

    A missing `model` was routed as the empty string, which meant real
    connection attempts against a local runtime — recorded as
    `connection_failure`, which is a fact about the runtime and was caused
    entirely by a request that should never have left. Refusing early means the
    attempt never happens.
    """
    refused = _post(client, '{"messages":[{"role":"user","content":"x"}]}')

    assert refused.status_code == 400
    assert "upstream" not in refused.json()["error"]["message"].lower()


def test_a_well_formed_request_still_routes(client: Any) -> None:
    """The falsifier. Validation that refused everything would pass every
    assertion above and take the service with it."""
    answered = _post(
        client, '{"model":"ravis/chat","messages":[{"role":"user","content":"x"}]}'
    )

    assert answered.status_code != 400


def test_an_upstream_400_is_the_clients_fault_not_the_upstreams() -> None:
    """The second family, and the subtler one.

    `max_tokens: "lots"` is forwarded — correctly, since a transparent proxy does
    not own the upstream's schema — and Anthropic answers *400: max_tokens: Input
    should be a valid integer*. RAVIS then reported that as `502 upstream_error`
    with outcome `unknown`: the caller is told the upstream failed, when the
    upstream worked perfectly and said so.

    `_class_for` already carried the right argument for the neighbouring case —
    "without this distinction one client sending an untranslatable body would
    walk the provider's circuit breaker toward open" — and applied it only to a
    `TranslationError`. An upstream that answers 4xx is making the same
    statement in its own words.
    """
    from ravis.api.openai.chat import _adapter_failure
    from ravis.providers.anthropic import AnthropicUpstreamError
    from ravis.reliability.failures import FailureClass

    refused = AnthropicUpstreamError("anthropic 400: max_tokens must be an integer", status=400)
    assert _adapter_failure(refused) is FailureClass.INVALID_REQUEST

    # A 5xx is genuinely the upstream's, and must stay that way — otherwise a
    # provider that is actually down never trips its own breaker.
    broke = AnthropicUpstreamError("anthropic 503: overloaded", status=503)
    assert _adapter_failure(broke) is not FailureClass.INVALID_REQUEST


def test_a_client_fault_does_not_count_against_the_provider() -> None:
    """Why the classification matters beyond the status code.

    `INVALID_REQUEST` carries `HealthScope.NONE`, so it is recorded and does not
    walk the breaker. Left as `UNKNOWN` it would be recorded the same way today —
    both are NONE — but the two say different things to a reader, and a
    classification that happens to be harmless is not the same as one that is
    right.
    """
    from ravis.reliability.failures import FailureClass, HealthScope

    assert FailureClass.INVALID_REQUEST.policy.scope is HealthScope.NONE


def test_a_chain_that_exhausted_on_a_client_error_answers_400() -> None:
    """Classifying it right is not enough if the status still says 502.

    After the classification fix the attempt read `invalid_request` and the
    response was still `502 upstream_error`, because chain exhaustion chose its
    status from *whether anything was tried* rather than from *why everything
    failed*. Both halves are needed: one so the record is true, one so the caller
    is told the truth.

    Driven through a stand-in carrying exactly the three things
    `_chain_exhausted` reads, rather than a real `AttemptChain` — building one
    needs a health registry and a provider, neither of which has anything to do
    with the question being asked.
    """
    from ravis.api.openai.chat import _chain_exhausted
    from ravis.reliability.failures import FailureClass

    class _Attempt:
        outcome = "invalid_request"

    class _Chain:
        attempts = [_Attempt()]

        def __init__(self, failure_class: FailureClass) -> None:
            self.last_class = failure_class

        def exhausted_message(self) -> str:
            return "No upstream attempt succeeded."

        def summary(self) -> dict[str, object]:
            return {"attempts": []}

    client_fault = _chain_exhausted(_Chain(FailureClass.INVALID_REQUEST))  # type: ignore[arg-type]
    assert client_fault.status_code == 400

    # The falsifier: a genuine upstream failure must still read as one, or a
    # provider that is really down would be reported as the caller's mistake.
    upstream_fault = _chain_exhausted(_Chain(FailureClass.CONNECTION))  # type: ignore[arg-type]
    assert upstream_fault.status_code == 502
