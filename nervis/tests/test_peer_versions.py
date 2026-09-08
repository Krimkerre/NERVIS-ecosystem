"""NERVIS says whether a peer's version is one it supports (§12).

**§12: "Publish compatibility matrices and minimum/maximum peer versions."** The
registry has read every peer's `build_version` since M2, put it on
`/api/v1/services` and compared it to nothing. So the ecosystem shipped four
products on independent cadences with no statement anywhere about which
combinations were supported, and an operator running a NERVIS six minors ahead of
its RAVIS had no way to be told.

**The clause that decides the shape:** *"NERVIS must tolerate peers one supported
minor behind during a rolling upgrade."* Tolerate, not refuse. A peer outside the
range is reported and still used — this is a compatibility statement, not an
admission gate. Refusing would turn an upgrade in the wrong order into an outage,
which is the opposite of what a rolling upgrade is for.
"""

from __future__ import annotations

from typing import Any

import pytest

from nervis.compatibility import SUPPORTED_PEERS, supported


def test_a_peer_at_the_version_we_build_against_is_supported() -> None:
    assert supported("ravis", SUPPORTED_PEERS["ravis"].maximum).supported


def test_a_peer_one_minor_behind_is_supported() -> None:
    """§12's rolling-upgrade clause, in its own test.

    The whole point of the range is that an operator upgrading one service at a
    time is never told the ecosystem is broken because they started with the
    wrong one.
    """
    assert supported("ravis", "0.22.0").supported


def test_a_peer_far_behind_is_reported_and_still_used() -> None:
    """Reported, not refused. §12 says *tolerate*.

    A version check that refused would convert a routine upgrade ordering into
    an outage — and NERVIS has no business deciding that a running RAVIS is
    unusable because a number is low.
    """
    answer = supported("ravis", "0.1.0")
    assert not answer.supported
    assert "0.1.0" in answer.reason
    assert SUPPORTED_PEERS["ravis"].minimum in answer.reason


def test_a_peer_ahead_of_us_is_reported_too() -> None:
    """The direction people forget. A NERVIS that has not been upgraded is as
    much of a mismatch as a RAVIS that has not, and the operator upgrading
    downwards needs the same sentence."""
    answer = supported("ravis", "99.0.0")
    assert not answer.supported
    assert "99.0.0" in answer.reason


def test_a_peer_that_reports_no_version_is_not_called_unsupported() -> None:
    """Absent is not wrong.

    A peer that has not answered yet, or one whose identity call failed, has no
    version — and saying "unsupported" about a number nobody has is the
    confident-wrong-answer failure this repository keeps finding.
    """
    answer = supported("ravis", "")
    assert answer.supported
    assert answer.reason == ""


def test_an_unparseable_version_says_so_rather_than_guessing() -> None:
    answer = supported("ravis", "not-a-version")
    assert not answer.supported
    assert "not-a-version" in answer.reason


def test_every_peer_with_a_window_is_one_nervis_can_actually_see() -> None:
    """A range for a peer that does not exist is a claim about nothing.

    Two surfaces, because peers arrive two ways: RAVIS and SIRVIS are declared
    and probed, and a Clarvis Bridge registers itself. Both report a version and
    both are judged; a window for anything else would apply to nothing.
    """
    from nervis.config import Settings
    from nervis.instances import DYNAMIC_SERVICES
    from nervis.registry import declared_services

    settings = Settings(database_path=":memory:", workspace_path="/tmp", _env_file=None)  # type: ignore[call-arg]
    known = {service.key for service in declared_services(settings)} | set(DYNAMIC_SERVICES)
    assert set(SUPPORTED_PEERS) <= known, set(SUPPORTED_PEERS) - known


@pytest.mark.parametrize("peer", sorted(SUPPORTED_PEERS))
def test_each_range_tolerates_one_minor_behind_its_own_maximum(peer: str) -> None:
    """§12's clause, held for every peer rather than for the one I remembered.

    The minimum must be at least a minor below the maximum, or the range cannot
    contain the rolling upgrade it exists to permit.
    """
    window = SUPPORTED_PEERS[peer]
    low = tuple(int(part) for part in window.minimum.split("."))
    high = tuple(int(part) for part in window.maximum.split("."))
    assert low < high, f"{peer}: {window.minimum} is not below {window.maximum}"
    assert low[0] == high[0], f"{peer}: a range spanning majors is not one minor behind"
    assert high[1] - low[1] >= 1, f"{peer}: {window.minimum}..{window.maximum} is under a minor"


def an_api() -> Any:
    """A real NERVIS, the way the other suites build one."""
    from fastapi.testclient import TestClient

    from nervis.app import create_app
    from nervis.config import Settings

    settings = Settings(  # type: ignore[call-arg]
        database_path=":memory:", workspace_path="/tmp",
        served_hosts=["127.0.0.1", "localhost", "::1", "testserver"],
        _env_file=None,
    )
    return TestClient(create_app(settings))


def test_the_registry_reports_it_on_the_services_surface() -> None:
    """The declaration has a reader, which is the point.

    A supported-version table nothing consults is the defect this ecosystem has
    found six times — a value computed correctly and applied nowhere. It is on
    every row of `/api/v1/services`, beside the version it judges.
    """
    with an_api() as client:
        answered = client.get("/api/v1/services")

    assert answered.status_code == 200
    rows = {row["key"]: row for row in answered.json()["items"]}
    # The declared peers only: a Bridge is judged on the instances surface it
    # registers into, which `test_the_instance_row_carries_the_version_and_the_verdict`
    # covers.
    for peer in set(SUPPORTED_PEERS) & set(rows):
        assert "peer_supported" in rows[peer], f"{peer} row carries no compatibility answer"
        assert "peer_support_detail" in rows[peer]


# ── The Bridge's own version, once it publishes one ─────────────────────────


def test_a_bridge_may_claim_its_build_version() -> None:
    """§12's window needs a number, and the Bridge published none.

    Every other peer states its version on `/ecosystem/identity`; a Clarvis
    Bridge is an extension host rather than a service and registers instead, so
    the claim is where its version has to arrive. `CLAIMABLE` is a closed
    allowlist — the field does not exist until it is on it.
    """
    from nervis.instances import CLAIMABLE

    assert "build_version" in CLAIMABLE


def test_the_claimed_version_is_judged_like_any_other_peer() -> None:
    from nervis.compatibility import SUPPORTED_PEERS, supported

    assert "clarvis" in SUPPORTED_PEERS
    assert supported("clarvis", SUPPORTED_PEERS["clarvis"].maximum).supported
    assert not supported("clarvis", "0.1.0").supported


def test_a_bridge_registering_without_one_is_not_called_unsupported() -> None:
    """Older Bridges exist and keep working.

    A Bridge built before this field registers exactly as it always did, and an
    absent version is not a mismatch — saying "unsupported" about a number
    nobody sent is the confident-wrong-answer failure again.
    """
    from nervis.compatibility import supported

    assert supported("clarvis", "").supported


def test_the_instance_row_carries_the_version_and_the_verdict() -> None:
    """Registered, stored, published, judged — the whole path.

    A version accepted by the allowlist and dropped before the surface would be
    the same defect one step later: a value read correctly and applied nowhere.
    """
    from nervis.instances import Instance

    instance = Instance(service="clarvis", instance_id="i1", base_url="http://127.0.0.1:1",
                        build_version="0.1.0")
    row = instance.as_dict(now=instance.renewed_at)

    assert row["build_version"] == "0.1.0"
    assert row["peer_supported"] is False
    assert "0.1.0" in row["peer_support_detail"]
