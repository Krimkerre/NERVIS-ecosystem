"""M3 — what NERVIS reads from RAVIS, and what it refuses to (§8).

The exit criteria: *"NERVIS inspects RAVIS without touching its DB; provider
health and recent routes visible; the RAVIS-unavailable state works."*

The first is asserted structurally rather than behaviourally — see
`test_nervis_has_no_way_to_reach_ravis_s_database`. The third gets a section of
its own, because "RAVIS is down" has four distinct causes and a dashboard that
renders them identically is hiding three of them.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from nervis.app import create_app
from nervis.config import Settings
from nervis.peers import ravis as peer
from nervis.registry import RegistryEntry, RegistryState, ServiceDeclaration

DECLARATION = ServiceDeclaration("ravis", "RAVIS", "http://127.0.0.1:8731")


def entry_with(capabilities: dict[str, str], state: RegistryState = RegistryState.HEALTHY):
    return RegistryEntry(
        declaration=DECLARATION, state=state, capabilities=capabilities, checked_at=1.0
    )


class Recorder:
    """A transport that answers, and remembers whether it was ever asked.

    §5.2's gate is *"never calls a guessed endpoint"*, which is a claim about a
    call that must **not** happen. Asserting on a returned value cannot show
    that; counting requests can.
    """

    def __init__(self, body: Any = None, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self._body, self._status = body if body is not None else {"items": []}, status

    def client(self) -> httpx.AsyncClient:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self._status, json=self._body)

        return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def do_read(recorder: Recorder, entry: RegistryEntry | None, key: str = "providers", **kw: Any):
    return asyncio.run(
        peer.read(recorder.client(), entry, peer.BY_KEY[key], **{"credential": "", **kw})
    )


# ── Without touching its DB ─────────────────────────────────────────────────


def test_nervis_has_no_way_to_reach_ravis_s_database() -> None:
    """§8: *"It never reads RAVIS's database."*

    Asserted as an absence of capability rather than an absence of behaviour. A
    test that watched for a file open would pass right up until somebody added
    the setting that made it possible; this fails the moment one exists.
    """
    fields = set(Settings.model_fields)

    assert not [name for name in fields if "ravis" in name and "database" in name]
    assert not [name for name in fields if "ravis" in name and "path" in name]
    # And the reader itself takes a URL and a client. There is no argument
    # through which a filesystem path could arrive.
    assert "path" not in peer.read.__annotations__


def test_every_surface_is_an_http_path_on_ravis() -> None:
    """No surface names a file, a socket or a database."""
    for surface in peer.SURFACES:
        assert surface.path.startswith("/api/v1/")


# ── Never calls a guessed endpoint (§5.2) ───────────────────────────────────


def test_an_unadvertised_capability_produces_no_request_at_all() -> None:
    """The gate's exact wording, tested as an absence.

    RAVIS M11 has not shipped, so `ravis.sessions@1` is advertised
    `unavailable`. NERVIS must not call `/api/v1/sessions` to find out — that
    is the guess the rule forbids.
    """
    recorder = Recorder()

    result = do_read(recorder, entry_with({"ravis.sessions": "unavailable"}), key="sessions")

    assert recorder.requests == []
    assert result.available is False
    assert "unavailable" in result.reason


def test_a_capability_nobody_advertised_is_also_not_guessed() -> None:
    recorder = Recorder()

    result = do_read(recorder, entry_with({}), key="providers")

    assert recorder.requests == []
    assert result.availability == "unknown"


def test_a_degraded_capability_is_still_read() -> None:
    """RAVIS's management surface has its reads and none of its mutations.

    Refusing the reads because the writes are missing would remove a working
    screen, which is what §4.1's `degraded` state exists to prevent.
    """
    recorder = Recorder({"items": [{"name": "default"}]})

    result = do_read(recorder, entry_with({"ravis.management": "degraded"}))

    assert len(recorder.requests) == 1
    assert result.available is True
    assert result.data == {"items": [{"name": "default"}]}


# ── The RAVIS-unavailable state, in each of its four causes ─────────────────


def test_a_service_thought_down_is_still_attempted() -> None:
    """Liveness is not a veto, and treating it as one was a bug.

    The registry's reading is up to one probe interval old, so gating on it made
    NERVIS refuse a healthy RAVIS for twenty seconds after it came up —
    reporting `ConnectError` for a service that was answering. A capability last
    seen usable is attempted; the connection refuses in about a millisecond on
    loopback, and the transport's answer is fresher and more specific than the
    registry's.
    """
    recorder = Recorder({"items": [{"name": "default"}]})
    down = entry_with({"ravis.management": "available"}, state=RegistryState.UNREACHABLE)
    down.detail = "no response: ConnectError"

    result = do_read(recorder, down)

    assert len(recorder.requests) == 1
    assert result.available is True


def test_a_service_that_was_never_reached_is_not_attempted() -> None:
    """Nothing was ever advertised, so there is nothing but a guess to call.

    This is the case the gate exists for, and it is what separates it from the
    one above: `discovering` with an empty capability map means NERVIS has no
    idea what RAVIS offers, not that it once knew and has doubts.
    """
    recorder = Recorder()
    never = entry_with({}, state=RegistryState.DISCOVERING)

    result = do_read(recorder, never)

    assert recorder.requests == []
    assert result.availability == "service_down"


def test_a_withdrawn_capability_stays_refused_even_when_the_service_is_up() -> None:
    """The gate is about capability, not reachability."""
    recorder = Recorder()

    result = do_read(recorder, entry_with({"ravis.management": "unavailable"}))

    assert recorder.requests == []
    assert result.available is False


def test_a_service_that_dies_between_the_probe_and_the_read_is_reported() -> None:
    """The registry's reading is up to one probe interval old.

    "It was up twenty seconds ago" is not a claim a screen should make on its
    own, so a call that fails after a healthy probe is reported rather than
    smoothed over.
    """

    def handle(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.ConnectError("gone")

    result = asyncio.run(
        peer.read(
            httpx.AsyncClient(transport=httpx.MockTransport(handle)),
            entry_with({"ravis.management": "available"}),
            peer.BY_KEY["providers"],
            credential="",
        )
    )

    assert result.available is False
    assert result.availability == "service_down"
    assert "did not answer" in result.reason


def test_a_refusal_carries_ravis_s_own_words() -> None:
    """RAVIS publishes §4.3's envelope, so a refusal has a code and a message.

    Replacing those with "HTTP 422" throws away the whole reason that envelope
    exists, which is that a consumer can show the reason to a person.
    """
    recorder = Recorder(
        {"error": {"code": "INVALID_CONFIGURATION", "message": "limit must be positive"}},
        status=422,
    )

    result = do_read(recorder, entry_with({"ravis.management": "available"}))

    assert result.available is False
    assert result.reason == "INVALID_CONFIGURATION: limit must be positive"


def test_a_non_json_answer_is_not_mistaken_for_data() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"<html>a proxy</html>")

    result = asyncio.run(
        peer.read(
            httpx.AsyncClient(transport=httpx.MockTransport(handle)),
            entry_with({"ravis.management": "available"}),
            peer.BY_KEY["providers"],
            credential="",
        )
    )

    assert result.available is False
    assert "not JSON" in result.reason


def test_no_failure_ever_returns_an_empty_collection() -> None:
    """An empty list means *RAVIS has none of these*, which is a real answer.

    Confusing it with "nobody managed to ask" is how a dashboard renders a
    confident zero over an outage.
    """
    recorder = Recorder()

    for entry in (None, entry_with({}), entry_with({"ravis.management": "unavailable"})):
        result = do_read(recorder, entry)
        assert result.data is None
        assert result.available is False


# ── Correlation (§4.3) ──────────────────────────────────────────────────────


def test_the_request_id_is_forwarded_rather_than_regenerated() -> None:
    """One click should produce one id in three logs."""
    recorder = Recorder()

    do_read(recorder, entry_with({"ravis.management": "available"}), request_id="abc123")

    assert recorder.requests[0].headers["x-request-id"] == "abc123"


def test_query_parameters_reach_ravis() -> None:
    """So `?limit=` works without NERVIS re-implementing RAVIS's paging."""
    recorder = Recorder()

    do_read(
        recorder, entry_with({"ravis.routing.explanations": "available"}),
        key="routes", params={"limit": "5"},
    )

    assert recorder.requests[0].url.params["limit"] == "5"


