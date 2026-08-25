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
    cpu_percent: float
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

    Nothing here blocks: every call is a read of something the kernel already
    maintains. That is what makes it safe to take on every dashboard poll.
    """
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return SystemSample(
        sampled_at=time.time(),
        hostname=platform.node() or None,
        os_description=_os_description(),
        cpu_count=os.cpu_count(),
        load_average=_load_average(),
        memory_total_bytes=memory.total,
        memory_available_bytes=memory.available,
        swap_total_bytes=swap.total,
        swap_used_bytes=swap.used,
        **_disk(),
        thermal_state=_thermal_state(),
        processes=_processes(processes),
    )


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


def _processes(limit: int) -> tuple[ProcessSample, ...]:
    """The heaviest processes by memory, which is the one that matters here.

    Memory rather than CPU: this ecosystem's failure mode is a multi-gigabyte
    model resident when something else needs the room, and CPU on a machine
    running an inference server is either idle or pinned. `cpu_percent` is
    reported for each anyway — as the value since that process was last polled,
    which for a fresh process object is since it started.
    """
    found: list[ProcessSample] = []
    for process in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent"]):
        try:
            info = process.info
            memory = info.get("memory_info")
            if memory is None:
                continue
            found.append(
                ProcessSample(
                    pid=int(info["pid"]),
                    command=str(info.get("name") or "?"),
                    cpu_percent=float(info.get("cpu_percent") or 0.0),
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
