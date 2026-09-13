"""RAVIS's one long-lived Codex app-server: started, watched, restarted and stopped (design §4.4).

Owner decision D1 has RAVIS run **one** `codex app-server` for the whole Mac. It hosts every Codex
thread, the sign-in and the allowance reads (design §2.1): Codex allows one stdio client per
process, and one process on the Codex home means one token cache, so two processes never race to
refresh a sign-in. The price is stated plainly in the design: if this process crashes or hangs,
every project's running turn is cut off at once, so recovery has to be explicit. This module is
that recovery.

**The command** (design §4.4), with the file-rules profile's flags added once calibration fixes
their syntax (`pin.py`):

    <executable> app-server --listen stdio:// -c cli_auth_credentials_store="file"
      -c analytics.enabled=false -c features.plugins=false
      -c sandbox_workspace_write.exclude_slash_tmp=true
      -c sandbox_workspace_write.exclude_tmpdir_env_var=true

`features.plugins=false` is there so the process doesn't fetch OpenAI's plugin marketplace each
time it starts (brief §1, design §4.7); calibration K10 confirms the flag holds.

**Its environment is an allow-list** (design §4.4): `PATH` (plus Homebrew's `bin`), `HOME` — the
real one, which commands need for npm, pip and git — `LANG`, `LC_*`, `USER`, `LOGNAME`, `SHELL`,
`TERM=dumb`, `CODEX_HOME` and a `TMPDIR` inside that home. No `RAVIS_*`, `NERVIS_*`, `CLARVIS_*`
or `OPENAI_*` variable, and nothing named like a token, secret, key, credential or password.

**Its own session** (`start_new_session=True`), so a signal meant for RAVIS doesn't reach it, and
signals RAVIS sends reach only its pid — never a process group, which would span every project's
commands (design §4.5).

**Watching it.** The process has ended when it exits or its output ends. It has hung when a write
to it stays blocked for ten seconds (`rpc.py`), or when it stops answering the local health probe,
`thread/loaded/list {limit: 1}`: checked every 60 s while no request is in flight, it needs no
network, so a network blip can't restart Codex and lose a sign-in (review AL6). Three misses in a
row while no turn is running restart it; while turns run, two minutes of misses do.

**Restarting it.** After 1 s, 5 s, 30 s, then every 5 minutes. After five failures within 30
minutes RAVIS stops trying (`runtime_down`) until the check passes again on a changed binary
(design §4.4). Threads are never resumed automatically.

**A new binary on disk, or one that no longer passes the check,** is swapped only when no turn is
active: the old process gets the end sequence and a new one starts if the new binary may run
(design §4.4). **The end sequence** closes Codex's input, waits, then SIGTERM, waits, then SIGKILL,
and confirms the exit. RAVIS's own shutdown uses shorter waits than a swap does, because the
launcher gives RAVIS six seconds before it forces it (`tools/run.py`, `DEFAULT_GRACE_BEFORE_KILL`);
and should RAVIS be killed anyway, Codex sees its input close and exits by itself (brief §1).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from ravis.codex.routing import ActiveTurns, MessageRouter
from ravis.codex.rpc import (
    LINE_LIMIT_BYTES,
    CodexRpcError,
    CodexTimeoutError,
    CodexUnavailableError,
    Connection,
    RpcTimings,
)

logger = logging.getLogger("ravis")

#: What the process is doing, as `GET /api/v1/codex` reports it in `runtime.process.state`.
ProcessState = Literal["not_started", "starting", "running", "restarting", "failed", "stopped"]

#: The flags every start carries (design §4.4). Values are TOML, so the string is quoted.
FIXED_FLAGS: tuple[str, ...] = (
    "-c", 'cli_auth_credentials_store="file"',
    "-c", "analytics.enabled=false",
    "-c", "features.plugins=false",
    "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
    "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
    # **Let Codex ask for a command's extra permissions** (owner decision, 13 September 2026:
    # network after approval). Calibration run 1 found that no command, approved or not, reached
    # the network, and that Codex never asked (K3, K4) and never requested permissions (K8): both
    # features are off by default. With them on, Codex can ask to run one command with
    # `additional_permissions` such as `network.enabled`, which arrives as an approval RAVIS relays.
    # Both are marked "underDevelopment" in Codex 0.154.0's `experimentalFeature/list`, so a new
    # build's acceptance check and calibration re-test must confirm they still behave.
    "-c", "features.exec_permission_approvals=true",
    "-c", "features.request_permissions_tool=true",
)
#: The variables passed through from RAVIS's own environment, by exact name (design §4.4).
PASSED_VARIABLES = ("PATH", "HOME", "LANG", "USER", "LOGNAME", "SHELL")
#: Never passed, even when a name above would allow it: RAVIS's and its peers' settings, OpenAI's,
#: and anything named like a secret.
WITHHELD_PREFIXES = ("RAVIS_", "NERVIS_", "CLARVIS_", "OPENAI_")
SECRET_LIKE = re.compile(r"TOKEN|SECRET|KEY|CREDENTIAL|PASSWORD", re.IGNORECASE)
#: How much of Codex's error output is kept, only to log why a start failed.
STDERR_TAIL_BYTES = 4096


@dataclass(frozen=True)
class LaunchPlan:
    """Which Codex to start, and where: decided by the runtime check, never by a request."""

    #: The file the check verified — signature, sha256, schema trees — which is what runs.
    executable: Path
    sha256: str
    version: str
    home: Path
    #: The folder holding Homebrew's link, added to `PATH` so Codex finds its own helpers.
    link_folder: Path | None = None
    #: The file-rules profile's flags, once calibration has fixed them (`pin.py`).
    profile_flags: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str, tuple[str, ...]]:
        """What makes two plans the same process: the file, its build, and its flags."""
        return (str(self.executable), self.sha256, self.profile_flags)


@dataclass(frozen=True)
class SupervisorTimings:
    """The supervisor's clocks. Tests shorten them; the defaults are the design's (§4.4, §4.8)."""

    initialize_seconds: float = 15.0
    health_interval_seconds: float = 60.0
    health_timeout_seconds: float = 10.0
    health_misses_when_idle: int = 3
    health_miss_seconds_when_busy: float = 120.0
    restart_backoff_seconds: tuple[float, ...] = (1.0, 5.0, 30.0, 300.0)
    failure_window_seconds: float = 1800.0
    failures_before_giving_up: int = 5
    #: How often the watch re-reads the plan, even when nothing woke it.
    watch_tick_seconds: float = 1.0
    #: The end sequence's two waits, for a swap when idle, for RAVIS's shutdown, and after a crash.
    swap_waits: tuple[float, float] = (10.0, 3.0)
    shutdown_waits: tuple[float, float] = (3.0, 1.0)
    failure_waits: tuple[float, float] = (1.0, 1.0)
    rpc: RpcTimings = RpcTimings()


class _StartFailedError(Exception):
    """The process started but did not become usable; `identity` when it isn't the Codex checked."""

    def __init__(self, reason: str, *, identity: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.identity = identity


def child_arguments(plan: LaunchPlan) -> list[str]:
    """The whole command line the app-server is started with."""
    return [
        str(plan.executable), "app-server", "--listen", "stdio://",
        *FIXED_FLAGS, *plan.profile_flags,
    ]


def child_environment(plan: LaunchPlan, environ: Mapping[str, str]) -> dict[str, str]:
    """The app-server's environment, built from an allow-list rather than by removing names."""
    passed = {name: environ[name] for name in PASSED_VARIABLES if name in environ}
    passed.update({name: value for name, value in environ.items() if name.startswith("LC_")})
    if plan.link_folder is not None:
        passed["PATH"] = os.pathsep.join(
            part for part in (passed.get("PATH", ""), str(plan.link_folder)) if part
        )
    passed.update(CODEX_HOME=str(plan.home), TMPDIR=str(plan.home / "tmp"), TERM="dumb")
    return {name: value for name, value in passed.items() if not _withheld(name)}


def _withheld(name: str) -> bool:
    return name.startswith(WITHHELD_PREFIXES) or SECRET_LIKE.search(name) is not None


def user_agent_version(user_agent: object) -> str | None:
    """The Codex version inside `initialize`'s `userAgent`: the text between the first `/` and ` (`.

    `ravis/0.154.0 (Mac OS 27.0.0; arm64) unknown (ravis; 0.23.9)` reads as `0.154.0` (brief §2).
    There is no protocol version field, so this is the only way to see which build answered.
    """
    if not isinstance(user_agent, str) or "/" not in user_agent:
        return None
    return user_agent.split("/", 1)[1].split(" (", 1)[0] or None


def identity_problem(result: object, plan: LaunchPlan) -> str | None:
    """Why the process that answered `initialize` isn't the Codex the check verified, if it isn't.

    Design §4.1 item 6: the version in `userAgent` must equal the one `--version` printed, and the
    home Codex reports must be RAVIS's, compared as real paths. A mismatch means the binary changed
    between the check and the start, or Codex ignored `CODEX_HOME` — either way RAVIS doesn't know
    what it is talking to, and says `not_available` rather than carrying on.
    """
    if not isinstance(result, dict):
        return "Codex's answer to initialize was not an object"
    version = user_agent_version(result.get("userAgent"))
    if version != plan.version:
        return (
            f"Codex answered initialize as version {version}, but the checked binary is "
            f"{plan.version}"
        )
    home = result.get("codexHome")
    if not isinstance(home, str) or os.path.realpath(home) != os.path.realpath(plan.home):
        return f"Codex is using the home {home!r}, not RAVIS's {plan.home}"
    return None


class CodexSupervisor:
    """Keeps the one Codex process running while the runtime check allows it, and no longer.

    `plan` is asked, over and over, which Codex may run now; `None` means none may (not installed,
    not available, an untested build, or switched off). `on_ready` is called when a process has
    answered `initialize`, and `on_ended` with the reason whenever one stops. Both only schedule
    work, like every handler on the reader's path.
    """

    def __init__(
        self,
        *,
        plan: Callable[[], LaunchPlan | None],
        router: MessageRouter,
        turns: ActiveTurns,
        on_ready: Callable[[], None],
        on_ended: Callable[[str], None],
        client_version: str,
        timings: SupervisorTimings = SupervisorTimings(),
        clock: Callable[[], float] = time.monotonic,
        environ: Callable[[], Mapping[str, str]] = lambda: os.environ,
    ) -> None:
        self._plan = plan
        self._router = router
        self._turns = turns
        self._on_ready = on_ready
        self._on_ended = on_ended
        self._client_version = client_version
        self._timings = timings
        self._clock = clock
        self._environ = environ
        self._connection: Connection | None = None
        self._failures: deque[float] = deque()
        self._restarts: deque[float] = deque()
        self._change = asyncio.Event()
        self._stopping = False
        self._started_before = False
        # The plan RAVIS stopped trying to start, so a changed binary is tried afresh.
        self._given_up_on: tuple[str, str, tuple[str, ...]] | None = None
        self.state: ProcessState = "not_started"
        self.since: datetime | None = None
        self.running_sha256: str | None = None
        #: The running app-server's pid: calibration's K6 attributes its descendants to tasks.
        self.pid: int | None = None
        #: Codex's last error line when a start failed — its own words when it rejects a `-c` flag,
        #: which is how calibration learns the profile's syntax (design §4.9).
        self.start_error: str | None = None
        #: Why the last process ended unexpectedly, or failed to start.
        self.failure: str | None = None
        #: Set when the process that started wasn't the Codex the check verified (§4.1 item 6).
        self.identity_failure: str | None = None

    def restarts_24h(self) -> int:
        """How many times Codex was started again, after its first start, in the last 24 hours."""
        horizon = self._clock() - 86400.0
        while self._restarts and self._restarts[0] < horizon:
            self._restarts.popleft()
        return len(self._restarts)

    def wake(self) -> None:
        """Look at the plan again now: the check's answer, or the turns, may have changed."""
        self._change.set()

    async def request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float
    ) -> Any:
        """One request to the running process; `CodexUnavailableError` when none is running."""
        connection = self._connection
        if connection is None:
            raise CodexUnavailableError("Codex's process is not running")
        return await connection.request(method, params, timeout=timeout)

    async def run(self) -> None:
        """Supervise until `stop()`. Started once, by the Codex service, from RAVIS's lifespan."""
        while not self._stopping:
            plan = self._plan()
            if plan is None or plan.key == self._given_up_on:
                self._rest(plan)
                await self._wait_for_change(None)
                continue
            if self.identity_failure is not None and plan.key != self._given_up_on:
                self.identity_failure = None
            ended = await self._run_child(plan)
            if ended is not None and not self._stopping:
                await self._after_failure(plan, ended)
        self._set_state("stopped")

    async def stop(self) -> None:
        """Ask the supervisor to end the process with the shutdown waits; `run` then returns."""
        self._stopping = True
        self._change.set()

    def _rest(self, plan: LaunchPlan | None) -> None:
        """No process: either none may run, or RAVIS gave up on this one."""
        self._set_state("failed" if plan is not None else "not_started")

    async def _after_failure(self, plan: LaunchPlan, reason: str) -> None:
        now = self._clock()
        self._failures.append(now)
        while self._failures and self._failures[0] < now - self._timings.failure_window_seconds:
            self._failures.popleft()
        self.failure = reason
        if len(self._failures) >= self._timings.failures_before_giving_up:
            logger.error("codex: giving up after %d failures: %s", len(self._failures), reason)
            self._given_up_on = plan.key
            self._set_state("failed")
            return
        logger.warning("codex: the process ended unexpectedly, restarting: %s", reason)
        self._set_state("restarting")
        backoff = self._timings.restart_backoff_seconds
        await self._wait_for_change(backoff[min(len(self._failures), len(backoff)) - 1])

    async def _run_child(self, plan: LaunchPlan) -> str | None:
        """Start one process and watch it; the reason it ended unexpectedly, or None if asked to."""
        self._set_state("restarting" if self._started_before else "starting")
        try:
            process = await self._spawn(plan)
        except OSError as failure:
            return f"Codex could not be started: {failure.strerror or failure}"
        self.pid = process.pid
        tail = _StderrTail()
        connection = Connection(
            process.stdout,  # type: ignore[arg-type]
            process.stdin,  # type: ignore[arg-type]
            on_notification=self._router.notification,
            on_request=self._router.request,
            timings=self._timings.rpc,
            clock=self._clock,
        )
        connection.start()
        draining = asyncio.create_task(tail.drain(process.stderr))
        ended, waits = await self._supervise(process, connection, plan, tail)
        self._connection = None
        self.running_sha256 = None
        await _end(process, connection, waits)
        self.pid = None
        await connection.stop(ended or "Codex's process was stopped")
        draining.cancel()
        self._turns.forget_all()
        self._on_ended(ended or "Codex's process was stopped")
        return ended

    async def _supervise(
        self,
        process: asyncio.subprocess.Process,
        connection: Connection,
        plan: LaunchPlan,
        tail: _StderrTail,
    ) -> tuple[str | None, tuple[float, float]]:
        """Initialize, then watch until the process must end: (the unexpected reason, the waits)."""
        try:
            await self._initialize(connection, plan)
        except _StartFailedError as failure:
            logger.warning("codex: start failed: %s (last error line: %s)", failure, tail.last())
            self.start_error = tail.last() or failure.reason
            if not failure.identity:
                return failure.reason, self._timings.failure_waits
            self.identity_failure = failure.reason
            self._given_up_on = plan.key
            return None, self._timings.failure_waits
        self._became_ready(connection, plan)
        return await self._watch(process, connection, plan)

    async def _spawn(self, plan: LaunchPlan) -> asyncio.subprocess.Process:
        _prepare_home(plan.home)
        return await asyncio.create_subprocess_exec(
            *child_arguments(plan),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_environment(plan, self._environ()),
            cwd=str(plan.home),
            start_new_session=True,
            limit=LINE_LIMIT_BYTES,
        )

    async def _initialize(self, connection: Connection, plan: LaunchPlan) -> None:
        """`initialize` with the experimental API switched on, then `initialized` (brief §2).

        The experimental API is needed for permission profiles and background terminals, which the
        file rules and per-task clean-up depend on; the version pin covers that surface (§4.4).
        """
        params = {
            "clientInfo": {"name": "ravis", "title": "RAVIS", "version": self._client_version},
            "capabilities": {"experimentalApi": True},
        }
        try:
            result = await connection.request(
                "initialize", params, timeout=self._timings.initialize_seconds
            )
        except (CodexRpcError, CodexUnavailableError) as failure:
            raise _StartFailedError(f"Codex did not complete initialize: {failure}") from None
        problem = identity_problem(result, plan)
        if problem is not None:
            raise _StartFailedError(problem, identity=True)
        connection.notify("initialized")

    def _became_ready(self, connection: Connection, plan: LaunchPlan) -> None:
        self._connection = connection
        self.running_sha256 = plan.sha256
        self.start_error = None
        self.since = datetime.now(UTC)
        if self._started_before:
            self._restarts.append(self._clock())
        self._started_before = True
        self._set_state("running")
        self._on_ready()

    async def _watch(
        self, process: asyncio.subprocess.Process, connection: Connection, plan: LaunchPlan
    ) -> tuple[str | None, tuple[float, float]]:
        probe = _HealthProbe(connection, self._turns, self._timings, self._clock)
        probing = asyncio.create_task(probe.run())
        exited = asyncio.create_task(process.wait())
        try:
            while True:
                self._change.clear()
                unexpected = self._unexpected_end(process, connection, probe)
                if unexpected is not None:
                    return unexpected, self._timings.failure_waits
                deliberate = self._deliberate_end(plan)
                if deliberate is not None:
                    return None, deliberate
                await _first_of(
                    [exited, connection.closed.wait(), self._change.wait(), probe.failed.wait()],
                    self._timings.watch_tick_seconds,
                )
        finally:
            probing.cancel()
            exited.cancel()

    def _unexpected_end(
        self, process: asyncio.subprocess.Process, connection: Connection, probe: _HealthProbe
    ) -> str | None:
        hung_after = self._timings.rpc.hung_after_seconds
        if connection.hung or connection.writer_blocked_seconds() >= hung_after:
            return f"Codex stopped reading its input for {hung_after:g} s"
        if process.returncode is not None:
            return f"Codex's process exited with status {process.returncode}"
        if connection.closed.is_set():
            return connection.close_reason
        return probe.verdict

    def _deliberate_end(self, plan: LaunchPlan) -> tuple[float, float] | None:
        """The waits to end with when RAVIS is stopping, or the plan changed while idle."""
        if self._stopping:
            return self._timings.shutdown_waits
        current = self._plan()
        changed = current is None or current.key != plan.key
        if changed and self._turns.count == 0:
            return self._timings.swap_waits
        return None

    async def _wait_for_change(self, seconds: float | None) -> None:
        self._change.clear()
        # `asyncio.timeout`, not `wait_for`, for the cancellation reason given in `rpc.py`.
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(seconds):
                await self._change.wait()

    def _set_state(self, state: ProcessState) -> None:
        if state != "running":
            self.since = None if state in ("not_started", "stopped", "failed") else self.since
        self.state = state


