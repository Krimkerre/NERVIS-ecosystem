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
    if platform.system() != "Darwin":
        return None
    output = _run(["osascript", "-l", "JavaScript", "-e", _READ_THERMAL_STATE])
    if output is None:
        return None
    return THERMAL_STATES.get(output.strip())


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
