"""The MEP surface, and a mismatched protocol major failing cleanly."""

from __future__ import annotations

from ecosystem_protocol import PROTOCOL_VERSION, is_supported_protocol
from fastapi.testclient import TestClient

from ravis.app import create_app
from ravis.config import Settings


def _client(settings: Settings) -> TestClient:
    """A client speaking to the app through ASGI — no socket, no network."""
    return TestClient(create_app(settings))


def test_health_reports_live_and_ready(settings: Settings) -> None:
    response = _client(settings).get("/ecosystem/health")

    assert response.status_code == 200
    assert response.json()["ready"] is True


def test_health_readiness_is_a_real_check_not_a_constant(settings: Settings) -> None:
    """Runbook §4.1: a successful TCP connect is not readiness."""
    body = _client(settings).get("/ecosystem/health").json()

    assert any(check["name"] == "database" for check in body["checks"])


def test_identity_reports_the_service_type(settings: Settings) -> None:
    body = _client(settings).get("/ecosystem/identity").json()

    assert body["service_type"] == "ravis"


def test_identity_machine_id_is_not_hardware_derived(settings: Settings) -> None:
    """Runbook §4.1 forbids a serial number, MAC address or username."""
    body = _client(settings).get("/ecosystem/identity").json()

    assert len(body["machine_id"]) == 32


def test_version_declares_the_compatible_protocol_range(settings: Settings) -> None:
    body = _client(settings).get("/ecosystem/version").json()

    assert body["compatible_protocol"]["min"] <= PROTOCOL_VERSION


def test_only_conformance_passing_operations_are_advertised(settings: Settings) -> None:
    """RAVIS.md §4.1: never advertise an operation that has not passed conformance.

    This asserted `states == {"unavailable"}` until M9, which was true at M0 and
    quietly stopped being true four milestones later — RAVIS spent Stage 3
    telling peers it could not do things it demonstrably could. The rule was
    never "everything is unavailable"; it is that `available` requires
    conformance. So the assertion is now about the two that have it.
    """
    body = _client(settings).get("/ecosystem/capabilities").json()
    states = {c["id"]: c["state"] for c in body["capabilities"]}

    assert states["ravis.openai_compatible.chat_completions"] == "available"
    # §4.1's condition for this one is *"a translated adapter ships"*, not
    # "conformance passes" — the table sets a bar per capability. Anthropic's
    # adapter shipped at M4 and the local ones at M8. This assertion said
    # `unavailable` for the whole of Stage 5, which is the same drift the
    # docstring above describes, one capability along.
    assert states["ravis.providers.native"] == "available"
    # And the same table read in the other direction. §4.1's condition for
    # virtual profiles is *"profiles are versioned and revisioned"*, and
    # `VirtualModelPool` has neither field. Thirteen pools route, so this is
    # `degraded` rather than `unavailable` — but not `available`, because a peer
    # reading that is entitled to pin a revision that does not exist.
    assert states["ravis.virtual_profiles"] == "degraded"


def test_anything_not_fully_available_says_why(settings: Settings) -> None:
    """A peer disabling a control deserves the reason, not just the refusal.

    Available capabilities need no excuse; everything else must carry one, which
    is the half of §4.1 that keeps `unavailable` from becoming a shrug.
    """
    body = _client(settings).get("/ecosystem/capabilities").json()

    assert all(c["reason"] for c in body["capabilities"] if c["state"] != "available")


def test_a_degraded_capability_is_distinguishable_from_a_missing_one(
    settings: Settings,
) -> None:
    """Partly-shipped is its own state, and collapsing it loses real information.

    `management` has its reads and none of its mutations; `usage_cost` counts
    real traffic and knows no prices. Reporting either as `unavailable` would
    make a peer hide a working screen, and as `available` would make it offer a
    control that does nothing.
    """
    body = _client(settings).get("/ecosystem/capabilities").json()
    states = {c["id"]: c["state"] for c in body["capabilities"]}

    assert states["ravis.management"] == "degraded"
    assert states["ravis.usage_cost"] == "degraded"


def test_a_matching_protocol_major_is_supported() -> None:
    assert is_supported_protocol("1.4.2") is True


def test_a_mismatched_protocol_major_fails_cleanly() -> None:
    """Runbook §4.2: reject an unsupported major structurally, never by guessing."""
    assert is_supported_protocol("2.0.0") is False


def test_responses_carry_a_request_id(settings: Settings) -> None:
    """Runbook §4.3 — created if absent, so a bug report always has one."""
    response = _client(settings).get("/ecosystem/version")

    assert response.headers["X-Request-ID"]


def test_a_supplied_request_id_is_preserved(settings: Settings) -> None:
    """Correlation across services only works if the ID survives the hop."""
    response = _client(settings).get("/ecosystem/version", headers={"X-Request-ID": "abc123"})

    assert response.headers["X-Request-ID"] == "abc123"
