"""Memory sampling light enough to run eight times around one generation (§11.8).

M1's `detect_system()` already reads memory, and it is the wrong tool here: it
shells out to `sysctl`, `df`, `system_profiler` and `pmset` to build a whole
`SystemSnapshot`, which costs hundreds of milliseconds. §11.8 wants a reading at
baseline, after each load, before generation, at peak prompt, at peak generation,
post-run and post-unload — plus a poll *during* generation, because a peak that
is only sampled at the end is not a peak. A snapshot-weight probe at that rate
would measure the observer.

So this reads one thing, cheaply: `vm_stat`, once per sample, with the page size
taken from its own header and the machine's total memory read once and cached
because it does not change. Swap is a second call and is therefore optional —
the labelled points take it, the high-frequency poll does not.

**The trap this shares with RAVIS, restated because it is not obvious.** `top`'s
`PhysMem … unused` figure is *not* available memory: macOS counts inactive,
speculative and purgeable pages as used even though all three are reclaimable on
demand. RAVIS was once told 619 MB was free while 8.6 GB genuinely was. The
same arithmetic is used here — free + inactive + speculative + purgeable — and
it is duplicated rather than imported, because runbook §3 permits services to
share only the protocol package's transport types. Two services reading the same
machine is not a reason to couple their memory probes.

Nothing here fabricates. A platform that will not answer produces `None`, which
is a different thing from zero: zero available memory would make a benchmark
look like it ran under extreme pressure, and §11.8 makes memory pressure a
validity warning.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import subprocess
import time
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Callable

# vm_stat states its own page size in its header. Assuming one is a 4x error on
# Apple Silicon, which uses 16 KiB pages against Intel's 4 KiB — and the number
# every reading here depends on.
DEFAULT_PAGE_SIZE = 4096

# A sample that has not answered in this long is not going to be useful: the
# whole point is that it costs less than the thing being measured.
PROBE_TIMEOUT_SECONDS = 2.0

# How often the watcher polls while a generation is in flight. Four times a
# second is enough to catch the peak of a load-and-generate cycle measured in
# seconds, and cheap enough — one `vm_stat` is a few milliseconds — that it does
# not compete with the inference for CPU.
DEFAULT_POLL_SECONDS = 0.25

# §11.8's named points. Strings rather than an enum because they are written
# into stored telemetry and read back by consumers that do not import this
# module, and a string that survives serialisation is the honest shape.
BASELINE = "baseline"
AFTER_LOAD = "after_load"
BEFORE_GENERATION = "before_generation"
DURING_GENERATION = "during_generation"
POST_RUN = "post_run"
POST_UNLOAD = "post_unload"

# The page classes macOS will reclaim on demand. `wired` and `compressor` are
# deliberately absent: wired pages cannot be reclaimed, and a large compressor
# is not pressure — it holds compressed pages that are still doing their job.
_RECLAIMABLE = ("free", "inactive", "speculative", "purgeable")


@dataclass(frozen=True)
class MemorySample:
    """One reading, labelled with the moment in the lifecycle it was taken at.

    `available_bytes` is `None` when the platform could not be read. Absence
    rather than zero, for the same reason M1's snapshot leaves gaps visible: a
    consumer deciding whether a result is comparable needs to know the
    difference between "the machine was out of memory" and "we could not tell".
    """

    point: str
    captured_at: float
    available_bytes: int | None = None
    total_bytes: int | None = None
    swap_used_bytes: int | None = None

    @property
    def is_known(self) -> bool:
        return self.available_bytes is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "point": self.point,
            "captured_at": self.captured_at,
            "available_bytes": self.available_bytes,
            "total_bytes": self.total_bytes,
            "swap_used_bytes": self.swap_used_bytes,
        }


class MemoryProbe:
    """Reads available memory and swap, cheaply, on demand.

    Holds two pieces of cached state and no more: the page size and the total
    physical memory, both of which are properties of the machine rather than of
    the moment. Everything else is read fresh, because a cached *reading* would
    be a benchmark reporting memory conditions from some earlier point in the
    run.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._page_size: int | None = None
        self._total_bytes: int | None = None
        self._total_read = False

    def sample(self, point: str, include_swap: bool = True) -> MemorySample:
        """One reading at a named point in the lifecycle.

        `include_swap` is not an optimisation knob for its own sake: swap costs
        a second subprocess, and the in-flight poll runs four times a second
        while the labelled points run seven times in a whole experiment. §11.8
        asks for swap at baseline, peak and final, which are all labelled
        points — so the poll can skip it without losing anything asked for.
        """
        available = self._read_available()
        return MemorySample(
            point=point,
            captured_at=self._clock(),
            available_bytes=available,
            total_bytes=self._total(),
            swap_used_bytes=self._read_swap() if include_swap else None,
        )

    # ── Reading the machine ──────────────────────────────────────────────────

    def _read_available(self) -> int | None:
        """Reclaimable memory in bytes, or None when `vm_stat` will not answer."""
        output = _run(["vm_stat"])
        if output is None:
            return None
        pages = _vm_stat_pages(output)
        if not pages:
            return None
        size = self._resolve_page_size(output)
        reclaimable = sum(pages.get(name, 0) for name in _RECLAIMABLE)
        return reclaimable * size

    def _resolve_page_size(self, output: str) -> int:
        """The page size vm_stat reports, read once and remembered."""
        if self._page_size is None:
            match = re.search(r"page size of (\d+) bytes", output)
            self._page_size = int(match.group(1)) if match else DEFAULT_PAGE_SIZE
        return self._page_size

    def _total(self) -> int | None:
        """Physical memory, read once. It does not change while we run."""
        if not self._total_read:
            self._total_read = True
            text = _run(["sysctl", "-n", "hw.memsize"])
            self._total_bytes = int(text.strip()) if text and text.strip().isdigit() else None
        return self._total_bytes

    def _read_swap(self) -> int | None:
        """Swap in use, in bytes.

        Swap is what turns a benchmark result into a lie about the model: a run
        that swapped measured the disk as much as the inference, which is why
        §11.8 makes it a validity warning rather than a footnote.
        """
        return _parse_swap_used(_run(["sysctl", "-n", "vm.swapusage"]))


