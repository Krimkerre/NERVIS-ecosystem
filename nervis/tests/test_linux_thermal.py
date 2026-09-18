"""NERVIS's Linux thermal reading, the copy of SIRVIS's (19 September 2026).

The Live load card's Thermal cell read only macOS, so on Linux it said "not reported" even on
bare metal with thermal zones. Pinned against a tree shaped like `/sys/class/thermal`.
"""

from __future__ import annotations

from pathlib import Path

from nervis.telemetry import linux_thermal_state


def _zone(root: Path, name: str, temp: int, trips: dict[str, int]) -> None:
    zone = root / name
    zone.mkdir(parents=True)
    (zone / "temp").write_text(f"{temp}\n")
    for index, (kind, limit) in enumerate(trips.items()):
        (zone / f"trip_point_{index}_type").write_text(kind + "\n")
        (zone / f"trip_point_{index}_temp").write_text(f"{limit}\n")


def test_zones_are_judged_against_their_own_trip_points_and_the_worst_wins(
    tmp_path: Path,
) -> None:
    _zone(tmp_path, "thermal_zone0", 50_000, {"passive": 90_000, "critical": 105_000})
    assert linux_thermal_state(tmp_path) == "nominal"
    _zone(tmp_path, "thermal_zone1", 91_000, {"passive": 90_000})
    assert linux_thermal_state(tmp_path) == "serious"


def test_nothing_to_judge_is_not_reported(tmp_path: Path) -> None:
    assert linux_thermal_state(tmp_path) is None
    _zone(tmp_path, "thermal_zone0", 99_000, {})
    assert linux_thermal_state(tmp_path) is None
