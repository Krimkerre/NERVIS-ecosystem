"""M1 — machine detection, and the discipline of admitting a gap.

    Immutable snapshot persisted; a missing metric returns Unknown rather than
    a fabricated value; system endpoint works.

The middle clause is the one with teeth. Every benchmark result references one
of these snapshots, so a field invented here becomes a fact in evidence RAVIS
routes on, and nothing downstream can catch the invention.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from sirvis.app import create_app
from sirvis.config import Settings
from sirvis.core.machine import (
    latest_snapshot,
    machine_identity,
    record_snapshot,
    reset_machine_identity,
)
from sirvis.storage import prepare_database
from sirvis.telemetry import SystemSnapshot, detect_system


def test_an_unreadable_metric_is_unknown_rather_than_invented() -> None:
    """M1's exit, stated as the property rather than as a platform.

    A snapshot from a machine that reports nothing must be all-unknown and must
    say so, rather than defaulting to zeros that a reader would take for
    measurements.
    """
    bare = SystemSnapshot(platform_name="Linux", architecture="x86_64", is_apple_silicon=False)

    assert bare.chip is None
    assert bare.unified_memory_bytes is None
    assert "chip" in bare.unknown_fields
    assert "unified_memory_bytes" in bare.unknown_fields


def test_a_snapshot_publishes_which_fields_it_could_not_read() -> None:
    """A consumer judging comparability needs the gaps, not just the values."""
    partial = SystemSnapshot(
        platform_name="Darwin", architecture="arm64", is_apple_silicon=True, chip="Apple M5"
    )

    body = partial.as_dict()

    assert body["chip"] == "Apple M5"
    assert "gpu_cores" in body["unknown_fields"]
    assert "chip" not in body["unknown_fields"]


def test_detection_never_raises_on_this_machine() -> None:
    """Detection runs at startup, so a probe that throws is a service that will
    not start — and M0's exit says it must start anywhere."""
    snapshot = detect_system()

    assert snapshot.platform_name
    assert snapshot.architecture


def test_the_machine_identity_is_opaque_and_stable() -> None:
    """§5.1: a locally generated UUID, never a hostname or a fingerprint."""
    database = prepare_database(":memory:")

    first = machine_identity(database)
    again = machine_identity(database)

    assert first == again
    assert len(first) == 32 and first.isalnum()


def test_resetting_the_identity_leaves_old_snapshots_alone() -> None:
    """§5.1 requires resettable. Rewriting history to tidy up a reset would
    falsify the results that cited the old machine."""
    database = prepare_database(":memory:")
    original = machine_identity(database)
    record_snapshot(database, detect_system())

    replacement = reset_machine_identity(database)

    assert replacement != original
    assert latest_snapshot(database, original) is not None
    assert latest_snapshot(database, replacement) is None


def test_snapshots_are_appended_never_overwritten() -> None:
    """Immutable, because a benchmark cites one and the citation must keep
    meaning what it meant."""
    database = prepare_database(":memory:")

    first = record_snapshot(database, detect_system())
    second = record_snapshot(database, detect_system())
    rows = database.connection.execute("SELECT COUNT(*) AS n FROM machine_snapshot").fetchone()

    assert first["snapshot_id"] != second["snapshot_id"]
    assert rows["n"] == 2


def test_the_system_endpoint_answers_and_records(settings: Settings) -> None:
    client = TestClient(create_app(settings))

    body = client.get("/api/v1/system").json()

    assert body["snapshot_id"]
    assert body["platform_name"]
    assert "unknown_fields" in body


def test_a_machine_with_no_snapshot_yet_is_absent_not_missing(settings: Settings) -> None:
    """`snapshot: null` rather than a 404: the machine exists, nobody has
    captured it (runbook §14.4)."""
    client = TestClient(create_app(settings))

    body = client.get("/api/v1/machines/some-other-machine").json()

    assert body["snapshot"] is None
    assert body["is_local_machine"] is False


def test_available_memory_is_read_and_kept_apart_from_capacity() -> None:
    """Reclaimable memory, in the same class as free disk and used swap.

    Added for the System screen, which needed a live "available now" figure and
    had been inventing one. It belongs on the snapshot rather than on a separate
    endpoint because `/api/v1/system` already detects on read and already
    carries volatile fields — free disk, used swap, thermal state — and §11.8
    judges a result's validity on exactly those.

    Capacity and availability are asserted separately on purpose: the machine
    has its unified memory whatever is running, and how much of it is free is a
    different claim about a different moment.
    """
    snapshot = detect_system()

    if not snapshot.is_apple_silicon:  # pragma: no cover - CI is Linux
        return
    assert snapshot.unified_memory_bytes, "capacity is a property of the machine"
    assert snapshot.memory_available_bytes is not None, "availability is read live"
    assert 0 < snapshot.memory_available_bytes <= snapshot.unified_memory_bytes


def test_available_memory_is_absent_rather_than_zero_when_unreadable() -> None:
    """`None` and `0` are different claims — one is ignorance, the other is a
    machine with no reclaimable memory left, which is a crisis worth seeing."""
    snapshot = SystemSnapshot(platform_name="Linux", architecture="x86_64", is_apple_silicon=False)

    assert snapshot.memory_available_bytes is None
    assert "memory_available_bytes" in snapshot.as_dict()


def test_the_hostname_is_recorded_but_labelled_sensitive() -> None:
    """§5.1 permits a hostname on the snapshot and forbids it as the identity.

    "Never a hostname alone" governs the machine *ID*, which stays a locally
    generated UUID. The same section requires that transport and display "label
    sensitivity and support redaction", so the label travels in the payload
    rather than living in a consumer's head — anything forwarding a snapshot
    knows which key to drop without having to recognise it by name.

    It is worth recording at all because a corpus spanning two machines needs
    something a person recognises, and an opaque UUID is precisely what nobody
    does.
    """
    payload = detect_system().as_dict()

    assert payload["sensitive_fields"] == ["hostname"]
    assert "hostname" in payload


def test_the_machine_id_is_not_derived_from_the_hostname() -> None:
    """The two coexist and must not be confused: one is opaque and resettable,
    the other is a name somebody chose."""
    database = prepare_database(":memory:")
    snapshot = detect_system()

    identity = machine_identity(database)

    assert identity != snapshot.hostname
    assert len(identity) == 32 and identity.isalnum()
