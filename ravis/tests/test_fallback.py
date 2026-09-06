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

# Both models satisfy the agent pool's hard invariant in full, so a fallback
# between them is *valid* in §10's sense. The preference fragment "coder"
# matches both, which leaves alphabetical order to decide — making `coder-a` the
# primary and `coder-b` the fallback, predictably.
#
# **All four fields, because §5.1 asks for all four.** This declared tools and
# 32K, which was the whole invariant while `ravis/clarvis-agent` was identical
# to `ravis/agent`. The pool now also requires structured output and 128K —
# Clarvis parses what comes back and reasons over a repository — and a fixture
# short of that describes a model the pool refuses, which turns every test in
# this file into a test of the refusal.
TWO_CODERS = {
    "coder-a": {"tools": "SUPPORTED", "structured_output": "SUPPORTED",
                "context_window": "131072"},
    "coder-b": {"tools": "SUPPORTED", "structured_output": "SUPPORTED",
                "context_window": "131072"},
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
        refuse_transport: set[str] | None = None,
        break_after: dict[str, int] | None = None,
        frames: list[bytes] | None = None,
        answers: dict[str, Any] | None = None,
    ) -> None:
        self.catalogue = catalogue
        # model → (status, body) for models that answer with an HTTP error.
        self.refuse = refuse or {}
        # Models that never answer at all: the connection itself fails before
        # any status line arrives, which is a different failure from `refuse`
        # and reaches RAVIS as an `httpx.ConnectError` rather than a response.
        self.refuse_transport = refuse_transport or set()
        # model → what it answers with a **200**. A dict is a JSON body where a
        # stream was asked for, which is how LM Studio reports anything it will
        # not serve; a list is a frame sequence, and an empty one is a stream
        # that closes without a byte. Both look like success to a status check.
        self.answers = answers or {}
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
        if model in self.refuse_transport:
            raise httpx.ConnectError(f"connection refused for {model}", request=request)
        if model in self.refuse:
            status, body = self.refuse[model]
            return httpx.Response(status, json=body)
        if model in self.answers:
            scripted = self.answers[model]
            if isinstance(scripted, dict):
                return httpx.Response(200, json=scripted)
            return httpx.Response(200, stream=_Frames(iter(scripted)))
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


def test_a_connection_that_never_completes_still_falls_back() -> None:
    """§10's "network loss": a pre-first-byte transport failure, not an HTTP
    error. `refuse` scripts an upstream that answers wrong; this scripts one
    that never answers at all — `_handle` raises before a status line exists,
    which is what `httpx.ConnectError` actually looks like on the wire. The
    two branches this is written against (`chat.py`'s `except httpx.HTTPError`
    around the non-streaming `client.post`, and its streaming twin) had never
    been driven by a real connection failure through a real route.

    A connection failure classifies as `FailureClass.CONNECTION`
    (`classify_exception`), which `AttemptChain.failed` gives exactly one
    same-target retry before falling through — the policy is "this class
    proves the request never arrived", not "give up immediately" — so the
    primary is genuinely asked twice before the fallback is.
    """
    upstream = ScriptedUpstream(TWO_CODERS, refuse_transport={"coder-a"})
    with _app_with(upstream) as client:
        response = client.post("/v1/chat/completions", json={"model": AGENT_POOL})

        assert response.status_code == 200
        assert upstream.served == ["coder-a", "coder-a", "coder-b"]
        assert response.json()["model"] == "coder-b"


def test_a_connection_that_never_completes_still_falls_back_while_streaming() -> None:
    """The streaming twin of the test above (`chat.py`'s `_stream_failed`,
    :1755-1757). Nothing has been sent to the client yet when the connection
    fails — `committed` is false — so the policy is the same fall-through
    rather than a terminated stream: the failure happened before anything was
    promised to whoever is reading the response.
    """
    upstream = ScriptedUpstream(TWO_CODERS, refuse_transport={"coder-a"})
    with _app_with(upstream) as client, client.stream(
        "POST", "/v1/chat/completions", json={"model": AGENT_POOL, "stream": True}
    ) as response:
        received = b"".join(response.iter_bytes())

    assert upstream.served == ["coder-a", "coder-a", "coder-b"]
    assert received == b"".join(FRAMES)


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