class _HealthProbe:
    """The local health check (design §4.4, review AL6), as a task beside one process's watch."""

    def __init__(
        self,
        connection: Connection,
        turns: ActiveTurns,
        timings: SupervisorTimings,
        clock: Callable[[], float],
    ) -> None:
        self._connection = connection
        self._turns = turns
        self._timings = timings
        self._clock = clock
        self.failed = asyncio.Event()
        self.verdict: str | None = None

    async def run(self) -> None:
        misses = 0
        first_miss: float | None = None
        while not self.failed.is_set():
            await asyncio.sleep(self._timings.health_interval_seconds)
            if self._connection.pending_requests:
                continue
            answered = await self._probe()
            if answered is None:
                return
            if answered:
                misses, first_miss = 0, None
                continue
            misses += 1
            first_miss = self._clock() if first_miss is None else first_miss
            self._judge(misses, first_miss)

    async def _probe(self) -> bool | None:
        """True when Codex answered (a refusal is an answer), False on timeout, None if closed."""
        try:
            await self._connection.request(
                "thread/loaded/list", {"limit": 1}, timeout=self._timings.health_timeout_seconds
            )
        except CodexRpcError:
            return True
        except CodexTimeoutError:
            return False
        except CodexUnavailableError:
            return None
        return True

    def _judge(self, misses: int, first_miss: float) -> None:
        if self._turns.count == 0 and misses >= self._timings.health_misses_when_idle:
            self.verdict = f"Codex did not answer {misses} health checks in a row"
        elif self._clock() - first_miss >= self._timings.health_miss_seconds_when_busy:
            self.verdict = "Codex did not answer its health checks for two minutes during a turn"
        else:
            return
        self.failed.set()


