"""The MEP surface, tested against a host that is not any of the three services.

Deliberately mounted on a bare FastAPI app with a made-up service type. If these
tests needed RAVIS to run, the package would not be shared — it would be RAVIS's
router with extra steps, and the first SIRVIS-shaped assumption would go
unnoticed until SIRVIS tripped over it.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from ecosystem_protocol import (
    AVAILABLE,
    PROTOCOL_VERSION,
    UNAVAILABLE,
    Capability,
    EcosystemSurface,
    capability_snapshot,
    is_supported_protocol,
    router,
)

DECLARED = {
    "example.thing@1": Capability(version="1.0.0", state=AVAILABLE),
    "example.other@1": Capability(version="2.1.0", state=UNAVAILABLE, reason="lands at M4"),
}


def _app(**overrides: object) -> FastAPI:
    app = FastAPI()
    app.state.ecosystem = EcosystemSurface(
        service_type="example",
        service_id="example-1",
        machine_id="machine-1",
        build_version="9.9.9",
        declared=DECLARED,
        **overrides,  # type: ignore[arg-type]
    )
    app.include_router(router)
    return app


async def _get(app: FastAPI, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://svc.invalid"
    ) as client:
        return await client.get(path)


async def test_identity_reports_the_host_service_not_a_hardcoded_one() -> None:
    """The bug this package exists to prevent: one service's identity baked in."""
    body = (await _get(_app(), "/ecosystem/identity")).json()

    assert body["service_type"] == "example"
    assert body["build_version"] == "9.9.9"
    assert body["protocol_version"] == PROTOCOL_VERSION


async def test_two_instances_have_different_instance_ids() -> None:
    """§4.1: an instance id distinguishes a restart from a second process.

    Generated rather than configured, because a value an operator can set is a
    value an operator can set twice.
    """
    first = (await _get(_app(), "/ecosystem/identity")).json()
    second = (await _get(_app(), "/ecosystem/identity")).json()

    assert first["instance_id"] != second["instance_id"]


async def test_readiness_is_reported_separately_from_liveness() -> None:
    """A successful TCP connect is not readiness, and the runbook says so."""

    def broken() -> None:
        raise RuntimeError("disk is on fire")

    body = (await _get(_app(checks={"storage": broken}), "/ecosystem/health")).json()

    assert body["live"] is True
    assert body["ready"] is False
    assert body["status"] == "degraded"
    assert body["checks"] == [{"name": "storage", "status": "fail", "detail": "disk is on fire"}]


async def test_one_failing_check_does_not_hide_the_others() -> None:
    """Otherwise an operator fixes the first fault and rediscovers the second."""

    def broken() -> None:
        raise RuntimeError("nope")

    app = _app(checks={"a_fine": lambda: None, "b_broken": broken})
    body = (await _get(app, "/ecosystem/health")).json()

    assert [c["status"] for c in body["checks"]] == ["pass", "fail"]


async def test_a_service_with_nothing_to_verify_is_ready() -> None:
    """An empty check set is a real answer, not a default standing in for one."""
    body = (await _get(_app(), "/ecosystem/health")).json()

    assert body["ready"] is True and body["checks"] == []


async def test_version_answers_without_touching_anything_that_can_break() -> None:
    """§4.1 requires this precisely when the service is unwell.

    Driven with a check that raises, so the endpoint is being asked while the
    service is degraded — which is the only time anyone reads it.
    """

    def broken() -> None:
        raise RuntimeError("everything is down")

    body = (await _get(_app(checks={"x": broken}), "/ecosystem/version")).json()

    assert body["protocol_version"] == PROTOCOL_VERSION
    assert body["compatible_protocol"]["min"] == "1.0.0"


async def test_mounting_without_a_surface_fails_loudly() -> None:
    """A wiring bug must not publish an invented identity to every peer."""
    bare = FastAPI()
    bare.include_router(router)

    with pytest.raises(RuntimeError, match="app.state.ecosystem"):
        await _get(bare, "/ecosystem/identity")


def test_capability_order_is_stable_so_a_revision_means_something() -> None:
    first = capability_snapshot(3, DECLARED)
    second = capability_snapshot(3, dict(reversed(list(DECLARED.items()))))

    assert first == second
    assert [c["id"] for c in first["capabilities"]] == sorted(DECLARED)


def test_an_available_capability_needs_no_excuse_and_others_do() -> None:
    body = capability_snapshot(1, DECLARED)
    reasons = {c["id"]: c["reason"] for c in body["capabilities"]}

    assert reasons["example.thing@1"] == ""
    assert reasons["example.other@1"] == "lands at M4"


@pytest.mark.parametrize(
    ("requested", "supported"),
    [("1.0.0", True), ("1.999.999", True), ("1", True), ("2.0.0", False), ("", False),
     ("banana", False)],
)
def test_only_the_major_decides_compatibility(requested: str, supported: bool) -> None:
    """§4.2: majors must match, minors and patches must not be refused.

    Minor and patch changes are additive by definition and consumers must ignore
    unknown optional fields, so refusing on them breaks compatibility rather
    than protecting it. A peer that cannot state a version at all is refused —
    that is the one not to improvise with.
    """
    assert is_supported_protocol(requested) is supported