class MemoryWatcher:
    """Polls memory while something else runs, so a peak can be observed at all.

    An async context manager rather than a thread: everything around it in the
    benchmark engine is already async, and a peak that is sampled only before
    and after a generation is not a peak — it is two endpoints with the
    interesting part missing.

    The task is cancelled and awaited on exit, so a watcher never outlives the
    generation it was watching. A stray poller would attribute one run's memory
    to the next one.
    """

    def __init__(
        self,
        probe: MemoryProbe,
        point: str = DURING_GENERATION,
        interval: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        self._probe = probe
        self._point = point
        self._interval = interval
        self._task: asyncio.Task[None] | None = None
        self.samples: list[MemorySample] = []

    async def __aenter__(self) -> MemoryWatcher:
        self._task = asyncio.create_task(self._poll())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._task is not None:
            self._task.cancel()
            # Expected: cancellation is how the poll stops. Suppressed rather
            # than propagated, because the generation the watcher was observing
            # succeeded or failed on its own terms, and a cancellation raised
            # from the telemetry would replace that outcome with an unrelated
            # one.
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    @property
    def lowest_available_bytes(self) -> int | None:
        """The peak of memory *use*, expressed as the trough of what was free.

        None when nothing could be read, rather than zero — the same rule as
        the samples themselves.
        """
        known = [s.available_bytes for s in self.samples if s.available_bytes is not None]
        return min(known) if known else None

    async def _poll(self) -> None:
        while True:
            # Sampled before the first sleep so a generation shorter than one
            # interval still produces a reading rather than an empty list.
            self.samples.append(self._probe.sample(self._point, include_swap=False))
            await asyncio.sleep(self._interval)


def _vm_stat_pages(output: str) -> dict[str, int]:
    """Page counts by class, from vm_stat's `Pages free: 12345.` lines."""
    pages: dict[str, int] = {}
    for line in output.splitlines():
        match = re.match(r"Pages? ([\w\s-]+?):\s+(\d+)\.", line)
        if match:
            # "Pages wired down" and "Pages free" both key on their first word,
            # which is all any caller here asks for.
            pages[match.group(1).split()[0].lower()] = int(match.group(2))
    return pages


def _parse_swap_used(text: str | None) -> int | None:
    """`total = 3072.00M  used = 1234.50M  free = …` → bytes used."""
    if not text:
        return None
    match = re.search(r"used\s*=\s*([\d.]+)([KMG])", text)
    if not match:
        return None
    scale = {"K": 2**10, "M": 2**20, "G": 2**30}[match.group(2)]
    return int(float(match.group(1)) * scale)


def _run(command: list[str]) -> str | None:
    """Run one short command, or return None if it will not answer.

    Never raises. A memory probe that could fail a benchmark would make the
    telemetry more dangerous than the thing it is measuring.
    """
    try:
        finished = subprocess.run(
            command, capture_output=True, text=True,
            timeout=PROBE_TIMEOUT_SECONDS, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return finished.stdout
