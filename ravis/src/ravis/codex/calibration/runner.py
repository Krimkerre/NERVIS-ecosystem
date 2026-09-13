"""One calibration run, from its projects' lock files to its result (design §10.4).

**Before anything runs** (`acquire_locks`, called by the service while it answers the route, so a
refusal is immediate): RAVIS creates each project's checkout lock file, as the writer for these
threads (design §6.3). A file already there is judged with the shared lock rule. Only one naming a
previous RAVIS instance whose holder is `gone` is replaced (the restart adoption rule, strictly);
anything else — a Clarvis window, a live Codex task — refuses the run and says whose it is.

**While it runs** (`CalibrationRun.run`): the questions are asked one at a time, in `SCENARIOS`'
order. Both lock files are heartbeaten every 15 s; if either stops naming this run, the questions
not yet asked are marked not run, and the run never releases or rewrites that lock again (the
fence). A scenario that raises for anything Codex did is `inconclusive`, never a crash.

**When Codex couldn't start with the profile** — its `-c` syntax rejected — nothing can be asked:
K5 fails in Codex's own words, which is how the syntax gets fixed, and every other question is
inconclusive.

**At the end, whatever happened:** the decision (`outputs.decide`), the transcripts and summary,
the pin entry only when the decision proves the rules, the decoys and every file a scenario made
removed, and the lock files released in reverse order.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ravis.codex.calibration import scenarios as files
from ravis.codex.calibration import scenarios_network as network
from ravis.codex.calibration import scenarios_turns as turns
from ravis.codex.calibration.harness import ScenarioContext, ScenarioResult
from ravis.codex.calibration.outputs import (
    BuildFacts,
    Decision,
    decide,
    redactor_for,
    summary,
    write_pin_entry,
    write_summary,
    write_transcripts,
)
from ravis.codex.calibration.plan import (
    BY_ID,
    CalibrationPlan,
    CalibrationRequestError,
    Project,
    discard_plan,
)
from ravis.codex.lock_file import (
    HeldLockFile,
    Holder,
    create_lock_file,
    lock_content,
    lock_file_path,
)
from ravis.codex.lock_rule import (
    FoundLockFile,
    JudgedLock,
    Verdict,
    adoption_decision,
    judge_lock,
    observer_awake_seconds,
    on_finding_a_lock,
    probe_process,
    same_start,
)
from ravis.codex.rpc import CodexRpcError, CodexUnavailableError

logger = logging.getLogger("ravis")

Scenario = Callable[[ScenarioContext], Awaitable[ScenarioResult]]
RUNNERS: dict[str, Scenario] = {
    "K10": files.k10, "K5a": files.k5a, "K5": files.k5, "K5c": files.k5c, "K1": files.k1,
    "K2": files.k2, "K2b": files.k2b, "K3": network.k3, "K4": turns.k4, "K8": files.k8,
    "K9": files.k9, "K11": turns.k11, "K12": turns.k12, "K7": turns.k7, "K6": turns.k6,
    "K13": turns.k13,
}
#: What a writer finding a lock would do, as the refusal says it (`on_finding_a_lock`).
LOCKED_WORDS = {
    "attach": "a Codex task is working on it",
    "reconcile": "a Clarvis run that has ended still holds it; open the project in Clarvis first",
    "refuse": "another Clarvis window is working on it",
    "take_over_with_confirmation": "a Clarvis window holds it, waiting or stalled; finish it there",
}


class CalibrationLockedError(Exception):
    """A project can't be locked for calibration; the message says whose it is."""


def acquire_locks(
    projects: tuple[Project, Project], run_id: str, *, pid: int, pid_start: str
) -> list[HeldLockFile]:
    """Both projects' checkout lock files, created by this RAVIS for this run — or a refusal."""
    held: list[HeldLockFile] = []
    try:
        for project in projects:
            held.append(_lock(project, run_id, pid=pid, pid_start=pid_start))
    except CalibrationLockedError:
        for lock in reversed(held):
            lock.release()
        raise
    return held


