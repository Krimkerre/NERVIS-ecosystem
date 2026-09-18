"""Thermal pressure, on a machine that will not report a temperature (§11.8).

§11.8 makes thermal state part of result validity and asks for it "where
possible". On Apple Silicon that phrase does a lot of work:

- **`pmset -g therm` says nothing.** It is an Intel-era interface, and on this
  hardware it prints "No thermal warning level has been recorded" whatever the
  machine is doing — including while it is throttling hard.
- **Actual temperatures need a private API.** The `AppleARMPMUTempSensor` nodes
  exist in the registry and expose no readable values; °C comes from
  `IOHIDEventSystemClient`, which is undocumented, shifts between macOS
  releases, and is not worth binding a measurement tool to.
- **`NSProcessInfo.thermalState` is documented, unprivileged and works.** Four
  levels, no dependency, no root. It is a pressure reading rather than a
  temperature, which is exactly what §11.8 needs: the question is whether a
  result was taken under duress, not how many degrees it was.

Established rather than assumed: under sustained inference on a fanless
MacBook Air this sat at `nominal` for 64 seconds and moved to `fair` at ~72,
while the same benchmark's throughput fell by nearly half between a rested and a
heat-soaked machine.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

# Apple's `NSProcessInfoThermalState`. Named here rather than passed around as
# integers because these end up in stored evidence, where `2` means nothing to
# whoever reads it later.
THERMAL_STATES = {"0": "nominal", "1": "fair", "2": "serious", "3": "critical"}

NOMINAL = "nominal"

# The first `osascript` call in a process pays for loading the JavaScript-ObjC
# bridge. Generous enough to absorb that, short enough that a wedged probe
# cannot stall a benchmark.
PROBE_TIMEOUT_SECONDS = 10.0

_READ_THERMAL_STATE = 'ObjC.import("Foundation"); $.NSProcessInfo.processInfo.thermalState'


def read_thermal_pressure() -> str | None:
    """The OS's own thermal-pressure level, or None when it will not say.

    `None` is a real answer and is deliberately not collapsed into `nominal`:
    the previous probe did exactly that, reporting a comfortable machine while
    it was losing 48% of its throughput to heat. §11.8 makes this part of
    validity, so "we could not tell" has to stay distinguishable from "it was
    fine".
    """
    if platform.system() == "Linux":
        return linux_thermal_state()
    if platform.system() != "Darwin":
        return None
    output = _run(["osascript", "-l", "JavaScript", "-e", _READ_THERMAL_STATE])
    if output is None:
        return None
    return THERMAL_STATES.get(output.strip())


# Linux's thermal zones (added 19 September 2026: on Linux this module said nothing at all,
# bare metal included, and the owner saw an empty Thermal card in the Ubuntu VM).
LINUX_THERMAL = Path("/sys/class/thermal")
# Within this much of the throttling point, "fair": warm enough to be worth saying before the
# kernel starts slowing the CPU down.
FAIR_MARGIN_MILLIDEGREES = 10_000
_ORDER = ("nominal", "fair", "serious", "critical")


def linux_thermal_state(root: Path = LINUX_THERMAL) -> str | None:
    """The kernel's thermal zones, each judged against its own trip points; the worst wins.

    Linux publishes temperatures (`thermal_zoneN/temp`, millidegrees) and, per zone, the
    temperatures at which it acts (`trip_point_N_type` / `_temp`). Reading a temperature
    against the machine's *own* thresholds says what `NSProcessInfo.thermalState` says on a
    Mac — whether results are being taken under duress — without inventing a limit:
    - at or past a `critical` trip: `critical` (the kernel is about to shut down);
    - at or past `hot` or `passive`: `serious` (passive cooling is the CPU being slowed);
    - within 10 °C of `passive`: `fair`;
    - otherwise `nominal`.
    A zone with no trip points can't be judged and is skipped; a machine with none to judge —
    most VMs — is `None`, "not reported", never a comfortable guess.

    **The second copy**, beside `nervis/src/nervis/telemetry.py`'s. Kept apart for the reason
    the macOS reader is: the protocol package is the wire contract, not a host library.
    """
    worst: str | None = None
    for zone in sorted(root.glob("thermal_zone*")):
        state = _zone_state(zone)
        if state is not None and (worst is None or _ORDER.index(state) > _ORDER.index(worst)):
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
    """A zone's trip points by kind (`passive`, `hot`, `critical`…), the lowest of each."""
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


def is_compromised(state: str | None) -> bool:
    """Whether a reading means the numbers taken under it deserve a warning.

    Unknown is **not** compromised. A machine that declines to report is not
    evidence of heat, and treating it as such would put a warning on every
    result from every platform that does not implement this.
    """
    return state is not None and state != NOMINAL


def _run(command: list[str]) -> str | None:
    """Run one probe, or return None if it will not answer.

    Its own runner rather than the system module's: this one tolerates a much
    longer first call, and a thermal probe that raised would fail a benchmark
    over a diagnostic.
    """
    try:
        finished = subprocess.run(
            command, capture_output=True, text=True,
            timeout=PROBE_TIMEOUT_SECONDS, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return finished.stdout