# ── The endpoints ───────────────────────────────────────────────────────────


def test_chat_s_route_lookup_names_nervis_to_ravis() -> None:
    """Found 12 September 2026: this read, and the API Inspector's two, went out
    anonymously, because the reader's credential defaulted to empty and none of them
    passed one. The reader now has no default, so a caller that forgets fails the type
    check; this pins the one that has no request to take the credential from."""
    recorder = Recorder({"items": [{"request_id": "req-1", "decision_id": "d-1"}]})
    entry = entry_with({"ravis.routing.explanations": "available"})

    found = asyncio.run(peer.decision_for(recorder.client(), entry, "req-1", "client.nervis"))

    assert found is not None and found["decision_id"] == "d-1"
    assert recorder.requests[0].headers["authorization"] == "Bearer client.nervis"


def an_api() -> TestClient:
    """A NERVIS whose RAVIS is a dead port."""
    return TestClient(
        create_app(
            Settings(
                database_path=":memory:",
                ravis_base_url="http://127.0.0.1:9",
                sirvis_base_url="http://127.0.0.1:9",
                clarvis_base_url="http://127.0.0.1:9",
                lmstudio_base_url="http://127.0.0.1:9",
                ollama_base_url="http://127.0.0.1:9",
                _env_file=None,  # type: ignore[call-arg]
            )
        )
    )


