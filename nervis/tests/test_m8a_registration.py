"""M8a — authenticated local dynamic registration (§5.1, `CLARVIS.md` §6.7).

The receiving half of the Clarvis Bridge integration. The Bridge itself does
not exist yet — `CLARVIS.md` §6 specifies it and the Clarvis repository has no
implementation — so these tests exercise the contract NERVIS offers, which is
the half that can be built without inventing another component's API.

The last test in this file is the unusual one: it reads NERVIS's own source to
assert that no code path exists that could act on a registered instance. That
is a strange thing to test and the right thing to test, because §6.7's limits
are not behaviour that can go wrong at runtime — they are code that must not be
written, and the only way to check for code that must not be written is to look.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nervis.app import create_app
from nervis.config import Settings
from nervis.instances import LEASE_SECONDS, Instances, RegistrationRefusedError


def an_api(tmp_path: Path) -> TestClient:
    settings = Settings(database_path=str(tmp_path / "nervis.db"))
    return TestClient(create_app(settings))


def a_claim(**overrides: object) -> dict[str, object]:
    claim: dict[str, object] = {
        "service": "clarvis",
        "instance_id": "window-a1b2c3d4e5",
        "machine_id": "machine-1",
        "port": 7073,
        "api_version": "1.0.0",
        "protocol_version": "1.0.0",
        "capabilities": {"clarvis.status.read": "1.0.0"},
    }
    claim.update(overrides)
    return claim


def enrolled(client: TestClient) -> dict[str, str]:
    return {"Authorization": f"Bearer {client.app.state.enrollment_secret}"}


# ── §5.1: do not accept an unauthenticated process's claimed service type ───


def test_registration_without_the_enrollment_secret_is_refused(tmp_path: Path) -> None:
    client = an_api(tmp_path)

    response = client.post("/api/v1/registry/instances", json=a_claim())

    assert response.status_code == 401
    assert client.get("/api/v1/registry/instances").json()["items"] == []


def test_registration_with_a_wrong_secret_is_refused(tmp_path: Path) -> None:
    client = an_api(tmp_path)

    response = client.post(
        "/api/v1/registry/instances",
        json=a_claim(),
        headers={"Authorization": "Bearer not-the-secret"},
    )

    assert response.status_code == 401


def test_the_enrollment_secret_is_readable_only_by_its_owner(tmp_path: Path) -> None:
    """The permission bits *are* the authentication, so they are the test.

    A secret at `0644` fails nothing and breaks everything: registration keeps
    working, and the check has quietly stopped meaning anything.
    """
    from nervis.enrollment import secret_path

    an_api(tmp_path)

    mode = secret_path(str(tmp_path / "nervis.db")).stat().st_mode & 0o777
    assert mode == 0o600, f"the enrollment secret is mode {mode:o}"


def test_a_service_that_is_not_per_instance_may_not_register(tmp_path: Path) -> None:
    """Enrolment proves the caller is the user, not that it is what it claims.

    So a local process that read the secret must not be able to register itself
    as RAVIS and be handed the chat traffic.
    """
    client = an_api(tmp_path)

    response = client.post(
        "/api/v1/registry/instances",
        json=a_claim(service="ravis"),
        headers=enrolled(client),
    )

    assert response.status_code == 409
    assert "may not register dynamically" in response.text


# ── Per-extension-host instances, with no cross-instance leakage ────────────


def test_two_extension_hosts_appear_as_two_instances(tmp_path: Path) -> None:
    client = an_api(tmp_path)

    first = client.post(
        "/api/v1/registry/instances",
        json=a_claim(instance_id="window-one", port=7073),
        headers=enrolled(client),
    )
    second = client.post(
        "/api/v1/registry/instances",
        json=a_claim(instance_id="window-two", port=7074),
        headers=enrolled(client),
    )

    assert first.status_code == second.status_code == 201
    items = client.get("/api/v1/registry/instances").json()["items"]
    assert [one["instance_id"] for one in items] == ["window-one", "window-two"]
    assert [one["endpoint"] for one in items] == [
        "http://127.0.0.1:7073", "http://127.0.0.1:7074"
    ]
    # Two windows, two labels. Merging them would show one window's activity
    # under the other's name, which is the wrong direction to be wrong in.
    assert items[0]["label"] != items[1]["label"]


def test_one_instances_token_does_not_work_on_another(tmp_path: Path) -> None:
    client = an_api(tmp_path)
    first = client.post(
        "/api/v1/registry/instances",
        json=a_claim(instance_id="window-one"),
        headers=enrolled(client),
    ).json()
    client.post(
        "/api/v1/registry/instances",
        json=a_claim(instance_id="window-two", port=7074),
        headers=enrolled(client),
    )

    stolen = client.delete(
        "/api/v1/registry/instances/clarvis/window-two",
        headers={"Authorization": f"Bearer {first['token']}"},
    )

    assert stolen.status_code == 401
    assert len(client.get("/api/v1/registry/instances").json()["items"]) == 2


def test_a_live_instance_id_is_not_taken_over(tmp_path: Path) -> None:
    """§5.1: *"resolve duplicate stable IDs without overwriting a live instance."*

    Letting the newest writer win is a race whose timing an attacker controls,
    which is not a tie-break.
    """
    client = an_api(tmp_path)
    client.post("/api/v1/registry/instances", json=a_claim(), headers=enrolled(client))

    duplicate = client.post(
        "/api/v1/registry/instances", json=a_claim(port=9999), headers=enrolled(client)
    )

    assert duplicate.status_code == 409
    items = client.get("/api/v1/registry/instances").json()["items"]
    assert items[0]["endpoint"] == "http://127.0.0.1:7073"


# ── Redaction: nothing a registrant sends can carry a path or a secret ──────


def test_a_workspace_path_cannot_be_stored_because_there_is_no_field_for_it(
    tmp_path: Path,
) -> None:
    """§6.7 forbids NERVIS holding the workspace root, so the claim has no room for it.

    The allowlist is checked by sending every field a well-meaning Bridge author
    might add and asserting none of it survives anywhere in the response.
    """
    client = an_api(tmp_path)

    response = client.post(
        "/api/v1/registry/instances",
        json=a_claim(
            workspace_root="/Users/mathias/Documents/secret-project",
            label="secret-project",
            open_file="/Users/mathias/.ssh/id_ed25519",
            bridge_token="brg-live-abcdef",
        ),
        headers=enrolled(client),
    )

    assert response.status_code == 201
    everything = json.dumps(response.json()) + client.get("/api/v1/registry/instances").text
    assert "secret-project" not in everything
    assert "id_ed25519" not in everything
    assert "brg-live-abcdef" not in everything


def test_the_instance_token_is_returned_once_and_never_listed(tmp_path: Path) -> None:
    """§5.1: secrets are stored separately — and a listing is where one leaks.

    If the dashboard could read this token, a browser tab would be sufficient
    to impersonate an editor window.
    """
    client = an_api(tmp_path)
    token = client.post(
        "/api/v1/registry/instances", json=a_claim(), headers=enrolled(client)
    ).json()["token"]

    listing = client.get("/api/v1/registry/instances").text

    assert token not in listing
    assert client.app.state.enrollment_secret not in listing


def test_a_registrant_may_not_choose_a_non_loopback_endpoint(tmp_path: Path) -> None:
    """It sends a port; NERVIS supplies the host.

    Accepting a URL would hand the registrant the SSRF primitive that
    `allowed_endpoint` exists to deny, from an endpoint whose whole purpose is
    to be called by processes NERVIS has not vetted.
    """
    client = an_api(tmp_path)

    response = client.post(
        "/api/v1/registry/instances",
        json=a_claim(port="http://169.254.169.254/latest/meta-data"),
        headers=enrolled(client),
    )

    assert response.status_code == 409
    assert "port must be a number" in response.text


# ── Leases: an editor window that closed stops counting ─────────────────────


def test_a_lease_lapses_without_a_heartbeat() -> None:
    clock = [1000.0]
    instances = Instances(now=lambda: clock[0])
    instance, token = instances.register(a_claim())

    assert instances.live("clarvis") == [instance]
    clock[0] += LEASE_SECONDS + 1
    assert instances.live("clarvis") == []

    # And a heartbeat brings it back rather than requiring re-registration.
    instances.renew("clarvis", instance.instance_id, token)
    assert instances.live("clarvis") == [instance]


def test_a_lapsed_instance_stays_visible_before_it_is_evicted() -> None:
    """`stale` is a state in §5.1, not a deletion.

    An instance that vanished five seconds ago is information; one that
    vanished an hour ago is clutter.
    """
    from nervis.instances import EVICT_AFTER_SECONDS

    clock = [1000.0]
    instances = Instances(now=lambda: clock[0])
    instances.register(a_claim())

    clock[0] += LEASE_SECONDS + 1
    assert len(instances.all()) == 1
    assert instances.all()[0].is_live(clock[0]) is False

    clock[0] += EVICT_AFTER_SECONDS
    assert instances.all() == []


def test_deregistration_needs_the_instances_own_token() -> None:
    instances = Instances()
    instance, token = instances.register(a_claim())

    with pytest.raises(RegistrationRefusedError):
        instances.deregister("clarvis", instance.instance_id, "guessed")

    instances.deregister("clarvis", instance.instance_id, token)
    assert instances.all() == []


# ── §6.7, enforced structurally rather than by behaviour ────────────────────


def test_no_write_surface_is_declared_for_clarvis() -> None:
    """`CLARVIS.md` §6.7's limits are code that must not exist.

    NERVIS may not approve or refuse gates, invoke tools, expand the workspace
    root, change safety settings, read SecretStorage, or keep Clarvis running.
    None of those is behaviour that could regress at runtime — each would have
    to be *written*, which is why this reads the source instead of calling
    anything.

    NERVIS talks to peers through one function, `peers.reader.read`, over a
    table of declared surfaces. A surface may declare a method, and one does:
    SIRVIS's recommendation endpoint takes a body, which is ordinary. The
    invariant §6.7 needs is narrower and it is this — **no surface declared for
    Clarvis is anything but a read.** Every §6.7 verb would need one.
    """
    from nervis.peers import clarvis as clarvis_peer

    writes = [
        surface.name
        for surface in clarvis_peer.SURFACES
        if getattr(surface, "method", "GET") != "GET"
    ]

    assert writes == [], f"NERVIS declares a write surface on Clarvis: {writes}"


def test_a_registered_instance_is_never_handed_to_an_http_client() -> None:
    """The other half: NERVIS holds instances, and does not call them.

    M8a is the receiving half — registration, leases, redaction. Reading a live
    Bridge is M8b and is blocked on Clarvis building one. Until then an
    `Instance` should reach the listing endpoint and nothing else, and this
    fails the moment somebody wires one into a request without also revisiting
    §6.7.
    """
    package = Path(__file__).parent.parent / "src" / "nervis"
    callers = [
        source.name
        for source in package.rglob("*.py")
        if source.name not in {"instances.py", "app.py"}
        and "Instance" in source.read_text(encoding="utf-8")
        and source.name != "instances.py"
        and "httpx" in source.read_text(encoding="utf-8")
    ]

    assert callers == [], f"an Instance reaches an HTTP client in: {callers}"


def test_no_gate_vocabulary_leaks_into_what_nervis_can_send() -> None:
    """A weaker, broader companion to the test above.

    Greps the package for the verbs §6.7 names. Matching in a comment or a
    docstring is fine and expected — this file and `instances.py` both discuss
    them at length. What it catches is a *call* whose name says it resolves a
    gate.
    """
    forbidden = ("approve_gate", "resolve_gate", "deny_gate", "invoke_tool", "run_command")
    offenders = [
        f"{source.name}: {word}"
        for source in (Path(__file__).parent.parent / "src" / "nervis").rglob("*.py")
        for word in forbidden
        if f"def {word}" in source.read_text(encoding="utf-8")
    ]

    assert offenders == [], f"NERVIS defines a gate-resolving operation: {offenders}"
