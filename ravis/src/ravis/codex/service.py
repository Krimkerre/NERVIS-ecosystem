"""RAVIS's Codex, put together: the check, the one process, the account, the allowance, the re-test.

This is M29's second increment (R2) in one place. Each part lives in its own module; this service
owns their state and the order things happen in, and is what the `/api/v1/codex` routes call
(`api/management/codex.py`). It is built with the application — building it runs nothing — and
started by RAVIS's lifespan (`app.py`).

**At start** (`start()`): read RAVIS's own record (`codex-state.json`), report honestly what a
restart cut off (a sign-in, a re-test), sweep the throwaway folders a killed check or re-test left,
run the runtime check, and start the background tasks:

- **the supervisor** (`supervisor.py`), which keeps one `codex app-server` running while the check
  allows it: a build that is tested or accepted. **A build neither tested nor accepted gets no
  process at all**, and so no sign-in, since sign-in happens inside that process (design §4.4).
  **A tested or accepted build whose file rules are unproven does get one**, because the sign-in,
  the allowance and the re-test that could prove those rules all live in it; only tasks stay
  paused (owner decision D2 refuses *sessions*, M29's third increment). Reading the design any
  other way would leave calibration and the re-test with no process to run in.
- **the account messages** Codex sends — a sign-in completed, the account changed, the allowance
  updated — applied in order, without waiting on Codex (§4.4's per-consumer queue);
- **the account refresh**: `account/read`, then the models, then the allowance, whenever the
  process starts or the account changes;
- **the allowance loop**: a read every 15 minutes while no turn runs, one 30 seconds after the last
  turn ends, one straight after a turn failed on the plan's usage limit, and one just after a
  used-up allowance's reset — and none while turns run, when Codex's own notifications keep the
  figure current (design §3.3);
- **housekeeping**, every few seconds: a sign-in left past its ten minutes ends; a stalled message
  consumer is restarted without touching the process; and once a minute the executable is looked
  at again, and the whole check re-run only if it changed (design §4.1).

**State changes are published** as `ravis.codex.state_changed {from, to, reason_code}` (design
§3.9) whenever the one state word moves, whatever moved it.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ravis.codex import refusals
from ravis.codex.acceptance import HandshakeTimings, VersionCheck, check_version
from ravis.codex.account import Account, account_from_read, needs_account_id
from ravis.codex.idempotency import KeptAnswers
from ravis.codex.pin import FileRulesProfile, PinUnreadableError, file_rules_profile, read_pin
from ravis.codex.reprove import (
    Outcome,
    ReproofTimings,
    decoy_folder,
    discard_plan,
    prepare_plan,
    run_reproof,
    sweep_reproof_folders,
)
from ravis.codex.routing import ActiveTurns, Inbox, MessageRouter
from ravis.codex.rpc import CodexRpcError, CodexTimeoutError, CodexUnavailableError
from ravis.codex.runtime import CODESIGN, SCRATCH_FOLDER, BuildRecords, CodexRuntime, sweep_scratch
from ravis.codex.sign_in import (
    RESTARTED_DURING_SIGN_IN,
    SIGN_IN_LIFETIME,
    SIGN_IN_PORTS,
    SignIn,
    busy_ports,
    callback_port,
)
from ravis.codex.state import (
    ModelFacts,
    ProcessFacts,
    Reading,
    codex_body,
    decide,
    models_from_list,
    used_up_reason,
)
from ravis.codex.store import (
    FILE_NAME,
    AcceptedBuild,
    CodexRecord,
    CodexStateFile,
    ConfirmedAccount,
    ReproofRecord,
)
from ravis.codex.supervisor import CodexSupervisor, LaunchPlan, SupervisorTimings
from ravis.codex.usage import UNKNOWN, Usage, from_read, is_exhausted, iso, merged, next_reset
from ravis.config import Settings, codex_home, data_directory
from ravis.credentials import config_directory
from ravis.ecosystem.capabilities import BUILD_VERSION

logger = logging.getLogger("ravis")

#: Why the state word moved, by the account message that moved it (`reason_code`, design §3.9).
MESSAGE_REASONS = {
    "account/login/completed": "sign_in",
    "account/updated": "account_updated",
    "account/rateLimits/updated": "usage",
    "turn/completed": "turn_completed",
}


@dataclass(frozen=True)
class ServiceTimings:
    """The service's clocks; tests shorten them. Defaults are the design's (§3.3, §4.1, §4.8)."""

    recheck_seconds: float = 60.0
    housekeeping_seconds: float = 5.0
    account_read_seconds: float = 10.0
    rate_limits_read_seconds: float = 15.0
    model_list_seconds: float = 15.0
    login_seconds: float = 10.0
    usage_after_turn_seconds: float = 30.0
    consumer_stall_seconds: float = 30.0
    #: Codex abandons a sign-in after ten minutes, and RAVIS ends its own record of it then too.
    sign_in_lifetime_seconds: float = SIGN_IN_LIFETIME.total_seconds()
    supervisor: SupervisorTimings = SupervisorTimings()
    handshake: HandshakeTimings = HandshakeTimings()
    reproof: ReproofTimings = ReproofTimings()


@dataclass(frozen=True)
class _Harness:
    """RAVIS's Codex process, as the re-test harness may use it: requests, and its own threads."""

    supervisor: CodexSupervisor
    router: MessageRouter

    async def request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float
    ) -> Any:
        return await self.supervisor.request(method, params, timeout=timeout)

    def hold(self, thread_id: str, inbox: Inbox) -> None:
        self.router.hold(thread_id, inbox)

    def release(self, thread_id: str) -> None:
        self.router.release(thread_id)


