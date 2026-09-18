"""What this machine is doing right now (§6, M1).

**Deliberately not SIRVIS's `telemetry/system.py`, and the distinction is the
whole design.** SIRVIS answers *"what is this machine"* — chip, core counts,
installed memory — as an immutable snapshot recorded once and attached to
benchmark results as provenance. NERVIS answers *"what is it doing right now"*,
which is a different question with a different lifetime, and the two may not
even describe the same machine once NERVIS watches a remote peer.

**Sampled on demand, never in a loop.** M1's exit says sampling must not
noticeably load the machine, and the cheapest way to guarantee that is to have
no background sampler at all: a reading is taken when a screen asks for one.
That also makes the number honest — a dashboard showing a value from a timer is
showing whatever the timer last caught, not the state at the moment you looked.

**CPU utilisation is a load average, not a percentage.** A percentage needs two
readings separated by real time, and `psutil.cpu_percent(interval=…)` blocks for
that interval — inside a request handler, on every poll. Load average is already
maintained by the kernel, costs nothing to read, and answers the question people
actually have. Where it is unavailable (Windows before psutil emulates it) the
field is absent rather than zero.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import psutil

# How many processes the list carries. §6 wants a process list, not every
# process: a machine mid-benchmark has several hundred and the interesting ones
# are always at the top of one of the two orderings.
TOP_PROCESSES = 8

# Fields that identify the machine or its user rather than describe its load.
# §5.1's rule, applied here for the same reason it applies to SIRVIS's snapshot:
# a display must be able to label sensitivity and redact.
SENSITIVE_FIELDS: tuple[str, ...] = ("hostname",)

# macOS publishes `NSProcessInfo.thermalState` and nothing else does. The
# numbers are Apple's, not ours.
_THERMAL_QUERY = 'ObjC.import("Foundation"); $.NSProcessInfo.processInfo.thermalState'
THERMAL_STATES = {"0": "nominal", "1": "fair", "2": "serious", "3": "critical"}

# The first `osascript` call in a process pays for loading the JavaScript-ObjC
# bridge. Generous enough to absorb that, short enough that a wedged probe
# cannot stall a dashboard poll.
THERMAL_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class ProcessSample:
    """One process, as a dashboard row.

    `command` is the executable name and never the command line. A command line
    carries file paths, and §15 forbids publishing a raw workspace path — the
    argument list of an editor or a benchmark runner is exactly where one shows
    up.
    """

    pid: int
    command: str
    memory_bytes: int


@dataclass(frozen=True)
class SystemSample:
    """One reading of this machine's load.

    Every field is `| None`-able where the platform may not answer. Absent is a
    real state and reported as such: a zero for "we could not read swap" is a
    number somebody will believe.
    """

    sampled_at: float
    hostname: str | None
    os_description: str
    cpu_count: int | None
    load_average: tuple[float, float, float] | None
    memory_total_bytes: int | None
    memory_available_bytes: int | None
    swap_total_bytes: int | None
    swap_used_bytes: int | None
    disk_total_bytes: int | None
    disk_free_bytes: int | None
    thermal_state: str | None
    processes: tuple[ProcessSample, ...] = field(default_factory=tuple)

    def as_dict(self, *, redact: bool = False) -> dict[str, Any]:
        """The sample as a body, with the sensitive fields optionally withheld.

        `redact` blanks them rather than dropping the keys, so a consumer can
        tell "withheld" from "this platform did not answer" — which is the
        distinction §5.1 asks a display to be able to make.
        """
        body = asdict(self)
        body["processes"] = [asdict(process) for process in self.processes]
        body["sensitive_fields"] = list(SENSITIVE_FIELDS)
        if redact:
            for name in SENSITIVE_FIELDS:
                body[name] = None
        return body


def sample_system(*, processes: int = TOP_PROCESSES) -> SystemSample:
    """One reading, taken now.

    Cheap, but not free, and it does block. The process scan walks every
    process, and on macOS the thermal state is an `osascript` call — tens of
    milliseconds, up to `THERMAL_TIMEOUT_SECONDS` when it wedges. This docstring
    used to say nothing here blocks, and the route believed it: taken inside
    the event loop, a sample made every other NERVIS read wait behind it, which
    the load test measured on 12 September 2026 as `health` going from 3.9 ms to
    203.6 ms with ten readers. The route takes it in a worker thread.
    """
    memory = _memory()
    swap = _swap()
    return SystemSample(
        sampled_at=time.time(),
        hostname=platform.node() or None,
        os_description=_os_description(),
        cpu_count=os.cpu_count(),
        load_average=_load_average(),
        memory_total_bytes=memory[0],
        memory_available_bytes=memory[1],
        swap_total_bytes=swap[0],
        swap_used_bytes=swap[1],
        **_disk(),
        thermal_state=_thermal_state(),
        processes=_processes(processes),
    )


def _memory() -> tuple[int | None, int | None]:
    """Total and available memory, or a pair of absences.

    **The two readings this module took raw (§16 item 11).** Every other probe
    here already degrades — `_load_average`, `_disk` and `_thermal_state` each
    catch and return `None` — and `SystemSample`'s own docstring states why:
    "absent is a real state and reported as such: a zero for 'we could not read
    swap' is a number somebody will believe". These two were called at the top of
    `sample_system` with nothing around them, so a platform refusing either took
    the whole sample down rather than one field of it.

    Reported by an external audit from a sandbox where `psutil.swap_memory()`
    raised. That is not only a sandbox: a hardened profile can deny the same
    call on a real machine, and a control plane that stops reporting its own
    host because one counter is unreadable is exactly the failure this module's
    absence-as-a-value design exists to avoid.
    """
    try:
        reading = psutil.virtual_memory()
    except OSError:
        return None, None
    return reading.total, reading.available


def _swap() -> tuple[int | None, int | None]:
    """Swap total and used, or a pair of absences. See `_memory`."""
    try:
        reading = psutil.swap_memory()
    except OSError:
        return None, None
    return reading.total, reading.used


def _os_description() -> str:
    """A full OS name, because "macOS" alone is not one.

    The column reads `OS` and the value underneath has to be specific enough to
    matter on a bug report — and specific enough to distinguish the three
    platforms this has to run on rather than assuming one of them.
    """
    system = platform.system()
    if system == "Darwin":
        return f"macOS {platform.mac_ver()[0]}".strip()
    if system == "Linux":
        # `platform.freedesktop_os_release` is 3.10+ stdlib and reads the same
        # `/etc/os-release` every distribution publishes. Falling back to the
        # kernel release rather than guessing a distribution name.
        try:
            release = platform.freedesktop_os_release()
        except (OSError, AttributeError):
            return f"Linux {platform.release()}"
        return release.get("PRETTY_NAME") or f"Linux {platform.release()}"
    if system == "Windows":
        return f"Windows {platform.release()} {platform.version()}".strip()
    return f"{system} {platform.release()}".strip()


def _load_average() -> tuple[float, float, float] | None:
    """One, five and fifteen minute load, or None where there is no such thing."""
    try:
        one, five, fifteen = os.getloadavg()
    except (OSError, AttributeError):
        return None
    return (one, five, fifteen)


def _disk() -> dict[str, int | None]:
    """Free space on the volume this installation lives on.

    The root volume rather than every mount: a control plane reporting "disk"
    means the one that fills up and stops it writing, and enumerating mounts
    turns one number into a table nobody asked for.
    """
    try:
        usage = shutil.disk_usage(os.path.abspath(os.sep))
    except OSError:
        return {"disk_total_bytes": None, "disk_free_bytes": None}
    return {"disk_total_bytes": usage.total, "disk_free_bytes": usage.free}


def _thermal_state() -> str | None:
    """macOS thermal pressure, where the platform reports it.

    §6 asks for *basic* thermal, and this is the honest extent of it: macOS
    publishes a pressure level and nothing portable exists elsewhere. Absent
    rather than invented on the other two platforms.

    **`None` is not collapsed into `nominal`.** SIRVIS's first probe did exactly
    that and reported a comfortable machine while it was losing 48% of its
    throughput to heat. "We could not tell" has to stay distinguishable from
    "it was fine" — a benchmark run *this session* was contaminated by thermal
    state going unnoticed, and a fabricated "Nominal" would have hidden it just
    as well as silence did.

    **This is the second copy of these fifteen lines**, the first being
    `sirvis/src/sirvis/telemetry/thermal.py`. Not extracted: `ecosystem_protocol` is the
    wire contract, and putting `osascript` in it would make the one package
    every service depends on platform-specific. Two copies is a cost; a
    protocol package that knows about macOS is a worse one. A third copy is the
    point at which that trade changes.
    """
    if platform.system() == "Linux":
        return linux_thermal_state()
    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", _THERMAL_QUERY],
            capture_output=True,
            text=True,
            timeout=THERMAL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return THERMAL_STATES.get(result.stdout.strip())


# Linux's thermal zones, judged against their own trip points (19 September 2026: this said
# nothing on Linux, bare metal included). The second copy of SIRVIS's
# `sirvis/src/sirvis/telemetry/thermal.py` `linux_thermal_state`, kept apart for the reason
# the macOS reader above is.
LINUX_THERMAL = Path("/sys/class/thermal")
FAIR_MARGIN_MILLIDEGREES = 10_000
_THERMAL_ORDER = ("nominal", "fair", "serious", "critical")


def linux_thermal_state(root: Path = LINUX_THERMAL) -> str | None:
    """The worst of the kernel's thermal zones: `critical` past a critical trip, `serious` past a
    hot or passive one (passive cooling is the CPU being slowed), `fair` within 10 °C of passive,
    else `nominal`. A zone without trip points is skipped; none to judge — most VMs — is None."""
    worst: str | None = None
    for zone in sorted(root.glob("thermal_zone*")):
        state = _zone_state(zone)
        if state is not None and (
            worst is None or _THERMAL_ORDER.index(state) > _THERMAL_ORDER.index(worst)
        ):
            worst = state
    return worst


def _zone_state(zone: Path) -> str | None:
    try:
        temperature = int((zone / "temp").read_text().strip())
    except (OSError, ValueError):
        return None
    trips = _trips(zone)
    if not trips:
        return None
    if "critical" in trips and temperature >= trips["critical"]:
        return "critical"
    if any(kind in trips and temperature >= trips[kind] for kind in ("hot", "passive")):
        return "serious"
    if "passive" in trips and temperature >= trips["passive"] - FAIR_MARGIN_MILLIDEGREES:
        return "fair"
    return "nominal"


def _trips(zone: Path) -> dict[str, int]:
    trips: dict[str, int] = {}
    for kind_file in zone.glob("trip_point_*_type"):
        try:
            kind = kind_file.read_text().strip()
            limit = int(kind_file.with_name(kind_file.name[: -len("type")] + "temp")
                        .read_text().strip())
        except (OSError, ValueError):
            continue
        if limit > 0:
            trips[kind] = min(limit, trips.get(kind, limit))
    return trips


def _processes(limit: int) -> tuple[ProcessSample, ...]:
    """The heaviest processes by memory, which is the one that matters here.

    Memory rather than CPU: this ecosystem's failure mode is a multi-gigabyte
    model resident when something else needs the room, and CPU on a machine
    running an inference server is either idle or pinned.

    **`cpu_percent` used to be reported beside it and is gone.** Nothing read
    it — not the ordering, not the screen, not a test — and psutil's first
    reading for a fresh process object is the average since that process
    started, which is not a useful number to put next to a live memory figure
    even for whoever might have read it.
    """
    found: list[ProcessSample] = []
    for process in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            info = process.info
            memory = info.get("memory_info")
            if memory is None:
                continue
            found.append(
                ProcessSample(
                    pid=int(info["pid"]),
                    command=str(info.get("name") or "?"),
                    memory_bytes=int(memory.rss),
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # A process that exited between the listing and the read, or one
            # this user may not inspect. Both are ordinary on a shared machine
            # and neither is a reason to fail the whole sample.
            continue
    found.sort(key=lambda item: item.memory_bytes, reverse=True)
    return tuple(found[:limit])
