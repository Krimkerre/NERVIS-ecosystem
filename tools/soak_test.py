#!/usr/bin/env python3
"""Stage 10's long-running suite: the stack for hours under light traffic.

**What a load test cannot see.** `tools/load_test.py` pushes hard for a few
minutes and asks whether anything breaks. This asks the slower question: left
running, does anything creep — memory that is never given back, file handles or
threads that pile up, reads that get a little slower every hour, a service that
quietly restarts or stops answering. Those only show over time, so this runs for
hours at the kind of traffic the stack actually gets.

**Two kinds of traffic, for the same reason as the load test.** The running
NERVIS, RAVIS and SIRVIS get the reads one open dashboard makes: the status bar's
poll every eight seconds, the Overview screen's reads every thirty. Nothing is
written and no model is called. Chat traffic goes to a private RAVIS in front of
the load test's stand-in model, about two requests a second — plain, streamed,
from a named client, and inside twenty rotating sessions — so RAVIS's request
path runs for hours without spending money or loading a model.

**Watched once a minute.** Resident memory, threads and open files of every
service process, and its PID, so a restart shows as a change rather than as a
gap; the size of every database and log; each read's failures and timings for
that minute. Each sample is written to `.run/soak/` as it is taken, so a run cut
short still leaves its record, and `--report` reads one back.

**Sleep is not a hang.** The run lasts the requested time *awake*: macOS's
monotonic clock stops while the machine sleeps and the wall clock does not, so a
sample whose wall time jumped records how long the machine slept instead of
counting it as a service that stopped answering. The launcher runs this under
`caffeinate -i`, which holds off idle sleep for as long as this process lives and
no longer; a closed lid still sleeps.

**Verdicts are fixed before the run.**

1. The running services answer: every read at least 99.9% answered, and never
   three failures of the same read in a row.
2. No service restarts: each keeps one PID for the whole run.
3. The private RAVIS stays correct: at least 99.9% answered, none with somebody
   else's answer.
4. Memory does not creep: each process's median memory over the last thirty
   minutes is within 15% or 30 MB, whichever is larger, of its median over the
   thirty minutes after a fifteen-minute warm-up.
5. Handles do not pile up: open files within 20 and threads within 5 of the same
   baseline.
6. Reads do not slow down: each read's median over the last thirty minutes is at
   most 1.5 times its baseline median plus 2 ms.

Verdicts 4 to 6 compare a baseline with an end, so they need at least 75 minutes
awake and read "not judged" on a shorter run rather than passing it. Growth that
is not a verdict — databases, logs, how much memory the machine had free — is
reported as figures.

    ravis/.venv/bin/python tools/soak_test.py [--hours 4] [--minutes N] [--report FILE]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import itertools
import json
import os
import signal
import statistics
import sys
import tempfile
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import psutil

# The stand-in model, the private RAVIS and the request helpers live beside this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import load_test as lt

ROOT = lt.ROOT
RECORDS = lt.RUN / "soak"
LIVE_SIRVIS = "http://127.0.0.1:8721"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOT_RUNNABLE = 2
EXIT_NOT_JUDGED = 3

DEFAULT_HOURS = 4.0
SAMPLE_SECONDS = 60.0
# `POLL_MS` in nervis/index.html: the status bar's own timer.
DASHBOARD_SECONDS = 8.0
OVERVIEW_SECONDS = 30.0
# With the stand-in's 0.1 s pause, about two chat requests a second.
CHAT_INTERVAL_SECONDS = 0.5
SESSIONS = 20
PROGRESS_SECONDS = 15 * 60.0

AVAILABILITY = 0.999
OUTAGE_ROUNDS = 3
WARM_UP_SECONDS = 15 * 60.0
WINDOW_SECONDS = 30 * 60.0
MIN_JUDGED_SECONDS = WARM_UP_SECONDS + 2 * WINDOW_SECONDS
RSS_GROWTH_SHARE = 0.15
RSS_GROWTH_MB = 30.0
FD_SLACK = 20
THREAD_SLACK = 5
LATENCY_SHARE = 1.5
LATENCY_SLACK_MS = 2.0

LIVE_SERVICES = ("ravis", "nervis", "sirvis")
#: Where each live service listens, which is what tells it from the private RAVIS
#: (`lt.service_process`).
LIVE_PORTS = {"ravis": 8731, "nervis": 8790, "sirvis": 8721}
PRIVATE = "private ravis"

DASHBOARD_READS = (
    ("nervis health", f"{lt.LIVE_NERVIS}/api/v1/health"),
    ("nervis services", f"{lt.LIVE_NERVIS}/api/v1/services"),
    ("nervis registry", f"{lt.LIVE_NERVIS}/api/v1/registry/instances"),
)
CHAT_KINDS = ("chat", "chat streamed", "chat named", "chat in a session")

WATCHED_FILES = (
    ROOT / "ravis" / "ravis.db", ROOT / "ravis" / "ravis.db-wal",
    ROOT / "nervis" / "nervis.db", ROOT / "nervis" / "nervis.db-wal",
    ROOT / "sirvis" / "sirvis.db", ROOT / "sirvis" / "sirvis.db-wal",
    lt.RUN / "ravis.log", lt.RUN / "nervis.log", lt.RUN / "sirvis.log",
)


def overview_reads() -> tuple[tuple[str, str], ...]:
    since = int(time.time()) - 3600
    return (
        ("nervis system", f"{lt.LIVE_NERVIS}/api/v1/system"),
        ("spend via relay", f"{lt.LIVE_NERVIS}/api/v1/relay/ravis/api/v1/usage?since={since}"),
        ("ravis models", f"{lt.LIVE_RAVIS}/v1/models"),
        ("sirvis health", f"{LIVE_SIRVIS}/api/v1/health"),
        ("sirvis system", f"{LIVE_SIRVIS}/api/v1/system"),
    )


def is_chat(label: str) -> bool:
    return label.startswith("chat")


# ── Recording ───────────────────────────────────────────────────────────────


class Recorder:
    """One JSON object per line, flushed as written, and kept in memory for the report."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.records: list[dict[str, Any]] = []
        self._handle = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = path.open("a", buffering=1, encoding="utf-8")

    def write(self, kind: str, **fields: Any) -> dict[str, Any]:
        record = {"kind": kind, "at": time.time(), **fields}
        self.records.append(record)
        if self._handle is not None:
            self._handle.write(json.dumps(record, default=str) + "\n")
        return record

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()