def test_a_200_carrying_an_error_object_is_a_refusal_not_an_answer() -> None:
    """The trap the configured upstream actually sets.

    LM Studio answers **200 with an error body** for anything it will not serve
    — a model that has been unloaded, an endpoint it does not implement. RAVIS
    branched only on `status >= 400`, so this was forwarded to the client as a
    successful stream and recorded as a working attempt: no fallback, and a
    health record saying the model is fine.
    """
    upstream = ScriptedUpstream(
        TWO_CODERS, answers={"coder-a": {"error": "Model unloaded or unavailable"}}
    )
    with _app_with(upstream) as client, client.stream(
        "POST", "/v1/chat/completions", json={"model": AGENT_POOL, "stream": True}
    ) as response:
        received = b"".join(response.iter_bytes())

    assert upstream.served == ["coder-a", "coder-b"]
    assert received == b"".join(FRAMES)
    assert received.count(b"[DONE]") == 1


def test_an_error_in_the_first_frame_switches_before_the_client_sees_it() -> None:
    """The same refusal arriving inside the protocol rather than instead of it."""
    upstream = ScriptedUpstream(
        TWO_CODERS,
        answers={"coder-a": [b'data: {"error":{"message":"no model loaded"}}\n\n']},
    )
    with _app_with(upstream) as client, client.stream(
        "POST", "/v1/chat/completions", json={"model": AGENT_POOL, "stream": True}
    ) as response:
        received = b"".join(response.iter_bytes())

    assert upstream.served == ["coder-a", "coder-b"]
    assert b"no model loaded" not in received
    assert received == b"".join(FRAMES)


def test_a_200_that_sends_no_bytes_at_all_is_not_a_success() -> None:
    """Recorded as a success until now, on the reasoning that the upstream had
    nothing to say. An SSE response with no frames is not an empty answer — the
    client waits for a `[DONE]` that never arrives, and the chain that could
    have tried another model has been told everything went well."""
    upstream = ScriptedUpstream(TWO_CODERS, answers={"coder-a": []})
    with _app_with(upstream) as client, client.stream(
        "POST", "/v1/chat/completions", json={"model": AGENT_POOL, "stream": True}
    ) as response:
        received = b"".join(response.iter_bytes())

    assert upstream.served == ["coder-a", "coder-b"]
    assert received == b"".join(FRAMES)


