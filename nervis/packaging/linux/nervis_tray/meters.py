"""CPU and GPU use, read the Linux way — the tray's counterparts of the Mac app's meters.

The Mac reads the kernel's tick counters through `host_statistics` and the graphics driver's
"Device Utilization %" through the I/O registry. Linux publishes the first in `/proc/stat` and
has no single answer for the second, so each driver that states a figure is asked in turn, and a
machine whose driver states none shows no GPU line — never a zero.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class CPUMeter:
    """CPU use across all cores, as a percentage of the time since it was last asked.

    Busy ticks over all ticks between two readings of `/proc/stat`'s first line, which is what
    `top` and every desktop's system monitor draw from. The first reading has nothing to compare
    with, so it gives None rather than a made-up figure.
    """

    def __init__(self, source: Path = Path("/proc/stat")) -> None:
        self.source = source
        self.last: tuple[int, int] | None = None

    def percent(self) -> int | None:
        try:
            fields = self.source.read_text().split("\n", 1)[0].split()
        except OSError:
            return None
        if not fields or fields[0] != "cpu":
            return None
        ticks = [int(one) for one in fields[1:]]
        # user nice system idle iowait irq softirq steal …: idle and iowait are waiting.
        idle = ticks[3] + (ticks[4] if len(ticks) > 4 else 0)
        total = sum(ticks[:8])
        busy = total - idle
        previous, self.last = self.last, (busy, total)
        if previous is None or total <= previous[1]:
            return None
        return round((busy - previous[0]) / (total - previous[1]) * 100)


def gpu_percent(drm: Path = Path("/sys/class/drm")) -> int | None:
    """The busiest GPU's use, where its driver states one; None where none does.

    NVIDIA through `nvidia-smi`, which ships with its driver; AMD (and Intel's newer Xe driver)
    through `gpu_busy_percent` in sysfs, which needs no privilege. Nothing is estimated.
    """
    readings: list[int] = []
    for card in sorted(drm.glob("card*/device/gpu_busy_percent")):
        try:
            readings.append(int(card.read_text().strip()))
        except (OSError, ValueError):
            continue
    if shutil.which("nvidia-smi"):
        try:
            done = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5)
            readings += [int(line) for line in done.stdout.split() if line.strip().isdigit()]
        except (OSError, subprocess.TimeoutExpired):
            pass
    return max(readings) if readings else None
