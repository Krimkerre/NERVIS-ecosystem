#!/usr/bin/env python3
"""Stage 10's load suite: many requests at once, and what gives first.

**Two questions, kept apart because they need different machines.** Whether RAVIS
stays correct and quick with many callers is a question about RAVIS's code, and
answering it against the running stack would spend money on a hosted model or
load a local one. So parts 1 and 2 start a private RAVIS — its own database, no
keys, no peers — in front of a stand-in model that pauses and repeats what it was
sent. Whether the dashboard's reads hold up is a question about the running
NERVIS, and a copy would answer it about a NERVIS with nothing registered, so
part 3 reads the real one, read-only, at a volume that stays inside the limit it
shares with the page.

**Overhead is measured as a difference, never as a total.** The stand-in is hit
directly at the same concurrency before RAVIS is, and what RAVIS adds is the gap
between the two at the same percentile. A latency through RAVIS alone would
mostly measure the stand-in's pause and the load generator's own queueing, and
both cancel in the subtraction. RAVIS does not time its routing phases — §9.8
names them and nothing records them — so the gap is an upper bound on routing
overhead: a gateway under the budget has routing under it too, and one over it
says look closer, not that routing is at fault.

**Nothing outside this machine, enforced rather than assumed.** The first run's
private RAVIS had no keys and no peers and still asked Anthropic and Google for
their model lists on every request — a listing that fails is never cached, so
each of ~5,700 requests fetched both again, unauthenticated, over the internet.
No key or prompt left the machine, but "reaches nothing but the stand-in" was
false, and those fetches were 165 ms of the 180 ms the first run blamed on RAVIS.
Both vendors' addresses now point at a closed local port, and every proxy
variable does too, so any hosted adapter added later fails in a millisecond
instead of calling out. What a failed listing costs per request is still
measured — it is part of what RAVIS adds — it just no longer includes a
round trip to another continent.

**One client per caller.** One shared httpx pool gave out before anything it
was measuring did: the stand-in alone went from 104 ms to 983 ms at the median at
100 callers, with the stand-in's own CPU under 1% and the generator's at 69%.
A single-connection client per caller holds 140 ms at 100 callers and 171 ms at
200. The generator's CPU is still recorded on every row, so a row where the test
program was the bottleneck can be seen as such.

**Verdicts are fixed before the run.** Every request answered with its own
answer; §9.8's P50 < 5 ms and P95 < 20 ms at one and ten callers; exactly the
configured number admitted at the anonymous limit and the rest refused with
RATE_LIMITED; recovery after the heaviest level and after the refusal window;
NERVIS answering every dashboard read. Everything else — where latency bends,
memory, CPU, NERVIS's own timings — is reported as figures, because no document
states a number to judge them against, and choosing one after seeing the results
would be choosing the answer.

**What this cannot see.** The private RAVIS reads credentials from a file, never
the keychain, so what a keychain costs on the named path is measured only in part
3, on the real service. Memory over a few minutes says nothing about a leak over
hours; that is the long-running suite's question.

Run with the launcher's environment, which has httpx, uvicorn and psutil:

    ravis/.venv/bin/python tools/load_test.py [--part gateway|admission|live]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Self

try:
    import httpx
    import psutil
except ImportError:
    print("Run this with the launcher's environment: ravis/.venv/bin/python tools/load_test.py",
          file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / ".run"
BIN = ROOT / "ravis" / ".venv" / "bin"
LIVE_RAVIS = "http://127.0.0.1:8731"
LIVE_NERVIS = "http://127.0.0.1:8790"
# The launcher's cached administrative credential, read the way `run.py` reads it.
# It is only ever put in a header, never printed or written into the evidence.
ADMIN_TOKEN = RUN / "ravis-admin.token"
EVIDENCE = RUN / "load-test.json"
# A port nothing listens on: a connection to it is refused at once.
CLOSED = "http://127.0.0.1:9"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOT_RUNNABLE = 2
EXIT_INCOMPLETE = 3

PARTS = ("gateway", "admission", "live")

STUB_MODEL = "stub-model"
# Long enough that callers genuinely overlap, short enough that two thousand
# requests finish in seconds. A real model takes far longer, which only makes
# RAVIS's share of the total smaller than it is here.
STUB_PAUSE_SECONDS = 0.1

# §9.8's target for cached rule-based routing.
OVERHEAD_P50_MS = 5.0
OVERHEAD_P95_MS = 20.0
# Judged at one caller (the cost of a request) and ten (a busy evening: Clarvis,
# NERVIS's background work and the page at once). Above that the rows show where
# latency bends, which is reported rather than judged — see the docstring.
JUDGED_CALLERS = (1, 10)
# (callers at once, requests). Enough requests per row for a P95 that is not
# just the second-slowest request.
LEVELS = ((1, 50), (10, 200), (50, 500), (100, 1000), (200, 2000))
STREAM_LEVELS = ((10, 200), (100, 1000))
NAMED_LEVEL = (10, 200)
# After the heaviest row, one caller at a time must be back within this of the
# first row's latency.
RECOVERY_SLACK_MS = 2.0
RECOVERY_REQUESTS = 30
# A row where the test program used more than this much of a core measured the
# test program as much as the service, and says so.
GENERATOR_BUSY_CORES = 0.8

ADMISSION_BURST = 100
ADMISSION_CALLERS = 20
# RAVIS's limiter forgets a request sixty seconds after admitting it.
WINDOW_SECONDS = 60.0
AFTER_WINDOW_REQUESTS = 10

LIVE_IDENTITY_REQUESTS = 20
# (callers at once, rounds of every read). Seventy relayed reads in all, well
# inside the 600 a minute NERVIS's credential gets from RAVIS, which the open
# dashboard is spending from at the same time.
NERVIS_LEVELS = ((1, 10), (10, 20), (50, 40))

STARTUP_SECONDS = 60.0


class NotRunnable(Exception):
    """A part could not be performed, which is not the same as it failing."""


def say(text: str = "") -> None:
    print(text, flush=True)


def percentile(values: list[float], share: float) -> float:
    """Nearest-rank percentile: the smallest value at or above `share` of them."""
    if not values:
        return math.nan
    ordered = sorted(values)
    return ordered[max(1, math.ceil(share * len(ordered))) - 1]


def figure(value: float | None) -> str:
    return "—" if value is None or math.isnan(value) else f"{value:.1f}"


def cores(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


# ── Samples ─────────────────────────────────────────────────────────────────


@dataclass
class Sample:
    status: int  # 0 when no HTTP answer arrived at all
    seconds: float
    first_seconds: float | None = None  # to the first streamed text
    correct: bool = False  # the answer carried this request's own nonce
    detail: str = ""
    label: str = ""


@dataclass
class Level:
    target: str
    callers: int
    requests: int
    stream: bool
    samples: list[Sample]
    wall_seconds: float
    cpu_seconds: float | None = None  # the service's, over the timed part
    generator_seconds: float | None = None  # this program's, over the same

    @property
    def failures(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for sample in self.samples:
            if sample.status != 200:
                key = str(sample.status) if sample.status else "no answer"
                counts[key] = counts.get(key, 0) + 1
        return counts

    @property
    def wrong(self) -> int:
        """Answered, but with somebody else's answer or a mangled one."""
        return sum(1 for sample in self.samples if sample.status == 200 and not sample.correct)

    def ms(self, share: float, *, first: bool = False) -> float:
        values: list[float] = []
        for sample in self.samples:
            seconds = sample.first_seconds if first else sample.seconds
            if sample.status == 200 and seconds is not None:
                values.append(seconds * 1000)
        return percentile(values, share)

    @property
    def per_second(self) -> float:
        return len(self.samples) / self.wall_seconds if self.wall_seconds else math.nan

    def share_of_core(self, seconds: float | None) -> float | None:
        return seconds / self.wall_seconds if seconds is not None and self.wall_seconds else None

    def as_dict(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "target": self.target,
            "callers": self.callers,
            "requests": self.requests,
            "stream": self.stream,
            "p50_ms": self.ms(0.50),
            "p95_ms": self.ms(0.95),
            "p99_ms": self.ms(0.99),
            "max_ms": self.ms(1.0),
            "per_second": self.per_second,
            "failures": self.failures,
            "wrong_answers": self.wrong,
            "service_cores": self.share_of_core(self.cpu_seconds),
            "generator_cores": self.share_of_core(self.generator_seconds),
            "first_failure": next((s.detail for s in self.samples if s.status != 200), ""),
        }
        if self.stream:
            summary["first_text_p50_ms"] = self.ms(0.50, first=True)
            summary["first_text_p95_ms"] = self.ms(0.95, first=True)
        return summary


