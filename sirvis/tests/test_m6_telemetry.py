"""Memory sampling light enough to run around one generation (§11.8).

M1's `detect_system` already reads memory and is the wrong tool at this rate:
§11.8 wants eight readings around a single generation plus a poll while it is in
flight, and a probe that shells out four times per sample would measure itself.

The arithmetic is the part worth pinning. macOS counts inactive, speculative and
purgeable pages as *used* even though all three are reclaimable, so `top`'s
"unused" figure understates available memory badly — RAVIS was once told 619 MB
against 8.6 GB genuinely free. A benchmark that believed that would report
memory pressure that was not there, and §11.8 makes pressure a validity warning.
"""

from __future__ import annotations

import asyncio

import pytest

from sirvis.telemetry import memory
from sirvis.telemetry.memory import MemoryProbe, MemorySample, MemoryWatcher

# Recorded from an Apple Silicon machine: 16 KiB pages, which is the detail an
# assumed page size gets wrong by a factor of four.
VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                1000.
Pages active:                            500000.
Pages inactive:                            2000.
Pages speculative:                          500.
Pages throttled:                              0.
Pages wired down:                        300000.
Pages purgeable:                            100.
"""

SWAP = "total = 3072.00M  used = 1234.50M  free = 1837.50M  (encrypted)\n"


def _probe(monkeypatch: pytest.MonkeyPatch, vm_stat: str | None = VM_STAT,
           swap: str | None = SWAP, memsize: str | None = "17179869184") -> MemoryProbe:
    """A probe reading recorded output rather than this machine.

    Patched at this module's own `_run` rather than at `subprocess`: a guard
    that reaches further than its subject breaks unrelated code, which is how
    the first version of the suite's no-live-runtime fixture broke M1's machine
    detection."""

    def fake(command: list[str]) -> str | None:
        if command[0] == "vm_stat":
            return vm_stat
        if command[-1] == "hw.memsize":
            return memsize
        return swap

    monkeypatch.setattr(memory, "_run", fake)
    return MemoryProbe()


def test_available_memory_counts_every_reclaimable_page_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """free + inactive + speculative + purgeable, at the page size vm_stat
    states. Not `top`'s "unused", which is only the first of the four."""
    sample = _probe(monkeypatch).sample("baseline")

    assert sample.available_bytes == (1000 + 2000 + 500 + 100) * 16384
    assert sample.total_bytes == 17179869184
    assert sample.swap_used_bytes == int(1234.50 * 2**20)


def test_wired_and_compressed_pages_are_not_counted_as_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wired pages cannot be reclaimed, and a large compressor is not pressure —
    it holds compressed pages that are still doing their job."""
    sample = _probe(monkeypatch).sample("baseline")

    assert sample.available_bytes is not None
    assert sample.available_bytes < 300000 * 16384


def test_a_machine_that_will_not_answer_reports_absence_not_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero available memory would read as a machine under extreme pressure,
    which is a claim. `None` is the truth: we could not tell."""
    sample = _probe(monkeypatch, vm_stat=None, memsize=None, swap=None).sample("baseline")

    assert sample.available_bytes is None
    assert sample.is_known is False


def test_swap_is_skipped_when_the_caller_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """The in-flight poll runs four times a second; swap costs a second
    subprocess and §11.8 only asks for it at the labelled points."""
    sample = _probe(monkeypatch).sample("during_generation", include_swap=False)

    assert sample.available_bytes is not None
    assert sample.swap_used_bytes is None


def test_the_page_size_comes_from_the_output_rather_than_an_assumption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An Intel Mac reports 4096. Assuming one or the other is a 4x error in the
    number every reading depends on."""
    intel = VM_STAT.replace("16384", "4096")
    sample = _probe(monkeypatch, vm_stat=intel).sample("baseline")

    assert sample.available_bytes == (1000 + 2000 + 500 + 100) * 4096


async def test_the_watcher_samples_while_something_else_runs() -> None:
    """A peak sampled only before and after a generation is not a peak."""
    readings = iter([900, 400, 700])

    class Falling(MemoryProbe):
        def sample(self, point: str, include_swap: bool = True) -> MemorySample:
            del include_swap
            return MemorySample(point=point, captured_at=0.0,
                                available_bytes=next(readings, 700))

    async with MemoryWatcher(Falling(), interval=0.001) as watcher:
        await asyncio.sleep(0.02)

    assert len(watcher.samples) >= 2
    # The trough of what was free is the peak of what was used.
    assert watcher.lowest_available_bytes == 400


async def test_the_watcher_stops_with_the_generation_it_was_watching() -> None:
    """A stray poller would attribute one run's memory to the next one."""
    watcher = MemoryWatcher(MemoryProbe(), interval=10.0)
    async with watcher:
        pass

    assert watcher._task is None
