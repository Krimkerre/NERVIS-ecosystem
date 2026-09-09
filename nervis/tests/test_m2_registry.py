"""M2 — the registry, the probes, and capability negotiation (§5).

§5.1's gate names eight scenarios: *"restart, duplicate, stale lease, endpoint
change, malicious registration, unsupported major, auth failure and
two-Clarvis-instance tests all produce correct states."* §5.2 adds one more:
*"remove each capability in fixtures and confirm the UI disables or hides only
the dependent features, and never calls a guessed endpoint."*

Each has a section below. The whole file runs against a fake ecosystem built
from `httpx.MockTransport`, because the point of most of these is a peer
behaving badly and a real one will not oblige.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from ecosystem_protocol import PROTOCOL_VERSION

from nervis import adapters
from nervis.negotiation import Availability, Operation, negotiate
from nervis.probes import PROBE_TIMEOUT_SECONDS, probe
from nervis.registry import (
    USABLE_STATES,
    EndpointRefusedError,
    Registry,
    RegistryState,
    ServiceDeclaration,
    admissible,
    allowed_endpoint,
)

RAVIS = ServiceDeclaration("ravis", "RAVIS", "http://127.0.0.1:8731")
LMSTUDIO = ServiceDeclaration(
    "lmstudio", "LM Studio", "http://127.0.0.1:1234", mep=False, probe_path="/v1/models"
)


def mep(
    *,
    protocol: str = PROTOCOL_VERSION,
    status: str = "healthy",
    capabilities: list[dict[str, Any]] | None = None,
    checks: list[dict[str, Any]] | None = None,
    service_id: str = "ravis-1",
) -> dict[str, Any]:
    """A well-behaved peer's four MEP bodies."""
    return {
        "/ecosystem/version": {"protocol_version": protocol, "build_version": "0.0.1"},
        "/ecosystem/identity": {
            "service_id": service_id,
            "service_type": "ravis",
            "instance_id": "inst-1",
            "machine_id": "mach-1",
            "build_version": "0.0.1",
            "api_version": "1",
        },
        "/ecosystem/health": {
            "status": status,
            "live": True,
            "ready": status == "healthy",
            "checks": checks if checks is not None else [{"name": "database", "status": "pass"}],
        },
        "/ecosystem/capabilities": {
            "revision": 3,
            "capabilities": capabilities
            if capabilities is not None
            else [
                {"id": "ravis.openai_compatible.chat_completions", "state": "available"},
                {"id": "ravis.routing.explanations", "state": "available"},
                {"id": "ravis.management", "state": "degraded"},
                {"id": "ravis.sessions", "state": "unavailable"},
            ],
        },
    }