def _lock(project: Project, run_id: str, *, pid: int, pid_start: str) -> HeldLockFile:
    path = lock_file_path(project.root, project.git_dir)
    holder = Holder(kind="codex_session", session_id=f"{run_id}-{project.label.lower()}",
                    pid=pid, pid_start=pid_start)
    content = lock_content(holder, task_id=run_id)
    created = create_lock_file(path, content)
    if created.held is not None:
        return created.held
    if created.existing is None:
        raise CalibrationLockedError(
            f"Project {project.label}'s lock file couldn't be made: {created.failure}"
        )
    if not _adoptable(created.existing.content, created.existing.kind, pid, pid_start):
        raise CalibrationLockedError(_locked_sentence(project, created.existing.content))
    path.unlink(missing_ok=True)
    again = create_lock_file(path, content)
    if again.held is None:
        raise CalibrationLockedError(f"Project {project.label} was locked a moment ago.")
    return again.held


def _adoptable(content: dict[str, Any], kind: str, pid: int, pid_start: str) -> bool:
    """A file RAVIS may replace: it names a previous RAVIS instance, whose holder is gone."""
    if kind != "present":
        return False
    holder = content["holder"]
    ours = holder.get("pid") == pid and same_start(str(holder.get("pid_start")), pid_start)
    verdict = _verdict(content)
    found = FoundLockFile(
        names_previous_ravis_instance=holder.get("host") == "ravis" and not ours,
        verdict=verdict,
    )
    rewrite = adoption_decision(found, create_race_lost=False).action == "rewrite"
    return rewrite and verdict == "gone"


def _verdict(content: dict[str, Any]) -> Verdict:
    holder = content["holder"]
    probe = probe_process(int(holder["pid"]))
    awake = observer_awake_seconds()
    if probe is None or awake is None:
        # Not knowing is never "gone": such a holder is treated as alive.
        return "alive"
    beat = datetime.fromisoformat(str(content["heartbeatAt"]).replace("Z", "+00:00"))
    age = (datetime.now(UTC) - beat).total_seconds()
    return judge_lock(JudgedLock(int(holder["pid"]), str(holder["pid_start"]), age), probe, awake)


def _locked_sentence(project: Project, content: dict[str, Any]) -> str:
    if not content:
        return f"Project {project.label}'s lock file can't be read; calibration won't touch it."
    holder = content["holder"]
    outcome = on_finding_a_lock(
        str(holder.get("kind")), _verdict(content), content.get("waitingOnYou") is True
    )
    return f"Project {project.label} is locked: {LOCKED_WORDS[outcome]}."


@dataclass(frozen=True)
class OutputTarget:
    """Where a run's outputs go, and what to do once the pin says the rules are proven."""

    base: Path
    #: The pin to write the entry into; None when it isn't a file RAVIS can write.
    pin: Path | None
    codex_home: Path