class _StderrTail:
    """The end of Codex's error output: drained so its pipe never fills, kept for one log line."""

    def __init__(self) -> None:
        self._tail = bytearray()

    async def drain(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while chunk := await stream.read(65536):
            self._tail = (self._tail + chunk)[-STDERR_TAIL_BYTES:]

    def last(self) -> str:
        lines = [line.strip() for line in self._tail.decode("utf-8", "replace").splitlines()]
        present = [line for line in lines if line]
        return present[-1][:200] if present else "none"


def _prepare_home(home: Path) -> None:
    """The Codex home and its temporary folder, private to the owner (design §4.2: 0700)."""
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    home.chmod(0o700)
    (home / "tmp").mkdir(mode=0o700, exist_ok=True)


async def _end(
    process: asyncio.subprocess.Process, connection: Connection, waits: tuple[float, float]
) -> None:
    """The end sequence: close input → wait → SIGTERM → wait → SIGKILL → confirm (design §4.4)."""
    close_wait, terminate_wait = waits
    if process.returncode is None:
        connection.close_input()
        if not await _exited(process, close_wait):
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            if not await _exited(process, terminate_wait):
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
    await process.wait()


async def _exited(process: asyncio.subprocess.Process, seconds: float) -> bool:
    try:
        async with asyncio.timeout(seconds):
            await process.wait()
    except TimeoutError:
        return False
    return True


async def _first_of(waits: list[Awaitable[Any]], seconds: float) -> None:
    """Return when any of `waits` finishes or `seconds` pass, cancelling what this call created."""
    created = [
        wait if isinstance(wait, asyncio.Future) else asyncio.ensure_future(wait)
        for wait in waits
    ]
    owned = [task for task, wait in zip(created, waits, strict=True) if task is not wait]
    try:
        await asyncio.wait(created, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in owned:
            task.cancel()