def peer(bodies: dict[str, Any], *, status_code: int = 200) -> httpx.AsyncClient:
    """A client that answers from `bodies` and 404s anything else."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path not in bodies:
            return httpx.Response(404, json={"detail": "Not Found"})
        return httpx.Response(status_code, json=bodies[request.url.path])

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def refusing(failure: Exception) -> httpx.AsyncClient:
    """A client whose every request fails at the transport."""

    def handle(request: httpx.Request) -> httpx.Response:
        del request
        raise failure

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def observe(client: httpx.AsyncClient, declaration: ServiceDeclaration) -> dict[str, Any]:
    return asyncio.run(probe(client, declaration))


def registry_of(
    *declarations: ServiceDeclaration, now: Callable[[], float] | None = None
) -> Registry:
    return Registry(list(declarations), now=now or (lambda: 1000.0))


# ── Malicious registration: the SSRF guard (§5.1) ────────────────────────────


@pytest.mark.parametrize(
    ("url", "because"),
    [
        ("http://169.254.169.254/latest/meta-data/", "not loopback"),
        ("http://10.0.0.5:8080", "not loopback"),
        ("http://[::1]".replace("[::1]", "192.168.1.1"), "not loopback"),
        ("file:///etc/passwd", "not http or https"),
        ("gopher://127.0.0.1:8731", "not http or https"),
        ("http://evil.example.com", "a name, not a literal address"),
        ("http://", "no host"),
    ],
)
def test_an_endpoint_outside_the_allowlist_is_refused(url: str, because: str) -> None:
    """§5.1: allow only configured local transports and hosts by default.

    A control plane holds a list of URLs and fetches every one on a timer, which
    is a server-side request forgery primitive with a scheduler attached. The
    cloud metadata address is in this list because it is the canonical target.
    """
    with pytest.raises(EndpointRefusedError, match=because):
        allowed_endpoint(url)


def test_a_hostname_is_refused_rather_than_resolved() -> None:
    """Resolving would make the check depend on DNS at the moment of the check.

    A name that resolves to loopback now can resolve elsewhere on the next
    probe. That is the DNS-rebinding half of SSRF, and the half an allowlist
    that resolves first will always miss.
    """
    with pytest.raises(EndpointRefusedError, match="depend on DNS"):
        allowed_endpoint("http://localhost.attacker.example")

    # The literal forms it exists to permit still pass.
    assert allowed_endpoint("http://127.0.0.1:8731/") == "http://127.0.0.1:8731"
    assert allowed_endpoint("http://[::1]:8731") == "http://[::1]:8731"
    assert allowed_endpoint("http://localhost:8790") == "http://localhost:8790"


def test_an_operator_can_widen_the_allowlist_deliberately() -> None:
    assert allowed_endpoint(
        "http://10.0.0.5:8731", extra_hosts=frozenset({"10.0.0.5"})
    ).endswith(":8731")


def test_a_refused_declaration_is_dropped_rather_than_fatal() -> None:
    """One bad endpoint must not stop NERVIS starting.

    Same rule as §5.1's gate about an offline service never breaking the page: a
    malformed entry is reported and omitted, not raised.
    """
    good = ServiceDeclaration("sirvis", "SIRVIS", "http://127.0.0.1:8721")
    bad = ServiceDeclaration("evil", "Evil", "http://169.254.169.254")

    admitted, refused = admissible([good, bad], [])

    assert [entry.key for entry in admitted] == ["sirvis"]
    assert refused[0][0] == "evil"
    assert "not loopback" in refused[0][1]


# ── Unsupported major: the feature, not the dashboard (§5.2) ─────────────────


def test_a_probe_presents_nervis_own_credential_when_it_has_one() -> None:
    """RAVIS gives an anonymous caller sixty reads a minute and a named one six
    hundred. The probe loop is the steadiest reader NERVIS has, and a probe
    refused with 429 records the peer as *degraded* — a rate limit displayed as
    an outage."""
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        bodies = mep()
        if request.url.path not in bodies:
            return httpx.Response(404, json={"detail": "Not Found"})
        return httpx.Response(200, json=bodies[request.url.path])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    asyncio.run(probe(client, RAVIS, None, "nervis-is-named"))

    assert seen, "the probe made no request"
    assert all(header == "Bearer nervis-is-named" for header in seen)


def test_a_probe_without_a_credential_sends_no_authorization() -> None:
    """A peer NERVIS holds no credential for is read anonymously, and sending an
    empty bearer would be a header that means nothing and looks like something."""
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        bodies = mep()
        if request.url.path not in bodies:
            return httpx.Response(404, json={"detail": "Not Found"})
        return httpx.Response(200, json=bodies[request.url.path])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    asyncio.run(probe(client, RAVIS))

    assert seen and all(header is None for header in seen)


def test_lm_studio_is_described_from_its_own_api() -> None:
    """"Answering; publishes no MEP surface" is true, useless, and the same
    sentence whether the runtime holds twenty models or none."""
    translated = adapters.lmstudio(
        {"data": [
            {"id": "qwen/qwen3-4b-2507", "state": "loaded"},
            {"id": "phi-4-mini-instruct", "state": "not-loaded"},
        ]},
        {"data": [{"id": "qwen/qwen3-4b-2507"}]},
    )

    assert translated["capability_source"] == "adapted"
    assert translated["capabilities"]["lmstudio.models.list"] == "available"
    assert "2 local build(s)" in translated["detail"]
    assert "qwen/qwen3-4b-2507" in translated["detail"]
    # The OpenAI surface answered a model list. That is not a completion, and
    # the reason says so rather than letting the state imply it.
    assert "no completion was attempted" in (
        translated["capability_reasons"]["lmstudio.openai.chat_completions"]
    )


def test_a_two_hundred_carrying_an_error_is_not_an_answer() -> None:
    """LM Studio returns HTTP 200 with `{"error": "Unexpected endpoint…"}` for
    every path it does not serve. Reading the status alone would report every
    endpoint as present, including the ones that do not exist."""
    translated = adapters.lmstudio(
        {"error": "Unexpected endpoint or method. (GET /api/v0/models)"},
        {"error": "Unexpected endpoint or method. (GET /v1/models)"},
    )

    assert "capabilities" not in translated
    assert "did not answer in the shape expected" in translated["detail"]

    # And an envelope that carries *both* — an error and a partial list — is
    # refused on the error, not read for the list. That is the case the check
    # exists for: a body with no `data` at all would be rejected anyway.
    both = adapters.lmstudio(
        {"error": "partial catalogue", "data": [{"id": "m", "state": "loaded"}]},
        {"data": [{"id": "m"}]},
    )

    assert "lmstudio.models.list" not in both.get("capabilities", {})


def test_an_adapter_never_invents_a_version() -> None:
    """A derived fact is only defensible while it is derived from an answer."""
    silent = adapters.codeserver({"status": "alive"}, {"name": "code-server"}, "")
    page = (
        '<meta id="coder-options" data-settings="'
        '{&quot;codeServerVersion&quot;:&quot;4.135.0&quot;}" />'
    )
    spoken = adapters.codeserver({"status": "expired"}, {"name": "code-server"}, page)

    assert "build_version" not in silent
    assert spoken["build_version"] == "4.135.0"
    # And the health endpoint's own word is reported as what it is: a browser
    # session heartbeat, not the health of the process.
    assert "no browser session" in spoken["detail"]
    assert "a browser session is connected" in silent["detail"]


def test_an_adapted_capability_says_it_was_derived() -> None:
    """§5.2 is about controls bound to things nobody promised. A capability
    NERVIS worked out is a weaker fact than one a service published, and the
    difference has to survive to the screen."""
    translated = adapters.codeserver({"status": "alive"}, {"name": "code-server"}, "")

    assert translated["capability_source"] == "adapted"
    assert "not published by it" in translated["capability_reasons"]["codeserver.workbench"]


def test_a_service_with_no_adapter_still_says_only_what_is_known() -> None:
    """Three adapters exist. Everything else keeps the honest empty answer."""
    assert "nervis" not in adapters.ADAPTERS
    assert set(adapters.ADAPTERS) == {"lmstudio", "codeserver", "ollama"}


def test_ollama_s_adapter_reports_a_version_and_no_invented_capability() -> None:
    """A bare version implies no capability, and claiming one would be the
    exact invention §5.2 forbids — this adapter has one fact and reports only it."""
    silent = adapters.ollama({})
    spoken = adapters.ollama({"version": "0.33.3"})

    assert "build_version" not in silent
    assert spoken["build_version"] == "0.33.3"
    assert "capabilities" not in spoken
    assert "0.33.3" in spoken["detail"]


def test_an_optional_peer_that_never_answered_is_not_a_warning() -> None:
    """A machine that never had Ollama installed produced a warning about
    Ollama — an alarm for a machine working exactly as configured, and four
    lines in "what has gone wrong lately" that nobody could act on.

    The entry itself already knew: `awaiting_first_contact` is the state §5.1
    distinguishes. Nothing read it when deciding severity.
    """
    optional = ServiceDeclaration(
        "ollama", "Ollama", "http://127.0.0.1:9", mep=False,
        probe_path="/api/tags", optional=True,
    )
    registry = registry_of(optional)
    registry.record("ollama", observe(refusing(httpx.ConnectError("no")), optional))
    entry = registry.get("ollama")

    assert entry is not None
    assert entry.awaiting_first_contact, "an optional peer that never answered"
    assert not entry.is_usable

    # A configured peer that goes down is the opposite case and stays a warning.
    configured = ServiceDeclaration("ravis", "RAVIS", "http://127.0.0.1:8731")
    live = registry_of(configured)
    live.record("ravis", observe(peer(mep()), configured))
    live.record("ravis", observe(refusing(httpx.ConnectError("no")), configured))
    fallen = live.get("ravis")

    assert fallen is not None
    assert not fallen.awaiting_first_contact, "it answered once, so this is an outage"


def test_an_unsupported_major_is_incompatible_not_unreachable() -> None:
    """Two different problems needing two different actions.

    "Upgrade something" and "start something" are not interchangeable, and a
    single `unreachable` for both sends the reader to the wrong one.
    """
    observation = observe(peer(mep(protocol="99.0.0")), RAVIS)

    assert observation["state"] is RegistryState.INCOMPATIBLE
    assert "99.0.0" in observation["detail"]


def test_an_incompatible_peer_disables_only_its_own_operations() -> None:
    """§5.2's sentence, which is the one that takes deliberate structure.

    *"An unsupported required major marks that feature incompatible — not the
    whole dashboard, if other surfaces remain compatible."* The natural
    implementation checks the protocol once per service and disables
    everything, so this asserts the neighbouring service is untouched.
    """
    sirvis = ServiceDeclaration("sirvis", "SIRVIS", "http://127.0.0.1:8721")
    registry = registry_of(RAVIS, sirvis)
    registry.record("ravis", observe(peer(mep(protocol="99.0.0")), RAVIS))
    registry.record(
        "sirvis",
        {
            "state": RegistryState.HEALTHY,
            "capabilities": {"sirvis.inventory.read": "available"},
        },
    )

    chat = Operation("chat", "ravis", "ravis.openai_compatible.chat_completions", "Chat")
    inventory = Operation("inv", "sirvis", "sirvis.inventory.read", "Inventory")

    assert negotiate(chat, registry.get("ravis")).availability is Availability.INCOMPATIBLE
    assert negotiate(inventory, registry.get("sirvis")).availability is Availability.AVAILABLE


# ── Auth failure ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("code", [401, 403])
def test_a_rejected_probe_is_unauthorized_not_unreachable(code: int) -> None:
    """NERVIS reached it. It said no. Those are different facts."""
    observation = observe(peer(mep(), status_code=code), RAVIS)

    assert observation["state"] is RegistryState.UNAUTHORIZED
    assert str(code) in observation["detail"]


# ── Reachability: never a TCP connect alone (§5.1) ──────────────────────────


def test_a_peer_that_answers_badly_is_not_healthy() -> None:
    """§5.1's load-bearing sentence.

    A service that accepts a connection and returns something that is not JSON
    has passed a TCP connect and nothing else, and the state must reflect that
    rather than the handshake.
    """

    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"<html>a proxy login page</html>")

    observation = observe(httpx.AsyncClient(transport=httpx.MockTransport(handle)), RAVIS)

    assert observation["state"] is RegistryState.DEGRADED
    assert "not JSON" in observation["detail"]


def test_a_peer_reporting_itself_unhealthy_is_degraded_not_unreachable() -> None:
    """NERVIS reached it perfectly well and it said no. That is information."""
    observation = observe(
        peer(mep(status="unhealthy", checks=[{"name": "database", "status": "fail"}])), RAVIS
    )

    assert observation["state"] is RegistryState.DEGRADED
    # The failing check by name. "1 check failed" sends the reader to another
    # screen; "database" ends the question.
    assert observation["detail"] == "failing: database"


def test_a_transport_failure_is_unreachable_and_never_stopped() -> None:
    """`stopped` is a claim NERVIS is not entitled to make yet.

    §5.1 lists both states and they mean different things: `stopped` is a
    service NERVIS supervises and knows it stopped, which needs M16's ownership.
    Inferring it from a refused connection would report a service somebody else
    killed as though NERVIS had done it deliberately.
    """
    observation = observe(refusing(httpx.ConnectError("refused")), RAVIS)

    assert observation["state"] is RegistryState.UNREACHABLE


def test_a_probe_never_raises() -> None:
    """§5.1's gate: an offline service never breaks the page.

    A probe that propagated would take the refresh loop with it and freeze every
    *other* entry at whatever it last held — worse than one service being down,
    because a frozen reading still looks current.
    """
    for failure in (httpx.ConnectError("x"), httpx.ReadTimeout("x"), httpx.PoolTimeout("x")):
        assert observe(refusing(failure), RAVIS)["state"] is RegistryState.UNREACHABLE


# ── Duplicate stable IDs, and two Clarvis instances ─────────────────────────


def test_a_duplicate_service_id_does_not_overwrite_a_live_instance() -> None:
    """§5.1: resolve duplicates without overwriting a live instance.

    Two processes claiming one `service_id` is a misconfiguration or an
    impersonation, and the resolution keeps the one already answering — a race
    whose timing an attacker controls is not a tie-break.
    """
    impostor = ServiceDeclaration("impostor", "Impostor", "http://127.0.0.1:9999")
    registry = registry_of(RAVIS, impostor)
    registry.record("ravis", observe(peer(mep(service_id="ravis-1")), RAVIS))

    registry.record("impostor", observe(peer(mep(service_id="ravis-1")), impostor))

    assert registry.get("ravis").state is RegistryState.HEALTHY
    assert registry.get("impostor").state is RegistryState.UNAUTHORIZED
    assert "already held by a live instance" in registry.get("impostor").detail


def test_two_instances_with_different_ids_both_register() -> None:
    """The legitimate case the duplicate rule must not break.

    Two Clarvis editor windows are two extension hosts with two lifetimes, and
    §10 forbids merging them. They differ by `instance_id`, so nothing here
    should object.
    """
    second = ServiceDeclaration("clarvis-2", "Clarvis Bridge 2", "http://127.0.0.1:7072")
    registry = registry_of(RAVIS, second)
    registry.record("ravis", observe(peer(mep(service_id="a")), RAVIS))
    registry.record("clarvis-2", observe(peer(mep(service_id="b")), second))

    assert all(entry.state is RegistryState.HEALTHY for entry in registry.all())


# ── Stale, and restart ──────────────────────────────────────────────────────


def test_an_entry_goes_stale_on_read_rather_than_by_a_timer() -> None:
    """A value that only goes stale when something runs stays fresh forever if
    that something dies — and the timer dying is exactly what this state exists
    to make visible."""
    clock = {"now": 1000.0}
    registry = Registry([RAVIS], stale_after_seconds=90.0, now=lambda: clock["now"])
    registry.record("ravis", observe(peer(mep()), RAVIS))

    assert registry.get("ravis").state is RegistryState.HEALTHY

    clock["now"] += 91.0

    assert registry.get("ravis").state is RegistryState.STALE
    assert "91s ago" in registry.get("ravis").detail


def test_a_restarted_peer_reports_a_new_instance_and_the_same_machine() -> None:
    """What a restart looks like, and why the two ids are separate fields.

    `machine_id` and `service_id` are stable per installation; `instance_id`
    changes per process. A registry that kept only one of them could not tell a
    restart from a second instance.
    """
    registry = registry_of(RAVIS)
    registry.record("ravis", observe(peer(mep()), RAVIS))
    before = registry.get("ravis")
    first_instance = before.instance_id

    restarted = mep()
    restarted["/ecosystem/identity"] = {**restarted["/ecosystem/identity"], "instance_id": "inst-2"}
    registry.record("ravis", observe(peer(restarted), RAVIS))
    after = registry.get("ravis")

    assert after.instance_id != first_instance
    assert after.machine_id == before.machine_id
    assert after.service_id == before.service_id


def test_an_endpoint_change_is_observed_as_a_different_service() -> None:
    """Pointing an entry at something else must not inherit the old readings."""
    registry = registry_of(RAVIS)
    registry.record("ravis", observe(peer(mep()), RAVIS))

    registry.record("ravis", observe(refusing(httpx.ConnectError("moved")), RAVIS))

    assert registry.get("ravis").state is RegistryState.UNREACHABLE


# ── Capability negotiation (§5.2) ───────────────────────────────────────────


def test_removing_one_capability_disables_only_its_operation() -> None:
    """§5.2's gate, run directly: remove each capability and check the blast radius."""
    registry = registry_of(RAVIS)
    registry.record(
        "ravis",
        observe(
            peer(
                mep(
                    capabilities=[
                        {"id": "ravis.routing.explanations", "state": "available"},
                    ]
                )
            ),
            RAVIS,
        ),
    )
    entry = registry.get("ravis")

    chat = Operation("chat", "ravis", "ravis.openai_compatible.chat_completions", "Chat")
    routes = Operation("routes", "ravis", "ravis.routing.explanations", "Routes")

    assert negotiate(routes, entry).availability is Availability.AVAILABLE
    assert negotiate(chat, entry).availability is Availability.UNKNOWN
    assert "does not advertise" in negotiate(chat, entry).reason


