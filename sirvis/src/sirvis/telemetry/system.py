"""Detecting the machine, and admitting what could not be detected.

SIRVIS.md §5.1 and M1's exit criterion say the same thing from two directions:
hardware metadata is attached to the *snapshot* rather than encoded in the
machine ID, and **a missing metric returns Unknown rather than a fabricated
value.** Both matter for the same reason. Every benchmark result references one
of these snapshots, so a field invented here becomes a fact in evidence RAVIS
routes on, and there is no later stage at which the invention gets caught.

`None` is Unknown throughout. Not zero, not an empty string, not a guess from a
neighbouring value — §12.1's provenance invariant works only if the absence of a
measurement is visibly an absence.

No third-party dependency. Everything here comes from tools macOS already has,
read through `subprocess` with a short timeout, because a detection step that
hangs makes the service unstartable and M0's exit says it must start anywhere.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import Any

from sirvis.telemetry.memory import MemoryProbe
from sirvis.telemetry.thermal import read_thermal_pressure

# Long enough for a cold `system_profiler`-class call, short enough that a wedged
# tool cannot stop the service starting.
PROBE_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class SystemSnapshot:
    """What this machine is, at one moment, with gaps left visible.

    Frozen because §5.1 requires benchmarks to reference an *immutable* snapshot.
    A snapshot that could be edited after a result cited it would let the
    machine's description drift away from the conditions the result was measured
    under, which is the one thing the reference exists to prevent.
    """

    platform_name: str
    architecture: str
    # Apple Silicon is the supported target; anything else is recorded honestly
    # rather than rejected, because a snapshot from an unsupported machine is
    # still the truth about where a number came from.
    is_apple_silicon: bool

    chip: str | None = None
    cpu_cores: int | None = None
    performance_cores: int | None = None
    efficiency_cores: int | None = None
    gpu_cores: int | None = None
    unified_memory_bytes: int | None = None
    os_version: str | None = None
    disk_total_bytes: int | None = None
    disk_free_bytes: int | None = None
    swap_total_bytes: int | None = None
    swap_used_bytes: int | None = None
    # Reclaimable memory at the moment of the read. In the same class as
    # `disk_free_bytes` and `swap_used_bytes` above — volatile, and recorded
    # because §11.8 uses exactly these to judge whether a result is comparable.
    # Kept out of the *hardware* fields deliberately: the machine has 24 GB
    # whatever is running, and how much of it is free is a different claim.
    memory_available_bytes: int | None = None
    # `nominal`, or whatever the OS reports. None when the OS declines to say —
    # which is different from "cool", and §11.8 makes thermal state part of
    # result validity, so the difference has to survive.
    thermal_state: str | None = None

    @property
    def unknown_fields(self) -> list[str]:
        """Every metric this machine would not report.

        Published rather than merely tolerated: a consumer deciding whether a
        result is comparable needs to know what was not measured, and a snapshot
        that hides its gaps invites the reader to assume there were none.
        """
        return sorted(name for name, value in asdict(self).items() if value is None)

    def as_dict(self) -> dict[str, Any]:
        body = asdict(self)
        body["unknown_fields"] = self.unknown_fields
        return body


def detect_system() -> SystemSnapshot:
    """Read the machine, filling in only what the machine actually reports."""
    system = platform.system()
    machine = platform.machine()
    apple_silicon = system == "Darwin" and machine == "arm64"
    if system != "Darwin":
        # Non-macOS is recorded rather than guessed at. SIRVIS targets Apple
        # Silicon (§3); pretending to know a Linux box's GPU core count from
        # nothing would be exactly the fabrication M1's exit forbids.
        return SystemSnapshot(
            platform_name=system, architecture=machine, is_apple_silicon=False
        )
    return _detect_macos(machine, apple_silicon)


def _detect_macos(machine: str, apple_silicon: bool) -> SystemSnapshot:
    """Every macOS metric, each read independently so one gap is only one gap."""
    disk_total, disk_free = _disk()
    swap_total, swap_used = _swap()
    # The same probe the benchmark engine samples with, rather than a second
    # reader of `vm_stat` that could disagree with it about what "available"
    # means (§11.8 counts reclaimable pages, not free ones).
    available = MemoryProbe().sample("system", include_swap=False).available_bytes
    return SystemSnapshot(
        platform_name="Darwin",
        architecture=machine,
        is_apple_silicon=apple_silicon,
        chip=_sysctl_text("machdep.cpu.brand_string"),
        cpu_cores=_sysctl_int("hw.ncpu"),
        # perflevel0 is the performance cluster and perflevel1 the efficiency
        # one. Absent on Intel, which is why they are read separately from
        # hw.ncpu rather than derived from it.
        performance_cores=_sysctl_int("hw.perflevel0.logicalcpu"),
        efficiency_cores=_sysctl_int("hw.perflevel1.logicalcpu"),
        gpu_cores=_gpu_cores(),
        unified_memory_bytes=_sysctl_int("hw.memsize"),
        os_version=_run(["sw_vers", "-productVersion"]),
        disk_total_bytes=disk_total,
        disk_free_bytes=disk_free,
        swap_total_bytes=swap_total,
        swap_used_bytes=swap_used,
        memory_available_bytes=available,
        thermal_state=_thermal_state(),
    )


def _run(command: list[str]) -> str | None:
    """One probe, or None when it is unavailable or does not answer.

    Every failure mode collapses to None on purpose: a missing binary, a
    non-zero exit and a timeout are all "this machine did not tell us", and the
    caller's job is to record that rather than to distinguish them.
    """
    if shutil.which(command[0]) is None:
        return None
    try:
        finished = subprocess.run(
            command, capture_output=True, text=True,
            timeout=PROBE_TIMEOUT_SECONDS, check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    answer = finished.stdout.strip()
    return answer or None


def _sysctl_text(key: str) -> str | None:
    return _run(["sysctl", "-n", key])


def _sysctl_int(key: str) -> int | None:
    """A numeric sysctl, or None when it is absent or not a number."""
    raw = _sysctl_text(key)
    return int(raw) if raw and raw.isdigit() else None


def _gpu_cores() -> int | None:
    """GPU core count from the accelerator's IORegistry entry.

    §5.1 asks for GPU cores "where available", and this is the unprivileged way
    to get them on Apple Silicon — `system_profiler` also knows but takes about
    a second, which is too long for something that runs at startup.
    """
    output = _run(["ioreg", "-rc", "AGXAccelerator", "-d", "1"])
    if not output:
        return None
    found = re.search(r'"gpu-core-count"\s*=\s*(\d+)', output)
    return int(found.group(1)) if found else None


def _disk() -> tuple[int | None, int | None]:
    """Capacity and free space on the boot volume.

    `shutil.disk_usage` rather than parsing `df`: it is stdlib, it returns bytes
    already, and there is no output format to misread.
    """
    try:
        usage = shutil.disk_usage("/")
    except OSError:
        return None, None
    return usage.total, usage.free


def _swap() -> tuple[int | None, int | None]:
    """Swap totals from `vm.swapusage`, which reports megabytes as text.

    Swap is here because §11.8 makes it a validity signal rather than trivia: a
    benchmark that swapped measured the disk as much as the model, and a result
    that cannot say whether it did is not comparable with one that can.
    """
    raw = _sysctl_text("vm.swapusage")
    if not raw:
        return None, None
    values = dict(re.findall(r"(total|used)\s*=\s*([\d.]+)M", raw))
    return _megabytes(values.get("total")), _megabytes(values.get("used"))


def _megabytes(value: str | None) -> int | None:
    return int(float(value) * 1024 * 1024) if value else None


def _thermal_state() -> str | None:
    """What the OS says about thermal pressure, or None when it says nothing.

    `pmset -g therm` is unprivileged and reports warning levels; `powermetrics`
    knows more and needs root, which a local service must not ask for.

    **"No thermal warning level has been recorded" is reported as `nominal`, and
    that is a claim worth being careful about.** It means the OS has not raised
    a warning — not that the machine is cool. §11.8 treats thermal state as part
    of result validity, so the distinction between "nothing recorded" and "known
    fine" matters, and this returns the weaker of the two readings.
    """
    # The documented API first, because `pmset` lies by omission on Apple
    # Silicon — see `thermal.py`. This machine reported `nominal` through a 48%
    # thermal throttle on the strength of the string matched below.
    pressure = read_thermal_pressure()
    if pressure is not None:
        return pressure
    output = _run(["pmset", "-g", "therm"])
    if not output:
        return None
    if "No thermal warning level has been recorded" in output:
        # Not `nominal`: it means no warning was *recorded*, which on hardware
        # that never records one is no information at all.
        return None
    found = re.search(r"CPU_Scheduler_Limit\s*=\s*(\d+)", output)
    if found:
        return "nominal" if found.group(1) == "100" else f"limited to {found.group(1)}%"
    return "unknown"