Call = Callable[[httpx.AsyncClient], Awaitable[Sample]]


def cpu_seconds(process: psutil.Process | None) -> float | None:
    if process is None:
        return None
    try:
        used = process.cpu_times()
    except psutil.Error:
        return None
    return float(used.user + used.system)


def rss_mb(process: psutil.Process | None) -> float | None:
    if process is None:
        return None
    try:
        return float(process.memory_info().rss) / 1024 / 1024
    except psutil.Error:
        return None


async def drive(target: str, calls: Sequence[Call], callers: int, *, stream: bool = False,
                warm: bool = True, timeout: float = 60.0,
                process: psutil.Process | None = None) -> Level:
    """Every call, `callers` at a time, each caller on its own connection.

    Each caller owns a single-connection client — see the docstring for what a
    shared pool did. Callers take the next call from one iterator between awaits
    on a single event loop, so no two take the same one. `warm` sends each caller
    one uncounted call first, so a connection handshake is not counted as latency.
    """
    limits = httpx.Limits(max_connections=1, max_keepalive_connections=1)
    clients = [httpx.AsyncClient(timeout=timeout, limits=limits) for _ in range(callers)]
    generator = psutil.Process()
    pending = iter(calls)
    samples: list[Sample] = []

    async def caller(client: httpx.AsyncClient) -> None:
        for call in pending:
            samples.append(await call(client))

    try:
        if warm:
            await asyncio.gather(*(calls[index % len(calls)](client)
                                   for index, client in enumerate(clients)))
        service_before, generator_before = cpu_seconds(process), cpu_seconds(generator)
        started = time.perf_counter()
        await asyncio.gather(*(caller(client) for client in clients))
        wall = time.perf_counter() - started
        service_after, generator_after = cpu_seconds(process), cpu_seconds(generator)
    finally:
        await asyncio.gather(*(client.aclose() for client in clients))
    return Level(target, callers, len(calls), stream, samples, wall,
                 None if service_before is None or service_after is None
                 else service_after - service_before,
                 None if generator_before is None or generator_after is None
                 else generator_after - generator_before)