def test_an_unadvertised_capability_is_unknown_rather_than_assumed_fine() -> None:
    """§5.2: unknown capabilities are unavailable.

    The gate's other half is *"never calls a guessed endpoint"*, and this is
    what enforces it — a control whose capability was never read is not usable,
    so nothing can call on its behalf.
    """
    registry = registry_of(RAVIS)
    registry.record("ravis", observe(peer(mep(capabilities=[])), RAVIS))

    verdict = negotiate(
        Operation("chat", "ravis", "ravis.openai_compatible.chat_completions", "Chat"),
        registry.get("ravis"),
    )

    assert verdict.availability is Availability.UNKNOWN
    assert verdict.usable is False


def test_a_capability_this_build_never_heard_of_is_ignored() -> None:
    """§5.2: a newer unknown *optional* capability is ignored.

    A peer that grows a capability is not a fault. Treating an unrecognised name
    as one would make every upgrade of a peer look like a regression in NERVIS.
    """
    registry = registry_of(RAVIS)
    registry.record(
        "ravis",
        observe(
            peer(
                mep(
                    capabilities=[
                        {"id": "ravis.routing.explanations", "state": "available"},
                        {"id": "ravis.time_travel", "state": "available"},
                    ]
                )
            ),
            RAVIS,
        ),
    )

    verdict = negotiate(
        Operation("routes", "ravis", "ravis.routing.explanations", "Routes"),
        registry.get("ravis"),
    )

    assert verdict.availability is Availability.AVAILABLE