def test_the_index_says_what_is_readable_without_reading_any_of_it() -> None:
    """Reading all seven to answer "which are available" would make the cheapest
    question on the screen the most expensive call on the service."""
    with an_api() as client:
        body = client.get("/api/v1/ravis").json()

    assert {s["key"] for s in body["surfaces"]} == set(peer.BY_KEY)
    for surface in body["surfaces"]:
        assert surface["reason"], f"{surface['key']} is unusable and says nothing"


def test_the_relay_reads_only_what_the_dashboard_may_read() -> None:
    """The page's reads of RAVIS travel through NERVIS since 12 September 2026. A dead
    RAVIS is marked for the page; the gateway itself is never relayed."""
    with an_api() as client:
        dead = client.get("/api/v1/relay/ravis/api/v1/usage")
        gateway = client.get("/api/v1/relay/ravis/v1/chat/completions")
        written = client.post("/api/v1/relay/ravis/api/v1/pools/curate")

    assert dead.headers.get("x-nervis-relay") == "unreachable"
    assert dead.status_code in (502, 503)
    assert gateway.status_code == 404
    assert written.status_code == 405, "read-only: no other verb is registered"


def test_an_unknown_surface_lists_the_known_ones() -> None:
    """A 404 that names the alternatives ends the question where it was asked."""
    with an_api() as client:
        response = client.get("/api/v1/ravis/telemetry")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert "providers" in body["error"]["details"]["known"]


def test_every_surface_answers_with_the_same_shape_when_ravis_is_down() -> None:
    """One shape for every outcome.

    The alternative is each screen inventing its own way to say "no", which is
    how a dashboard ends up with four empty states that mean the same thing and
    one that silently renders zero.
    """
    with an_api() as client:
        for key in peer.BY_KEY:
            body = client.get(f"/api/v1/ravis/{key}").json()
            assert set(body) == {"surface", "available", "availability", "reason", "data"}
            assert body["available"] is False
            assert body["data"] is None
            assert body["reason"]


@pytest.mark.parametrize("surface", ["health", "providers", "models", "routes", "usage"])
def test_m3_names_five_surfaces_and_all_five_exist(surface: str) -> None:
    """M3's list is health, providers, models, routes, usage and sessions.

    Sessions is the sixth and is expected to refuse: RAVIS M11 has not shipped.
    Listing it anyway is what makes "planned and absent" visible rather than
    leaving a reader to infer it from a gap.
    """
    assert surface in peer.BY_KEY
    assert "sessions" in peer.BY_KEY
