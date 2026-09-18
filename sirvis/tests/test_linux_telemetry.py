"""What SIRVIS reads on Linux: memory and swap, thermal state, the GPU (19 September 2026).

All three were read only the macOS way — `vm_stat`, `sysctl`, `ioreg`, `NSProcessInfo` — so a
Linux machine, bare metal included, showed them as unknown, and the dashboard rendered "null".
The owner saw it in the Ubuntu VM. Each reader here is given a tree shaped like the kernel's own
files, so the rules are pinned on any system this suite runs on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sirvis.telemetry import system
from sirvis.telemetry.memory import meminfo, swap_used
from sirvis.telemetry.thermal import linux_thermal_state

MEMINFO = """MemTotal:       16303520 kB
MemFree:         1203456 kB
MemAvailable:    9876544 kB
SwapTotal:       2097148 kB
SwapFree:        1048574 kB
HugePages_Total:       0
"""


def test_meminfo_is_read_in_bytes(tmp_path: Path) -> None:
    path = tmp_path / "meminfo"
    path.write_text(MEMINFO)

    found = meminfo(path)

    assert found is not None
    assert found["MemAvailable"] == 9876544 * 1024
    assert found["HugePages_Total"] == 0, "a count with no unit stays a count"
    assert swap_used(found) == (2097148 - 1048574) * 1024


def test_no_meminfo_is_none_rather_than_zero(tmp_path: Path) -> None:
    assert meminfo(tmp_path / "absent") is None
    assert swap_used({"SwapTotal": 5}) is None


def _zone(root: Path, name: str, temp: int, trips: dict[str, int]) -> None:
    zone = root / name
    zone.mkdir(parents=True)
    (zone / "temp").write_text(f"{temp}\n")
    for index, (kind, limit) in enumerate(trips.items()):
        (zone / f"trip_point_{index}_type").write_text(kind + "\n")
        (zone / f"trip_point_{index}_temp").write_text(f"{limit}\n")


@pytest.mark.parametrize(
    ("temp", "state"),
    [(50_000, "nominal"), (82_000, "fair"), (90_000, "serious"), (97_000, "serious"),
     (105_000, "critical")],
)
def test_a_zone_is_judged_against_its_own_trip_points(tmp_path: Path, temp: int,
                                                       state: str) -> None:
    _zone(tmp_path, "thermal_zone0", temp, {"passive": 90_000, "hot": 95_000,
                                            "critical": 105_000})
    assert linux_thermal_state(tmp_path) == state


def test_the_worst_zone_wins_and_a_zone_without_trips_is_skipped(tmp_path: Path) -> None:
    _zone(tmp_path, "thermal_zone0", 40_000, {"critical": 100_000})
    _zone(tmp_path, "thermal_zone1", 99_000, {})  # hot, but nothing says hot for it
    _zone(tmp_path, "thermal_zone2", 85_000, {"passive": 90_000})
    assert linux_thermal_state(tmp_path) == "fair"


def test_a_machine_with_nothing_to_judge_is_not_reported(tmp_path: Path) -> None:
    """Most VMs: no zones at all, or none with thresholds. Never a comfortable guess."""
    assert linux_thermal_state(tmp_path) is None
    _zone(tmp_path, "thermal_zone0", 30_000, {})
    assert linux_thermal_state(tmp_path) is None


def _card(drm: Path, name: str, *, vendor: str | None, driver: str | None,
          vram: int | None = None) -> None:
    device = drm / name / "device"
    device.mkdir(parents=True)
    if vendor:
        (device / "vendor").write_text(vendor + "\n")
    if vram is not None:
        (device / "mem_info_vram_total").write_text(f"{vram}\n")
    if driver:
        target = drm / "drivers" / driver
        target.mkdir(parents=True, exist_ok=True)
        (device / "driver").symlink_to(target)


def test_nvidia_smi_names_the_gpu_and_its_memory(monkeypatch: pytest.MonkeyPatch,
                                                 tmp_path: Path) -> None:
    monkeypatch.setattr(system, "_run", lambda _command: "NVIDIA GeForce RTX 4090, 24564")
    assert system._linux_gpu(tmp_path) == ("NVIDIA GeForce RTX 4090", 24564 * 1024 * 1024)


def test_without_nvidia_smi_the_kernel_s_device_names_it(monkeypatch: pytest.MonkeyPatch,
                                                         tmp_path: Path) -> None:
    monkeypatch.setattr(system, "_run", lambda _command: None)
    (tmp_path / "card0-HDMI-A-1").mkdir()  # a connector, not a device
    _card(tmp_path, "card1", vendor="0x1002", driver="amdgpu", vram=17_163_091_968)
    assert system._linux_gpu(tmp_path) == ("AMD GPU (amdgpu)", 17_163_091_968)


def test_a_vm_s_display_adapter_says_it_is_virtual(monkeypatch: pytest.MonkeyPatch,
                                                   tmp_path: Path) -> None:
    monkeypatch.setattr(system, "_run", lambda _command: None)
    _card(tmp_path, "card0", vendor=None, driver="virtio_gpu")
    assert system._linux_gpu(tmp_path) == ("virtual display adapter (virtio_gpu)", None)


def test_no_gpu_at_all_is_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(system, "_run", lambda _command: None)
    assert system._linux_gpu(tmp_path) == (None, None)