def test_a_degraded_capability_stays_usable_and_says_why() -> None:
    """RAVIS's management surface has its reads and none of its mutations.

    Hiding the reads because the writes are missing would remove a working
    screen, which is the failure §4.1's `degraded` state exists to prevent.
    """
    registry = registry_of(RAVIS)
    registry.record("ravis", observe(peer(mep()), RAVIS))

    verdict = negotiate(
        Operation("mgmt", "ravis", "ravis.management", "Management"), registry.get("ravis")
    )

    assert verdict.availability is Availability.DEGRADED
    assert verdict.usable is True
    assert "degraded" in verdict.reason


def test_a_service_with_no_mep_surface_advertises_nothing() -> None:
    """LM Studio answers a catalogue endpoint and nothing else.

    Reachability is all NERVIS can honestly claim, so every operation on it is
    `unknown` — inventing `lmstudio.chat` because the port answered would be a
    guess a control could then be bound to.
    """
    registry = registry_of(LMSTUDIO)
    registry.record("lmstudio", observe(peer({"/v1/models": {"data": []}}), LMSTUDIO))
    entry = registry.get("lmstudio")

    assert entry.state is RegistryState.HEALTHY
    assert entry.capabilities == {}
    verdict = negotiate(Operation("x", "lmstudio", "lmstudio.chat", "Chat"), entry)
    assert verdict.availability is Availability.UNKNOWN