class CodexService:
    """Everything RAVIS does with Codex in this increment, and the one state it reports."""

    def __init__(
        self,
        settings: Settings,
        *,
        emit: Callable[..., None],
        codesign: str = CODESIGN,
        pin: Any = None,
        timings: ServiceTimings = ServiceTimings(),
        sign_in_ports: tuple[int, ...] = SIGN_IN_PORTS,
    ) -> None:
        self.settings = settings
        self._emit = emit
        self._timings = timings
        self._ports = sign_in_ports
        self._file = CodexStateFile(config_directory() / FILE_NAME)
        self._record = CodexRecord()
        runtime_pin = {} if pin is None else {"pin": pin}
        self.runtime = CodexRuntime(
            settings, codesign=codesign, records=self._build_records, **runtime_pin
        )
        self._home = codex_home(settings)
        self._home_fingerprint = "sha256:" + hashlib.sha256(
            os.path.realpath(self._home).encode("utf-8")
        ).hexdigest()
        self._turns = ActiveTurns()
        self._router = MessageRouter(self._account_message, self._turns)
        self._supervisor = CodexSupervisor(
            plan=self._launch_plan,
            router=self._router,
            turns=self._turns,
            on_ready=self._process_ready,
            on_ended=self._process_ended,
            client_version=BUILD_VERSION,
            timings=timings.supervisor,
        )
        self.kept_reproof_answers = KeptAnswers()
        self._account: Account | None = None
        self._account_read = False
        self._usage: Usage = UNKNOWN
        self._usage_attempted_at: float | None = None
        self._usage_read_now = False
        self._models: tuple[ModelFacts, ...] = ()
        self._sign_in = SignIn()
        self._sign_in_confirms = False
        self._profile: FileRulesProfile | None = None
        self._messages: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=1000)
        self._consumer_busy_since: float | None = None
        self._refresh_wanted = asyncio.Event()
        self._usage_wake = asyncio.Event()
        self._account_lock = asyncio.Lock()
        self._check_lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._version_check: VersionCheck | None = None
        self._version_task: asyncio.Task[VersionCheck] | None = None
        self._reproof_task: asyncio.Task[None] | None = None
        self._revision = 0
        self._last_body = ""
        self._last_state: str | None = None
        self._stopping = False
        # The accepted build whose acceptance RAVIS's log has already reported.
        self._accepted_logged: str | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Read the record, recover from a restart, check the runtime, start the background work."""
        self._record = self._file.load()
        self._recover_after_restart()
        if self.settings.codex_enabled is False:
            await self.runtime.check()
            self._state_moved("runtime_check")
            return
        data = data_directory()
        swept = sweep_scratch(data / SCRATCH_FOLDER) + sweep_reproof_folders(data)
        if swept:
            logger.info("codex: swept %d throwaway folders a stopped check left behind", swept)
        async with self._check_lock:
            await self.runtime.check()
        self._profile = self._load_profile()
        self._tasks = {
            "supervisor": asyncio.create_task(self._supervisor.run()),
            "consumer": asyncio.create_task(self._consume_messages()),
            "refresh": asyncio.create_task(self._refresh_account_loop()),
            "usage": asyncio.create_task(self._usage_loop()),
            "housekeeping": asyncio.create_task(self._housekeeping_loop()),
        }
        self._after_verdict("runtime_check")

    async def stop(self) -> None:
        """End the process within the launcher's wait, then every background task."""
        self._stopping = True
        await self._supervisor.stop()
        supervisor = self._tasks.get("supervisor")
        if supervisor is not None:
            # `asyncio.wait` never cancels what it waits on, and never waits past its budget.
            budget = sum(self._timings.supervisor.shutdown_waits) + 1.0
            await asyncio.wait({supervisor}, timeout=budget)
        pending = [*self._tasks.values(), self._reproof_task, self._version_task]
        running = [task for task in pending if task is not None and not task.done()]
        for task in running:
            task.cancel()
        if running:
            await asyncio.wait(running, timeout=1.0)

    def _recover_after_restart(self) -> None:
        """Say what the last RAVIS left unfinished, instead of pretending nothing was under way."""
        record = self._record
        changed = False
        if record.sign_in_started_at is not None:
            self._sign_in.fail(RESTARTED_DURING_SIGN_IN)
            record.sign_in_started_at = None
            changed = True
        if record.reproof is not None and record.reproof.state == "running":
            record.reproof = replace(
                record.reproof,
                state="finished",
                result="inconclusive",
                detail="RAVIS restarted during the re-test",
                finished_at=_iso_now(),
            )
            changed = True
        if changed:
            self._save()

    # ── What the process may be, and what it says ────────────────────────────

    def _build_records(self) -> BuildRecords:
        return BuildRecords(
            accepted=frozenset(build.sha256 for build in self._record.accepted),
            proven=frozenset(self._record.proven),
        )

    def _load_profile(self) -> FileRulesProfile | None:
        try:
            document = read_pin(self.runtime.pin)
        except PinUnreadableError:
            return None
        folders = {
            "user_home": Path.home(),
            "ravis_config": config_directory(),
            "codex_home": self._home,
            "reproof_decoys": decoy_folder(data_directory()),
        }
        return file_rules_profile(document, folders)

    def _launch_plan(self) -> LaunchPlan | None:
        """Which Codex may run now: a tested or accepted build the check found, or none."""
        report, executable = self.runtime.report, self.runtime.executable
        if report.verdict not in ("tested", "accepted") or executable is None:
            return None
        if report.installed_sha256 is None or report.version is None:
            return None
        return LaunchPlan(
            executable=executable,
            sha256=report.installed_sha256,
            version=report.version,
            home=self._home,
            link_folder=self.runtime.link_folder,
            profile_flags=self._profile.flags if self._profile is not None else (),
        )

    def _process_ready(self) -> None:
        self._account_read = False
        self._refresh_wanted.set()
        self._state_moved("process_ready")

    def _process_ended(self, reason: str) -> None:
        self._account_read = False
        if self._sign_in.waiting and not self._stopping:
            # Lost while RAVIS runs on: say so now, and no restart needs to report it later.
            self._sign_in.fail(f"Codex's process stopped during sign-in ({reason}); start again.")
            self._record.sign_in_started_at = None
            self._save()
        self._usage_wake.set()
        self._state_moved("process_ended")

    def _account_message(self, method: str, params: dict[str, Any]) -> None:
        """Called on the reader's path: only queue the message."""
        try:
            self._messages.put_nowait((method, params))
        except asyncio.QueueFull:
            logger.warning("codex: the account message queue is full; dropped %s", method)

    async def _consume_messages(self) -> None:
        while True:
            method, params = await self._messages.get()
            self._consumer_busy_since = time.monotonic()
            try:
                self._apply_message(method, params)
            except Exception:  # noqa: BLE001 — one bad message must not stop the rest
                logger.exception("codex: applying %s failed", method)
            finally:
                self._consumer_busy_since = None

    def _apply_message(self, method: str, params: dict[str, Any]) -> None:
        if method == "account/rateLimits/updated" and self._account is not None:
            self._usage = merged(self._usage, params, _now())
        elif method == "account/login/completed":
            self._login_completed(params)
        elif method == "account/updated":
            self._refresh_wanted.set()
        elif method == "turn/completed":
            self._turn_completed(params)
        self._state_moved(MESSAGE_REASONS.get(method, "codex_message"))

    def _login_completed(self, params: dict[str, Any]) -> None:
        outcome = self._sign_in.completed(params)
        if outcome == "ignored":
            return
        self._record.sign_in_started_at = None
        if outcome == "signed_in":
            # The owner signed in through RAVIS: the account that answers next is the one to keep.
            self._sign_in_confirms = True
            self._refresh_wanted.set()
        self._save()

    def _turn_completed(self, params: dict[str, Any]) -> None:
        """A turn that failed on the plan's usage limit is the moment to read the allowance (§9)."""
        error = _mapping(_mapping(params.get("turn")).get("error"))
        if error.get("codexErrorInfo") == "usageLimitExceeded":
            self._usage_read_now = True
        self._usage_wake.set()

    # ── The account ──────────────────────────────────────────────────────────

    async def _refresh_account_loop(self) -> None:
        while True:
            await self._refresh_wanted.wait()
            self._refresh_wanted.clear()
            try:
                await self._refresh_account()
            except (CodexRpcError, CodexUnavailableError) as failure:
                logger.warning("codex: reading the account failed: %s", failure)
            # The allowance loop sleeps while no account is known; a newly read one starts its
            # clock. Without this the idle 15-minute reads never began after a start.
            self._usage_wake.set()
            self._state_moved("account_read")

    async def _refresh_account(self) -> None:
        """`account/read`, then — when signed in — the models and the allowance (design §3.4)."""
        result = await self._supervisor.request(
            "account/read", {"refreshToken": False}, timeout=self._timings.account_read_seconds
        )
        usage = await self._read_rate_limits() if needs_account_id(result) else None
        account = account_from_read(result, usage.account_id if usage else None, _now())
        self._adopt(account)
        self._account_read = True
        if account is None:
            return
        self._models = await self._read_models()
        self._apply_usage(usage if usage is not None else await self._read_rate_limits())

    def _adopt(self, account: Account | None) -> None:
        """Take the account Codex reports, confirming it only if the owner just signed in."""
        previous, self._account = self._account, account
        if account is None:
            self._usage, self._models = UNKNOWN, ()
            return
        if self._sign_in_confirms:
            self._sign_in_confirms = False
            self._confirm(account)
        elif previous is not None and previous.fingerprint != account.fingerprint:
            # Another account's allowance is not this one's.
            self._usage = UNKNOWN
        self._warn_if_unconfirmed(previous, account)

    def _warn_if_unconfirmed(self, previous: Account | None, account: Account) -> None:
        """Log, once per change, that Codex answers as an account the owner hasn't confirmed.

        Names only when the owner last confirmed an account — never the email of either.
        """
        confirmed = self._record.confirmed
        if confirmed is None or account.fingerprint == confirmed.fingerprint:
            return
        if previous is not None and previous.fingerprint == account.fingerprint:
            return
        logger.warning(
            "codex: Codex is signed in to a different account from the one confirmed at %s; new "
            "Codex work waits until the owner confirms it",
            confirmed.confirmed_at,
        )

    def _confirm(self, account: Account) -> None:
        self._record.confirmed = ConfirmedAccount(
            fingerprint=account.fingerprint,
            strength=account.strength,
            plan=account.plan,
            confirmed_at=_iso_now(),
        )
        self._record.signed_out_on_purpose = False
        self._save()

    async def _read_models(self) -> tuple[ModelFacts, ...]:
        try:
            result = await self._supervisor.request(
                "model/list",
                {"limit": 100, "includeHidden": False},
                timeout=self._timings.model_list_seconds,
            )
        except (CodexRpcError, CodexUnavailableError) as failure:
            logger.warning("codex: reading the models failed, keeping the last list: %s", failure)
            return self._models
        return models_from_list(result)

    async def _read_rate_limits(self) -> Usage | None:
        """One allowance read; None when it failed, which leaves the last figure to go stale."""
        self._usage_attempted_at = time.monotonic()
        try:
            result = await self._supervisor.request(
                "account/rateLimits/read", None, timeout=self._timings.rate_limits_read_seconds
            )
        except (CodexRpcError, CodexUnavailableError) as failure:
            logger.warning("codex: reading the allowance failed: %s", failure)
            return None
        return from_read(result, _now())

    def _apply_usage(self, usage: Usage | None) -> None:
        self._usage_read_now = False
        if usage is not None and usage.known:
            self._usage = usage

    # ── The allowance loop ───────────────────────────────────────────────────

    async def _usage_loop(self) -> None:
        while True:
            await _any_of((self._usage_wake, self._turns.changed), self._usage_due_in())
            self._usage_wake.clear()
            self._turns.changed.clear()
            if self._usage_due_in() == 0.0:
                self._apply_usage(await self._read_rate_limits())
                self._state_moved("usage")

    def _usage_due_in(self) -> float | None:
        """Seconds until the next allowance read; None while none may happen (design §3.3)."""
        if self._account is None or self._supervisor.state != "running" or self._turns.count:
            return None
        now = time.monotonic()
        attempted = self._usage_attempted_at
        due = now if attempted is None else attempted + self.settings.codex_usage_refresh_seconds
        ended = self._turns.last_ended_at
        if ended is not None and (attempted is None or ended > attempted):
            due = min(due, ended + self._timings.usage_after_turn_seconds)
        if self._usage_read_now:
            due = now
        return max(0.0, min(due, self._after_reset(now)) - now)

    def _after_reset(self, now: float) -> float:
        """When a used-up allowance resets, plus five seconds: worth reading then."""
        wall = _now()
        reset = next_reset(self._usage, wall) if is_exhausted(self._usage, wall) else None
        return now + (reset - wall).total_seconds() + 5.0 if reset is not None else float("inf")

    # ── Housekeeping ─────────────────────────────────────────────────────────

    async def _housekeeping_loop(self) -> None:
        last_recheck = time.monotonic()
        while True:
            await asyncio.sleep(self._timings.housekeeping_seconds)
            self._expire_sign_in()
            self._watch_consumer()
            if time.monotonic() - last_recheck >= self._timings.recheck_seconds:
                last_recheck = time.monotonic()
                await self._recheck()
            self._state_moved("time")

    def _expire_sign_in(self) -> None:
        if not self._sign_in.is_expired(_now()):
            return
        login_id = self._sign_in.expire()
        self._record.sign_in_started_at = None
        self._save()
        if login_id is not None:
            self._background(self._cancel_quietly(login_id))

    async def _cancel_quietly(self, login_id: str) -> None:
        with contextlib.suppress(CodexRpcError, CodexUnavailableError):
            await self._supervisor.request(
                "account/login/cancel", {"loginId": login_id}, timeout=self._timings.login_seconds
            )

    def _watch_consumer(self) -> None:
        """Restart a message consumer stuck on one message, without touching the process (§4.4)."""
        since = self._consumer_busy_since
        if since is None or time.monotonic() - since < self._timings.consumer_stall_seconds:
            return
        logger.error("codex: the account message consumer stalled; restarting it")
        self._tasks["consumer"].cancel()
        self._consumer_busy_since = None
        self._tasks["consumer"] = asyncio.create_task(self._consume_messages())

    async def _recheck(self) -> None:
        """The 60-second look at the executable; the whole check again only if it changed."""
        if not await asyncio.to_thread(self.runtime.executable_changed):
            return
        async with self._check_lock:
            await self.runtime.check()
        self._profile = self._load_profile()
        self._after_verdict("binary_changed")

    def _after_verdict(self, reason_code: str) -> None:
        """The verdict may have moved: the supervisor looks again, and an untested build's report
        is prepared in the background (design §4.1 item 4)."""
        self._supervisor.wake()
        if self.runtime.report.verdict == "untested" and self.runtime.executable is not None:
            self._background(self._version_check_quietly())
        self._log_accepted_build()
        self._state_moved(reason_code)

    def _log_accepted_build(self) -> None:
        """Say in RAVIS's log, once per build, that Codex is a build the owner accepted untested.

        The record keeps who accepted it, when, and what its protocol changed against the pinned
        build (design §4.2); after a restart this line is where an operator finds that out. All of
        it is metadata: definition names and counts, never content.
        """
        installed = self.runtime.report.installed_sha256
        if self.runtime.report.verdict != "accepted" or installed == self._accepted_logged:
            return
        build = next((b for b in self._record.accepted if b.sha256 == installed), None)
        if build is None:
            return
        self._accepted_logged = installed
        logger.warning(
            "codex: Codex %s is a build accepted without a test by %s at %s; against the pinned "
            "build: %s",
            build.version,
            build.accepted_by,
            build.accepted_at,
            json.dumps(build.protocol_summary, sort_keys=True),
        )

    # ── The state ────────────────────────────────────────────────────────────

    def _reading(self) -> Reading:
        record, supervisor = self._record, self._supervisor
        confirmed = record.confirmed
        return Reading(
            report=self.runtime.report,
            process=ProcessFacts(
                state=supervisor.state,
                since=supervisor.since,
                restarts_24h=supervisor.restarts_24h(),
                active_turns=self._turns.count,
                running_sha256=supervisor.running_sha256,
                failure=supervisor.failure,
                identity_failure=supervisor.identity_failure,
            ),
            account_read=self._account_read,
            account=self._account,
            confirmed_fingerprint=confirmed.fingerprint if confirmed else None,
            confirmed_plan=confirmed.plan if confirmed else None,
            signed_out_on_purpose=record.signed_out_on_purpose,
            usage=self._usage,
            models=self._models,
            sign_in=self._sign_in.public_view(),
            home_fingerprint=self._home_fingerprint,
            now=_now(),
        )

    def snapshot(self) -> dict[str, Any]:
        """`GET /api/v1/codex`: computed from what the service holds, never waiting on Codex.

        `revision` moves whenever anything in the body does, so a reader can tell "unchanged".
        """
        body = codex_body(self._reading(), revision=0)
        fingerprint = json.dumps(body, sort_keys=True)
        if fingerprint != self._last_body:
            self._last_body = fingerprint
            self._revision += 1
        body["revision"] = self._revision
        return body

    def _state_moved(self, reason_code: str) -> None:
        state, _ = decide(self._reading())
        if state == self._last_state:
            return
        previous, self._last_state = self._last_state, state
        if previous is None:
            # The first reading is the baseline, not a change: publishing it would put a Codex event
            # on the hub at every RAVIS start, even with Codex switched off and nothing moving.
            return
        self._emit(
            "ravis.codex.state_changed",
            trace_id=uuid.uuid4().hex,
            data={"from": previous, "to": state, "reason_code": reason_code},
        )

    # ── Sign-in, sign-out, the account's confirmation ────────────────────────

    def sign_in_view(self) -> dict[str, Any]:
        return self._sign_in.admin_view()

    async def start_sign_in(self) -> tuple[int, dict[str, Any]]:
        """Start the browser sign-in (design §3.4): 202 when started, 200 when already waiting."""
        async with self._account_lock:
            self._refuse_unless_signing_in_is_possible()
            if self._sign_in.waiting:
                return 200, {"sign_in": self._sign_in.admin_view()}
            if self._account is not None:
                raise refusals.already_signed_in()
            if self._busy():
                raise refusals.run_in_progress("sign in")
            busy = busy_ports(self._ports)
            if len(busy) == len(self._ports):
                raise refusals.sign_in_ports_busy()
            started = await self._login_start()
            free = next(port for port in self._ports if port not in busy)
            auth_url = started["authUrl"]
            self._sign_in.begin(
                started["loginId"],
                auth_url,
                callback_port(auth_url, free),
                _now(),
                timedelta(seconds=self._timings.sign_in_lifetime_seconds),
            )
            self._record.sign_in_started_at = _iso_now()
            self._save()
            self._state_moved("sign_in")
            return 202, {"sign_in": self._sign_in.admin_view()}

    def _refuse_unless_signing_in_is_possible(self) -> None:
        report, supervisor = self.runtime.report, self._supervisor
        if report.state in ("checking", "not_installed", "not_available"):
            raise refusals.not_available(report.reason)
        if supervisor.identity_failure is not None:
            raise refusals.not_available(supervisor.identity_failure)
        if report.verdict == "untested":
            raise refusals.untested_version(
                f"Codex changed (now {report.version}) and needs re-testing first."
            )
        if supervisor.state != "running" or not self._account_read:
            raise refusals.not_available("Codex's process is not running yet.")

    def _busy(self) -> bool:
        """A Codex turn is active, or the re-test is running."""
        return self._turns.count > 0 or self._reproof_running()

    async def _login_start(self) -> dict[str, Any]:
        try:
            result = await self._supervisor.request(
                "account/login/start", {"type": "chatgpt"}, timeout=self._timings.login_seconds
            )
        except CodexTimeoutError:
            raise refusals.runtime_unavailable(
                "Codex did not answer the sign-in request in time."
            ) from None
        except CodexUnavailableError as gone:
            raise refusals.not_available(str(gone)) from None
        except CodexRpcError as refusal:
            if "already in use" in refusal.message:
                raise refusals.sign_in_ports_busy() from None
            raise refusals.runtime_unavailable(
                f"Codex refused to start the sign-in: {refusal.message}"
            ) from None
        if not _is_browser_sign_in(result):
            raise refusals.runtime_unavailable("Codex's answer named no sign-in page to open.")
        return result  # type: ignore[no-any-return]

    async def cancel_sign_in(self) -> bool:
        """`account/login/cancel` for the waiting sign-in; whether Codex cancelled one."""
        async with self._account_lock:
            if not self._sign_in.waiting:
                return False
            try:
                result = await self._supervisor.request(
                    "account/login/cancel",
                    {"loginId": self._sign_in.login_id},
                    timeout=self._timings.login_seconds,
                )
            except (CodexRpcError, CodexUnavailableError) as failure:
                raise refusals.runtime_unavailable(
                    f"Codex couldn't cancel the sign-in now: {failure}"
                ) from None
            self._sign_in.cancelled()
            self._record.sign_in_started_at = None
            self._save()
            self._state_moved("sign_in_cancelled")
            return isinstance(result, dict) and result.get("status") == "canceled"

    async def sign_out(self) -> dict[str, Any]:
        """`account/logout`, the confirmed account forgotten, and the full state (design §3.4)."""
        async with self._account_lock:
            if self._supervisor.state != "running":
                raise refusals.not_available(
                    "Codex's process is not running, so it can't sign out."
                )
            if self._busy():
                raise refusals.run_in_progress("sign out")
            if self._sign_in.waiting:
                self._sign_in.cancelled()
            try:
                await self._supervisor.request(
                    "account/logout", None, timeout=self._timings.login_seconds
                )
            except (CodexRpcError, CodexUnavailableError) as failure:
                raise refusals.runtime_unavailable(
                    f"Codex couldn't sign out now: {failure}"
                ) from None
            self._record.confirmed = None
            self._record.signed_out_on_purpose = True
            self._record.sign_in_started_at = None
            self._save()
            self._adopt(None)
            self._account_read = True
            self._state_moved("signed_out")
            return self.snapshot()

    async def confirm_account(self, email_hint: str | None) -> dict[str, Any]:
        """The owner says the account now signed in is theirs (design §3.4)."""
        async with self._account_lock:
            try:
                result = await self._supervisor.request(
                    "account/read", {"refreshToken": False},
                    timeout=self._timings.account_read_seconds,
                )
            except (CodexRpcError, CodexUnavailableError) as failure:
                raise refusals.runtime_unavailable(
                    f"Codex couldn't read the account now, so it can't be confirmed: {failure}"
                ) from None
            usage = await self._read_rate_limits() if needs_account_id(result) else None
            account = account_from_read(result, usage.account_id if usage else None, _now())
            if account is None or account.email_hint != email_hint:
                raise refusals.account_moved()
            self._confirm(account)
            self._account, self._account_read = account, True
            self._refresh_wanted.set()
            self._state_moved("account_confirmed")
            return self.snapshot()

    # ── Versions ─────────────────────────────────────────────────────────────

    async def version_check(self) -> dict[str, Any]:
        check = await self._current_version_check()
        report = self.runtime.report
        return check.body(report.verdict, report.strict_rules)

    async def _current_version_check(self) -> VersionCheck:
        """The report on the installed binary: the kept one, or one run now (shared if running)."""
        report, target = self.runtime.report, self.runtime.executable
        if target is None or report.installed_sha256 is None:
            raise refusals.not_available(report.reason)
        kept = self._version_check
        if kept is not None and kept.sha256 == report.installed_sha256:
            return kept
        if self._version_task is None or self._version_task.done():
            self._version_task = asyncio.create_task(
                check_version(
                    target,
                    self.settings,
                    installed_sha256=report.installed_sha256,
                    codesign=self.runtime.codesign,
                    pin=self.runtime.pin,
                    profile=self._profile,
                    timings=self._timings.handshake,
                )
            )
        self._version_check = await asyncio.shield(self._version_task)
        return self._version_check

    async def _version_check_quietly(self) -> None:
        with contextlib.suppress(refusals.CodexRefusalError):
            await self._current_version_check()

    async def accept_version(self, sha256: str, accepted_by: str) -> tuple[dict[str, Any], bool]:
        """Record an untested build as accepted — always with unproven file rules (review AM1)."""
        report = self.runtime.report
        if report.installed_sha256 is None or sha256 != report.installed_sha256:
            raise refusals.hash_mismatch()
        if report.verdict in ("tested", "accepted"):
            return self.snapshot(), False
        check = await self._current_version_check()
        if check.sha256 != sha256 or not check.acceptable:
            raise refusals.version_check_failed()
        kept = [build for build in self._record.accepted if build.sha256 != sha256]
        self._record.accepted = [*kept, _accepted(check, sha256, accepted_by)]
        self._record.proven.pop(sha256, None)
        self._save()
        await self._revise("version_accepted")
        return self.snapshot(), True

    async def revoke_version(self, sha256: str) -> tuple[dict[str, Any], bool]:
        before = len(self._record.accepted)
        self._record.accepted = [build for build in self._record.accepted if build.sha256 != sha256]
        revoked = len(self._record.accepted) != before
        if revoked:
            self._record.proven.pop(sha256, None)
            self._save()
            await self._revise("version_acceptance_revoked")
        return self.snapshot(), revoked

    async def _revise(self, reason_code: str) -> None:
        async with self._check_lock:
            self.runtime.revise()
        self._after_verdict(reason_code)

    # ── The file-rules re-test ───────────────────────────────────────────────

    def reproof_view(self) -> dict[str, Any]:
        """`GET /api/v1/codex/reprove`: the last result; only `state` and `result` are contract."""
        record = self._record.reproof
        if record is None:
            return {"reproof": {"state": "idle", "result": None}}
        body: dict[str, Any] = {"state": record.state}
        if record.state == "finished":
            body["result"] = record.result
        body.update(
            detail=record.detail,
            sha256=record.sha256,
            started_at=record.started_at,
            finished_at=record.finished_at,
        )
        return {"reproof": body}

    def _reproof_running(self) -> bool:
        return self._reproof_task is not None and not self._reproof_task.done()

    def start_reproof(self, application_id: str) -> None:
        """Start the re-test in the background, or leave the one running alone (design §3.4)."""
        if self._reproof_running():
            return
        refusal = self._reproof_refusal()
        if refusal is not None:
            raise refusal
        self._record.reproof = ReproofRecord(
            state="running", sha256=self.runtime.report.installed_sha256, started_at=_iso_now()
        )
        self._save()
        self._reproof_task = asyncio.create_task(self._run_reproof(application_id))

    def _reproof_refusal(self) -> refusals.CodexRefusalError | None:
        """Why the re-test can't run now. An allowance used up is 409, never 429 (C1's notes)."""
        if self._turns.count:
            return refusals.run_in_progress("re-test")
        report = self.runtime.report
        if report.verdict != "accepted":
            return refusals.not_ready(_not_accepted_reason(report.verdict, report.version))
        if self._profile is None:
            return refusals.not_ready(
                "The clarvis_run file-rules profile isn't calibrated yet, so there is nothing the "
                "re-test could prove."
            )
        if self._supervisor.state != "running" or self._account is None:
            return refusals.not_ready(
                "Codex must be running and signed in: the re-test runs one short turn on the "
                "plan's allowance."
            )
        if is_exhausted(self._usage, _now()):
            # The allowance's own sentence, not the state's: an unproven build's pause outranks
            # "used up" in the state table, but it isn't why the re-test can't run.
            return refusals.not_ready(used_up_reason(self._usage, _now()))
        return None

    async def _run_reproof(self, application_id: str) -> None:
        trace = uuid.uuid4().hex
        outcome = Outcome("inconclusive", "the re-test didn't finish")
        plan = None
        try:
            plan = await asyncio.to_thread(prepare_plan, data_directory())
            profile_name = self._profile.name if self._profile is not None else "clarvis_run"
            outcome = await run_reproof(
                _Harness(self._supervisor, self._router),
                plan,
                profile_name,
                on_answer=lambda index, decision: self._audit_answer(
                    trace, application_id, index, decision
                ),
                timings=self._timings.reproof,
            )
        except OSError as failure:
            outcome = Outcome("inconclusive", f"the re-test's folders couldn't be made: {failure}")
        finally:
            if plan is not None:
                discard_plan(plan)
            self._finish_reproof(outcome, trace, application_id)

    def _audit_answer(
        self, trace: str, application_id: str, index: int | None, decision: str
    ) -> None:
        self._emit(
            "ravis.codex.reproof_approval_answered",
            trace_id=trace,
            data={"application_id": application_id, "command_index": index, "decision": decision},
        )

    def _finish_reproof(self, outcome: Outcome, trace: str, application_id: str) -> None:
        record = self._record.reproof or ReproofRecord(state="running")
        self._record.reproof = replace(
            record,
            state="finished",
            result=outcome.result,
            detail=outcome.detail,
            finished_at=_iso_now(),
        )
        installed = self.runtime.report.installed_sha256
        if outcome.result == "proven" and record.sha256 is not None and record.sha256 == installed:
            self._record.proven[record.sha256] = _iso_now()
        self._save()
        self.runtime.revise()
        self._supervisor.wake()
        self._emit(
            "ravis.codex.reproof_finished",
            trace_id=trace,
            data={"application_id": application_id, "result": outcome.result},
        )
        self._state_moved("reproof")

    # ── Small things ─────────────────────────────────────────────────────────

    def _save(self) -> None:
        try:
            self._file.save(self._record)
        except OSError as failure:
            logger.error("codex: %s can't be written: %s", FILE_NAME, failure)

    def _background(self, work: Any) -> None:
        """Run `work` beside the service, kept so it isn't collected mid-way."""
        task = asyncio.create_task(work)
        self._tasks[f"background-{id(task)}"] = task
        task.add_done_callback(lambda done: self._tasks.pop(f"background-{id(done)}", None))


