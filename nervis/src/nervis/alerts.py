"""The rest of §18's notices: what NERVIS files a note about besides a service changing state.

§18 lists service offline, benchmark finished, budget threshold, route-failure spike,
memory pressure, swap warning and *Clarvis waiting for approval*, and says **keep
notification volume low**. Until 16 September 2026 only the first had a producer. This
module is the rest, and it decides only *whether* something is worth a note; the note
itself goes through `notifications.post`, with the reason its rules require.

**Two feeds, both already running.** Events arrive from the hub as they are stored —
benchmarks, request outcomes, Clarvis's gates. Readings arrive on the registry's probe
timer — RAVIS's budget band and its memory reading, and this machine's swap. Nothing here
asks a service anything of its own.

**Low volume is a rule in each trigger, not a filter at the end.** A trigger files once
when its condition begins and not again until the condition has ended (a memory reading
back to normal, a budget back below the band) or, for the spike, until a quiet period has
passed. A benchmark files once per run, and a Clarvis gate once per wait, and only if the
wait outlasts the first two minutes — the status line already shows a gate the owner is
looking at.

State is in memory: a restart forgets what was noted, which at worst files one note again.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

# A route-failure spike: at least this many failures in the window, and at least half of
# what finished in it. Both, so a single broken request on a quiet evening is not a spike
# and a busy hour with a handful of failures among hundreds is not one either.
SPIKE_WINDOW_SECONDS = 300.0
SPIKE_MIN_FAILURES = 5
SPIKE_SHARE = 0.5
# After a spike note, another only once this has passed.
SPIKE_QUIET_SECONDS = 1800.0

# How long a Clarvis gate may wait before it is worth a note, and its kinds in words.
GATE_GRACE_SECONDS = 120.0
GATE_WORDS = {
    "command": "a command's approval",
    "sensitive_read": "an approval to read a sensitive file",
    "step": "a step's approval",
}

# Swap growing by this much within the window is a warning: the machine is paging hard.
# A growth rather than a level, because macOS sizes swap on demand and a large amount
# already in use says little about now.
SWAP_GROWTH_BYTES = 2 * 1024**3
SWAP_WINDOW_SECONDS = 600.0
SWAP_QUIET_SECONDS = 3600.0

# RAVIS's budget bands (§14), lowest first, and how each reads in a note.
BUDGET_BANDS = ("NORMAL", "PREFER_CHEAPER", "STRONG_PENALTY", "EXHAUSTED")
BUDGET_WORDS = {
    "PREFER_CHEAPER": "70% of the budget is spent; RAVIS now prefers cheaper models",
    "STRONG_PENALTY": "90% of the budget is spent; RAVIS now strongly avoids paid models",
    "EXHAUSTED": "the budget is spent",
}


@dataclass(frozen=True)
class Reading:
    """One probe tick's worth of readings. `None` means not read this time."""

    now: float
    budget: Mapping[str, Any] | None = None
    memory_under_pressure: bool | None = None
    memory_detail: str = ""
    swap_used: int | None = None
    # Editor windows by instance id, for naming the one that waits.
    window_labels: Mapping[str, str] | None = None


