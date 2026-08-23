"""M12 end to end — what §10's rules actually do to a request.

`test_reliability.py` asserts the rules. This file asserts the behaviour they
produce when a real request goes through the real application: which model ends
up answering, what the client receives, and — the acceptance criterion — that
none of it can corrupt a stream or turn a cancellation into a second request.

Every upstream here is an in-process fake (runbook §14.5). Nothing opens a
socket, and the failures are scripted rather than provoked, so a test that says
"the primary was overloaded" means exactly that and not "the machine was busy".
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from ravis.api.management.decisions import DecisionLog
from ravis.api.openai.chat import UPSTREAM_PROVIDER, _Call, _relay
from ravis.app import create_app
from ravis.config import Settings
from ravis.reliability import AttemptChain, FailureClass, HealthRegistry, HealthScope
from ravis.routing.explain import RouteDecision

AGENT_POOL = "ravis/clarvis-agent"

# Both models declare tools, so both satisfy the agent pool's hard invariant and
# a fallback between them is *valid* in §10's sense. The preference fragment
# "coder" matches both, which leaves alphabetical order to decide — making
# `coder-a` the primary and `coder-b` the fallback, predictably.
TWO_CODERS = {
    "coder-a": {"tools": "SUPPORTED", "context_window": "32768"},
    "coder-b": {"tools": "SUPPORTED", "context_window": "32768"},
}

FRAMES = [
    b'data: {"choices":[{"delta":{"content":"he"},"index":0}]}\n\n',
    b'data: {"choices":[{"delta":{"content":"llo"},"index":0}]}\n\n',
    b"data: [DONE]\n\n",
]


class ScriptedUpstream:
    """An upstream that behaves differently per model, and remembers who asked.

    `served` is the assertion surface for most of this file: whether a fallback
    happened is a question about which models received a request, and only the
    upstream can answer it. Reading it from RAVIS's own report would let a bug
    in the reporting hide a bug in the routing.
    """

    def __init__(
        self,
        catalogue: dict[str, dict[str, str]],
        refuse: dict[str, tuple[int, dict[str, Any]]] | None = None,
        break_after: dict[str, int] | None = None,
        frames: list[bytes] | None = None,
    ) -> None:
        self.catalogue = catalogue
        # model → (status, body) for models that answer with an HTTP error.
        self.refuse = refuse or {}
        # model → how many frames to emit before the connection dies. This is
        # the mid-stream failure: the client already holds part of an answer.
        self.break_after = break_after or {}
        self.frames = frames if frames is not None else FRAMES
        self.served: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v0/models":
            return httpx.Response(404, json={"error": "not lmstudio"})
        if request.url.path.endswith("/models"):
            data = [{"id": model} for model in self.catalogue]
            return httpx.Response(200, json={"object": "list", "data": data})
        payload = json.loads(request.content or b"{}")
        model = payload.get("model", "")
        self.served.append(model)
        if model in self.refuse:
            status, body = self.refuse[model]
            return httpx.Response(status, json=body)
        if not payload.get("stream"):
            return httpx.Response(200, json={"id": "c1", "choices": [], "model": model})
        return httpx.Response(200, stream=_Frames(self._emit(model)))

    def _emit(self, model: str) -> Iterator[bytes]:
        limit = self.break_after.get(model)
        for index, frame in enumerate(self.frames):
            if limit is not None and index >= limit:
                raise httpx.ReadError("connection lost mid-stream")
            yield frame


class _Frames(httpx.AsyncByteStream):
    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


def _app_with(upstream: ScriptedUpstream, **overrides: Any) -> TestClient:
    """The real application, with only the transport underneath it replaced.

    Returned un-entered on purpose: every test uses it as a context manager, so
    the lifespan runs and the model catalogue is warm. Without that the registry
    is empty, every pool resolves to "no models available", and the tests would
    all fail for a reason that has nothing to do with fallback.
    """
    settings = Settings(
        database_path=":memory:",
        upstream_base_url="http://upstream.invalid",
        model_capabilities=upstream.catalogue,
        _env_file=None,  # type: ignore[call-arg]
        **overrides,
    )
    app = create_app(settings)
    fake_client = httpx.AsyncClient(transport=upstream.transport())
    app.app.state.upstream_client = fake_client
    app.app.state.model_registry.use_client(fake_client)
    return TestClient(app)


# ── Fallback happens, and only when it should ────────────────────────────────


def test_an_overloaded_primary_falls_back_to_a_compatible_model() -> None:
    """§10's chain, and §8.8's Stage 3 scenario, on the non-streaming path."""
    upstream = ScriptedUpstream(TWO_CODERS, refuse={"coder-a": (503, {"error": "busy"})})
    with _app_with(upstream) as client:
        response = client.post("/v1/chat/completions", json={"model": AGENT_POOL})

        assert response.status_code == 200
        assert upstream.served == ["coder-a", "coder-b"]
        assert response.json()["model"] == "coder-b"


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, {"error": {"message": "messages must be an array"}}),
        (400, {"error": {"code": "content_filter", "message": "refused"}}),
        (400, {"error": {"message": "maximum context length is 4096 tokens"}}),
    ],
    ids=["invalid_request", "content_refusal", "context_overflow"],
)
def test_a_request_level_failure_is_never_retried_on_another_model(
    status: int, body: dict[str, Any]
) -> None:
    """§10: do not route around a refusal, and do not chase a bad request.

    All three fail identically on every model, so a fallback would spend a
    second model's time to produce the same answer — and in the refusal case it
    would be shopping for a more permissive provider, which §10 forbids
    outright.
    """
    upstream = ScriptedUpstream(TWO_CODERS, refuse={"coder-a": (status, body)})
    with _app_with(upstream) as client:
        response = client.post("/v1/chat/completions", json={"model": AGENT_POOL})

        assert upstream.served == ["coder-a"]
        assert response.status_code == status
        # The upstream's own error object reaches the client untouched: a client
        # that can parse OpenAI's errors must not have to learn RAVIS's (§8).
        assert response.json() == body


def test_an_exhausted_chain_returns_the_last_upstreams_own_status() -> None:
    """A 429 must arrive as a 429, or the client gives up where it should wait."""
    upstream = ScriptedUpstream(
        TWO_CODERS,
        refuse={
            "coder-a": (429, {"error": "slow down"}),
            "coder-b": (429, {"error": "slow down"}),
        },
    )
    with _app_with(upstream) as client:
        response = client.post("/v1/chat/completions", json={"model": AGENT_POOL})

        assert upstream.served == ["coder-a", "coder-b"]
        assert response.status_code == 429


def test_the_retry_budget_caps_how_many_models_are_tried() -> None:
    """Two attempts configured, three eligible candidates, two requests made."""
    catalogue = dict(TWO_CODERS)
    catalogue["coder-c"] = {"tools": "SUPPORTED", "context_window": "32768"}
    upstream = ScriptedUpstream(
        catalogue,
        refuse={model: (503, {"error": "busy"}) for model in catalogue},
    )
    with _app_with(upstream, retry_max_attempts=2) as client:
        client.post("/v1/chat/completions", json={"model": AGENT_POOL})

        assert upstream.served == ["coder-a", "coder-b"]


# ── Streaming: the acceptance criterion ──────────────────────────────────────


def test_a_fallback_before_the_first_byte_leaves_the_stream_intact() -> None:
    """M12's acceptance criterion: fallback never corrupts a stream.

    The primary fails before sending anything, so the client sees exactly one
    stream — the fallback's — with nothing prepended and no duplicate `[DONE]`.
    """
    upstream = ScriptedUpstream(TWO_CODERS, refuse={"coder-a": (503, {"error": "busy"})})
    with _app_with(upstream) as client:
        with client.stream(
            "POST", "/v1/chat/completions", json={"model": AGENT_POOL, "stream": True}
        ) as response:
            received = b"".join(response.iter_bytes())

        assert upstream.served == ["coder-a", "coder-b"]
        assert received == b"".join(FRAMES)
        assert received.count(b"[DONE]") == 1


def test_a_failure_after_the_first_byte_does_not_fall_back() -> None:
    """The other half of the same criterion, and the harder half.

    Appending a second model's answer to a first model's half-answer is not a
    recovery: the client would parse both as one message. So the stream is
    terminated where it broke — with a `[DONE]`, because a stream that simply
    stops leaves Clarvis waiting forever (§8.2).
    """
    upstream = ScriptedUpstream(
        TWO_CODERS, break_after={"coder-a": 1}, refuse={}
    )
    with _app_with(upstream) as client:
        with client.stream(
            "POST", "/v1/chat/completions", json={"model": AGENT_POOL, "stream": True}
        ) as response:
            received = b"".join(response.iter_bytes())

        assert upstream.served == ["coder-a"]
        assert received.startswith(FRAMES[0])
        assert received.endswith(b"data: [DONE]\n\n")
        assert b"upstream_error" in received


def test_an_interrupted_stream_is_counted_as_an_interruption() -> None:
    """§10 tracks stream interruptions separately, because they cannot be retried."""
    upstream = ScriptedUpstream(TWO_CODERS, break_after={"coder-a": 1})
    with _app_with(upstream) as client:
        with client.stream(
            "POST", "/v1/chat/completions", json={"model": AGENT_POOL, "stream": True}
        ) as response:
            b"".join(response.iter_bytes())

        health = client.app.app.state.health  # type: ignore[attr-defined]
        assert health.of(HealthScope.MODEL, "coder-a").stream_interruptions == 1


async def test_cancellation_does_not_trigger_a_fallback() -> None:
    """§10: client cancellation is not a retry.

    Driven against the relay generator directly, because that is the only way to
    express a disconnect halfway through a stream — an HTTP client buffers the
    whole response first. The chain is loaded with a fallback that must never be
    reached: if closing the generator were mistaken for a failure, `coder-b`
    would appear in `served`, and the whole point of §10's rule is that it
    does not.
    """
    upstream = ScriptedUpstream(TWO_CODERS)
    client = httpx.AsyncClient(transport=upstream.transport())
    chain = AttemptChain(health=HealthRegistry(), provider=UPSTREAM_PROVIDER)
    chain.load("coder-a", ["coder-b"])
    call = _Call(
        client=client,
        target="http://upstream.invalid/v1/chat/completions",
        headers={},
        body=json.dumps({"model": "coder-a", "stream": True}).encode(),
        payload={"model": "coder-a", "stream": True},
        chain=chain,
        recorded=None,
    )

    relay = _relay(call)
    await relay.__anext__()
    await relay.aclose()
    await client.aclose()

    assert upstream.served == ["coder-a"]
    # Succeeded on the first byte, then cancelled — and `coder-b` never
    # appears, which is the whole assertion. The cancellation is *recorded*
    # rather than merely not-retried: without it the route decision stays
    # indistinguishable from a request still in flight.
    assert [a["outcome"] for a in chain.summary()["attempts"]] == ["succeeded", "cancelled"]
    assert "cancellation is never a failure" in chain.summary()["stopped_because"]


async def test_a_cancelled_stream_records_that_it_was_cancelled() -> None:
    """Found by pointing the real Clarvis at a running gateway.

    A mid-stream disconnect — which is exactly what Clarvis's Stop button does,
    and a Stage 3 exit criterion — left the recorded route decision with
    `execution: null`. That is the same thing an unfinished request shows, so a
    dashboard could not tell "the user pressed Stop" from "this has been hanging
    for four minutes", which are the two readings a person most needs separated.
    """
    upstream = ScriptedUpstream(TWO_CODERS)
    client = httpx.AsyncClient(transport=upstream.transport())
    log = DecisionLog()
    recorded = log.record(
        RouteDecision(requested=AGENT_POOL, selected="coder-a", fallbacks=["coder-b"]),
        application_id="clarvis",
        request_id="r1",
    )
    chain = AttemptChain(health=HealthRegistry(), provider=UPSTREAM_PROVIDER)
    chain.load("coder-a", ["coder-b"])
    call = _Call(
        client=client,
        target="http://upstream.invalid/v1/chat/completions",
        headers={},
        body=json.dumps({"model": "coder-a", "stream": True}).encode(),
        payload={"model": "coder-a", "stream": True},
        chain=chain,
        recorded=recorded,
    )

    relay = _relay(call)
    await relay.__anext__()
    await relay.aclose()
    await client.aclose()

    assert recorded.attempts is not None
    assert recorded.attempts["attempts"][-1] == {
        "model": "coder-a",
        "outcome": "cancelled",
        "detail": "",
    }


# ── The circuit breaker, seen from outside ───────────────────────────────────


def test_a_repeatedly_failing_model_is_dropped_from_routing() -> None:
    """§10: do not keep routing to a failing provider.

    After the threshold is reached the model stops being *chosen*, which is
    visible in the route explanation rather than only in a log — §9.7 requires
    every excluded candidate to say why it was excluded.
    """
    upstream = ScriptedUpstream(
        TWO_CODERS, refuse={"coder-a": (404, {"error": "model_not_found"})}
    )
    with _app_with(upstream, breaker_failure_threshold=2) as client:
        for _ in range(2):
            client.post("/v1/chat/completions", json={"model": AGENT_POOL})
        upstream.served.clear()
        client.post("/v1/chat/completions", json={"model": AGENT_POOL})

        assert upstream.served == ["coder-b"]
        latest = client.get("/api/v1/route-decisions?limit=1").json()["items"][0]
        excluded = {entry["model"]: entry["reasons"] for entry in latest["excluded"]}
        assert any("circuit open" in reason for reason in excluded["coder-a"])


def test_every_candidate_behind_an_open_circuit_is_refused_without_a_call() -> None:
    """503, not 502: RAVIS declined to try rather than tried and failed.

    A 404 is scoped to the model, so opening both models' circuits takes the
    whole pool out — and the pool being unavailable is a no-route (§5.2), which
    is a 422 rather than an upstream error. The distinction matters to whoever
    is debugging: nothing was asked, so nothing upstream is implicated.
    """
    upstream = ScriptedUpstream(
        TWO_CODERS,
        refuse={
            "coder-a": (404, {"error": "model_not_found"}),
            "coder-b": (404, {"error": "model_not_found"}),
        },
    )
    with _app_with(upstream, breaker_failure_threshold=1) as client:
        client.post("/v1/chat/completions", json={"model": AGENT_POOL})
        upstream.served.clear()
        response = client.post("/v1/chat/completions", json={"model": AGENT_POOL})

        assert upstream.served == []
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "no_route"


def test_a_directly_named_model_still_respects_its_own_circuit() -> None:
    """§5.3 and §10 meet here, and both survive.

    A model named explicitly by the client is never second-guessed at routing
    time — §5.3 puts an explicit request above any inference, so the breaker is
    not consulted while choosing. It is still consulted before *calling*, which
    is the only way a named model with an open circuit fails fast instead of
    paying another timeout to rediscover what is already known.

    503 rather than 502, and the difference is the point: nothing was asked, so
    nothing upstream is implicated. There is no fallback either, because the
    client named one model and substituting another would be exactly the
    second-guessing §5.3 forbids.
    """
    upstream = ScriptedUpstream(
        TWO_CODERS, refuse={"coder-a": (404, {"error": "model_not_found"})}
    )
    with _app_with(upstream, breaker_failure_threshold=1) as client:
        client.post("/v1/chat/completions", json={"model": "coder-a"})
        upstream.served.clear()
        response = client.post("/v1/chat/completions", json={"model": "coder-a"})

        assert upstream.served == []
        assert response.status_code == 503
        assert "circuit open" in response.json()["error"]["message"]


# ── What the explanation records ─────────────────────────────────────────────


def test_a_route_decision_publishes_its_fallback_order() -> None:
    """§10's chain is part of the decision, not an implementation detail."""
    upstream = ScriptedUpstream(TWO_CODERS)
    with _app_with(upstream) as client:
        client.post("/v1/chat/completions", json={"model": AGENT_POOL})

        latest = client.get("/api/v1/route-decisions?limit=1").json()["items"][0]
        assert latest["selected"] == "coder-a"
        assert latest["fallbacks"] == ["coder-b"]


def test_a_route_decision_records_what_happened_when_it_was_executed() -> None:
    """The half of an explanation that matters during an incident (§9.7)."""
    upstream = ScriptedUpstream(TWO_CODERS, refuse={"coder-a": (503, {"error": "busy"})})
    with _app_with(upstream) as client:
        client.post("/v1/chat/completions", json={"model": AGENT_POOL})

        latest = client.get("/api/v1/route-decisions?limit=1").json()["items"][0]
        outcomes = [attempt["outcome"] for attempt in latest["execution"]["attempts"]]
        assert outcomes == [FailureClass.OVERLOAD.value, "succeeded"]
