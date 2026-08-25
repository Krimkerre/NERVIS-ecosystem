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

import os
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


# Fields a consumer should treat as personal. A hostname is very often somebody's
# first name — this machine answers "Govert" — and while §5.1 permits it on the
# snapshot it is not the sort of thing to forward without noticing.
SENSITIVE_FIELDS: tuple[str, ...] = ("hostname",)


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

    # §5.1 names "Mac model" as snapshot metadata explicitly. `hw.model` is the
    # board identifier (`Mac17,3`), not the marketing name — the marketing name
    # needs a lookup table that would be wrong the week a new machine ships, and
    # a stale name is worse than an exact identifier.
    model_identifier: str | None = None
    # The machine's own name. §5.1 permits it on the snapshot and forbids it as
    # the *identity* — "never a hostname alone" is about the ID, which stays a
    # locally generated UUID. It is labelled sensitive because §5.1 also
    # requires that transport and display "label sensitivity and support
    # redaction", and a hostname is very often a person's first name. It is here
    # because a benchmark corpus spanning two machines needs something a human
    # recognises, and an opaque UUID is exactly what nobody recognises.
    hostname: str | None = None
    # The operating system as a person names it — "macOS 27.0", "Linux 6.8.0",
    # "Windows 11". Composed rather than left as a bare version, because `27.0`
    # alone is meaningless on a corpus that spans machines, and §5.1 asks for
    # metadata a reader can compare.
    os_description: str | None = None
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
        # §5.1: "transport and display must label sensitivity and support
        # redaction". The label travels with the payload rather than living in a
        # consumer's head, so anything forwarding a snapshot knows which field
        # to drop without having to recognise it by name. Declarative on
        # purpose: a reader that does not understand `hostname` still
        # understands "this key is sensitive".
        body["sensitive_fields"] = list(SENSITIVE_FIELDS)
        return body


def detect_system() -> SystemSnapshot:
    """Read the machine, filling in only what the machine actually reports."""
    system = platform.system()
    machine = platform.machine()
    apple_silicon = system == "Darwin" and machine == "arm64"
    if system != "Darwin":
        return _detect_portable(system, machine)
    return _detect_macos(machine, apple_silicon)


def _os_description(system: str) -> str | None:
    """The operating system as a person would name it.

    `platform.mac_ver()` gives the product version on macOS and
    `platform.release()` the kernel, which are different numbers — 27.0 against
    25.0.0 — and the product version is the one anybody recognises. Elsewhere
    the release *is* the recognisable string.
    """
    if system == "Darwin":
        product = platform.mac_ver()[0]
        return f"macOS {product}" if product else "macOS"
    if system == "Windows":
        release = platform.release()
        return f"Windows {release}" if release else "Windows"
    if system == "Linux":
        # `/etc/os-release` is the distribution's own name for itself and is far
        # more useful than a kernel version. Falling back to the kernel rather
        # than to nothing, because "Linux 6.8.0" still tells a reader more than
        # an empty column.
        pretty = _os_release_name()
        release = platform.release()
        return pretty or (f"Linux {release}" if release else "Linux")
    release = platform.release()
    return f"{system} {release}".strip() or None


def _os_release_name() -> str | None:
    """`PRETTY_NAME` from /etc/os-release, or None when it cannot be read."""
    try:
        with open("/etc/os-release", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"') or None
    except OSError:
        return None
    return None


def _portable_memory_bytes() -> int | None:
    """Physical memory on Linux or Windows, or None where neither answers.

    `sysconf` covers Linux and any other POSIX host. Windows has no sysconf, so
    it takes a `GlobalMemoryStatusEx` call through `ctypes` — still the standard
    library, and a good deal less than adding a dependency for one number.
    """
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        if pages > 0 and size > 0:
            return int(pages) * int(size)
    except (OSError, ValueError, AttributeError):
        pass
    if platform.system() != "Windows":
        return None
    try:
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _Status()
        status.dwLength = ctypes.sizeof(_Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return int(status.ullTotalPhys)
    except Exception:  # noqa: BLE001 - any ctypes failure is simply "unknown"
        return None
    return None


def _detect_portable(system: str, machine: str) -> SystemSnapshot:
    """Linux, Windows and anything else, read with the standard library only.

    This used to return the platform name and nothing else, on the reasoning
    that SIRVIS targets Apple Silicon (§3) and inventing a Linux box's GPU core
    count would be fabrication. The first half is right and the second half
    overshot: refusing to *read* what a machine plainly reports is not the same
    as refusing to guess at what it does not. Cores, memory, disk, hostname and
    the OS name are all available everywhere, and a dashboard that showed a
    Linux machine as entirely unknown would be describing SIRVIS's reticence
    rather than the machine.

    What stays absent stays absent: no chip marketing name, no GPU core count,
    no performance/efficiency split, no thermal state. Those are Apple Silicon
    facts read from `sysctl`, and there is no portable equivalent to read.
    """
    disk_total, disk_free = _disk()
    return SystemSnapshot(
        platform_name=system,
        architecture=machine,
        is_apple_silicon=False,
        hostname=_hostname(),
        os_description=_os_description(system),
        os_version=platform.release() or None,
        cpu_cores=os.cpu_count(),
        unified_memory_bytes=_portable_memory_bytes(),
        memory_available_bytes=MemoryProbe().sample(
            "system", include_swap=False
        ).available_bytes,
        disk_total_bytes=disk_total,
        disk_free_bytes=disk_free,
    )


def _hostname() -> str | None:
    """The machine's name, or None when it will not answer.

    `platform.node()` rather than a subprocess: it is already available, it
    needs no shell, and it returns the same thing `scutil --get LocalHostName`
    does with the `.local` suffix that `socket` adds. The suffix is trimmed
    because it is a Bonjour artefact rather than part of the name a person gave
    the machine.
    """
    name = platform.node().strip()
    if not name:
        return None
    return name[: -len(".local")] if name.endswith(".local") else name


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
        model_identifier=_sysctl_text("hw.model"),
        hostname=_hostname(),
        os_description=_os_description("Darwin"),
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
