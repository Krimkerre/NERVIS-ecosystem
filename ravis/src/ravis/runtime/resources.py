"""How much memory the machine actually has spare.

RAVIS.md §12.3 tracks memory pressure, and M14's acceptance is that pressure
produces a safe route change. Both need a number that means what it says, which
is harder on macOS than it looks.

**The trap, recorded here because it has already been fallen into.** `top`'s
`PhysMem: 23G used … 619M unused` line reads like 619 MB is what remains. It is
not. macOS counts inactive, speculative and purgeable pages as *used* even though
all three are reclaimable on demand, so that figure understates available memory
severely — on one occasion by more than an order of magnitude: 619 MB reported
against 8.6 GB genuinely available, with the system's own
`memory_pressure` reporting 71% free at that same moment.

Getting this wrong is not a harmless misreading. A router that believes memory is
exhausted will refuse to load models it could comfortably run, and will report
its reason confidently.

So availability is summed from the page classes that are actually reclaimable,
and a high compressor figure is deliberately *not* treated as pressure — the
compressor holds compressed pages that are still doing their job.

No third-party dependency: `vm_stat` and `/proc/meminfo` are both already there,
and a psutil dependency for two numbers would not earn itself.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

# Apple silicon uses 16 KiB pages; Intel Macs use 4 KiB. Read from vm_stat's own
# header rather than assumed, because assuming the wrong one is a 4x error in
# the number everything else here depends on.
_DEFAULT_PAGE_SIZE = 4096

# Below this fraction of memory free, prefer routes that load nothing new.
# Deliberately generous: the cost of being wrong in this direction is a slightly
# worse model choice, while being wrong the other way means swapping a laptop.
PRESSURE_THRESHOLD = 0.20


@dataclass(frozen=True)
class MemoryReading:
    """What is available, and whether we could tell.

    `available_bytes` is `None` when the platform could not be read — genuinely
    unknown rather than zero, because zero would read as "no memory at all" and
    make every route decision refuse to load anything (runbook §14.4).
    """

    available_bytes: int | None = None
    total_bytes: int | None = None
    detail: str = ""

    @property
    def is_known(self) -> bool:
        return self.available_bytes is not None and self.total_bytes not in (None, 0)

    @property
    def free_fraction(self) -> float | None:
        """Available memory as a fraction of the total, or None if unknown."""
        if not self.is_known:
            return None
        assert self.available_bytes is not None and self.total_bytes  # narrowed by is_known
        return self.available_bytes / self.total_bytes

    @property
    def under_pressure(self) -> bool:
        """Whether loading a new model would be unwise right now.

        An unknown reading is **not** treated as pressure. Refusing to load
        anything because the platform could not be read would turn a diagnostic
        gap into a routing outage, and the honest response to not knowing is to
        carry on as normal while saying so.
        """
        fraction = self.free_fraction
        return fraction is not None and fraction < PRESSURE_THRESHOLD


def read_memory() -> MemoryReading:
    """Read available memory, by whatever means the platform offers."""
    linux = _read_proc_meminfo()
    if linux.is_known:
        return linux
    return _read_vm_stat()


def _read_proc_meminfo() -> MemoryReading:
    """Linux: `MemAvailable` is exactly this number, already computed."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            fields = dict(_meminfo_pairs(handle.read()))
    except OSError:
        return MemoryReading(detail="/proc/meminfo not readable")
    available, total = fields.get("MemAvailable"), fields.get("MemTotal")
    if available is None or total is None:
        return MemoryReading(detail="/proc/meminfo lacked MemAvailable")
    return MemoryReading(available_bytes=available * 1024, total_bytes=total * 1024)


def _meminfo_pairs(text: str) -> list[tuple[str, int]]:
    pairs = []
    for line in text.splitlines():
        match = re.match(r"(\w+):\s+(\d+)", line)
        if match:
            pairs.append((match.group(1), int(match.group(2))))
    return pairs


def _read_vm_stat() -> MemoryReading:
    """macOS: sum the page classes that are genuinely reclaimable.

    free + inactive + speculative + purgeable. Not `top`'s "unused", which
    excludes the last three and therefore reports a small fraction of the truth.
    """
    try:
        output = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=5, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return MemoryReading(detail="vm_stat unavailable")

    page_size = _page_size_from(output)
    counts = {
        name: int(value)
        for name, value in re.findall(r'"?([A-Za-z][^":]*?)"?:\s+(\d+)', output)
    }
    reclaimable = sum(
        counts.get(key, 0)
        for key in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable")
    )
    resident = sum(
        counts.get(key, 0)
        for key in ("Pages active", "Pages wired down", "Pages occupied by compressor")
    )
    if not reclaimable and not resident:
        return MemoryReading(detail="vm_stat output not understood")
    return MemoryReading(
        available_bytes=reclaimable * page_size,
        total_bytes=(reclaimable + resident) * page_size,
    )


def _page_size_from(output: str) -> int:
    """Read the page size vm_stat announces, rather than assuming one."""
    match = re.search(r"page size of (\d+) bytes", output)
    return int(match.group(1)) if match else _DEFAULT_PAGE_SIZE