class CalibrationRun:
    """One run: its questions asked in order, its progress readable while it goes."""

    def __init__(
        self,
        *,
        plan: CalibrationPlan,
        profile: dict[str, Any],
        selected: tuple[str, ...],
        context: ScenarioContext,
        locks: list[HeldLockFile],
        outputs: OutputTarget,
        build: BuildFacts,
        start_failure: str | None = None,
    ) -> None:
        self.plan = plan
        self.profile = profile
        self.selected = selected
        self._context = context
        self._locks = locks
        self._outputs = outputs
        self._build = build
        self._start_failure = start_failure
        self.state = "running"
        self.current: str | None = None
        self.results: dict[str, ScenarioResult] = {}
        self.decision: Decision | None = None
        self.folder: Path | None = None
        self.pin_updated = False
        self.outputs_error: str | None = None
        self.lost_lock: Path | None = None
        self.started_at = _stamp()
        self.finished_at: str | None = None

    @property
    def run_id(self) -> str:
        return self.plan.run_id

    async def run(self) -> None:
        heartbeat = asyncio.create_task(self._heartbeat())
        try:
            for key in self.selected:
                self.current = key
                self.results[key] = await self._ask(key)
            self.current = None
            self.decision = decide(self.results, self.selected)
            await asyncio.to_thread(self._write_outputs)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(OSError):
                await asyncio.to_thread(discard_plan, self.plan)
            self._release()
            self.state, self.finished_at, self.current = "finished", _stamp(), None

    async def _ask(self, key: str) -> ScenarioResult:
        if self._start_failure is not None:
            return self._not_started(key)
        if self.lost_lock is not None:
            return ScenarioResult(
                key, "not_run", f"The lock file {self.lost_lock} no longer names this run."
            )
        try:
            return await RUNNERS[key](self._context)
        except (CodexRpcError, CodexUnavailableError) as failure:
            return ScenarioResult(key, "inconclusive", f"Codex couldn't answer: {failure}")
        except (CalibrationRequestError, OSError) as failure:
            return ScenarioResult(key, "inconclusive", str(failure))

    def _not_started(self, key: str) -> ScenarioResult:
        if key == "K5":
            return ScenarioResult(
                key, "failed",
                f"Codex didn't start with the {self.profile['name']} profile, so its syntax isn't "
                f"accepted. Codex said: {self._start_failure}",
                {"syntax_accepted": False},
            )
        return ScenarioResult(
            key, "inconclusive", "Codex isn't running with the profile under test."
        )

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self._context.timings.heartbeat_seconds)
            for lock in self._locks:
                if lock.heartbeat() == "lost" and self.lost_lock is None:
                    logger.warning("codex calibration: %s no longer names this run", lock.path)
                    self.lost_lock = lock.path

    def _release(self) -> None:
        """Release in reverse order; a lock this run lost is left alone (the fence)."""
        for lock in reversed(self._locks):
            if lock.path == self.lost_lock:
                lock.abandon()
            else:
                lock.release()

    def _write_outputs(self) -> None:
        decision = self.decision
        assert decision is not None
        self.folder = self._outputs.base / f"{self._build.version}-{self.run_id}"
        redactor = redactor_for(self.plan, self._outputs.codex_home)
        try:
            written = write_transcripts(self.folder, self.results.values(), redactor)
            body = summary(
                run_id=self.run_id, build=self._build, profile=self.profile,
                selected=self.selected, results=self.results, decision=decision,
                transcripts=written,
            )
            write_summary(self.folder, redactor.value(body))
            if decision.strict_rules_proven:
                self._write_pin()
        except OSError as failure:
            self.outputs_error = f"The outputs couldn't be written: {failure}"

    def _write_pin(self) -> None:
        folder = self.folder
        assert folder is not None
        pin = self._outputs.pin
        if pin is None:
            self.outputs_error = (
                "tested_runtimes.json isn't a file RAVIS can write; copy the summary's entry."
            )
            return
        write_pin_entry(
            pin, build=self._build, profile=self.profile, summary_name=f"{folder.name}/summary.json"
        )
        self.pin_updated = True

    def view(self) -> dict[str, Any]:
        """The run's progress and result, as `GET …/calibration/runs/{id}` returns it."""
        return {
            "run_id": self.run_id,
            "state": self.state,
            "current": self.current,
            "projects": {"A": self.plan.project_a.root.name, "B": self.plan.project_b.root.name},
            "scenarios": [self._scenario_view(key) for key in self.selected],
            "result": self._result_view(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    def _scenario_view(self, key: str) -> dict[str, Any]:
        spec, result = BY_ID[key], self.results.get(key)
        verdict = result.verdict if result else ("running" if key == self.current else "pending")
        return {
            "id": key, "question": spec.question, "kind": spec.kind,
            "verdict": verdict, "detail": result.detail if result else None,
        }

    def _result_view(self) -> dict[str, Any] | None:
        decision = self.decision
        if self.state != "finished" or decision is None:
            return None
        proven = decision.strict_rules_proven and self.pin_updated
        return {
            "strict_rules_proven": proven,
            "sentence": decision.sentence if proven or not decision.strict_rules_proven
            else self.outputs_error,
            "owner_decision_needed": decision.owner_decision_needed,
            "owner_questions": list(decision.owner_questions),
            "protected_repositories": decision.protected_repositories,
            "outputs": str(self.folder) if self.folder else None,
            "pin_updated": self.pin_updated,
            "outputs_error": self.outputs_error,
        }


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