def _accepted(check: VersionCheck, sha256: str, accepted_by: str) -> AcceptedBuild:
    return AcceptedBuild(
        sha256=sha256,
        version=check.version,
        stable_tree=check.stable_tree,
        experimental_tree=check.experimental_tree,
        accepted_at=_iso_now(),
        accepted_by=accepted_by,
        protocol_summary=check.protocol,
    )


def _not_accepted_reason(verdict: str | None, version: str | None) -> str:
    if verdict == "tested":
        return (
            f"Codex {version} is a tested build: calibration proves its file rules, not the "
            "re-test."
        )
    return f"Codex {version} hasn't been accepted; check the version and accept it first."


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _is_browser_sign_in(result: object) -> bool:
    return (
        isinstance(result, dict)
        and result.get("type") == "chatgpt"
        and isinstance(result.get("loginId"), str)
        and isinstance(result.get("authUrl"), str)
        and result["authUrl"].startswith("https://")
    )


async def _any_of(events: tuple[asyncio.Event, ...], seconds: float | None) -> None:
    """Return when any event is set, or after `seconds` (never, for None)."""
    waits = [asyncio.create_task(event.wait()) for event in events]
    try:
        await asyncio.wait(waits, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for wait in waits:
            wait.cancel()


def _now() -> datetime:
    return datetime.now(UTC)


def _iso_now() -> str:
    return iso(_now()) or ""
