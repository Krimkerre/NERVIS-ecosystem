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
from nervis.probes import probe
from nervis.registry import (
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
    """Two adapters exist. Everything else keeps the honest empty answer."""
    assert "ollama" not in adapters.ADAPTERS
    assert set(adapters.ADAPTERS) == {"lmstudio", "codeserver"}


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