def test_an_operation_on_a_service_with_no_entry_is_unknown() -> None:
    verdict = negotiate(Operation("x", "nowhere", "some.capability", "X"), None)

    assert verdict.availability is Availability.UNKNOWN
    assert verdict.usable is False


def test_every_unusable_verdict_carries_a_reason() -> None:
    """A disabled control that does not say why is what §4.1 spends a section
    preventing at the service level, and it is no better one layer up."""
    registry = registry_of(RAVIS)
    registry.record("ravis", observe(refusing(httpx.ConnectError("x")), RAVIS))

    verdict = negotiate(Operation("chat", "ravis", "c", "Chat"), registry.get("ravis"))

    assert not verdict.usable
    assert verdict.reason


# ── The endpoint (§14) ──────────────────────────────────────────────────────


def an_api(**overrides: Any) -> Any:
    """A NERVIS with every peer pointed at a dead port.

    Nothing is running, which is the state M2's gate cares about most: an
    offline ecosystem must produce a complete registry of honest states rather
    than an error.
    """
    from fastapi.testclient import TestClient

    from nervis.app import create_app
    from nervis.config import Settings

    dead = "http://127.0.0.1:9"
    fields: dict[str, Any] = {
        "database_path": ":memory:",
        "ravis_base_url": dead,
        "sirvis_base_url": dead,
        "clarvis_base_url": dead,
        "lmstudio_base_url": dead,
        "ollama_base_url": dead,
        "_env_file": None,
    }
    fields.update(overrides)
    return TestClient(create_app(Settings(**fields)))


def test_the_launcher_and_nervis_agree_on_code_servers_default_port() -> None:
    """One number for one thing, checked rather than remembered.

    The launcher writes a code-server config only for a machine that has none,
    and NERVIS's registry has to look at whatever that config says. They were
    briefly 8741 and 8080 — the launcher had continued the ecosystem's own 87x1
    sequence, NERVIS had taken code-server's default — so a fresh install would
    have served an editor the Clarvis tab reported as absent. The comment in
    `config.py` already says a launcher and a service disagreeing about a port
    produces a dashboard reporting everything as down; this is that sentence with
    a test behind it.
    """
    from nervis.config import Settings  # local, like the two other uses in this file

    launcher = (Path(__file__).parent.parent.parent / "tools" / "run.py").read_text()
    declared = re.search(r"^CODE_SERVER_PORT = (\d+)$", launcher, re.MULTILINE)
    assert declared, "tools/run.py no longer declares CODE_SERVER_PORT"

    assert Settings().code_server_base_url.endswith(":" + declared.group(1))


def test_the_registry_lists_every_declared_service_with_nothing_running() -> None:
    """§5.1's initial entries, and §5.1's gate that an offline peer is a state."""
    with an_api() as client:
        body = client.get("/api/v1/services").json()

    assert {entry["key"] for entry in body["items"]} == {
        # `codeserver` joined at Stage 9: NERVIS embeds it in the Clarvis tab, so
        # the tab needs a truthful answer to "is there an editor to show here".
        # Observed and never supervised, like the two runtimes beside it.
        #
        # `clarvis` left at the same time. The Bridge's port is assigned per
        # editor window and announced at registration — the runbook says it is
        # "never assumed" — so a static row probing a fixed address could never
        # go green, and it made the map draw a red node while two Bridges were
        # live. Registrations are in `/api/v1/registry/instances`, which is the
        # only place that can know.
        "nervis", "ravis", "sirvis", "codeserver", "lmstudio", "ollama",
    }
    assert body["refused"] == []


def test_every_operation_appears_even_when_it_cannot_run() -> None:
    """A control that vanishes teaches nobody anything.

    One that says which service is down and why tells the reader both what is
    missing and when to look again.
    """
    with an_api() as client:
        body = client.get("/api/v1/services").json()

    assert body["operations"]
    for operation in body["operations"]:
        assert operation["reason"], f"{operation['key']} is unusable and says nothing"
        assert operation["usable"] is False


def test_every_operation_declares_what_section_5_2_requires() -> None:
    """§5.2: owning service, capability, read-versus-mutate, timeout, idempotency.

    Asserted on the wire rather than on the dataclass, because the requirement
    is that a *control* declares them and the control reads this body.
    """
    with an_api() as client:
        body = client.get("/api/v1/services").json()

    for operation in body["operations"]:
        assert set(operation) >= {
            "service", "capability", "mutates", "idempotent", "confirm",
            "timeout_seconds", "availability", "reason",
        }
        assert operation["timeout_seconds"] > 0


def test_nothing_mutates_yet_and_the_field_says_so() -> None:
    """§15.1 keeps RAVIS's management surface read-only until M18b.

    The field exists as structure for the milestone that adds the first
    mutation rather than as one nobody filled in.
    """
    with an_api() as client:
        body = client.get("/api/v1/services").json()

    assert not any(operation["mutates"] for operation in body["operations"])


def test_a_refused_endpoint_is_published_rather_than_silently_missing() -> None:
    """A service absent because it was refused looks exactly like one nobody
    configured, unless the refusal is said out loud."""
    with an_api(ravis_base_url="http://169.254.169.254") as client:
        body = client.get("/api/v1/services").json()

    assert [item["key"] for item in body["refused"]] == ["ravis"]
    assert "not loopback" in body["refused"][0]["reason"]
    assert "ravis" not in {entry["key"] for entry in body["items"]}
    # And the operations that depended on it say so rather than disappearing.
    ravis_ops = [o for o in body["operations"] if o["service"] == "ravis"]
    assert ravis_ops and all(o["availability"] == "unknown" for o in ravis_ops)


def test_one_service_can_be_asked_about_on_its_own() -> None:
    """§5.2 requires the blast radius of an incompatible peer to be checkable,
    which needs a way to ask for exactly the controls one service owns."""
    with an_api() as client:
        body = client.get("/api/v1/services/sirvis").json()

    assert body["key"] == "sirvis"
    assert {operation["service"] for operation in body["operations"]} == {"sirvis"}


