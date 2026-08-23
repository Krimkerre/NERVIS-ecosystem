"""M1 — the transparent path forwards bytes without disturbing them.

These are the tests the milestone exists for. The proxy's whole claim is that a
client cannot tell an intermediary was inserted, and the way to check that is to
compare what the upstream sent against what the client received, byte for byte.
"""

from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient
from tests.conftest_upstream import TOOL_CALL_FRAMES, RecordingUpstream, failing_transport

from ravis.api.openai.chat import _relay
from ravis.app import create_app
from ravis.compatibility.clarvis.conformance import _single_attempt
from ravis.config import Settings


def _app_with(upstream: RecordingUpstream | httpx.MockTransport) -> tuple[TestClient, Settings]:
    """Build the real app, with its upstream client pointed at a fake transport.

    The application is not modified for testing: only the transport underneath
    its client is swapped, so everything above it — middleware, admission,
    identity, the proxy itself — is the code that runs in production.
    """
    settings = Settings(
        database_path=":memory:",
        upstream_base_url="http://upstream.invalid",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    transport = upstream.transport() if isinstance(upstream, RecordingUpstream) else upstream
    fake_client = httpx.AsyncClient(transport=transport)
    app.app.state.upstream_client = fake_client
    app.app.state.model_registry.use_client(fake_client)
    return TestClient(app), settings


def test_streamed_bytes_arrive_exactly_as_the_upstream_sent_them() -> None:
    """The core claim of the transparent path, checked byte for byte.

    Not "the tool call parses correctly" — *identical bytes*. A proxy that
    reassembles and re-emits could still produce valid JSON while changing where
    the fragment boundaries fall, and Clarvis's incremental parser sees those
    boundaries.
    """
    upstream = RecordingUpstream()
    client, _ = _app_with(upstream)

    with client.stream(
        "POST", "/v1/chat/completions", json={"model": "any", "stream": True}
    ) as response:
        received = b"".join(response.iter_bytes())

    assert received == b"".join(TOOL_CALL_FRAMES)


def test_the_stream_terminates_with_done() -> None:
    """§8.2: a stream that ends without [DONE] leaves the client waiting."""
    client, _ = _app_with(RecordingUpstream())

    with client.stream(
        "POST", "/v1/chat/completions", json={"model": "any", "stream": True}
    ) as response:
        received = b"".join(response.iter_bytes())

    assert received.endswith(b"data: [DONE]\n\n")


def test_tool_call_argument_fragments_are_not_reassembled() -> None:
    """§8.3 is release-critical: the fragment boundaries must survive.

    Asserting on the split itself rather than the reassembled value, because a
    proxy that merges these into one frame would still yield the right filename
    and still be wrong.
    """
    client, _ = _app_with(RecordingUpstream())

    with client.stream(
        "POST", "/v1/chat/completions", json={"model": "any", "stream": True}
    ) as response:
        received = b"".join(response.iter_bytes())

    assert b'"arguments":"{\\"pa"' in received
    assert b'"arguments":"th\\":\\"foo"' in received
    assert b'"arguments":".ts\\"}"' in received


async def test_a_disconnect_stops_the_upstream_generation() -> None:
    """§8.6, and it is a release requirement: abandoning the client must abandon
    the upstream.

    Driven against the relay generator directly rather than through TestClient,
    which cannot express this: it consumes the whole response into a buffer
    before handing anything back, so breaking out of `iter_bytes()` reads every
    frame anyway and the test would pass or fail for reasons unrelated to the
    proxy. Closing the generator is exactly what Starlette does when a client
    disconnects mid-stream, so this exercises the real mechanism.

    The assertion is that the upstream was *not* drained. If RAVIS kept reading
    after the client left, every frame would have been pulled — on a paid
    provider, a completion nobody reads and somebody pays for.
    """
    upstream = RecordingUpstream()
    client = httpx.AsyncClient(transport=upstream.transport())
    # The call is built by the conformance harness rather than here: it is
    # shipped code written for exactly this — driving the relay without an HTTP
    # client — and a second copy of the setup is a second thing to keep correct.
    relay = _relay(_single_attempt(client, "any"))

    await relay.__anext__()
    await relay.aclose()
    await client.aclose()

    assert upstream.frames_pulled == 1


def test_a_blocking_completion_is_forwarded_whole() -> None:
    client, _ = _app_with(RecordingUpstream())

    response = client.post("/v1/chat/completions", json={"model": "any"})

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "hello"


def test_the_client_credential_is_not_forwarded_upstream() -> None:
    """A caller's token authenticates it to RAVIS, not to a third party.

    Forwarding it would let any local process borrow whatever the caller held,
    and would hand that token to the upstream (§9.6.0, and runbook §9 on
    credentials staying in their owning store).
    """
    upstream = RecordingUpstream()
    client, _ = _app_with(upstream)

    client.post(
        "/v1/chat/completions",
        json={"model": "any"},
        headers={"Authorization": "Bearer caller-secret"},
    )

    forwarded = upstream.requests[-1].headers.get("authorization", "")
    assert "caller-secret" not in forwarded


def test_an_upstream_error_status_reaches_the_client_unchanged() -> None:
    """A 429 must arrive as a 429: turning it into a 500 tells a client to give
    up where it should have backed off and retried."""
    transport = failing_transport(429, {"error": {"message": "slow down", "type": "rate_limit"}})
    client, _ = _app_with(transport)

    response = client.post("/v1/chat/completions", json={"model": "any"})

    assert response.status_code == 429


def test_no_upstream_configured_is_a_clear_refusal_not_a_crash() -> None:
    settings = Settings(database_path=":memory:", _env_file=None)  # type: ignore[call-arg]
    client = TestClient(create_app(settings))

    response = client.post("/v1/chat/completions", json={"model": "any"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_not_configured"


def test_the_image_cap_still_applies_on_the_transparent_path() -> None:
    """§4.4 is not suspended because the body is forwarded untouched."""
    client, settings = _app_with(RecordingUpstream())
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    messages = [{"role": "user", "content": [image] * (settings.max_images_per_request + 1)}]

    response = client.post("/v1/chat/completions", json={"model": "any", "messages": messages})

    assert response.status_code == 413


def test_malformed_json_is_refused_in_the_shape_v1_clients_parse() -> None:
    client, _ = _app_with(RecordingUpstream())

    response = client.post(
        "/v1/chat/completions", content=b"{not json", headers={"content-type": "application/json"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_a_pool_id_is_resolved_before_forwarding() -> None:
    """The one field the transparent path rewrites.

    A pool ID is not a model any upstream knows, so resolving it means the
    substitution has to happen somewhere. The upstream must receive a real model
    name — and everything else the client sent must arrive unchanged.
    """
    upstream = RecordingUpstream()
    client, _ = _app_with(upstream)

    # Entering the client runs the lifespan, which warms the model catalogue.
    # Without candidates there is nothing to resolve a pool to, and the request
    # would be correctly refused before reaching the upstream.
    with client:
        client.post(
            "/v1/chat/completions", json={"model": "ravis/clarvis-chat", "temperature": 0.4}
        )

    forwarded = json.loads(upstream.requests[-1].content)
    assert forwarded["model"] != "ravis/clarvis-chat"
    assert forwarded["temperature"] == 0.4


def test_an_unresolvable_pool_never_reaches_the_upstream() -> None:
    """§9.2 forbids relaxing a constraint to find something that fits, so the
    request is refused here rather than sent somewhere it might succeed."""
    upstream = RecordingUpstream()
    client, _ = _app_with(upstream)
    before = len(upstream.requests)

    response = client.post("/v1/chat/completions", json={"model": "ravis/clarvis-agent"})

    assert response.status_code == 422
    assert response.json()["error"]["type"] == "no_route"
    assert len(upstream.requests) == before


def test_a_no_route_carries_its_explanation() -> None:
    """§9.7: a no-route decision is first-class and explainable.

    The upstream fixture serves a catalogue but the registry is only populated
    by a refresh, so this exercises the empty-catalogue case — which is why the
    assertion is on the requirements and the reason rather than on exclusions.
    "Nothing is available" and "nothing qualifies" are different failures with
    different fixes, and the explanation has to say which one happened.
    """
    client, _ = _app_with(RecordingUpstream())

    body = client.post("/v1/chat/completions", json={"model": "ravis/clarvis-agent"}).json()

    explanation = body["error"]["route_decision"]
    assert "tools REQUIRED" in explanation["requirements"]
    assert explanation["reason"]
    assert explanation["pool"] == "ravis/clarvis-agent"


def test_the_models_endpoint_advertises_the_clarvis_pools() -> None:
    """Clarvis picks a model from this list, so the pools must appear in it (§5)."""
    client, _ = _app_with(RecordingUpstream())

    ids = [entry["id"] for entry in client.get("/v1/models").json()["data"]]

    assert "ravis/clarvis-chat" in ids
    assert "ravis/clarvis-agent" in ids


def test_no_model_entry_carries_created() -> None:
    """§5.0.1: Clarvis sorts by timestamp only when *every* entry has it, so a
    mixed list scrambles the intended order. Pools have no creation time."""
    client, _ = _app_with(RecordingUpstream())

    entries = client.get("/v1/models").json()["data"]

    assert all("created" not in entry for entry in entries)
