"""The shared lock rule: is a project lock's holder alive, unresponsive, or gone? (design §6.3)

**Specified once, implemented twice.** Clarvis judges the checkout lock file with
`src/engine/lock/lockRule.ts`; RAVIS judges it — and, from M29's fourth increment, its own
`project_lock` rows — with this module. Both are held to one case table,
`tests/fixtures/lock-rule-cases.json`, which RAVIS owns and Clarvis copies with a hash check. If the
two ever disagreed, one engine would call a holder dead that the other calls alive: two writers in
one project, which is exactly what the lock exists to prevent. `tests/test_codex_lock_rule.py` runs
every table in that file against this module.

**The three verdicts:**
- `gone`: the holder's pid isn't running, or its process start time differs from the one recorded
  (the pid was reused). **Only `gone` counts as dead.**
- `unresponsive`: the right process is still running, but its heartbeat is more than 90 s old
  **and** this observer has itself been awake for at least 90 s. A Mac that just woke hasn't given
  anyone time to heartbeat, so a stale heartbeat right after a sleep never makes a holder look dead.
- `alive`: otherwise.

The case file also carries two decisions built on the verdict — what a window does on finding a
lock, and RAVIS's rule for a lock file it finds after a restart — and both are here, as Clarvis has
them, so every table in the shared file is exercised on both sides.

**The two facts from this Mac** (`probe_process`, `observer_awake_seconds`) are read with `ps` and
`sysctl` in the C locale: `ps -o lstart=` formats its date through the locale, so a Dutch locale
would print "zo 13 sep" where the lock file recorded "Sun Sep 13", and a live holder would look
`gone` (C1's hand-over notes). A probe that couldn't run answers None, never "not running": a
failed `ps` must not become a `gone` verdict and a takeover.

Reading only: nothing here signals, starts or writes anything.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

Verdict = Literal["alive", "unresponsive", "gone"]
FindingOutcome = Literal["attach", "reconcile", "refuse", "take_over_with_confirmation"]

#: Both limits: a heartbeat older than this, seen by an observer awake at least this long.
THRESHOLD_SECONDS = 90
#: How long `ps` or `sysctl` may take before the probe says it doesn't know.
PROBE_TIMEOUT_SECONDS = 5.0

#: Runs a program and hands back its exit code and output; injectable so tests needn't run `ps`.
Runner = Callable[[list[str]], tuple[int | None, str]]


@dataclass(frozen=True)
class JudgedLock:
    """What the rule needs from a lock: who holds it and how old its heartbeat is."""

    pid: int
    pid_start: str
    heartbeat_age_seconds: float


@dataclass(frozen=True)
class ProcessProbe:
    """What was found when the holder's pid was looked up."""

    pid_running: bool
    #: `ps -o lstart= -p <pid>`, or None when the pid isn't running.
    lstart: str | None


def judge_lock(lock: JudgedLock, probe: ProcessProbe, observer_awake_seconds: float) -> Verdict:
    """`judgeLock(lock, probe, observerAwakeSeconds)`, the signature both implementations share."""
    if not probe.pid_running or probe.lstart is None:
        return "gone"
    if not same_start(probe.lstart, lock.pid_start):
        return "gone"
    # "Older than 90 s" is strict and "awake at least 90 s" is not: the cases pin both edges.
    stale = lock.heartbeat_age_seconds > THRESHOLD_SECONDS
    settled = observer_awake_seconds >= THRESHOLD_SECONDS
    return "unresponsive" if stale and settled else "alive"


def same_start(first: str, second: str) -> bool:
    """Whether two `ps -o lstart=` readings name the same moment.

    `ps` pads a one-digit day with a second space ("Sat Sep  5") and ends its line with spaces, and
    a value stored in JSON may have lost either, so both are compared with whitespace collapsed.
    """
    return normalise_start(first) == normalise_start(second)