def test_an_unknown_service_is_a_structured_404() -> None:
    with an_api() as client:
        response = client.get("/api/v1/services/teleporter")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_no_registry_entry_carries_a_credential() -> None:
    """§5.1: secrets are stored separately.

    A registry listing is exactly the surface where one would otherwise be
    published by accident.
    """
    with an_api() as client:
        body = client.get("/api/v1/services").json()

    forbidden = {"api_key", "token", "credential", "authorization", "secret", "password"}
    for entry in body["items"]:
        assert not (forbidden & set(entry)), entry


# ── Optional peers: absent is a fact, not an outage ─────────────────────────


def test_an_unconfigured_runtime_is_optional_and_a_named_one_is_not() -> None:
    """"Configured" means the operator supplied the address, not that it happens
    to equal the default.

    Comparing against the default would call somebody who deliberately typed
    `http://127.0.0.1:11434` unconfigured, which is the opposite of what they
    did. pydantic records which fields were actually supplied.
    """
    from nervis.config import Settings
    from nervis.registry import declared_services

    plain = {d.key: d for d in declared_services(
        Settings(database_path=":memory:", _env_file=None)  # type: ignore[call-arg]
    )}
    named = {d.key: d for d in declared_services(
        Settings(  # type: ignore[call-arg]
            database_path=":memory:", ollama_base_url="http://127.0.0.1:11434", _env_file=None
        )
    )}

    assert plain["ollama"].optional is True
    assert named["ollama"].optional is False
    # RAVIS and SIRVIS are never optional: NERVIS exists to watch them, and one
    # being down is the thing it is for.
    assert plain["ravis"].optional is False
    assert plain["sirvis"].optional is False


def test_an_optional_peer_that_never_answered_is_absent_rather_than_broken() -> None:
    """An installation with LM Studio and no Ollama is an ordinary machine.

    Reporting it as an outage forever is an alarm about software that was never
    installed.
    """
    ollama = ServiceDeclaration(
        "ollama", "Ollama", "http://127.0.0.1:11434", mep=False,
        probe_path="/api/tags", optional=True,
    )
    registry = registry_of(ollama)
    registry.record("ollama", observe(refusing(httpx.ConnectError("refused")), ollama))

    entry = registry.get("ollama")
    assert entry is not None
    assert entry.state is RegistryState.UNREACHABLE  # still truthful
    assert entry.awaiting_first_contact is True


def test_an_optional_peer_that_answered_once_is_a_real_outage_after() -> None:
    """The distinction that keeps this honest is *has it ever answered*.

    A runtime somebody was using and which then stopped is a real outage,
    whatever it was configured from.
    """
    ollama = ServiceDeclaration(
        "ollama", "Ollama", "http://127.0.0.1:11434", mep=False,
        probe_path="/api/tags", optional=True,
    )
    registry = registry_of(ollama)
    registry.record("ollama", observe(peer({"/api/tags": {"models": []}}), ollama))
    assert registry.get("ollama").awaiting_first_contact is False

    registry.record("ollama", observe(refusing(httpx.ConnectError("gone")), ollama))

    entry = registry.get("ollama")
    assert entry is not None
    assert entry.state is RegistryState.UNREACHABLE
    assert entry.awaiting_first_contact is False


def test_ollama_s_version_reaches_the_registry_entry() -> None:
    """End to end through `probe()`, not just the adapter function in isolation
    — proving the version actually survives `_adapted`'s capability filter
    rather than being discarded because Ollama has no capability to derive."""
    ollama = ServiceDeclaration(
        "ollama", "Ollama", "http://127.0.0.1:11434", mep=False,
        probe_path="/api/tags", optional=True,
    )
    observed = observe(
        peer({"/api/tags": {"models": []}, "/api/version": {"version": "0.33.3"}}),
        ollama,
    )

    assert observed["build_version"] == "0.33.3"
    assert observed["state"] is RegistryState.HEALTHY


def test_a_required_peer_is_never_quietly_absent() -> None:
    """RAVIS being down is the thing NERVIS is for."""
    registry = registry_of(RAVIS)
    registry.record("ravis", observe(refusing(httpx.ConnectError("x")), RAVIS))

    assert registry.get("ravis").awaiting_first_contact is False


# ── Hardening found by an adversarial review of the registration design ─────


def test_userinfo_cannot_disguise_a_remote_host() -> None:
    """`http://127.0.0.1@evil.example/` has hostname `evil.example`.

    It reads to a person as loopback, which is the entire trick, and the check
    used to look only at `hostname`.
    """
    with pytest.raises(EndpointRefusedError, match="may not carry credentials"):
        allowed_endpoint("http://127.0.0.1@evil.example/")


def test_a_base_url_may_not_carry_a_path() -> None:
    """A base URL is an origin.

    A path on it is silently prepended to every surface path, so an entry that
    passes the loopback check could still point at a proxying path on a service
    that is genuinely local.
    """
    with pytest.raises(EndpointRefusedError, match="may not carry a path"):
        allowed_endpoint("http://127.0.0.1:8731/proxy/to/anywhere")


def test_an_obviously_remote_address_reports_that_rather_than_its_path() -> None:
    """Order matters in a refusal. Both facts are true of the cloud metadata
    address; only one of them is the reason anybody cares."""
    with pytest.raises(EndpointRefusedError, match="not loopback"):
        allowed_endpoint("http://169.254.169.254/latest/meta-data/")


def test_the_canonical_origin_is_what_gets_stored() -> None:
    """The guard returned a normalised origin and the caller threw it away, so
    the check ran on one string and every probe used another."""
    admitted, _ = admissible(
        [ServiceDeclaration("ravis", "RAVIS", "http://127.0.0.1:8731/")], []
    )

    assert admitted[0].base_url == "http://127.0.0.1:8731"