async def completion(client: httpx.AsyncClient, base: str, stream: bool,
                     headers: dict[str, str]) -> Sample:
    """One chat request carrying a fresh nonce, and whether the nonce came back."""
    nonce = uuid.uuid4().hex
    payload = {"model": STUB_MODEL, "stream": stream,
               "messages": [{"role": "user", "content": nonce}]}
    url = f"{base}/v1/chat/completions"
    started = time.perf_counter()
    try:
        if not stream:
            response = await client.post(url, json=payload, headers=headers)
            elapsed = time.perf_counter() - started
            if response.status_code != 200:
                return Sample(response.status_code, elapsed, detail=response.text[:2000])
            said = response.json()["choices"][0]["message"]["content"]
            return Sample(200, elapsed, correct=said == nonce)
        first: float | None = None
        pieces: list[str] = []
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode(errors="replace")
                return Sample(response.status_code, time.perf_counter() - started,
                              detail=body[:2000])
            async for line in response.aiter_lines():
                data = line[5:].strip() if line.startswith("data:") else ""
                if not data or data == "[DONE]":
                    continue
                for choice in json.loads(data).get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        if first is None:
                            first = time.perf_counter() - started
                        pieces.append(piece)
        return Sample(200, time.perf_counter() - started, first, "".join(pieces) == nonce)
    except (httpx.HTTPError, ValueError, LookupError, TypeError, AttributeError) as failure:
        return Sample(0, time.perf_counter() - started,
                      detail=f"{type(failure).__name__}: {failure}")


async def completion_level(target: str, base: str, callers: int, requests: int, *,
                           stream: bool = False, headers: dict[str, str] | None = None,
                           process: psutil.Process | None = None) -> Level:
    def call(client: httpx.AsyncClient) -> Awaitable[Sample]:
        return completion(client, base, stream, headers or {})

    return await drive(target, [call] * requests, callers, stream=stream, process=process)


class PeakMemory:
    """The highest resident memory seen while a part runs, sampled four times a second."""

    def __init__(self, process: psutil.Process) -> None:
        self.process = process
        self.peak = rss_mb(process) or 0.0

    async def watch(self) -> None:
        while True:
            self.peak = max(self.peak, rss_mb(self.process) or 0.0)
            await asyncio.sleep(0.25)


# ── Private services ────────────────────────────────────────────────────────


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def tail(path: Path, lines: int = 15) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return "(no log)"


def wait_listening(port: int, process: subprocess.Popen[bytes], log: Path, name: str) -> None:
    """Until the port accepts a connection.

    uvicorn runs the application's startup before it binds, so an accepting port
    means a started service — and asking this way spends no request against the
    rate limit part 2 is about to count exactly.
    """
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise NotRunnable(f"{name} exited during startup (code {process.returncode}):\n"
                              f"{tail(log)}")
        with socket.socket() as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise NotRunnable(f"{name} did not start within {STARTUP_SECONDS:.0f} s:\n{tail(log)}")


def stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