def normalise_start(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def on_finding_a_lock(holder_kind: str, verdict: Verdict, waiting_on_you: bool) -> FindingOutcome:
    """What a writer does when it finds the project locked (`on_finding_a_lock`)."""
    # A Codex task lives in RAVIS: a window joins it and can stop it, and never takes it over.
    if holder_kind == "codex_session":
        return "attach"
    if verdict == "gone":
        return "reconcile"
    # Asking first is only fair when the holder can't be working: stalled, or waiting on the owner.
    if verdict == "unresponsive" or waiting_on_you:
        return "take_over_with_confirmation"
    return "refuse"


@dataclass(frozen=True)
class FoundLockFile:
    """A checkout lock file RAVIS finds when it wants the project (`adoption_cases`)."""

    names_previous_ravis_instance: bool
    verdict: Verdict


@dataclass(frozen=True)
class AdoptionOutcome:
    """The rule's one decision, and what follows from it (`adoption_cases` → `expected`)."""

    action: Literal["rewrite", "superseded", "create"]

    @property
    def file_touched(self) -> bool:
        return self.action != "superseded"

    @property
    def ravis_lock_state(self) -> Literal["running", "superseded"]:
        return "superseded" if self.action == "superseded" else "running"

    @property
    def codex_session_state(self) -> Literal["uncertain"] | None:
        """A superseded lock leaves the Codex session uncertain until the window's run ends."""
        return "uncertain" if self.action == "superseded" else None

    @property
    def refused_with_409_lock_superseded(self) -> tuple[str, ...]:
        return ("settle-claim", "turns", "resume") if self.action == "superseded" else ()


def adoption_decision(found: FoundLockFile | None, create_race_lost: bool) -> AdoptionOutcome:
    """RAVIS's restart adoption rule (review AB1).

    RAVIS may rewrite a lock file only when it still names RAVIS's own previous instance. Anyone
    else's file is left alone **whatever its verdict** — even a `gone` window's — because a window
    that is merely slow to reconnect still owns uncommitted work.
    """
    ours = not create_race_lost if found is None else found.names_previous_ravis_instance
    if not ours:
        return AdoptionOutcome("superseded")
    return AdoptionOutcome("create" if found is None else "rewrite")


# ── The two facts from this Mac ──────────────────────────────────────────────


def run_quietly(arguments: list[str]) -> tuple[int | None, str]:
    """Run a reading program in the C locale, never raising: (exit code or None, its output)."""
    try:
        done = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            env={**os.environ, "LC_ALL": "C"},
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return done.returncode, done.stdout


def probe_process(pid: int, run: Runner = run_quietly) -> ProcessProbe | None:
    """Look `pid` up with `ps -o lstart= -p <pid>`; None when `ps` couldn't say."""
    if pid <= 0:
        return ProcessProbe(pid_running=False, lstart=None)
    code, output = run(["ps", "-o", "lstart=", "-p", str(pid)])
    lstart = normalise_start(output)
    if lstart:
        return ProcessProbe(pid_running=True, lstart=lstart)
    # ps prints nothing and exits 1 for a pid that isn't running. Anything else is not knowing.
    return ProcessProbe(pid_running=False, lstart=None) if code == 1 else None


def own_start(run: Runner = run_quietly) -> str | None:
    """This process's own `pid_start`, in the form a lock file records it."""
    probe = probe_process(os.getpid(), run)
    return probe.lstart if probe is not None else None


def observer_awake_seconds(
    now: float | None = None, run: Runner = run_quietly, platform: str = sys.platform
) -> float | None:
    """Seconds since this Mac last woke (`kern.waketime`), or booted (`kern.boottime`).

    Elsewhere the time since boot stands in, which overstates the time awake after a suspend: that
    can only make a holder `unresponsive` — a takeover the owner confirms — never `gone`.
    """
    moment = time.time() if now is None else now
    if platform != "darwin":
        return max(0.0, time.monotonic())
    woke = parse_sysctl_seconds(run(["sysctl", "-n", "kern.waketime"])[1])
    since = woke or parse_sysctl_seconds(run(["sysctl", "-n", "kern.boottime"])[1])
    if not since:
        return None
    return max(0.0, moment - since)


def parse_sysctl_seconds(text: str) -> int | None:
    """`{ sec = 1789231177, usec = 263628 } Sat Sep 12 18:39:37 2026` → 1789231177; 0 if never."""
    match = re.search(r"\{\s*sec = (\d+)", text)
    return int(match.group(1)) if match else None