def test_an_observation_cannot_rewrite_what_a_service_is() -> None:
    """`record()` blind-`setattr`ed whatever it was handed.

    Harmless while the only caller is `probes.py` returning a fixed shape, and a
    hole the moment anything else can reach it — `declaration` and `state` are
    both attributes and both spellable. What a service *is* comes from
    configuration; what it *did* comes from observation.
    """
    registry = registry_of(RAVIS)
    hostile = ServiceDeclaration("ravis", "RAVIS", "http://127.0.0.1:9999")

    registry.record("ravis", {"state": RegistryState.HEALTHY, "declaration": hostile})

    entry = registry.get("ravis")
    assert entry is not None
    assert entry.declaration.base_url == RAVIS.base_url


def test_the_graded_version_is_embeddable_and_says_which_it_is() -> None:
    """Stage 9's exit asks that unsupported combinations be blocked in the UI.
    The gate lives in the capability rather than in a bespoke field, so the
    screen reads the same mechanism every other capability uses."""
    page = (
        '<meta id="coder-options" data-settings="'
        '{&quot;codeServerVersion&quot;:&quot;4.135.0&quot;}" />'
    )
    graded = adapters.codeserver({"status": "alive"}, {"name": "code-server"}, page)

    assert graded["capabilities"]["codeserver.workbench"] == "available"
    assert "Stage 9's matrix graded" in graded["capability_reasons"]["codeserver.workbench"]
    # The identification caveat survives alongside the new answer rather than
    # being replaced by it — §5.2's point is that a derived fact stays labelled.
    assert "not published by it" in graded["capability_reasons"]["codeserver.workbench"]


def test_an_older_code_server_is_refused_rather_than_embedded() -> None:
    """Nothing was ever run against it, and an untested editor presented as a
    working one is the failure this clause exists to prevent."""
    page = (
        '<meta id="coder-options" data-settings="'
        '{&quot;codeServerVersion&quot;:&quot;4.100.2&quot;}" />'
    )
    old = adapters.codeserver({"status": "alive"}, {"name": "code-server"}, page)

    assert old["capabilities"]["codeserver.workbench"] == "unavailable"
    assert "older than 4.135.0" in old["capability_reasons"]["codeserver.workbench"]


def test_a_newer_code_server_is_embedded_but_labelled_untested() -> None:
    """Newer is not evidence of breakage. Blocking every version but the graded
    one would take the editor away the first time somebody upgrades, which
    punishes the user for a gap in NERVIS's testing rather than a fault in
    theirs."""
    page = (
        '<meta id="coder-options" data-settings="'
        '{&quot;codeServerVersion&quot;:&quot;4.140.0&quot;}" />'
    )
    newer = adapters.codeserver({"status": "alive"}, {"name": "code-server"}, page)

    assert newer["capabilities"]["codeserver.workbench"] == "degraded"
    assert "newer than 4.135.0" in newer["capability_reasons"]["codeserver.workbench"]


def test_an_unreadable_version_is_a_gap_in_reading_not_an_old_host() -> None:
    """`_as_numbers` returns () for anything that does not start with a number,
    which would sort below every real version. Treating that as "older than the
    floor" would block a working host because NERVIS could not parse a page."""
    silent = adapters.codeserver({"status": "alive"}, {"name": "code-server"}, "")

    assert silent["capabilities"]["codeserver.workbench"] == "degraded"
    assert "did not publish a version" in silent["capability_reasons"]["codeserver.workbench"]
    # And still no invented version, which the adapter's older test also guards.
    assert "build_version" not in silent


def test_version_numbers_compare_by_number_and_not_as_text() -> None:
    """`"4.9.0" > "4.135.0"` as strings, which would embed a host two years
    older than anything tested."""
    assert adapters._as_numbers("4.135.0") == (4, 135, 0)
    assert adapters._as_numbers("4.136.1+deadbeef") == (4, 136, 1)
    assert adapters._as_numbers("") == ()
    assert adapters._as_numbers("nightly") == ()
    assert adapters._workbench_state("4.9.0")[0] == "unavailable"


# ── §10: a dependency that answers, slowly ───────────────────────────────────
#
# The matrix scored this PARTIAL because the slow path was covered by unit
# tests on helpers — a deadline object, a cache — and nothing made a dependency
# slow and then asked whether the *service* stayed answerable and truthful.


def test_a_peer_that_is_simply_absent_costs_one_request_a_pass() -> None:
    """The deadline bounds a peer that hangs; this bounds one that is not there.

    A full MEP probe is four reads — version, identity, health, capabilities —
    and the first of them failing is what an absent service looks like. The
    probe stops there rather than asking the remaining three of a port nothing
    is listening on, and it does not try the same read twice: an absent peer
    costs one connection attempt a pass, whether it has been absent for a
    second or since boot. A probe that walked all four would quadruple the cost
    of every stopped service on the machine, and RAVIS's anonymous rate limit
    is the reason this loop was made cheaper once already.
    """
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        raise httpx.ConnectError("nothing is listening", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))

    first = observe(client, RAVIS)
    second = observe(client, RAVIS)

    assert first["state"] is RegistryState.UNREACHABLE
    assert second["state"] is RegistryState.UNREACHABLE
    assert asked == ["/ecosystem/version", "/ecosystem/version"], asked


def test_a_peer_that_never_answers_is_bounded_by_the_probe_deadline() -> None:
    """**The probe's own deadline is the thing that keeps one slow peer from
    stalling the loop**, and it was passed to `httpx` with nothing checking it.
    Three services probed four ways each is twelve requests a pass; if any one
    of them could block indefinitely, the registry would stop refreshing for
    every other service on the machine."""
    deadlines: list[Any] = []

    def handle(request: httpx.Request) -> httpx.Response:
        deadlines.append(request.extensions.get("timeout"))
        raise httpx.ReadTimeout("accepted, then silence", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))

    observed = observe(client, RAVIS)

    assert deadlines, "the probe never reached the transport"
    assert deadlines[0]["read"] == PROBE_TIMEOUT_SECONDS
    assert observed["state"] is RegistryState.UNREACHABLE