@dataclass
class Traffic:
    """What happened to each kind of request, per minute and in total.

    Only one minute of samples is held at a time — a tool that watches for creep
    must not creep itself over four hours.
    """

    recorder: Recorder
    started: float = field(default_factory=time.monotonic)
    window: dict[str, list[lt.Sample]] = field(default_factory=dict)
    streaks: dict[str, int] = field(default_factory=dict)

    def note(self, label: str, sample: lt.Sample) -> None:
        self.window.setdefault(label, []).append(sample)
        failed = sample.status != 200 if is_chat(label) else not sample.correct
        if not failed:
            if self.streaks.get(label, 0) >= OUTAGE_ROUNDS:
                self.recorder.write("outage_ended", label=label, elapsed=self.elapsed(),
                                    failures=self.streaks[label])
            self.streaks[label] = 0
            return
        self.streaks[label] = self.streaks.get(label, 0) + 1
        if self.streaks[label] == OUTAGE_ROUNDS:
            self.recorder.write("outage_started", label=label, elapsed=self.elapsed(),
                                detail=f"{sample.status} {sample.detail}"[:300])

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def flush(self) -> dict[str, dict[str, Any]]:
        summary: dict[str, dict[str, Any]] = {}
        for label, samples in self.window.items():
            chat = is_chat(label)
            answered = [s for s in samples if (s.status == 200 if chat else s.correct)]
            times = [s.seconds * 1000 for s in answered]
            summary[label] = {
                "n": len(samples),
                "failed": len(samples) - len(answered),
                "wrong": sum(1 for s in answered if chat and not s.correct),
                "p50_ms": lt.percentile(times, 0.5) if times else None,
                "p95_ms": lt.percentile(times, 0.95) if times else None,
                "first_failure": next((f"{s.status} {s.detail}"[:200] for s in samples
                                       if s not in answered), ""),
            }
        self.window = {}
        return summary