class Alerts:
    """Decides which events and readings become notes, and files them through `post`."""

    def __init__(self, post: Callable[..., Any]) -> None:
        self._post = post
        self._outcomes: deque[tuple[float, bool]] = deque()
        self._spike_noted_at: float | None = None
        self._gates: dict[tuple[str, str], tuple[float, str]] = {}
        self._gates_noted: set[tuple[str, str]] = set()
        self._benchmarks_noted: set[str] = set()
        self._band = "NORMAL"
        self._memory_noted = False
        self._swap: deque[tuple[float, int]] = deque()
        self._swap_noted_at: float | None = None

    # ── Events ───────────────────────────────────────────────────────────────

    def on_event(self, event: Mapping[str, Any], now: float) -> None:
        """One stored hub event. Never raises for a shape it does not expect."""
        kind = str(event.get("event_type") or "")
        data = event.get("data")
        data = data if isinstance(data, Mapping) else {}
        if kind in ("sirvis.benchmark.completed", "sirvis.benchmark.failed"):
            self._benchmark(kind, data)
        elif kind == "ravis.request.completed":
            if not data.get("cancelled"):
                self._outcome(now, failed=data.get("succeeded") is False)
        elif kind == "ravis.route.refused":
            self._outcome(now, failed=True)
        elif kind in ("clarvis.gate.requested", "clarvis.gate.resolved"):
            self._gate(kind, event, data, now)

    def _benchmark(self, kind: str, data: Mapping[str, Any]) -> None:
        run = str(data.get("run_id") or "")
        if not run or run in self._benchmarks_noted:
            return
        self._benchmarks_noted.add(run)
        model = str(data.get("model_key") or "a model")[:120]
        failed = kind.endswith(".failed")
        self._post(
            kind="benchmark_finished",
            title=f"Benchmark of {model} {'failed' if failed else 'finished'}",
            reason=f"SIRVIS reported run {run[:40]} as {'failed' if failed else 'completed'}",
            body=str(data.get("detail") or "")[:300] if failed else "",
            severity="warning" if failed else "info",
            source="sirvis",
        )

    def _outcome(self, now: float, *, failed: bool) -> None:
        self._outcomes.append((now, failed))
        while self._outcomes and self._outcomes[0][0] < now - SPIKE_WINDOW_SECONDS:
            self._outcomes.popleft()
        failures = sum(1 for _, bad in self._outcomes if bad)
        spike = failures >= SPIKE_MIN_FAILURES and failures >= SPIKE_SHARE * len(self._outcomes)
        quiet = self._spike_noted_at is None or now - self._spike_noted_at >= SPIKE_QUIET_SECONDS
        if not (spike and quiet):
            return
        self._spike_noted_at = now
        self._post(
            kind="route_failures",
            title="Requests through RAVIS are failing",
            reason=(
                f"{failures} of the last {len(self._outcomes)} requests failed or were "
                f"refused within {int(SPIKE_WINDOW_SECONDS // 60)} minutes"
            ),
            body="RAVIS → Traces and the API inspector show which models and providers failed.",
            severity="warning",
            source="ravis",
        )

    def _gate(
        self, kind: str, event: Mapping[str, Any], data: Mapping[str, Any], now: float
    ) -> None:
        source = event.get("source")
        window = str(source.get("instance_id") or "") if isinstance(source, Mapping) else ""
        key = (window, str(data.get("activity_id") or ""))
        if kind == "clarvis.gate.requested":
            self._gates.setdefault(key, (now, str(data.get("awaiting") or "")[:40]))
        else:
            self._gates.pop(key, None)
            self._gates_noted.discard(key)

    # ── Readings ─────────────────────────────────────────────────────────────

    def on_reading(self, reading: Reading) -> None:
        """One probe tick. Each part is skipped when it was not read."""
        self._waiting_gates(reading)
        if reading.budget is not None:
            self._budget(reading.budget)
        if reading.memory_under_pressure is not None:
            self._memory(reading.memory_under_pressure, reading.memory_detail)
        if reading.swap_used is not None:
            self._swap_growth(reading.now, reading.swap_used)

    def _waiting_gates(self, reading: Reading) -> None:
        labels = reading.window_labels or {}
        for key, (since, awaiting) in list(self._gates.items()):
            if key in self._gates_noted or reading.now - since < GATE_GRACE_SECONDS:
                continue
            self._gates_noted.add(key)
            label = labels.get(key[0]) or f"editor window {key[0][:8] or '?'}"
            self._post(
                kind="clarvis_waiting",
                title=f"Clarvis is waiting for you in {label[:80]}",
                reason=(
                    f"{GATE_WORDS.get(awaiting, 'an approval')} has been open for "
                    f"{int((reading.now - since) // 60)} minutes"
                ),
                body="Answer it in the editor; NERVIS shows a wait and cannot answer one.",
                severity="info",
                source="clarvis",
            )

    def _budget(self, budget: Mapping[str, Any]) -> None:
        band = str(budget.get("band") or "")
        if band not in BUDGET_BANDS:
            return
        rose = BUDGET_BANDS.index(band) > BUDGET_BANDS.index(self._band)
        self._band = band
        if not rose:
            return
        spent, limit = budget.get("spent_estimated"), budget.get("limit")
        currency = str(budget.get("currency") or "")[:8]
        self._post(
            kind="budget_threshold",
            title="RAVIS budget: " + BUDGET_WORDS[band],
            reason=(
                f"RAVIS's estimated spend is {spent} of {limit} {currency} for its "
                f"{str(budget.get('period') or '')[:20]} budget, which puts it in band {band}"
            ),
            body=(
                "An estimate from published prices, never an invoice."
                + (
                    " Paid models are blocked."
                    if band == "EXHAUSTED" and budget.get("hard")
                    else ""
                )
            ),
            severity="warning",
            source="ravis",
        )

    def _memory(self, under_pressure: bool, detail: str) -> None:
        if not under_pressure:
            self._memory_noted = False
            return
        if self._memory_noted:
            return
        self._memory_noted = True
        self._post(
            kind="memory_pressure",
            title="This Mac is short of memory",
            reason="RAVIS's memory reading moved to under pressure",
            body=(detail[:300] + " " if detail else "")
            + "Loading another local model now may slow everything down.",
            severity="warning",
            source="ravis",
        )

    def _swap_growth(self, now: float, used: int) -> None:
        self._swap.append((now, used))
        while self._swap and self._swap[0][0] < now - SWAP_WINDOW_SECONDS:
            self._swap.popleft()
        grown = used - min(value for _, value in self._swap)
        quiet = self._swap_noted_at is None or now - self._swap_noted_at >= SWAP_QUIET_SECONDS
        if grown < SWAP_GROWTH_BYTES or not quiet:
            return
        self._swap_noted_at = now
        self._post(
            kind="swap_warning",
            title="This Mac is swapping heavily",
            reason=(
                f"swap in use grew by {grown / 1024**3:.1f} GB within "
                f"{int(SWAP_WINDOW_SECONDS // 60)} minutes"
            ),
            body="Something is using more memory than the machine has; local models slow sharply.",
            severity="warning",
            source="nervis",
        )


__all__ = ["Alerts", "Reading"]