def test_a_slow_peer_is_reported_as_unreachable_rather_than_healthy() -> None:
    """The outcome §10 asks for: truthful within the detection interval. A peer
    that accepts a connection and then goes quiet must not be carried forward as
    the last thing it said — a stale `healthy` is worse than an honest
    `unreachable`, because it is acted on."""

    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("still thinking", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))

    observed = observe(client, RAVIS)

    assert observed["state"] is RegistryState.UNREACHABLE
    assert "timeout" in observed["detail"].lower() or "ReadTimeout" in observed["detail"]


def test_a_runtime_without_a_mep_surface_is_bounded_the_same_way() -> None:
    """LM Studio and Ollama are probed down a different branch — no MEP, one
    GET — and a deadline missing from that branch would be just as effective at
    stalling the loop, so it is asserted rather than assumed to match."""
    deadlines: list[Any] = []

    def handle(request: httpx.Request) -> httpx.Response:
        deadlines.append(request.extensions.get("timeout"))
        raise httpx.ReadTimeout("slow runtime", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))

    observed = observe(client, LMSTUDIO)

    assert deadlines[0]["read"] == PROBE_TIMEOUT_SECONDS
    assert observed["state"] is RegistryState.UNREACHABLE


# ── §10: code-server going down after it had answered ───────────────────────
#
# The matrix scored this PARTIAL because the loss semantics were proven only
# through registry tests written against ollama and RAVIS. Code-server is a
# different shape from both — its capability is *adapted* rather than
# published, and NERVIS embeds it in a tab — so what happens to a derived
# capability when the thing it was derived from stops answering is its own
# question.


CODESERVER = ServiceDeclaration(
    "codeserver", "code-server", "http://127.0.0.1:8080", mep=False, probe_path="/healthz"
)

#: What `probes._unreachable` writes for a refused connection. Spelled out here
#: rather than imported: a test reaching into another module's private helper
#: passes when that helper changes shape, which is the one thing this must not
#: do — the observation *is* the contract between the probe and the registry.
GONE = {"state": RegistryState.UNREACHABLE, "detail": "no response: ConnectError"}


def test_a_code_server_that_stops_answering_says_so_in_its_state() -> None:
    """**The state is the field a consumer must gate on, and this pins why.**
    The last derived capability survives the loss — deliberately: it is what
    NERVIS last established, and clearing it would make a service that blinked
    look like one that never had the capability at all. What must change is the
    state, because that is what the Code tab reads before it decides whether to
    embed anything."""
    registry = registry_of(CODESERVER)
    registry.record("codeserver", {
        "state": RegistryState.HEALTHY,
        "detail": "serving the workbench; a browser session is connected",
        "capabilities": {"codeserver.workbench": "available"},
        "capability_source": "adapted",
    })

    registry.record("codeserver", dict(GONE))
    entry = registry.get("codeserver")

    assert entry is not None
    assert entry.state is RegistryState.UNREACHABLE
    assert entry.state not in USABLE_STATES, (
        "the Code tab embeds an editor only for a usable state, and this is the "
        "check standing between a dead port and an iframe pointed at it"
    )


def test_a_code_server_that_stopped_is_not_reported_as_stopped() -> None:
    """§5.1 keeps `unreachable` and `stopped` apart and this is the case that
    tempts a guess: NERVIS did not stop code-server, somebody else did, and
    reporting it as stopped would claim an action NERVIS never took."""
    registry = registry_of(CODESERVER)
    registry.record("codeserver", {"state": RegistryState.HEALTHY})

    registry.record("codeserver", dict(GONE))

    assert registry.get("codeserver").state is RegistryState.UNREACHABLE  # type: ignore[union-attr]


def test_a_code_server_that_answers_again_gets_its_capability_back() -> None:
    """The falsifier, and the reason the loss must not be sticky: somebody
    restarts code-server and the tab has to come back without restarting
    NERVIS."""
    registry = registry_of(CODESERVER)
    registry.record("codeserver", dict(GONE))

    registry.record("codeserver", {
        "state": RegistryState.HEALTHY,
        "detail": "serving the workbench",
        "capabilities": {"codeserver.workbench": "available"},
        "capability_source": "adapted",
    })
    entry = registry.get("codeserver")

    assert entry is not None
    assert entry.state is RegistryState.HEALTHY
    assert entry.capabilities["codeserver.workbench"] == "available"


# ── §10, at the route rather than at the probe ──────────────────────────────
#
# Both cells below claimed route evidence for tests that call `probe()` or the
# registry directly. Those are real tests of real rules and they are unit
# tests; what §10 asks is what a *service* answers under the condition, and
# these two are that question.


def test_the_services_route_still_answers_while_a_peer_is_slow() -> None:
    """§10's slow-response outcome is about the service's own routes, not about
    the probe helper. Every peer here points at a dead port and each probe runs
    to its own deadline, so this is the shape of a machine where a dependency
    has gone quiet: the listing has to come back, name each peer, and say what
    it found rather than hanging with it."""
    with an_api() as client:
        answered = client.get("/api/v1/services")

    assert answered.status_code == 200
    rows = answered.json()["items"]
    assert rows, "the listing answered with nothing while its peers were unreachable"
    assert all(row.get("state") for row in rows), "a peer was listed with no state at all"


def test_the_services_route_reports_code_server_as_unreachable() -> None:
    """The editor's own row, at the route. `editor_check.js` proves the tab
    refuses to frame a code-server that is not answering; this proves the state
    that gate reads is the one NERVIS actually publishes."""
    with an_api() as client:
        rows = client.get("/api/v1/services").json()["items"]

    editor = [row for row in rows if row.get("key") == "codeserver"]
    assert editor, "code-server is not in the registry at all"
    # Not `healthy`, and not asserted to carry a reason: before the first probe
    # lands the honest state is `discovering` with nothing to say yet, and
    # demanding a detail there would be demanding an invention.
    assert editor[0]["state"] != "healthy"
    assert editor[0]["state"] in {"discovering", "unreachable", "stale", "stopped"}