# ── The loops ───────────────────────────────────────────────────────────────


async def pause(stop: asyncio.Event, seconds: float) -> None:
    """Sleep, but wake at once when the run is stopping."""
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=max(0.0, seconds))


async def forever(name: str, stop: asyncio.Event, recorder: Recorder, every: float,
                  round_: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Run `round_` every `every` seconds until stopped, surviving its failures.

    A loop that died on one unexpected exception would stop its traffic for the
    rest of the run while the samples went on looking healthy, so a failure is
    written down and the loop carries on.
    """
    while not stop.is_set():
        started = time.monotonic()
        try:
            await round_()
        except Exception as failure:  # noqa: BLE001 — recorded, and the run continues
            recorder.write("loop_error", loop=name, error=repr(failure)[:300])
        await pause(stop, every - (time.monotonic() - started))


def process_snapshot(process: psutil.Process | None) -> dict[str, Any]:
    if process is None:
        return {"running": False}
    try:
        with process.oneshot():
            used = process.cpu_times()
            return {"running": True, "pid": process.pid,
                    "rss_mb": process.memory_info().rss / 1024 / 1024,
                    "threads": process.num_threads(), "fds": process.num_fds(),
                    "cpu_s": used.user + used.system}
    except psutil.Error:
        return {"running": False}


def file_sizes(extra: tuple[Path, ...]) -> dict[str, int | None]:
    sizes: dict[str, int | None] = {}
    for path in (*WATCHED_FILES, *extra):
        label = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name
        try:
            sizes[label] = path.stat().st_size
        except OSError:
            sizes[label] = None
    return sizes


# ── Verdicts ────────────────────────────────────────────────────────────────


@dataclass
class Verdict:
    key: str
    state: str  # "pass", "fail" or "not judged"
    claim: str
    evidence: str

    def as_dict(self) -> dict[str, str]:
        return {"key": self.key, "state": self.state, "claim": self.claim,
                "evidence": self.evidence}


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def summarise(records: list[dict[str, Any]]) -> tuple[list[Verdict], dict[str, Any]]:
    """The six verdicts and the figures, from a run's records — live or read back."""
    samples = [r for r in records if r["kind"] == "sample"]
    outages = [r for r in records if r["kind"] == "outage_started"]
    errors = [r for r in records if r["kind"] == "loop_error"]
    awake = samples[-1]["elapsed"] if samples else 0.0
    totals: dict[str, dict[str, int]] = {}
    for sample in samples:
        for label, read in sample["reads"].items():
            held = totals.setdefault(label, {"n": 0, "failed": 0, "wrong": 0})
            for key in held:
                held[key] += read[key]
    verdicts: list[Verdict] = []

    live = {label: t for label, t in totals.items() if not is_chat(label)}
    short = [f"{label} {1 - t['failed'] / t['n']:.2%}" for label, t in live.items()
             if t["n"] and 1 - t["failed"] / t["n"] < AVAILABILITY]
    live_outages = [o for o in outages if not is_chat(o["label"])]
    verdicts.append(Verdict(
        "live.answers", "fail" if short or live_outages or not live else "pass",
        f"The running services answered at least {AVAILABILITY:.1%} of every read, "
        f"never failing the same read {OUTAGE_ROUNDS} times in a row",
        f"{sum(t['n'] for t in live.values()):,} reads, "
        f"{sum(t['failed'] for t in live.values()):,} failed"
        + (f"; below the line: {', '.join(short)}" if short else "")
        + ("; outages: " + ", ".join(f"{o['label']} at {o['elapsed'] / 60:.0f} min"
                                     for o in live_outages)
           if live_outages else ""),
    ))

    restarts = []
    for name in LIVE_SERVICES:
        pids = [s["processes"][name].get("pid") for s in samples]
        if any(pid is None for pid in pids) or len(set(pids)) > 1:
            changes = [f"{s['elapsed'] / 60:.0f} min" for before, s in itertools.pairwise(samples)
                       if before["processes"][name].get("pid") != s["processes"][name].get("pid")]
            restarts.append(f"{name} ({', '.join(changes) or 'not running'})")
    verdicts.append(Verdict(
        "live.no_restarts", "fail" if restarts or not samples else "pass",
        "No service restarted or stopped: each kept one process for the whole run",
        "; ".join(restarts) if restarts else
        ", ".join(f"{name} pid {samples[0]['processes'][name].get('pid')}" for name in LIVE_SERVICES)
        if samples else "no samples",
    ))

    chat = {label: t for label, t in totals.items() if is_chat(label)}
    asked = sum(t["n"] for t in chat.values())
    failed = sum(t["failed"] for t in chat.values())
    wrong = sum(t["wrong"] for t in chat.values())
    verdicts.append(Verdict(
        "chat.correct",
        "pass" if asked and failed / asked <= 1 - AVAILABILITY and not wrong else "fail",
        f"The private RAVIS answered at least {AVAILABILITY:.1%} of chat requests, "
        "none with somebody else's answer",
        f"{asked:,} requests, {failed:,} failed, {wrong:,} wrong answers",
    ))

    baseline = [s for s in samples if WARM_UP_SECONDS <= s["elapsed"] < WARM_UP_SECONDS + WINDOW_SECONDS]
    final = [s for s in samples if s["elapsed"] >= awake - WINDOW_SECONDS]
    judged = awake >= MIN_JUDGED_SECONDS and baseline and final
    not_judged = (f"needs {MIN_JUDGED_SECONDS / 60:.0f} minutes awake; "
                  f"this run had {awake / 60:.0f}")

    def per_process(metric: str) -> dict[str, tuple[float | None, float | None]]:
        names = [*LIVE_SERVICES, PRIVATE]
        return {name: (_median([s["processes"][name][metric] for s in baseline
                                if s["processes"].get(name, {}).get("running")]),
                       _median([s["processes"][name][metric] for s in final
                                if s["processes"].get(name, {}).get("running")]))
                for name in names}

    if judged:
        memory = per_process("rss_mb")
        crept = [f"{name} {b:.0f}→{f:.0f} MB" for name, (b, f) in memory.items()
                 if b is not None and f is not None and f > b + max(b * RSS_GROWTH_SHARE, RSS_GROWTH_MB)]
        verdicts.append(Verdict(
            "memory.flat", "fail" if crept else "pass",
            f"No process's memory crept: last half hour within {RSS_GROWTH_SHARE:.0%} or "
            f"{RSS_GROWTH_MB:.0f} MB of the half hour after warm-up",
            "; ".join(f"{name} {lt.figure(b)}→{lt.figure(f)} MB" for name, (b, f) in memory.items())
            + (f" — crept: {', '.join(crept)}" if crept else ""),
        ))
        fds, threads = per_process("fds"), per_process("threads")
        piled = [f"{name} files {fds[name][0]:.0f}→{fds[name][1]:.0f}" for name in fds
                 if None not in fds[name] and fds[name][1] > fds[name][0] + FD_SLACK]  # type: ignore[operator]
        piled += [f"{name} threads {threads[name][0]:.0f}→{threads[name][1]:.0f}" for name in threads
                  if None not in threads[name] and threads[name][1] > threads[name][0] + THREAD_SLACK]  # type: ignore[operator]
        verdicts.append(Verdict(
            "handles.flat", "fail" if piled else "pass",
            f"Open files stayed within {FD_SLACK} and threads within {THREAD_SLACK} of the baseline",
            "; ".join(f"{name} files {lt.figure(fds[name][0])}→{lt.figure(fds[name][1])}, "
                      f"threads {lt.figure(threads[name][0])}→{lt.figure(threads[name][1])}"
                      for name in fds) + (f" — piled up: {', '.join(piled)}" if piled else ""),
        ))
        slower = []
        steady = []
        for label in totals:
            b = _median([s["reads"][label]["p50_ms"] for s in baseline
                         if s["reads"].get(label, {}).get("p50_ms") is not None])
            f = _median([s["reads"][label]["p50_ms"] for s in final
                         if s["reads"].get(label, {}).get("p50_ms") is not None])
            if b is None or f is None:
                continue
            steady.append(f"{label} {b:.1f}→{f:.1f}")
            if f > b * LATENCY_SHARE + LATENCY_SLACK_MS:
                slower.append(f"{label} {b:.1f}→{f:.1f} ms")
        verdicts.append(Verdict(
            "latency.steady", "fail" if slower else "pass",
            f"No read slowed down: last half hour's median at most {LATENCY_SHARE}× "
            f"the baseline plus {LATENCY_SLACK_MS:.0f} ms",
            ("slower: " + ", ".join(slower)) if slower else "medians in ms: " + ", ".join(steady),
        ))
    else:
        for key, claim in (("memory.flat", "No process's memory crept"),
                           ("handles.flat", "Open files and threads did not pile up"),
                           ("latency.steady", "No read slowed down")):
            verdicts.append(Verdict(key, "not judged", claim, not_judged))

    hours = awake / 3600 if awake else 0.0
    first, last = (samples[0], samples[-1]) if samples else ({}, {})
    figures: dict[str, Any] = {
        "awake_minutes": round(awake / 60, 1),
        "slept_minutes": round(sum(s.get("slept_s", 0.0) for s in samples) / 60, 1),
        "loop_errors": len(errors),
        "memory_mb": {name: {"first": first["processes"][name].get("rss_mb"),
                             "last": last["processes"][name].get("rss_mb"),
                             "highest": max((s["processes"][name].get("rss_mb") or 0.0)
                                            for s in samples)}
                      for name in [*LIVE_SERVICES, PRIVATE]} if samples else {},
        "file_growth_mb_per_hour": {
            label: round(((last["files"].get(label) or 0) - (size or 0)) / 1024 / 1024 / hours, 2)
            for label, size in first["files"].items()
        } if samples and hours else {},
        "free_memory_mb": {"first": first.get("available_mb"), "last": last.get("available_mb")}
        if samples else {},
        "chat_requests": asked,
    }
    if errors:
        figures["first_loop_error"] = errors[0]
    return verdicts, figures


def print_report(verdicts: list[Verdict], figures: dict[str, Any]) -> None:
    lt.say()
    lt.say(f"Awake {figures['awake_minutes']:.0f} min"
           + (f", slept {figures['slept_minutes']:.0f} min" if figures["slept_minutes"] else "")
           + (f", {figures['loop_errors']} traffic-loop errors" if figures["loop_errors"] else ""))
    for verdict in verdicts:
        mark = {"pass": "PASS", "fail": "FAIL", "not judged": "NOT JUDGED"}[verdict.state]
        lt.say(f"  {mark:10s} {verdict.claim}")
        lt.say(f"             {verdict.evidence}")
    if figures.get("memory_mb"):
        lt.say("  Memory, first → last (highest), MB: " + "; ".join(
            f"{name} {lt.figure(m['first'])} → {lt.figure(m['last'])} ({lt.figure(m['highest'])})"
            for name, m in figures["memory_mb"].items()))
    growing = {label: rate for label, rate in figures.get("file_growth_mb_per_hour", {}).items()
               if rate}
    if growing:
        lt.say("  Files growing, MB an hour: " + ", ".join(f"{label} {rate:+.2f}"
                                                        for label, rate in growing.items()))


def exit_code(verdicts: list[Verdict]) -> int:
    if any(v.state == "fail" for v in verdicts):
        return EXIT_FAILED
    if any(v.state == "not judged" for v in verdicts):
        return EXIT_NOT_JUDGED
    return EXIT_OK


# ── The run ─────────────────────────────────────────────────────────────────


async def live_stack_answers() -> str:
    """Empty when all three services answer, otherwise which did not."""
    missing = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for name, url in (("NERVIS", f"{lt.LIVE_NERVIS}/api/v1/health"),
                          ("RAVIS", f"{lt.LIVE_RAVIS}/v1/models"),
                          ("SIRVIS", f"{LIVE_SIRVIS}/api/v1/health")):
            sample = await lt.timed_get(client, url, name)
            if not sample.correct:
                missing.append(f"{name} ({sample.status or 'no answer'})")
    return ", ".join(missing)


def progress_line(recorder: Recorder, traffic_totals: dict[str, list[int]], sample: dict[str, Any],
                  first: dict[str, Any]) -> str:
    live_n = sum(n for label, (n, _) in traffic_totals.items() if not is_chat(label))
    live_failed = sum(f for label, (_, f) in traffic_totals.items() if not is_chat(label))
    chat_n = sum(n for label, (n, _) in traffic_totals.items() if is_chat(label))
    chat_failed = sum(f for label, (_, f) in traffic_totals.items() if is_chat(label))
    memory = ", ".join(
        f"{name} {lt.figure(first['processes'][name].get('rss_mb'))}→"
        f"{lt.figure(sample['processes'][name].get('rss_mb'))}"
        for name in [*LIVE_SERVICES, PRIVATE])
    elapsed = int(sample["elapsed"])
    return (f"  {elapsed // 3600}:{elapsed % 3600 // 60:02d} awake — dashboard reads {live_n:,} "
            f"({live_failed} failed), chat {chat_n:,} ({chat_failed} failed); memory MB {memory}")


async def soak(duration: float, sample_seconds: float, path: Path) -> int:
    missing = await live_stack_answers()
    if missing:
        lt.say(f"Not started: the running stack is not answering — {missing}. "
               "Start it with `python3 tools/run.py start`.")
        return EXIT_NOT_RUNNABLE
    recorder = Recorder(path)
    lt.say(f"Long-running test — {datetime.now().astimezone():%Y-%m-%d %H:%M}, "
           f"commit {lt.git_head() or 'unknown'}, {duration / 3600:.2f} hours awake")
    lt.say(f"  Record: {path.relative_to(ROOT)}")
    with tempfile.TemporaryDirectory(prefix="ravis-soak-") as scratch:
        home = Path(scratch)
        with lt.Stub(home) as stub, \
                lt.PrivateRavis(home / "ravis", stub, raised_limits=True, named_client=True) as ravis:
            private = psutil.Process(ravis.process.pid) if ravis.process else None
            traffic = Traffic(recorder)
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for signum in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(signum, stop.set)
            recorder.write("start", duration_s=duration, sample_seconds=sample_seconds,
                           commit=lt.git_head(), cores=os.cpu_count(),
                           memory_gb=round(psutil.virtual_memory().total / 1024 ** 3, 1))
            totals: dict[str, list[int]] = {}

            async def dashboard(client: httpx.AsyncClient) -> None:
                for label, url in DASHBOARD_READS:
                    traffic.note(label, await lt.timed_get(client, url, label))

            async def overview(client: httpx.AsyncClient) -> None:
                for label, url in overview_reads():
                    traffic.note(label, await lt.timed_get(client, url, label))

            counter = {"n": 0}
            named = {"Authorization": f"Bearer {ravis.token}"}

            async def chat(client: httpx.AsyncClient) -> None:
                n = counter["n"]
                counter["n"] += 1
                kind = n % len(CHAT_KINDS)
                headers = named if kind == 2 else (
                    {"x-session-id": f"soak-{n % SESSIONS}"} if kind == 3 else {})
                traffic.note(CHAT_KINDS[kind],
                             await lt.completion(client, ravis.base, kind == 1, headers))

            async def sampler() -> None:
                last_wall, last_mono = time.time(), time.monotonic()
                deadline = traffic.started + duration
                first: dict[str, Any] | None = None
                next_progress = traffic.started + min(PROGRESS_SECONDS, sample_seconds)
                while not stop.is_set():
                    await pause(stop, sample_seconds)
                    wall, mono = time.time(), time.monotonic()
                    processes = {
                        name: process_snapshot(lt.service_process(name, LIVE_PORTS[name]))
                        for name in LIVE_SERVICES
                    }
                    processes[PRIVATE] = process_snapshot(private)
                    reads = traffic.flush()
                    for label, read in reads.items():
                        held = totals.setdefault(label, [0, 0])
                        held[0] += read["n"]
                        held[1] += read["failed"]
                    record = recorder.write(
                        "sample", elapsed=traffic.elapsed(),
                        slept_s=max(0.0, (wall - last_wall) - (mono - last_mono)),
                        processes=processes, reads=reads,
                        files=file_sizes((ravis.database,)),
                        available_mb=psutil.virtual_memory().available / 1024 / 1024)
                    first = first or record
                    last_wall, last_mono = wall, mono
                    if mono >= next_progress or mono >= deadline:
                        lt.say(progress_line(recorder, totals, record, first))
                        next_progress = mono + PROGRESS_SECONDS
                    if mono >= deadline:
                        stop.set()

            async with httpx.AsyncClient(timeout=15.0) as live_client, \
                    httpx.AsyncClient(timeout=60.0) as chat_client:
                await asyncio.gather(
                    forever("dashboard", stop, recorder, DASHBOARD_SECONDS,
                            lambda: dashboard(live_client)),
                    forever("overview", stop, recorder, OVERVIEW_SECONDS,
                            lambda: overview(live_client)),
                    forever("chat", stop, recorder, CHAT_INTERVAL_SECONDS,
                            lambda: chat(chat_client)),
                    sampler(),
                )
            for signum in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(signum)
    verdicts, figures = summarise(recorder.records)
    recorder.write("end", verdicts=[v.as_dict() for v in verdicts], figures=figures)
    recorder.close()
    print_report(verdicts, figures)
    lt.say(f"Record: {path.relative_to(ROOT)}")
    return exit_code(verdicts)


def report(path: Path) -> int:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    verdicts, figures = summarise(records)
    print_report(verdicts, figures)
    return exit_code(verdicts)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--hours", type=float, default=DEFAULT_HOURS,
                        help=f"how long to run, awake (default {DEFAULT_HOURS:g})")
    parser.add_argument("--minutes", type=float, help="how long to run, in minutes; overrides --hours")
    parser.add_argument("--sample-seconds", type=float, default=SAMPLE_SECONDS,
                        help=argparse.SUPPRESS)
    parser.add_argument("--report", type=Path, metavar="FILE",
                        help="summarise a recorded run instead of starting one")
    args = parser.parse_args()
    if args.report:
        return report(args.report)
    if not (lt.BIN / "ravis").exists():
        print("No launcher environment yet: run `python3 tools/run.py start` once first.",
              file=sys.stderr)
        return EXIT_NOT_RUNNABLE
    duration = args.minutes * 60 if args.minutes is not None else args.hours * 3600
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return asyncio.run(soak(duration, args.sample_seconds, RECORDS / f"soak-{stamp}.jsonl"))


if __name__ == "__main__":
    sys.exit(main())