def test_a_model_talking_about_errors_is_not_refusing() -> None:
    """The false positive that would matter most: a coding assistant discussing
    an error message is the ordinary case, not a refusal. Only a top-level
    `error` key counts, and content is never scanned."""
    talkative = [
        b'data: {"choices":[{"delta":{"content":"{\"error\": handle it}"},"index":0}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    upstream = ScriptedUpstream(TWO_CODERS, answers={"coder-a": talkative})
    with _app_with(upstream) as client, client.stream(
        "POST", "/v1/chat/completions", json={"model": AGENT_POOL, "stream": True}
    ) as response:
        received = b"".join(response.iter_bytes())

    assert upstream.served == ["coder-a"]
    assert received == b"".join(talkative)


def test_an_explicit_absence_of_an_error_is_not_an_error() -> None:
    """Some proxies include `"error": null` on a perfectly good response.
    Reading presence rather than truthiness would cost a working model its turn
    for saying that nothing went wrong."""
    polite = [
        b'data: {"error":null,"choices":[{"delta":{"content":"hi"},"index":0}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    upstream = ScriptedUpstream(TWO_CODERS, answers={"coder-a": polite})
    with _app_with(upstream) as client, client.stream(
        "POST", "/v1/chat/completions", json={"model": AGENT_POOL, "stream": True}
    ) as response:
        received = b"".join(response.iter_bytes())

    assert upstream.served == ["coder-a"]
    assert received == b"".join(polite)


def test_an_exhausted_chain_forwards_the_upstreams_own_words() -> None:
    """When every candidate refuses this way there is nothing to fall back to,
    and the client is owed the reason rather than a generic failure."""
    upstream = ScriptedUpstream(
        TWO_CODERS,
        answers={
            "coder-a": {"error": "Model unloaded or unavailable"},
            "coder-b": {"error": "Model unloaded or unavailable"},
        },
    )
    with _app_with(upstream) as client, client.stream(
        "POST", "/v1/chat/completions", json={"model": AGENT_POOL, "stream": True}
    ) as response:
        received = b"".join(response.iter_bytes())

    assert upstream.served == ["coder-a", "coder-b"]
    assert b"Model unloaded" in received
    assert received.endswith(b"data: [DONE]\n\n")


def test_the_non_streaming_path_refuses_a_200_carrying_an_error_too() -> None:
    """The more dangerous half of the same hole. Clarvis's §8.7 tool probe is a
    non-streamed request and treats any 2xx as "this model supports tools" —
    caching it for the session — so a 200 with an error object taught it the
    opposite of the truth."""
    upstream = ScriptedUpstream(
        TWO_CODERS, answers={"coder-a": {"error": "Model unloaded or unavailable"}}
    )
    with _app_with(upstream) as client:
        response = client.post(
            "/v1/chat/completions", json={"model": AGENT_POOL, "stream": False}
        )

    assert upstream.served == ["coder-a", "coder-b"]
    assert response.status_code == 200
    assert "error" not in response.json()


def test_an_exhausted_non_streaming_chain_does_not_forward_a_status_it_disbelieved(
) -> None:
    """The gate was a no-op for the ordinary case until this.

    With one eligible candidate — or with every candidate refusing the same way,
    which is what a runtime holding nothing produces — the chain exhausts and
    the last response was forwarded verbatim. RAVIS recorded a failure, opened
    the model's circuit, logged that no attempt succeeded, and then answered
    200 OK: the record and the response contradicting each other, and Clarvis's
    §8.7 probe still reading a 2xx.
    """
    refused = {"error": "Model unloaded or unavailable"}
    upstream = ScriptedUpstream(
        TWO_CODERS, answers={"coder-a": refused, "coder-b": refused}
    )
    with _app_with(upstream) as client:
        response = client.post(
            "/v1/chat/completions", json={"model": AGENT_POOL, "stream": False}
        )

    assert upstream.served == ["coder-a", "coder-b"]
    assert response.status_code == 502
    # The upstream's own words survive; only the status it chose is overruled.
    assert "Model unloaded" in response.text


def test_a_genuine_upstream_status_still_reaches_the_client_unchanged() -> None:
    """The other half: a 429 must arrive as a 429, or the client gives up where
    it should have retried."""
    upstream = ScriptedUpstream(
        TWO_CODERS,
        refuse={"coder-a": (429, {"error": "slow down"}),
                "coder-b": (429, {"error": "slow down"})},
    )
    with _app_with(upstream) as client:
        response = client.post(
            "/v1/chat/completions", json={"model": AGENT_POOL, "stream": False}
        )

    assert response.status_code == 429


def test_a_keep_alive_comment_does_not_hide_a_refusal() -> None:
    """Ordinary SSE framing defeated the first version of the gate: it read only
    the very first line, so a provider's own `: ping` — or an `event:` field —
    carried the refusal straight past it."""
    upstream = ScriptedUpstream(
        TWO_CODERS,
        answers={"coder-a": [b': ping\n\nevent: message\ndata: {"error":"no model loaded"}\n\n']},
    )
    with _app_with(upstream) as client, client.stream(
        "POST", "/v1/chat/completions", json={"model": AGENT_POOL, "stream": True}
    ) as response:
        received = b"".join(response.iter_bytes())

    assert upstream.served == ["coder-a", "coder-b"]
    assert received == b"".join(FRAMES)


def test_an_unrecognisable_refusal_stops_the_chain_rather_than_shopping_around() -> None:
    """§10: do not route around a refusal merely to find a more permissive
    provider. The words are not in any marker table, so RAVIS does not know
    whether this was a runtime fault or a safety decision — and the safe reading
    of that ambiguity is the one that does not go looking for a yes."""
    upstream = ScriptedUpstream(
        TWO_CODERS, answers={"coder-a": {"error": "this request was declined"}}
    )
    with _app_with(upstream) as client:
        response = client.post(
            "/v1/chat/completions", json={"model": AGENT_POOL, "stream": False}
        )

    assert upstream.served == ["coder-a"]
    assert response.status_code == 502
    assert "declined" in response.text


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
        destination=lambda _: ("http://upstream.invalid/v1/chat/completions", {}),
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
        destination=lambda _: ("http://upstream.invalid/v1/chat/completions", {}),
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
        # Whole-dict equality on purpose: it is what caught M11's metadata fields
        # being added, which is exactly the silent shape change this record
        # cannot afford. A cancelled attempt carries its destination but no
        # timings — nothing was measured, and 0 ms would read as an instant
        # answer rather than as one that never came.
        "provider": "upstream",
        "elapsed_ms": None,
        "ttft_ms": None,
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
    """503, not 502 and not 422: RAVIS declined to try rather than tried and failed.

    A 404 is scoped to the model, so opening both models' circuits takes the
    whole pool out. Nothing was asked upstream, so nothing upstream is
    implicated — but this is also not the same refusal as a pool nothing
    satisfies. The circuits are resting and the pool works again when they
    close, which is what 503 says and 422 does not.

    The difference is load-bearing rather than pedantic. Clarvis's §8.7 tool
    probe treats a 4xx as the model answering and remembers it for the session,
    so a 422 here teaches it that a capable model cannot call tools and it goes
    on believing that long after the cooldown expires.
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
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "no_route"


def test_a_pool_nothing_satisfies_is_still_a_422() -> None:
    """The other side of that line. A model excluded for a reason a cooldown will
    not fix is a configuration answer, and retrying changes nothing."""
    upstream = ScriptedUpstream(
        {"chatty": {"tools": "UNSUPPORTED", "context_window": "8192"}},
        refuse={"chatty": (404, {"error": "model_not_found"})},
    )
    with _app_with(upstream, breaker_failure_threshold=1) as client:
        response = client.post("/v1/chat/completions", json={"model": AGENT_POOL})

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


def test_a_model_no_upstream_lists_is_named_as_such() -> None:
    """**What it replaces is a true sentence about the wrong subject.**

    A retired model id, or a typo, went to whichever upstream happened to be the
    default — the local runtime — and came back "No models loaded. Please load a
    model in the developer page or use the `lms load` command." Nothing was
    wrong with LM Studio and loading a model would not have helped.
    """
    # The local runtime's real words, which is what a person actually saw.
    upstream = ScriptedUpstream(
        TWO_CODERS,
        answers={"vendor/retired-model-001": {
            "error": "No models loaded. Please load a model in the developer page"
        }},
    )
    with _app_with(upstream) as client, client.stream(
        "POST", "/v1/chat/completions",
        json={"model": "vendor/retired-model-001", "stream": True},
    ) as response:
        received = b"".join(response.iter_bytes())

    assert b"No upstream lists" in received
    assert b"vendor/retired-model-001" in received
    assert b"/v1/models" in received, "it should say where to check the id"
    assert b"ravis/<provider>/" in received, "and how to name a provider by hand"


def test_a_listed_model_that_fails_still_gets_the_upstreams_own_words() -> None:
    """The falsifier. A model the catalogue *does* list has an ordinary failure,
    and rewriting that would hide the reason behind a guess about the id."""
    upstream = ScriptedUpstream(
        TWO_CODERS, answers={"coder-a": {"error": "Model unloaded or unavailable"}}
    )
    with _app_with(upstream) as client, client.stream(
        "POST", "/v1/chat/completions", json={"model": "coder-a", "stream": True},
    ) as response:
        received = b"".join(response.iter_bytes())

    assert b"Model unloaded" in received
    assert b"No upstream lists" not in received


def test_the_non_streaming_path_explains_an_unlisted_model_too() -> None:
    """Both paths or neither. The streaming half was fixed first and the plain
    completion still relayed the local runtime's advice to load a model."""
    upstream = ScriptedUpstream(
        TWO_CODERS,
        answers={"vendor/retired-model-001": {"error": "No models loaded."}},
    )
    with _app_with(upstream) as client:
        answered = client.post(
            "/v1/chat/completions", json={"model": "vendor/retired-model-001"}
        )

    assert answered.status_code == 404
    assert "No upstream lists" in answered.json()["error"]["message"]