class Stub:
    """The stand-in model, in its own process so it never competes with the load for a CPU."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.port = free_port()
        self.process: subprocess.Popen[bytes] | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> Self:
        log = self.home / "stub.log"
        with log.open("wb") as output:
            self.process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--serve-stub", str(self.port)],
                cwd=self.home, stdout=output, stderr=subprocess.STDOUT,
            )
        wait_listening(self.port, self.process, log, "the stand-in model")
        return self

    def __exit__(self, *_: object) -> None:
        stop(self.process)


class PrivateRavis:
    """A RAVIS that can reach nothing but the stand-in.

    Every `RAVIS_*` variable is dropped and the ones that matter are set outright:
    its own database, no NERVIS or SIRVIS to publish to or read from, no
    capability trials (which would try to call a hosted model), no keychain, no
    keys from the environment, and a config directory of its own so the real
    credential file is never read. The hosted vendors' addresses and every proxy
    variable point at a closed port, with only loopback exempt, so nothing leaves
    the machine even from an adapter this list does not name. The working
    directory is private too, so a `.env` in the repository cannot leak in.
    """

    def __init__(self, home: Path, stub: Stub, *, raised_limits: bool,
                 named_client: bool = False) -> None:
        self.home = home
        self.stub = stub
        self.raised_limits = raised_limits
        self.named_client = named_client
        self.port = free_port()
        self.token = ""
        self.process: subprocess.Popen[bytes] | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def database(self) -> Path:
        return self.home / "ravis.db"

    def environment(self) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("RAVIS_") and key.lower() not in _PROXY_VARIABLES}
        env.update({
            "RAVIS_HOST": "127.0.0.1",
            "RAVIS_PORT": str(self.port),
            "RAVIS_DATABASE_PATH": str(self.database),
            "RAVIS_UPSTREAM_BASE_URL": self.stub.base,
            "RAVIS_UPSTREAM_KIND": "generic",
            "RAVIS_NERVIS_BASE_URL": "",
            "RAVIS_SIRVIS_BASE_URL": "",
            "RAVIS_EMBEDDING_BASE_URL": CLOSED,
            "RAVIS_ANTHROPIC_BASE_URL": CLOSED,
            "RAVIS_GOOGLE_BASE_URL": CLOSED,
            "RAVIS_CAPABILITY_TRIALS": "false",
            "RAVIS_CREDENTIALS_ALLOW_ENVIRONMENT": "false",
            "RAVIS_CREDENTIAL_KEYRING": "0",
            "XDG_CONFIG_HOME": str(self.home / "config"),
            "RAVIS_LOG_LEVEL": "WARNING",
        })
        for name in ("http_proxy", "https_proxy", "all_proxy"):
            env[name] = env[name.upper()] = CLOSED
        env["no_proxy"] = env["NO_PROXY"] = "127.0.0.1,localhost"
        if self.raised_limits:
            # Part 1 measures capacity, and a limiter refusing at 60 a minute would
            # measure the limiter. Part 2 keeps the defaults and measures that.
            env["RAVIS_RATE_LIMIT_PER_MINUTE"] = "10000000"
            env["RAVIS_ANONYMOUS_RATE_LIMIT_PER_MINUTE"] = "10000000"
        return env

    def _seed_client(self) -> None:
        """A `client.loadtest` credential in the private file store, private from creation."""
        directory = self.home / "config" / "ravis"
        directory.mkdir(parents=True, mode=0o700)
        self.token = secrets.token_urlsafe(32)
        descriptor = os.open(directory / "credentials.json",
                             os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump({"client.loadtest": self.token}, handle)

    def __enter__(self) -> Self:
        self.home.mkdir(parents=True)
        if self.named_client:
            self._seed_client()
        log = self.home / "ravis.log"
        with log.open("wb") as output:
            self.process = subprocess.Popen(
                [str(BIN / "ravis"), "serve"], cwd=self.home, env=self.environment(),
                stdout=output, stderr=subprocess.STDOUT,
            )
        wait_listening(self.port, self.process, log, "the private RAVIS")
        return self

    def __exit__(self, *_: object) -> None:
        stop(self.process)


_PROXY_VARIABLES = {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}


def serve_stub(port: int) -> None:
    """The stand-in model: OpenAI-shaped, pauses, then repeats the last message.

    A test double under the runbook's §7 rules: it says so in its model list and
    on every response, and it implements only what this suite sends.
    """
    import uvicorn
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response, StreamingResponse
    from starlette.routing import Route

    marked = {"x-test-double": "true"}

    async def models(_: Request) -> Response:
        return JSONResponse({
            "object": "list", "test_double": True,
            "data": [{"id": STUB_MODEL, "object": "model", "owned_by": "StubUpstream"}],
        }, headers=marked)

    async def chat(request: Request) -> Response:
        body = await request.json()
        said = str(body["messages"][-1]["content"])
        answer = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        def chunk(delta: dict[str, str], finish: str | None = None) -> bytes:
            return b"data: " + json.dumps({
                "id": answer, "object": "chat.completion.chunk", "created": created,
                "model": STUB_MODEL,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }).encode() + b"\n\n"

        if body.get("stream"):
            async def events() -> Any:
                await asyncio.sleep(STUB_PAUSE_SECONDS)
                half = len(said) // 2
                yield chunk({"role": "assistant", "content": said[:half]})
                yield chunk({"content": said[half:]})
                yield chunk({}, "stop")
                yield b"data: [DONE]\n\n"

            return StreamingResponse(events(), media_type="text/event-stream", headers=marked)
        await asyncio.sleep(STUB_PAUSE_SECONDS)
        return JSONResponse({
            "id": answer, "object": "chat.completion", "created": created, "model": STUB_MODEL,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": said}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }, headers=marked)

    routes = [Route(f"{root}/models", models) for root in ("", "/v1")]
    routes += [Route(f"{root}/chat/completions", chat, methods=["POST"]) for root in ("", "/v1")]
    uvicorn.run(Starlette(routes=routes), host="127.0.0.1", port=port, log_level="warning",
                backlog=4096)


# ── The report ──────────────────────────────────────────────────────────────


@dataclass
class Report:
    verdicts: list[dict[str, Any]] = field(default_factory=list)
    figures: dict[str, Any] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    def judge(self, key: str, passed: bool, claim: str, evidence: str) -> None:
        self.verdicts.append({"key": key, "passed": passed, "claim": claim,
                              "evidence": evidence})
        say(f"  {'PASS' if passed else 'FAIL'}  {claim}")
        say(f"        {evidence}")

    @property
    def failed(self) -> bool:
        return any(not verdict["passed"] for verdict in self.verdicts)


def overhead(direct: Level, through: Level, share: float, *, first: bool = False) -> float:
    """What RAVIS added at one percentile, as a difference of percentiles.

    Not the percentile of per-request differences — requests are not paired — but
    against a stand-in with a fixed pause the two agree closely, and this one needs
    no pairing that the load would disturb.
    """
    return through.ms(share, first=first) - direct.ms(share, first=first)


def failure_text(level: Level) -> str:
    parts = [f"{count} × {status}" for status, count in sorted(level.failures.items())]
    if level.wrong:
        parts.append(f"{level.wrong} wrong answers")
    return ", ".join(parts) if parts else "0"


def busy_note(*levels: Level) -> str:
    """A mark on a row where the test program itself was near a full core."""
    busiest = max((level.share_of_core(level.generator_seconds) or 0.0) for level in levels)
    return " ← test program busy" if busiest > GENERATOR_BUSY_CORES else ""


# ── Part 1 ──────────────────────────────────────────────────────────────────


GATEWAY_HEADER = (
    "  at once  requests │ stand-in alone │  through RAVIS  │   RAVIS adds   │"
    " per second │ failed │ CPU RAVIS / test\n"
    "                    │  p50 / p95 ms  │  p50 / p95 ms   │  p50 / p95 ms  │"
    "            │        │"
)


def gateway_row(callers: int, requests: int, direct: Level | None, through: Level,
                first: bool = False) -> str:
    alone = (f" {figure(direct.ms(0.5, first=first)):>6} / "
             f"{figure(direct.ms(0.95, first=first)):<6}│" if direct else "                │")
    adds = (f" {figure(overhead(direct, through, 0.5, first=first)):>5} / "
            f"{figure(overhead(direct, through, 0.95, first=first)):<7}│"
            if direct else "                │")
    levels = (direct, through) if direct else (through,)
    return (f"  {callers:7d}  {requests:8d} │{alone}"
            f" {figure(through.ms(0.5, first=first)):>6} / "
            f"{figure(through.ms(0.95, first=first)):<7}│{adds}"
            f" {through.per_second:10.0f} │ {failure_text(through):>6} │"
            f" {cores(through.share_of_core(through.cpu_seconds))} / "
            f"{cores(through.share_of_core(through.generator_seconds))}{busy_note(*levels)}")


async def gateway(home: Path, stub: Stub, report: Report) -> None:
    say("Part 1 of 3 — many chat requests at once, through a private RAVIS")
    say("  A copy of RAVIS with its own empty database and no keys, in front of a stand-in")
    say(f"  model that waits {STUB_PAUSE_SECONDS:.1f} s and repeats what it was sent. "
        "Nothing real is called and nothing is spent.")
    with PrivateRavis(home / "gateway", stub, raised_limits=True, named_client=True) as ravis:
        process = psutil.Process(ravis.process.pid) if ravis.process else None
        memory_start = rss_mb(process)
        peak = PeakMemory(process) if process else None
        watcher = asyncio.create_task(peak.watch()) if peak else None
        rows: list[dict[str, Any]] = []
        pairs: dict[tuple[int, bool], tuple[Level, Level]] = {}
        try:
            say()
            say(GATEWAY_HEADER)
            for stream in (False, True):
                if stream:
                    say("  streamed (time to the first words):")
                for callers, requests in (STREAM_LEVELS if stream else LEVELS):
                    direct = await completion_level("stand-in", stub.base, callers, requests,
                                                    stream=stream)
                    through = await completion_level("ravis", ravis.base, callers, requests,
                                                     stream=stream, process=process)
                    pairs[(callers, stream)] = (direct, through)
                    say(gateway_row(callers, requests, direct, through, first=stream))
                    rows.append({"stand_in": direct.as_dict(), "ravis": through.as_dict(),
                                 "overhead_p50_ms": overhead(direct, through, 0.5, first=stream),
                                 "overhead_p95_ms": overhead(direct, through, 0.95, first=stream)})

            say("  a named client instead of an anonymous one:")
            callers, requests = NAMED_LEVEL
            named = await completion_level("ravis named", ravis.base, callers, requests,
                                           headers={"Authorization": f"Bearer {ravis.token}"},
                                           process=process)
            direct, anonymous = pairs[(callers, False)]
            say(gateway_row(callers, requests, direct, named))
            recognised = callers_recorded(ravis.database)

            heaviest = max(LEVELS)[0]
            say(f"  one at a time again, straight after {heaviest} at once:")
            recovery = await completion_level("ravis recovery", ravis.base, 1, RECOVERY_REQUESTS,
                                              process=process)
            say(gateway_row(1, RECOVERY_REQUESTS, None, recovery))
        finally:
            if watcher:
                watcher.cancel()
        memory_end = rss_mb(process)
        alive = ravis.process is not None and ravis.process.poll() is None

    say()
    every = [level for pair in pairs.values() for level in pair] + [named, recovery]
    bad = [level for level in every if level.failures or level.wrong]
    stand_in_bad = [level for level in bad if level.target == "stand-in"]
    if stand_in_bad:
        # The stand-in failing means the load generator or the double broke, and
        # every figure above is suspect — that is not a RAVIS failure to report.
        raise NotRunnable("the stand-in model itself failed under load, so no RAVIS figure "
                          f"can be trusted: {stand_in_bad[0].as_dict()['first_failure']}")
    total = sum(len(level.samples) for level in every if level.target != "stand-in")
    report.judge(
        "gateway.correct", not bad,
        "Every request through RAVIS got its own answer back, at every level, streamed or not",
        f"{total} requests; " + ("no failures and no crossed or mangled answers" if not bad else
                                 "; ".join(f"{lvl.callers} at once"
                                           f"{' streamed' if lvl.stream else ''}: "
                                           f"{failure_text(lvl)} — "
                                           f"{lvl.as_dict()['first_failure'][:160]}"
                                           for lvl in bad)),
    )
    measured = {callers: (overhead(*pairs[(callers, False)], 0.5),
                          overhead(*pairs[(callers, False)], 0.95))
                for callers in JUDGED_CALLERS}
    within = all(p50 < OVERHEAD_P50_MS and p95 < OVERHEAD_P95_MS for p50, p95 in measured.values())
    report.judge(
        "gateway.overhead", within,
        f"What RAVIS adds stays inside §9.8's budget (P50 < {OVERHEAD_P50_MS:.0f} ms, "
        f"P95 < {OVERHEAD_P95_MS:.0f} ms) at 1 and 10 callers",
        "; ".join(f"{callers} at once: {p50:.1f} / {p95:.1f} ms"
                  for callers, (p50, p95) in measured.items()),
    )
    first_row = pairs[(1, False)][1]
    settled = (recovery.ms(0.5) <= first_row.ms(0.5) + RECOVERY_SLACK_MS
               and not recovery.failures and alive)
    report.judge(
        "gateway.recovers", settled,
        f"RAVIS is back to its one-caller speed straight after {heaviest} at once",
        f"one at a time: {recovery.ms(0.5):.1f} ms after, "
        f"{first_row.ms(0.5):.1f} ms before the burst (allowed +{RECOVERY_SLACK_MS:.0f} ms); "
        f"process {'still running' if alive else 'GONE'}",
    )
    report.figures["gateway"] = {
        "stand_in_pause_seconds": STUB_PAUSE_SECONDS,
        "rows": rows,
        "named": {**named.as_dict(),
                  "overhead_p50_ms": overhead(direct, named, 0.5),
                  "overhead_p95_ms": overhead(direct, named, 0.95),
                  "anonymous_overhead_p50_ms": overhead(direct, anonymous, 0.5),
                  "callers_recorded": recognised},
        "recovery": recovery.as_dict(),
        "memory_mb": {"start": memory_start, "peak": peak.peak if peak else None,
                      "end": memory_end},
    }
    say(f"  Memory: {figure(memory_start)} MB at the start, "
        f"{figure(peak.peak if peak else None)} MB at the busiest, {figure(memory_end)} MB at the end.")
    say(f"  Callers RAVIS recorded in its own database: {', '.join(recognised) or 'none'}"
        " — the named row only means something if 'loadtest' is among them.")
    say()


def callers_recorded(database: Path) -> list[str]:
    """Which application ids the private RAVIS wrote down, to check the named row was named.

    Read from its database rather than trusted: a credential RAVIS did not match is
    silently anonymous by design (§9.6.0), and a named row measured anonymously
    would look exactly like a fast credential check.
    """
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            return sorted(str(row[0]) for row in connection.execute(
                "SELECT DISTINCT application_id FROM usage_record") if row[0])
    except sqlite3.Error:
        return []


# ── Part 2 ──────────────────────────────────────────────────────────────────


async def admission(home: Path, stub: Stub, report: Report) -> None:
    from ravis.config import Settings

    limit = int(Settings.model_fields["anonymous_rate_limit_per_minute"].default)
    say("Part 2 of 3 — refusing a caller that sends too much")
    say(f"  A fresh private RAVIS with its normal limits. Callers without a key share {limit} "
        f"requests a minute; {ADMISSION_BURST} arrive at once.")
    with PrivateRavis(home / "admission", stub, raised_limits=False) as ravis:
        def call(client: httpx.AsyncClient) -> Awaitable[Sample]:
            return completion(client, ravis.base, False, {})

        # Not warmed: a warming request is a request, and this part counts them exactly.
        burst = await drive("ravis", [call] * ADMISSION_BURST, ADMISSION_CALLERS, warm=False,
                            timeout=30.0)
        ended = time.monotonic()
        admitted = [sample for sample in burst.samples if sample.status == 200]
        refused = [sample for sample in burst.samples if sample.status == 429]
        other = [sample for sample in burst.samples if sample.status not in (200, 429)]
        codes: dict[str, int] = {}
        for sample in refused:
            try:
                code = str(json.loads(sample.detail)["error"]["code"])
            except (ValueError, LookupError, TypeError):
                code = "unreadable"
            codes[code] = codes.get(code, 0) + 1
        report.judge(
            "admission.refuses",
            len(admitted) == limit and len(refused) == ADMISSION_BURST - limit
            and not other and set(codes) == {"RATE_LIMITED"}
            and all(sample.correct for sample in admitted),
            f"Exactly {limit} of {ADMISSION_BURST} are let through and the rest are refused "
            "with a clear 'rate limited' answer, not an error",
            f"{len(admitted)} answered, {len(refused)} refused "
            f"({', '.join(f'{n} × {c}' for c, n in codes.items()) or 'none'}), "
            f"{len(other)} other"
            + (f" — first other: {other[0].status} {other[0].detail[:160]}" if other else ""),
        )

        wait = max(0.0, WINDOW_SECONDS + 1.0 - (time.monotonic() - ended))
        say(f"  Waiting {wait:.0f} s for RAVIS's one-minute window to pass…")
        while wait > 0:
            step = min(15.0, wait)
            await asyncio.sleep(step)
            wait -= step
            if wait > 0:
                say(f"    {wait:.0f} s to go")
        later = await drive("ravis", [call] * AFTER_WINDOW_REQUESTS, 1, warm=False, timeout=30.0)
        alive = ravis.process is not None and ravis.process.poll() is None
    answered = sum(1 for sample in later.samples if sample.status == 200 and sample.correct)
    report.judge(
        "admission.recovers", answered == AFTER_WINDOW_REQUESTS and alive,
        "Once the minute has passed, the same caller is served again",
        f"{answered} of {AFTER_WINDOW_REQUESTS} answered; "
        f"process {'still running' if alive else 'GONE'}",
    )
    report.figures["admission"] = {"limit_per_minute": limit, "burst": ADMISSION_BURST,
                                   "admitted": len(admitted), "refused": len(refused),
                                   "refusal_codes": codes, "other": len(other),
                                   "after_window_answered": answered}
    say()


# ── Part 3 ──────────────────────────────────────────────────────────────────


def service_process(name: str, port: int | None = None) -> psutil.Process | None:
    """The running `<name> serve`, found by its command line — and, given `port`, the one of
    them listening there.

    **The port, because a name is not enough.** The soak test runs a private RAVIS beside the
    live one, and both are `ravis serve`. Picking the first by process number watched the live
    one on 16 September 2026 and the private one on 17 September, after process numbers wrapped,
    so that run never measured the live RAVIS's memory, files or threads.
    """
    for process in psutil.process_iter(["cmdline"]):
        cmdline = process.info.get("cmdline") or []
        if not ("serve" in cmdline and any(Path(part).name == name for part in cmdline)):
            continue
        if port is None or _listens_on(process, port):
            return process
    return None


def _listens_on(process: psutil.Process, port: int) -> bool:
    try:
        connections = process.net_connections(kind="inet")
    except (psutil.Error, OSError):
        return False
    return any(c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port
               for c in connections)


async def timed_get(client: httpx.AsyncClient, url: str, label: str,
                    headers: dict[str, str] | None = None) -> Sample:
    started = time.perf_counter()
    try:
        response = await client.get(url, headers=headers or {})
    except httpx.HTTPError as failure:
        return Sample(0, time.perf_counter() - started, detail=f"{type(failure).__name__}",
                      label=label)
    elapsed = time.perf_counter() - started
    relay_failure = response.headers.get("x-nervis-relay", "")
    ok = 200 <= response.status_code < 300 and not relay_failure
    return Sample(200 if ok else response.status_code, elapsed, correct=ok, label=label,
                  detail=relay_failure or f"{len(response.content)} bytes")


def read_call(url: str, label: str) -> Call:
    def call(client: httpx.AsyncClient) -> Awaitable[Sample]:
        return timed_get(client, url, label)

    return call


async def live(report: Report) -> None:
    say("Part 3 of 3 — the running NERVIS and RAVIS, read-only")
    say("  Only reads the dashboard already makes; nothing is changed, no model is called.")
    async with httpx.AsyncClient(timeout=15.0) as client:
        health = await timed_get(client, f"{LIVE_NERVIS}/api/v1/health", "health")
        if health.status != 200:
            raise NotRunnable("NERVIS is not answering on 8790 — start the stack with "
                              "`python3 tools/run.py start` and run this part again")

        token = ADMIN_TOKEN.read_text().strip() if ADMIN_TOKEN.exists() else ""
        identity: dict[str, Any] = {}
        if token:
            say(f"  RAVIS: {LIVE_IDENTITY_REQUESTS} anonymous and {LIVE_IDENTITY_REQUESTS} "
                "named reads of its model list, alternating, one at a time")
            named_header = {"Authorization": f"Bearer {token}"}
            await timed_get(client, f"{LIVE_RAVIS}/v1/models", "warm")
            await timed_get(client, f"{LIVE_RAVIS}/v1/models", "warm", named_header)
            anonymous: list[Sample] = []
            named: list[Sample] = []
            for _ in range(LIVE_IDENTITY_REQUESTS):
                anonymous.append(await timed_get(client, f"{LIVE_RAVIS}/v1/models", "anonymous"))
                named.append(await timed_get(client, f"{LIVE_RAVIS}/v1/models", "named",
                                             named_header))
            a = Level("ravis anonymous", 1, len(anonymous), False, anonymous, 1.0)
            n = Level("ravis named", 1, len(named), False, named, 1.0)
            identity = {"anonymous_p50_ms": a.ms(0.5), "named_p50_ms": n.ms(0.5),
                        "anonymous_p95_ms": a.ms(0.95), "named_p95_ms": n.ms(0.95),
                        "anonymous_body": anonymous[-1].detail, "named_body": named[-1].detail,
                        "failures": {**a.failures, **n.failures}}
            say(f"    anonymous {a.ms(0.5):.1f} / {a.ms(0.95):.1f} ms, "
                f"named {n.ms(0.5):.1f} / {n.ms(0.95):.1f} ms (p50 / p95); "
                f"bodies {anonymous[-1].detail} and {named[-1].detail}")
        else:
            say("  (no cached admin credential in .run, so the named-caller comparison is skipped)")

    since = int(time.time()) - 3600
    reads = (
        ("health", "/api/v1/health"),
        ("services", "/api/v1/services"),
        ("system", "/api/v1/system"),
        ("registry", "/api/v1/registry/instances"),
        ("spend via relay", f"/api/v1/relay/ravis/api/v1/usage?since={since}"),
    )
    nervis = service_process("nervis")
    memory_start = rss_mb(nervis)
    say("  NERVIS: the dashboard's reads, many at once")
    say("  at once  requests │ " + " │ ".join(f"{label:>15}" for label, _ in reads)
        + " │ failed │ CPU NERVIS / test")
    say("                    │ " + " │ ".join(f"{'p50 / p95 ms':>15}" for _ in reads) + " │")
    levels: list[dict[str, Any]] = []
    failed_total = 0
    first_failure = ""
    for callers, rounds in NERVIS_LEVELS:
        calls = [read_call(f"{LIVE_NERVIS}{path}", label)
                 for _ in range(rounds) for label, path in reads]
        level = await drive("nervis", calls, callers, timeout=15.0, process=nervis)
        by_read = {label: Level(label, callers, rounds, False,
                                [s for s in level.samples if s.label == label], level.wall_seconds)
                   for label, _ in reads}
        failures = [s for s in level.samples if not s.correct]
        failed_total += len(failures)
        first_failure = first_failure or next(
            (f"{s.label}: {s.status} {s.detail}" for s in failures), "")
        say(f"  {callers:7d}  {len(level.samples):8d} │ "
            + " │ ".join(f"{figure(read.ms(0.5)):>6} / {figure(read.ms(0.95)):<6}"
                        for read in by_read.values())
            + f" │ {len(failures):>6} │ {cores(level.share_of_core(level.cpu_seconds))} / "
            f"{cores(level.share_of_core(level.generator_seconds))}{busy_note(level)}")
        levels.append({"callers": callers, "requests": len(level.samples),
                       "service_cores": level.share_of_core(level.cpu_seconds),
                       "generator_cores": level.share_of_core(level.generator_seconds),
                       "reads": {label: read.as_dict() for label, read in by_read.items()},
                       "failed": len(failures)})
    memory_end = rss_mb(nervis)
    say()
    report.judge(
        "live.nervis_answers", failed_total == 0,
        "NERVIS answers every dashboard read, with up to 50 at once, and the relay to RAVIS "
        "never fails",
        f"{sum(level['requests'] for level in levels)} reads, {failed_total} failed"
        + (f" — first: {first_failure}" if first_failure else ""),
    )
    report.figures["live"] = {"ravis_identity": identity, "nervis_levels": levels,
                              "nervis_memory_mb": {"start": memory_start, "end": memory_end}}
    say(f"  NERVIS memory: {figure(memory_start)} MB before, {figure(memory_end)} MB after.")
    say()


# ── Entry ───────────────────────────────────────────────────────────────────


def git_head() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5,
                              check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


async def run(parts: list[str]) -> int:
    started = datetime.now(timezone.utc)
    say(f"Load test — {started.astimezone():%Y-%m-%d %H:%M}, commit {git_head() or 'unknown'}, "
        f"{os.cpu_count()} cores")
    say()
    report = Report()
    private = [part for part in parts if part in ("gateway", "admission")]
    if private:
        with tempfile.TemporaryDirectory(prefix="ravis-load-") as scratch:
            home = Path(scratch)
            try:
                with Stub(home) as stub:
                    for part, perform in (("gateway", gateway), ("admission", admission)):
                        if part not in private:
                            continue
                        try:
                            await perform(home, stub, report)
                        except NotRunnable as reason:
                            report.skipped.append(f"{part}: {reason}")
                            say(f"  NOT RUN — {reason}")
                            say()
            except NotRunnable as reason:
                report.skipped.extend(f"{part}: {reason}" for part in private)
                say(f"  NOT RUN — {reason}")
    if "live" in parts:
        try:
            await live(report)
        except NotRunnable as reason:
            report.skipped.append(f"live: {reason}")
            say(f"  NOT RUN — {reason}")
            say()

    passed = sum(1 for verdict in report.verdicts if verdict["passed"])
    say(f"Result: {passed} of {len(report.verdicts)} checks passed"
        + (f", {len(report.skipped)} part(s) not run" if report.skipped else ""))
    RUN.mkdir(exist_ok=True)
    EVIDENCE.write_text(json.dumps({
        "started": started.isoformat(),
        "finished": datetime.now(timezone.utc).isoformat(),
        "commit": git_head(),
        "machine": {"cores": os.cpu_count(),
                    "memory_gb": round(psutil.virtual_memory().total / 1024 ** 3, 1)},
        "parts": parts,
        "verdicts": report.verdicts,
        "skipped": report.skipped,
        "figures": report.figures,
    }, indent=2, default=str))
    say(f"Evidence: {EVIDENCE.relative_to(ROOT)}")
    if report.failed:
        return EXIT_FAILED
    if report.skipped:
        return EXIT_INCOMPLETE if report.verdicts else EXIT_NOT_RUNNABLE
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--part", action="append", choices=PARTS,
                        help="run only this part; repeat for several (default: all three)")
    parser.add_argument("--serve-stub", type=int, metavar="PORT", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve_stub:
        serve_stub(args.serve_stub)
        return EXIT_OK
    if not (BIN / "ravis").exists():
        print("No launcher environment yet: run `python3 tools/run.py start` once first.",
              file=sys.stderr)
        return EXIT_NOT_RUNNABLE
    parts = args.part or list(PARTS)
    return asyncio.run(run([part for part in PARTS if part in parts]))


if __name__ == "__main__":
    sys.exit(main())
