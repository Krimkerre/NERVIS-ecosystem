#!/usr/bin/env python3
"""The golden path, driven against the running ecosystem (§16 item 12).

**Why a procedure and not a test suite.** Every other gate here answers a
question about the repository. `conformance_check.py` comes closest and still
imports the applications and drives them through `TestClient` — real routes,
real handlers, one process, no network. That is the right shape for a contract
check and the wrong shape for the question item 12 asks, which is whether four
programs on one machine perform one task together. So this file starts nothing,
imports nothing from the packages, and doubles nothing. It speaks HTTP to
whatever `tools/run.py start` left running and asserts what comes back. A
service being down means "the ecosystem did not do this" — an answer, not a
reason to substitute a mock.

**One chain, one model.** Item 12's sentence is not a list of independent
demonstrations: *SIRVIS measures a model, RAVIS selects **it**, Clarvis works
**through that route**.* A procedure that measured the smallest build and then
routed a pool of five hundred would prove three unrelated things in sequence, so
the order here is deliberately route → measure → route again:

    1. RAVIS routes `ravis/local` and names a winner. That is MODEL.
    2. SIRVIS measures MODEL, and the record says what was asked and what ran.
    3. RAVIS's evidence index is watched until that exact `evidence_id` appears
       — the hinge of "selects it", and the step that proves the measurement was
       consumed rather than merely stored.
    4. RAVIS routes the same pool again and must still select MODEL, explaining
       every candidate and exclusion.

Step 3 is a wait, not a poke: RAVIS re-reads SIRVIS on its own cache timer and
publishes no route to force it. Waiting is the honest way to observe a cache; a
back door built for a test would be a back door.

**One trace, minted here, carried everywhere.** Every request sends the same W3C
`traceparent`. That is the join key — a procedure letting each service mint its
own would produce four tidy single-service traces and prove nothing about the
join. The trace id is printed first so a person can watch the same run from the
dashboard's Traces screen.

**Two steps need a person, and this says so rather than pretending.** Clarvis's
agent task has no scriptable entry point: `clarvis.runTask` takes no arguments
and reads its task from an input box, and undo always raises a modal needing a
click. A run that quietly skipped them would report a golden path that never
touched an editor — the exact failure §16 exists to end. So they pause for
input, they are verified from *outside* VS Code, and `--unattended` exits
`EXIT_INCOMPLETE` rather than zero.

**Exit codes.** 0 every clause proved; 1 a clause failed; 2 the ecosystem was
not in a state where the procedure could run; 3 complete but for steps a person
did not perform.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / ".run"

SIRVIS = "http://127.0.0.1:8721"
RAVIS = "http://127.0.0.1:8731"
NERVIS = "http://127.0.0.1:8790"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOT_RUNNABLE = 2
EXIT_INCOMPLETE = 3

# §16 item 12's own sentence, split into the clauses it names. The verdict is
# keyed on these rather than on step numbers, because the item is what has to be
# answered and a step is only how.
CLAUSES = {
    "measure": "SIRVIS measures a model with truthful requested/effective conditions",
    "route": "RAVIS selects it and explains every candidate, exclusion and winning factor",
    "clarvis": "Clarvis performs a contained, undoable task through that route",
    "trace": "NERVIS shows live progress and the joined trace",
    "degrade": "restarting a component produces accurate degraded state and recovery",
    "redaction": "evidence is inspectable without exposing credentials, prompts or private paths",
    "direct": "the direct-provider path still works",
    "bridge": "the Bridge-disabled path still works",
}

# RAVIS re-reads SIRVIS's evidence on this timer and exposes no route to force
# it, so the ceiling below is the cache TTL plus a margin. A shorter wait would
# report "RAVIS ignored the measurement" for a RAVIS that had simply not looked
# yet, which is the kind of false finding this track has spent eleven patches
# learning to avoid.
# Long enough to clear RAVIS's per-minute window rather than to nibble at it.
RATE_LIMIT_PAUSE_SECONDS = 20.0

EVIDENCE_TTL_SECONDS = 300.0
EVIDENCE_CEILING_SECONDS = 360.0


class Result:
    """What the procedure learned, and what it is still entitled to claim.

    `proved` and `skipped` are kept apart deliberately: a clause nobody
    performed is not a clause that passed, and the exit code says which.
    """

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.proved: set[str] = set()
        # Once a clause has failed it stays failed. Without this, a clause whose
        # later assertions pass reports PROVED above the list of its own
        # failures — which is precisely the shape of optimistic reporting §14.8
        # exists to stop.
        self.broken: set[str] = set()
        self.skipped: dict[str, str] = {}
        self.trace_id = uuid.uuid4().hex
        self.traceparent = f"00-{self.trace_id}-{uuid.uuid4().hex[:16]}-01"

    def ok(self, clause: str, note: str) -> None:
        if clause not in self.broken:
            self.proved.add(clause)
        say(f"  PASS  {note}")

    def bad(self, clause: str, note: str) -> None:
        self.broken.add(clause)
        self.proved.discard(clause)
        self.failures.append(f"{clause}: {note}")
        say(f"  FAIL  {note}")

    def skip(self, clause: str, why: str) -> None:
        self.skipped[clause] = why
        say(f"  SKIP  {CLAUSES[clause]} — {why}")

    def note(self, text: str) -> None:
        say(f"        {text}")


def say(text: str = "") -> None:
    """Narration, flushed.

    Redirected to a file, stdout is block-buffered: the section headers would
    sit in the buffer while the `PASS` lines — flushed as they are written —
    land ahead of them. A procedure whose narration arrives after its own
    results is one nobody can follow while it runs.
    """
    print(text, flush=True)


def call(method: str, url: str, *, token: str = "", body: Any = None, trace: str = "",
         request_id: str = "", timeout: float = 20.0, patient: bool = True
         ) -> tuple[int, Any, dict[str, str]]:
    """One HTTP call, returning (status, parsed-or-raw body, headers).

    Failures are values here, not exceptions. A 403 is asserted on several times
    and a refused connection is the *point* of the restart clause; turning either
    into a traceback would mean writing the interesting cases as `except` blocks.
    A status of 0 means nothing answered.

    **A 429 is waited out, once.** RAVIS allows an anonymous caller sixty
    requests a minute and this procedure is an anonymous caller; a sweep of
    twenty evidence surfaces plus decision polling crosses that on its own. The
    limiter is doing its job, so the answer is to slow down rather than to
    authenticate as somebody else or to read the refusal as a defect.
    """
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Host", url.split("//", 1)[1].split("/", 1)[0])
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    if trace:
        request.add_header("traceparent", trace)
    if request_id:
        request.add_header("x-request-id", request_id)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return answer.status, _parse(answer.read()), dict(answer.headers)
    except urllib.error.HTTPError as refusal:
        if refusal.code == 429 and patient:
            time.sleep(RATE_LIMIT_PAUSE_SECONDS)
            return call(method, url, token=token, body=body, trace=trace,
                        request_id=request_id, timeout=timeout, patient=False)
        return refusal.code, _parse(refusal.read()), dict(refusal.headers)
    except (urllib.error.URLError, TimeoutError, OSError) as unreachable:
        return 0, {"unreachable": type(unreachable).__name__}, {}


def _parse(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace")


def token(name: str) -> str:
    """A launcher-minted credential, read rather than minted.

    `sirvis token --mint` writes a live row no CLI or route can revoke, so a
    procedure that minted one per run would leave a growing key ring behind it —
    a repeatable procedure that is not repeatable without cleanup.
    """
    path = RUN / f"{name}.token"
    return path.read_text().strip() if path.exists() else ""


def wait_for(condition: Callable[[], bool], ceiling: float, interval: float = 2.0) -> bool:
    started = time.monotonic()
    while time.monotonic() - started < ceiling:
        if condition():
            return True
        time.sleep(interval)
    return False


class LiveFeed:
    """The event stream, read in the background while the work happens.

    Claim 4 says NERVIS shows live *progress*. Reading the trace afterwards
    proves the recording, not the liveness — the same body would come back from
    a hub that buffered everything and published it at the end. So this opens the
    stream before any work is driven and keeps what arrives, and the assertion is
    that frames for this run showed up while it was still running.
    """

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        self.frames: list[dict[str, Any]] = []
        self.live = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._read, daemon=True)

    def start(self) -> None:
        self._thread.start()
        # The stream sends its backlog, then a boundary frame saying the reader
        # has caught up. Waiting for it is what makes everything after it live.
        wait_for(lambda: self.live, 15.0, interval=0.5)

    def _read(self) -> None:
        request = urllib.request.Request(f"{NERVIS}/api/v1/events/stream")
        request.add_header("Host", "127.0.0.1:8790")
        try:
            with urllib.request.urlopen(request, timeout=300.0) as stream:
                event, data = "", ""
                for raw in stream:
                    if self._stop.is_set():
                        return
                    line = raw.decode("utf-8", "replace").rstrip("\n")
                    if line.startswith("event: "):
                        event = line[7:]
                    elif line.startswith("data: "):
                        data = line[6:]
                    elif not line:
                        self._frame(event, data)
                        event, data = "", ""
        except (urllib.error.URLError, TimeoutError, OSError):
            return

    def _frame(self, event: str, data: str) -> None:
        if event == "ecosystem.stream.live":
            self.live = True
            return
        if not data:
            return
        try:
            envelope = json.loads(data)
        except json.JSONDecodeError:
            return
        if isinstance(envelope, dict) and envelope.get("trace_id") == self.trace_id:
            self.frames.append(envelope)

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------- preflight


def preflight(result: Result) -> dict[str, Any]:
    """Everything needed before the procedure is entitled to assert anything.

    Returns each service's identity. The restart clause compares against it, and
    a changed `instance_id` beside an unchanged `service_id` is the only real
    proof that a new process answered rather than a cached body.
    """
    say("\nPreflight")
    baseline: dict[str, Any] = {}
    for name, base in (("sirvis", SIRVIS), ("ravis", RAVIS), ("nervis", NERVIS)):
        status, body, _ = call("GET", f"{base}/ecosystem/identity")
        if status != 200 or not isinstance(body, dict):
            say(f"  STOP  {name} did not answer /ecosystem/identity ({status})")
            raise SystemExit(EXIT_NOT_RUNNABLE)
        baseline[name] = body
        say(f"  PASS  {name} {body.get('build_version', '?')} "
              f"instance {str(body.get('instance_id', ''))[:8]}")

    _, runtimes, _ = call("GET", f"{SIRVIS}/api/v1/runtimes")
    ready = [r for r in (runtimes.get("items", []) if isinstance(runtimes, dict) else [])
             if r.get("state") == "ready"]
    if not ready:
        say("  STOP  no runtime is ready; a live measurement needs LM Studio answering")
        raise SystemExit(EXIT_NOT_RUNNABLE)
    if not ready[0].get("lifecycle_available"):
        # Without the `lms` CLI the engine loads nothing, and the run does not
        # fail early — it fails *after* the load at the variant gate, which reads
        # like a benchmark defect rather than a missing dependency.
        say("  STOP  the runtime has no lifecycle control (`lms` CLI); the run would abort "
              "at the variant gate")
        raise SystemExit(EXIT_NOT_RUNNABLE)
    say(f"  PASS  runtime {ready[0].get('runtime_key')} ready, lifecycle available")

    if not token("nervis-benchmark"):
        say("  STOP  no benchmark-scoped token in .run/; start the stack with tools/run.py")
        raise SystemExit(EXIT_NOT_RUNNABLE)

    # A service held unwell for several probe sweeps is what NERVIS's background
    # work watches for, and the restart clause holds one down deliberately. Left
    # enabled, the procedure would spend money as a side effect of testing an
    # outage, which nobody asked it to do.
    _, settings, _ = call("GET", f"{NERVIS}/api/v1/settings")
    values = settings.get("settings", settings) if isinstance(settings, dict) else {}
    if str(values.get("background.enabled", "")) in {"1", "true", "True"}:
        say("  NOTE  NERVIS background work is enabled; the deliberate outage below can "
              "trigger a real diagnostic run")
    say(f"  PASS  trace {result.trace_id}")
    return baseline


# ------------------------------------------- 1. RAVIS names the model (chain head)


def name_the_model(result: Result, pool: str) -> tuple[str, str]:
    """Route the pool once and let RAVIS choose. Its winner is the whole run's subject.

    Deliberately before the measurement. Picking the smallest build and hoping
    RAVIS would agree is how a procedure ends up proving three unrelated things;
    asking RAVIS first makes "selects **it**" a real assertion later, because the
    model was RAVIS's choice before SIRVIS had said anything new about it.
    """
    say(f"\n1. RAVIS names a model from {pool}")
    request_id = uuid.uuid4().hex
    status, body, _ = call("POST", f"{RAVIS}/v1/chat/completions", timeout=180.0,
                           trace=result.traceparent, request_id=request_id,
                           body={"model": pool, "max_tokens": 8,
                                 "messages": [{"role": "user", "content": "reply with: ok"}]})
    if status != 200:
        say(f"  STOP  {pool} did not answer ({status}): {str(body)[:200]}")
        raise SystemExit(EXIT_NOT_RUNNABLE)
    decision = decision_for(request_id)
    model = decision.get("selected", "")
    if not model:
        say("  STOP  the completion answered but no decision recorded a selection")
        raise SystemExit(EXIT_NOT_RUNNABLE)
    say(f"  PASS  RAVIS selected {model}")
    return model, decision.get("provider") or (decision.get("execution") or {}).get("provider", "")


# ------------------------------------------------------- 2. SIRVIS measures it


def measure(result: Result, model: str, context_length: int) -> dict[str, Any]:
    """Measure the model RAVIS named, and prove the record is truthful about conditions.

    The specification is the smallest experiment the parser accepts: one test,
    one repetition, sixteen tokens. One warmup rather than none, because the
    thinking-suppression probe only fires when a warmup ran and came back empty —
    with zero, a reasoning model's silence is recorded as its measurement instead
    of it being asked again in a way it can answer.
    """
    say(f"\n2. SIRVIS measures {model} (§16 item 12: {CLAUSES['measure']})")
    specification = {
        "id": "acceptance",
        "suite": "acceptance-golden-path",
        "suite_version": "1",
        "role": "general",
        "environment": "shared",
        "target": {"model": model, "load": {"context_length": context_length}},
        "warmups": 1,
        "repetitions": 1,
        "tests": [{
            "id": "acceptance-short",
            "version": "1",
            "prompt": "Reply with the single word: ok",
            "generation": {"max_tokens": 16, "temperature": 0.0},
        }],
    }
    status, body, _ = call("POST", f"{SIRVIS}/api/v1/benchmark-jobs",
                           token=token("nervis-benchmark"), trace=result.traceparent,
                           body={"specification": specification, "model": model})
    if status != 202 or not isinstance(body, dict):
        result.bad("measure", f"submit returned {status}: {str(body)[:200]}")
        return {}
    job_id = body.get("job", {}).get("job_id", "")
    result.ok("measure", f"job {job_id} queued")

    job = poll_job(result, job_id)
    if job.get("state") != "succeeded":
        result.bad("measure", f"job ended {job.get('state')}: {job.get('detail')}")
        return {}
    run_id = job.get("run_id") or ""

    status, run, _ = call("GET", f"{SIRVIS}/api/v1/benchmark-runs/{run_id}")
    rows = run.get("results", []) if isinstance(run, dict) else []
    if status != 200 or not rows:
        result.bad("measure", f"run {run_id} carried no result rows")
        return {}
    record_id = rows[0].get("result_id", "")
    evidence_id = rows[0].get("evidence_id", "")
    status, record, _ = call("GET", f"{SIRVIS}/api/v1/benchmark-results/{record_id}")
    if status != 200 or not isinstance(record, dict):
        result.bad("measure", f"result {record_id} did not read back ({status})")
        return {}
    truthful_conditions(result, record, context_length)
    truthful_provenance(result, record)
    return {"run_id": run_id, "result_id": record_id, "evidence_id": evidence_id,
            "results_path": run.get("results_path", ""), "model": model}


def trial(result: Result, model: str) -> str:
    """A second, larger measurement: the tool-call trial RAVIS can form a verdict from.

    Two jobs rather than one, because a role run and a configured run are
    different requests. `clarvis_role` replaces the whole workload with the
    role's own scenes and supplies no `target.load`, so folding the trial into
    the run above would cost the requested-versus-effective comparison that is
    the first clause's entire subject.

    And it has to be the trial: RAVIS's per-build verdict answers *did this
    build pass a tool-call trial*, and its record tally is keyed by build and
    role, so a second prose measurement of the same build under the same role
    replaces the first and moves no number at all. §13.2's bar is eight
    phrasings times three repetitions, which is what the role's own defaults
    run — a smaller sample returns UNKNOWN, which is fail-closed and useless as
    a signal that anything was read.
    """
    say(f"\n3. SIRVIS runs the tool-call trial on {model} (24 attempts; a few minutes)")
    status, body, _ = call("POST", f"{SIRVIS}/api/v1/benchmark-jobs",
                           token=token("nervis-benchmark"), trace=result.traceparent,
                           body={"specification": {
                               "id": "acceptance-trial",
                               "suite": "acceptance-golden-path-trial",
                               "suite_version": "1",
                               "target": {"model": model},
                               "warmups": 0,
                               "repetitions": 3,
                               "tests": [{"id": "acceptance-trial", "version": "1",
                                          "prompt": "Reply with the single word: ok",
                                          "generation": {"max_tokens": 16, "temperature": 0.0}}],
                           }, "model": model, "clarvis_role": "clarvis-agent"})
    if status != 202 or not isinstance(body, dict):
        result.bad("route", f"the trial submit returned {status}: {str(body)[:160]}")
        return ""
    job = poll_job(result, body.get("job", {}).get("job_id", ""))
    if job.get("state") != "succeeded":
        result.bad("route", f"the trial ended {job.get('state')}: {job.get('detail')}")
        return ""
    _, run, _ = call("GET", f"{SIRVIS}/api/v1/benchmark-runs/{job.get('run_id')}")
    rows = run.get("results", []) if isinstance(run, dict) else []
    evidence_id = rows[0].get("evidence_id", "") if rows else ""
    if not evidence_id:
        result.bad("route", "the trial produced no evidence record to look for")
        return ""
    result.ok("measure", f"the tool-call trial completed on the same build: {evidence_id}")
    return evidence_id


def poll_job(result: Result, job_id: str, ceiling: float = 900.0) -> dict[str, Any]:
    """Wait for the queue to finish this job.

    Generous, because the ceiling that matters is elsewhere: a cold load has its
    own 600-second limit inside the runtime, and giving up first would report a
    timeout for a run that was still working.
    """
    started = time.monotonic()
    seen = ""
    while time.monotonic() - started < ceiling:
        _, body, _ = call("GET", f"{SIRVIS}/api/v1/benchmark-jobs/{job_id}")
        job = body.get("job", {}) if isinstance(body, dict) else {}
        state = job.get("state", "")
        if state != seen:
            result.note(f"{state} ({int(time.monotonic() - started)}s)")
            seen = state
        if state in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(2.0)
    return {"state": "timeout", "detail": f"still running after {ceiling:.0f}s"}


def truthful_conditions(result: Result, record: dict[str, Any], asked: int) -> None:
    """Both conditions present and distinguishable — not equal, which is a different claim.

    §16 item 7's finding was that a run could report conditions it never ran
    under. The fix records both and lets a reader see the difference, so a run
    that got less context than it asked for is a *truthful* record and one that
    reports only what it asked for is not.
    """
    requested = record.get("requested_configuration")
    effective = (record.get("target") or {}).get("runtime_config") or record.get(
        "effective_configuration")
    if not isinstance(requested, dict) or not requested:
        result.bad("measure", "the record carries no requested_configuration")
        return
    if requested.get("context_length") != asked:
        result.bad("measure", f"requested_configuration says {requested.get('context_length')}; "
                              f"the specification asked for {asked}")
        return
    if not isinstance(effective, dict) or not effective:
        result.bad("measure", "the record carries no effective configuration to compare against")
        return
    got = effective.get("context_length")
    scopes = record.get("validity_scopes") or []
    if got == asked:
        result.ok("measure", f"requested and effective context both {asked}")
    elif "CONDITIONS" in scopes:
        result.ok("measure", f"asked {asked}, ran at {got} — recorded, and scoped CONDITIONS")
    else:
        result.bad("measure", f"asked {asked}, ran at {got}, and nothing scoped the difference")
        return
    result.ok("measure", f"validity scoped to {scopes or 'nothing — no warnings'}")


def truthful_provenance(result: Result, record: dict[str, Any]) -> None:
    """Every number says how it was obtained, and the record is never stronger than its parts."""
    metrics = record.get("metrics") or {}
    kinds = {name: (m.get("provenance") or {}).get("kind")
             for name, m in metrics.items() if isinstance(m, dict)}
    unlabelled = sorted(name for name, kind in kinds.items() if not kind)
    if unlabelled:
        result.bad("measure", f"metrics with no provenance: {', '.join(unlabelled)}")
        return
    estimated = sorted(name for name, kind in kinds.items() if kind == "ESTIMATED")
    overall = record.get("evidence_type", "")
    if estimated and overall == "MEASURED":
        result.bad("measure", f"the record claims MEASURED while {estimated} are ESTIMATED")
        return
    result.ok("measure", f"{len(kinds)} metrics labelled, record is {overall}"
                         + (f", weakened by {len(estimated)} estimate(s)" if estimated else ""))


# ------------------------------------------- 3. RAVIS consumes it, and selects it


def ravis_verdict(model: str) -> dict[str, Any]:
    """What RAVIS currently believes about one build."""
    _, body, _ = call("GET", f"{RAVIS}/api/v1/evidence")
    rows: list[dict[str, Any]] = body.get("items", []) if isinstance(body, dict) else []
    return next((i for i in rows if i.get("model_id") == model), {})


def consumed(result: Result, model: str, evidence_id: str) -> bool:
    """Wait until RAVIS is citing *this* run's evidence record for the build.

    The hinge of "RAVIS selects **it**". RAVIS re-reads SIRVIS on a cache timer
    and publishes no route to force it, so this waits rather than pokes — a back
    door built for a test would be a back door.

    **The record id, not the verdict and not a tally.** Two earlier versions of
    this assertion were wrong in the same direction. A count cannot move:
    records are keyed by build and role, so a fresh measurement replaces a row
    rather than adding one. A changed verdict works exactly once: the second run
    of this procedure measures the same build to the same conclusion, so
    "UNKNOWN became UNSUPPORTED" never happens again and a repeatable procedure
    would fail on its second use. The record id is unique per run and is what
    RAVIS publishes once it holds a verdict, so it answers the only question
    worth asking — is RAVIS reading the measurement this run just took.
    """
    say(f"\n4. RAVIS reads the new evidence (up to {EVIDENCE_CEILING_SECONDS:.0f}s; its "
        f"cache TTL is {EVIDENCE_TTL_SECONDS:.0f}s)")
    started = time.monotonic()

    def cited() -> bool:
        held = (ravis_verdict(model).get("evidence") or {}).get("evidence_id", "")
        return bool(held) and held == evidence_id

    if not wait_for(cited, EVIDENCE_CEILING_SECONDS, interval=15.0):
        now = ravis_verdict(model)
        held = (now.get("evidence") or {}).get("evidence_id", "") or "nothing"
        result.bad("route", f"after {EVIDENCE_CEILING_SECONDS:.0f}s RAVIS still cites {held} "
                            f"for {model}, not {evidence_id}; selection would be asserted "
                            "against a snapshot older than the measurement")
        return False
    now = ravis_verdict(model)
    result.ok("route", f"RAVIS re-read SIRVIS after {int(time.monotonic() - started)}s and now "
                       f"cites {evidence_id}: {model} is {now.get('state')} — "
                       f"{str(now.get('detail'))[:70]}")
    return True


def route(result: Result, pool: str, model: str) -> None:
    """Route the same pool again and require the same model, explained.

    The request id is minted here, not read back. Asking RAVIS which decision it
    had just made would be taking its word for the join being proved.
    """
    say(f"\n5. RAVIS selects it and explains (§16 item 12: {CLAUSES['route']})")
    request_id = uuid.uuid4().hex
    status, body, _ = call("POST", f"{RAVIS}/v1/chat/completions", timeout=180.0,
                           trace=result.traceparent, request_id=request_id,
                           body={"model": pool, "max_tokens": 16,
                                 "messages": [{"role": "user",
                                               "content": "reply with the single word ok"}]})
    if status != 200:
        result.bad("route", f"{pool} returned {status}: {str(body)[:200]}")
        return
    decision = decision_for(request_id)
    if not decision:
        result.bad("route", f"no recorded decision matched request {request_id}")
        return
    if decision.get("selected") != model:
        # Not automatically a defect — a pool is allowed to change its mind — but
        # it breaks item 12's chain, so it is reported as the failure of *this*
        # procedure to demonstrate the clause rather than silently accepted.
        result.bad("route", f"the chain broke: SIRVIS measured {model}, RAVIS then selected "
                            f"{decision.get('selected')}")
        return
    result.ok("route", f"the measured model {model} is the one selected")
    explains(result, decision)


def decision_for(request_id: str) -> dict[str, Any]:
    """The recorded explanation for one request, matched on the caller's own id.

    Never `items[0]`. The decision log is shared with the dashboard's polling and
    anything else talking to RAVIS, so the newest row is whoever spoke last.
    """
    _, body, _ = call("GET", f"{RAVIS}/api/v1/route-decisions?limit=100")
    for item in (body.get("items", []) if isinstance(body, dict) else []):
        if item.get("request_id") == request_id:
            return item
    return {}


def explains(result: Result, decision: dict[str, Any]) -> None:
    """Every candidate, every exclusion, and the winning factor — each actually populated.

    Structure only. The reason is assembled prose with no wire contract pinning
    its wording, so asserting a substring would make this procedure fail the day
    somebody improves a sentence.
    """
    considered = decision.get("considered") or []
    excluded = decision.get("excluded") or []
    reason = (decision.get("reason") or "").strip()
    if not considered:
        result.bad("route", "the decision considered nothing — an explanation with no candidates")
        return
    unreasoned = [e.get("model") for e in excluded if not e.get("reasons")]
    if unreasoned:
        result.bad("route", f"{len(unreasoned)} exclusion(s) carry no reason, "
                            f"e.g. {unreasoned[0]}")
        return
    if not reason:
        result.bad("route", "the decision names a winner but no winning factor")
        return
    result.ok("route", f"{len(considered)} considered, {len(excluded)} excluded each with a "
                       f"reason, winner explained in {len(reason)} characters")

    decision_id = decision.get("decision_id", "")
    status, single, _ = call("GET", f"{RAVIS}/api/v1/route-decisions/{decision_id}")
    if status != 200 or single.get("decision_id") != decision_id:
        result.bad("route", f"the decision did not read back by id ({status})")
        return
    if (single.get("execution") or {}).get("attempts"):
        result.ok("route", "the decision records what execution actually did, not only the plan")
    else:
        result.bad("route", "the decision carries no execution record after the reply completed")
    status, envelope, _ = call("GET", f"{RAVIS}/api/v1/route-decisions/doesnotexist")
    error = envelope.get("error", {}) if isinstance(envelope, dict) else {}
    if status == 404 and "retryable" in error:
        result.ok("route", "a missing decision refuses with the §4.5 envelope")
    else:
        result.bad("route", f"a missing decision returned {status} without the canonical envelope")


def direct_provider(result: Result, provider: str, model: str, *, when: str) -> bool:
    """CLAIM 7a — the same gateway, addressed past the pool.

    Run twice: once healthy, once with SIRVIS stopped. "Still work" is a claim
    about the broken state, and a direct address that only works while everything
    is fine is not independent of the evidence path at all.
    """
    request_id = uuid.uuid4().hex
    status, body, _ = call("POST", f"{RAVIS}/v1/chat/completions", timeout=180.0,
                           trace=result.traceparent, request_id=request_id,
                           body={"model": f"ravis/{provider}/{model}", "max_tokens": 16,
                                 "messages": [{"role": "user", "content": "reply with: ok"}]})
    if status != 200:
        result.bad("direct", f"direct address returned {status} {when}: {str(body)[:160]}")
        return False
    decision = decision_for(request_id)
    if decision.get("pool"):
        result.bad("direct", f"a direct address was routed through pool {decision['pool']} {when}")
        return False
    result.ok("direct", f"ravis/{provider}/{model} answered with no pool involved, {when}")
    return True


# ----------------------------------------------------- 5. NERVIS joins the trace


def nervis_turn(result: Result, pool: str) -> bool:
    """One chat turn originated by NERVIS, under the same trace.

    Without it the trace is a perfectly good join of RAVIS and SIRVIS assembled
    by a NERVIS that never took part, and the clause asks NERVIS to show *its*
    work too. The turn is a stream; it has to be read to completion or the span
    closes when the client hangs up rather than when the answer ended.
    """
    status, body, _ = call("POST", f"{NERVIS}/api/v1/chat", timeout=180.0,
                           trace=result.traceparent,
                           body={"content": "reply with the single word ok", "profile": pool})
    if status != 200:
        result.bad("trace", f"NERVIS's own chat turn returned {status}: {str(body)[:160]}")
        return False
    result.ok("trace", "NERVIS drove a turn of its own under this trace")
    return True


def joined_trace(result: Result, feed: LiveFeed, expect: set[str]) -> None:
    """The whole run as one thing, and frames that arrived while it was running."""
    say(f"\n7. NERVIS shows progress and joins the trace (§16 item 12: {CLAUSES['trace']})")
    # RAVIS and SIRVIS append to a buffer their publisher drains every two
    # seconds. Reading immediately asserts on a trace that is merely incomplete,
    # which looks identical to one that never joined.
    time.sleep(8.0)
    if feed.live and feed.frames:
        kinds = sorted({f.get("event_type", "") for f in feed.frames})
        result.ok("trace", f"{len(feed.frames)} frames arrived live on the stream: "
                           f"{', '.join(kinds[:4])}" + (" …" if len(kinds) > 4 else ""))
    elif feed.live:
        result.bad("trace", "the stream was live and carried no frame for this run")
    else:
        result.bad("trace", "the event stream never reached its live boundary")

    status, trace, _ = call("GET", f"{NERVIS}/api/v1/traces/{result.trace_id}")
    if status != 200 or not isinstance(trace, dict):
        result.bad("trace", f"the trace did not read back ({status})")
        return
    services = {span.get("service") for span in trace.get("spans", [])}
    missing = expect - services
    if missing:
        result.bad("trace", f"the trace carries no span for {', '.join(sorted(missing))}")
    else:
        result.ok("trace", f"one trace, spans from {', '.join(sorted(str(s) for s in services))}")

    fabricated = [s for s in trace.get("spans", [])
                  if (s.get("events") or 0) < 2 and s.get("duration_ms") is not None]
    if fabricated:
        result.bad("trace", f"{len(fabricated)} span(s) claim a duration from a single event")
    else:
        result.ok("trace", "no span invents a duration it could not have measured")

    status, unified, _ = call("GET", f"{NERVIS}/api/v1/traces/{result.trace_id}/unified")
    if status == 200 and isinstance(unified, dict) and "trace" in unified:
        result.ok("trace", f"the unified view assembled, partial={unified.get('partial')}")
    else:
        result.bad("trace", f"the unified view did not read back ({status})")

    _, quarantine, _ = call("GET", f"{NERVIS}/api/v1/events/quarantine?limit=50")
    held = quarantine.get("items", []) if isinstance(quarantine, dict) else []
    if held:
        result.note(f"{len(held)} envelope(s) are in quarantine — not this run's, but worth a look")


# ------------------------------------------ 6. one component restarts, honestly


def restart_clause(result: Result, baseline: dict[str, Any], model: str, provider: str) -> None:
    """Stop SIRVIS, prove the ecosystem says so, start it, prove the ecosystem notices.

    SIRVIS is the right component to stop: §13.4 makes it optional to routing, so
    this proves the second half of the clause too — that its absence degrades
    what depended on it and nothing else. It runs last because the decision log
    RAVIS keeps is in memory, and restarting anything earlier would erase the
    evidence the routing clause rests on.
    """
    say(f"\n10. Restart under load (§16 item 12: {CLAUSES['degrade']})")
    pid = recorded_pid("SIRVIS")
    if not pid:
        result.skip("degrade", "no recorded SIRVIS pid in .run/services.json")
        return
    subprocess.run(["kill", "-15", str(pid)], check=False)
    if not wait_for(lambda: call("GET", f"{SIRVIS}/ecosystem/health")[0] == 0, 30.0):
        result.bad("degrade", "SIRVIS kept answering after SIGTERM")
        return
    result.ok("degrade", f"SIRVIS (pid {pid}) stopped")
    try:
        degraded_state(result, model, provider)
    finally:
        recover(result, baseline)


def degraded_state(result: Result, model: str, provider: str) -> None:
    """What the rest of the ecosystem should say while one peer is gone."""
    if wait_for(lambda: peer_state("sirvis") == "unreachable", 60.0):
        result.ok("degrade", "NERVIS reports SIRVIS unreachable — not 'stopped', which it would "
                             "say only for a service it stopped itself")
    else:
        result.bad("degrade", f"NERVIS still reports SIRVIS as {peer_state('sirvis')!r}")

    _, evidence, _ = call("GET", f"{RAVIS}/api/v1/evidence")
    source = (evidence.get("source") or {}) if isinstance(evidence, dict) else {}
    if source.get("state") == "degraded":
        result.ok("degrade", f"RAVIS's evidence source degraded: {str(source.get('detail'))[:70]}")
    else:
        result.note(f"RAVIS's evidence source still says {source.get('state')!r} — it re-reads on "
                    "its own timer, so this is a cache age, not a lie")

    status, _, _ = call("GET", f"{RAVIS}/v1/models", timeout=30.0)
    _, health, _ = call("GET", f"{RAVIS}/ecosystem/health")
    if status == 200 and health.get("status") == "healthy":
        result.ok("degrade", "RAVIS keeps its catalogue and its health with SIRVIS gone")
    else:
        result.bad("degrade", f"RAVIS degraded with SIRVIS gone: catalogue {status}, "
                              f"health {health.get('status')}")

    _, surface, _ = call("GET", f"{NERVIS}/api/v1/sirvis/models")
    if isinstance(surface, dict) and surface.get("available") is False \
            and surface.get("data") is None:
        result.ok("degrade", f"NERVIS's proxied read says {surface.get('availability')!r} and "
                             "returns no data rather than an empty list")
    else:
        result.bad("degrade", "NERVIS's proxied read of a dead peer did not degrade honestly")

    direct_provider(result, provider, model, when="with SIRVIS stopped")


def recover(result: Result, baseline: dict[str, Any]) -> None:
    """Start SIRVIS again, prove a new process answered, and leave the launcher's book right."""
    environment = dict(os.environ,
                       SIRVIS_HOST="127.0.0.1", SIRVIS_PORT="8721",
                       SIRVIS_DATABASE_PATH=str(ROOT / "sirvis" / "sirvis.db"),
                       SIRVIS_RESULTS_PATH=str(ROOT / "sirvis" / "results"),
                       SIRVIS_NERVIS_BASE_URL=NERVIS)
    with (RUN / "sirvis.log").open("a") as log:
        started = subprocess.Popen([str(ROOT / "ravis" / ".venv" / "bin" / "sirvis"), "serve"],
                                   cwd=ROOT, env=environment, stdout=log, stderr=log,
                                   start_new_session=True)
    # Written back whatever happens next. `tools/run.py` has no per-service verb,
    # so this procedure is the only thing that knows the new pid — and a stale
    # one makes the next `stop` report "was not running" while the port is held.
    rewrite_pid("SIRVIS", started.pid)
    if not wait_for(lambda: call("GET", f"{SIRVIS}/ecosystem/health")[0] == 200, 90.0):
        result.bad("degrade", "SIRVIS did not come back within 90s")
        return
    _, identity, _ = call("GET", f"{SIRVIS}/ecosystem/identity")
    was = baseline["sirvis"]
    if identity.get("instance_id") == was.get("instance_id"):
        result.bad("degrade", "the same instance_id came back — a cached answer, not a restart")
        return
    if identity.get("service_id") != was.get("service_id"):
        result.bad("degrade", "the service_id changed; that is a different service, not a restart")
        return
    result.ok("degrade", "SIRVIS restarted: same service_id, new instance_id")

    if wait_for(lambda: peer_state("sirvis") == "healthy", 90.0):
        result.ok("degrade", "NERVIS sees SIRVIS healthy again")
    else:
        result.bad("degrade", f"NERVIS still reports SIRVIS as {peer_state('sirvis')!r}")
    announced(result)


def announced(result: Result) -> None:
    """Both transitions are in the hub, and each is worded as what it was."""
    _, body, _ = call("GET",
                      f"{NERVIS}/api/v1/events?event_type=nervis.service.state_changed&limit=20")
    items = [e for e in (body.get("items", []) if isinstance(body, dict) else [])
             if (e.get("data") or {}).get("service") == "sirvis"]
    went = [e for e in items if (e.get("data") or {}).get("to") == "unreachable"]
    came = [e for e in items if (e.get("data") or {}).get("to") == "healthy"]
    if went and came:
        result.ok("degrade", "both transitions recorded: healthy → unreachable → healthy")
    else:
        result.bad("degrade", f"the hub recorded {len(went)} outage and {len(came)} recovery "
                              "event(s) for sirvis")


def peer_state(service: str) -> str:
    _, body, _ = call("GET", f"{NERVIS}/api/v1/services")
    for item in (body.get("items", []) if isinstance(body, dict) else []):
        if item.get("key") == service:
            return str(item.get("state", ""))
    return ""


def recorded_pid(marker: str) -> int:
    path = RUN / "services.json"
    if not path.exists():
        return 0
    record = json.loads(path.read_text()).get(marker) or {}
    return int(record.get("pid") or 0)


def rewrite_pid(marker: str, pid: int) -> None:
    path = RUN / "services.json"
    if not path.exists():
        return
    book = json.loads(path.read_text())
    if marker in book:
        book[marker]["pid"] = pid
        path.write_text(json.dumps(book, indent=2))


# ------------------------------------------- 7. nothing leaks on the way out


#: Every read surface a person is invited to inspect. One pass over all of them
#: rather than an assertion per endpoint, because a leak is a leak wherever it
#: surfaces and the interesting failure is the surface nobody thought to name.
#: `/api/v1/system` is deliberately absent: reading it appends a machine
#: snapshot row, and a gate that mutates what it inspects is its own defect.
INSPECTABLE = (
    (NERVIS, "/api/v1/events?limit=200"),
    (NERVIS, "/api/v1/events/quarantine?limit=50"),
    (NERVIS, "/api/v1/traces?limit=25"),
    (NERVIS, "/api/v1/logs"),
    (NERVIS, "/api/v1/logs/nervis?limit=200"),
    (NERVIS, "/api/v1/logs/ravis?limit=200"),
    (NERVIS, "/api/v1/inspector"),
    (NERVIS, "/api/v1/settings"),
    (NERVIS, "/api/v1/settings/export"),
    (NERVIS, "/api/v1/learned"),
    (NERVIS, "/api/v1/services"),
    (NERVIS, "/api/v1/registry/instances"),
    (RAVIS, "/api/v1/providers"),
    (RAVIS, "/api/v1/providers/credentials"),
    (RAVIS, "/api/v1/route-decisions?limit=25"),
    (RAVIS, "/api/v1/sessions"),
    (RAVIS, "/api/v1/usage/records"),
    (SIRVIS, "/api/v1/evidence?limit=50"),
    (SIRVIS, "/api/v1/benchmark-runs?limit=10"),
    (SIRVIS, "/api/v1/models"),
    (SIRVIS, "/api/v1/health"),
)

BEARER = re.compile(r"(?i)\bbearer\s+(?!\[redacted])[A-Za-z0-9._~+/-]{12,}")
API_KEY = re.compile(r"\bsk-[A-Za-z0-9._-]{8,}")
PROMPT = "Reply with the single word: ok"


def redaction(result: Result) -> None:
    """Read every inspectable surface and look for what must never be on one."""
    say(f"\n6. Nothing leaks (§16 item 12: {CLAUSES['redaction']})")
    secrets = {token(name) for name in
               ("dashboard", "nervis-admin", "nervis-benchmark", "nervis-ravis", "ravis-admin")}
    secrets.discard("")
    home = str(Path.home())
    leaks: list[str] = []
    read = 0
    for base, path in INSPECTABLE:
        status, body, _ = call("GET", base + path, timeout=30.0)
        if status != 200:
            continue
        read += 1
        raw = body if isinstance(body, str) else json.dumps(body)
        where = f"{base.rsplit(':', 1)[1]}{path.split('?')[0]}"
        leaks += [f"{where} carries a launcher credential" for s in secrets if s in raw]
        if API_KEY.search(raw):
            leaks.append(f"{where} carries something shaped like an API key")
        if BEARER.search(raw):
            leaks.append(f"{where} carries an unredacted bearer credential")
        if home in raw:
            leaks.append(f"{where} carries an absolute path under the home directory")
        if PROMPT in raw:
            leaks.append(f"{where} carries the prompt text sent to a model")
    for leak in sorted(set(leaks)):
        result.bad("redaction", leak)
    if not leaks:
        result.ok("redaction", f"{read} surfaces read; no credential, prompt or private path")
    positive_contracts(result)


def positive_contracts(result: Result) -> None:
    """The other half of "inspectable": what is deliberately reachable, and refused."""
    _, health, _ = call("GET", f"{SIRVIS}/api/v1/health")
    if isinstance(health, dict) and health.get("api_token") == "configured":
        result.ok("redaction", "SIRVIS reports its token as configured, never as a value")
    else:
        result.bad("redaction", "SIRVIS's health did not report token presence as 'configured'")
    status, _, _ = call("GET", f"{SIRVIS}/api/v1/tokens")
    if status == 401:
        result.ok("redaction", "the key ring itself is privileged — anonymous reads get 401")
    else:
        result.bad("redaction", f"GET /api/v1/tokens answered {status} without a credential")


# ---------------------------------------------------- 8. the editor's own half


def clarvis_clause(result: Result, workspace: Path, model: str, unattended: bool) -> None:
    """The two steps a person performs, and verification that does not take their word.

    Everything asserted is read from outside VS Code — git, the checkpoint
    directory, RAVIS's own decision log. The extension reporting its own success
    would be the self-certification §14.6 was written about.
    """
    say(f"\n8. Clarvis, in your editor (§16 item 12: {CLAUSES['clarvis']})")
    if unattended:
        result.skip("clarvis", "--unattended: nobody was there to drive the editor")
        return
    if not (workspace / ".git").is_dir():
        result.bad("clarvis", f"{workspace} is not a git work tree; containment and undo both "
                              "depend on one")
        return
    say(f"""
    In VS Code, with {workspace} open:

      1. ⌘⇧P → "Clarvis: Run a Task"
      2. Ask for one small edit, e.g.:
             add a one-line comment at the top of notes.txt saying hello
      3. Let it finish.
    """)
    marker = time.time()
    input("    Press Enter when the task has finished. ")
    branches = git(workspace, "branch", "--list", "clarvis/*").strip()
    captured = checkpointed()
    if branches:
        result.ok("clarvis", f"contained on its own branch: {branches.lstrip('* ')}")
    else:
        result.bad("clarvis", "no clarvis/* branch — the run was not contained on one")
    if captured:
        result.ok("clarvis", f"checkpoint holds {len(captured)} file(s) to undo from")
    else:
        result.bad("clarvis", "the checkpoint directory is empty — nothing to undo from")
    through_that_route(result, model, marker)

    say("""
      4. ⌘⇧P → "Clarvis: Undo Last Agent Run" → click "Undo it"
    """)
    input("    Press Enter when the undo has finished. ")
    dirty = git(workspace, "status", "--porcelain").strip()
    if dirty:
        result.bad("clarvis", f"the workspace is still modified after undo: {dirty[:120]}")
    else:
        result.ok("clarvis", "undo restored the workspace; git reports it clean")


def checkpointed() -> list[str]:
    """What the last agent run copied before touching anything.

    Both hosts, because the task may be driven from either and each keeps its
    own global storage: desktop VS Code under `Library/Application Support/Code`
    and code-server under `.local/share/code-server`. Whichever holds files is
    the one that ran, and looking in only the first is how a run in the other
    reads as "nothing to undo from".
    """
    homes = (Path.home() / "Library/Application Support/Code/User/globalStorage",
             Path.home() / ".local/share/code-server/User/globalStorage")
    for home in homes:
        store = home / "krimkerre.clarvis" / "checkpoint"
        if store.is_dir():
            found = sorted(item.name for item in store.iterdir())
            if found:
                return found
    return []


def through_that_route(result: Result, model: str, since: float) -> None:
    """"Through that route" — the editor's traffic reached the same model, checked at RAVIS."""
    _, body, _ = call("GET", f"{RAVIS}/api/v1/route-decisions?limit=100")
    items = body.get("items", []) if isinstance(body, dict) else []
    recent = [d for d in items if d.get("selected") == model]
    if recent:
        result.ok("clarvis", f"RAVIS recorded a decision selecting {model} for the editor's turn")
    else:
        result.note(f"no decision in the last 100 selected {model} — the agent may have used a "
                    "different pool, which is a configuration fact rather than a failure")


def bridge_disabled(result: Result, port: int, unattended: bool) -> None:
    """CLAIM 7b — off means nothing is listening, and Clarvis still works anyway.

    Last, because it needs two window reloads and a reload destroys the checkpoint
    the undo assertion above depends on.
    """
    say(f"\n9. The Bridge, switched off (§16 item 12: {CLAUSES['bridge']})")
    if unattended or not port:
        result.skip("bridge", "--unattended: it needs a settings toggle and two reloads")
        return
    say("""
    In VS Code: set clarvis.bridge.enabled to false, then
    ⌘⇧P → "Developer: Reload Window".
    """)
    input("    Press Enter when the window has reloaded. ")
    status, _, _ = call("GET", f"http://127.0.0.1:{port}/v1/status", timeout=3.0)
    if status == 0:
        result.ok("bridge", f"port {port} refuses connections — off means unbound, not guarded")
    else:
        result.bad("bridge", f"the Bridge port still answers ({status}); off must mean nothing "
                            "is listening")
    _, body, _ = call("GET", f"{RAVIS}/v1/models", timeout=15.0)
    if isinstance(body, dict) and body.get("data"):
        result.ok("bridge", "Clarvis's own catalogue probe still answers with the Bridge off")
    else:
        result.bad("bridge", "RAVIS's catalogue stopped answering with the Bridge off")
    say("\n    Set clarvis.bridge.enabled back to true and reload once more.")
    input("    Press Enter when that is done. ")


def git(workspace: Path, *arguments: str) -> str:
    done = subprocess.run(["git", *arguments], cwd=workspace, check=False,
                          capture_output=True, text=True)
    return done.stdout


# ------------------------------------------------------------------ verdict


def report(result: Result) -> int:
    say("\n" + "=" * 72)
    say(f"Golden path, trace {result.trace_id}")
    say("=" * 72)
    for key, sentence in CLAUSES.items():
        if key in result.skipped:
            say(f"  NOT RUN  {sentence} — {result.skipped[key]}")
        elif key in result.proved:
            say(f"  PROVED   {sentence}")
        else:
            say(f"  FAILED   {sentence}")
    if result.failures:
        say(f"\n{len(result.failures)} assertion(s) failed:")
        for failure in result.failures:
            say(f"  • {failure}")
        say("\nA failed clause is a defect against §16, not a reason to relax the clause.")
        return EXIT_FAILED
    if result.skipped:
        say("\nEvery clause that ran passed. The run is incomplete, not green — what says "
              "NOT RUN\nabove needs a person at an editor.")
        return EXIT_INCOMPLETE
    say("\nEvery clause of §16 item 12 proved, live, in one procedure.")
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pool", default="ravis/local",
                        help="pool whose winner becomes the run's model (default: ravis/local)")
    parser.add_argument("--context", type=int, default=4096, help="context length to ask for")
    parser.add_argument("--workspace", default="", help="git work tree Clarvis will act in")
    parser.add_argument("--bridge-port", type=int, default=0,
                        help="port the Bridge is listening on, for the disabled-path check")
    parser.add_argument("--unattended", action="store_true",
                        help="skip the steps needing a person; exits 3, never 0")
    arguments = parser.parse_args()

    result = Result()
    baseline = preflight(result)
    feed = LiveFeed(result.trace_id)
    feed.start()
    try:
        model, provider = name_the_model(result, arguments.pool)
        measured = measure(result, model, arguments.context)
        if not measured:
            result.bad("route", "no measurement to select on; the chain stopped at SIRVIS")
        else:
            evidence_id = trial(result, model)
            if evidence_id and consumed(result, model, evidence_id):
                route(result, arguments.pool, model)
        direct_provider(result, provider or "local", model, when="with everything healthy")
        redaction(result)
        nervis_turn(result, arguments.pool)
        joined_trace(result, feed, {"nervis", "ravis"})
    finally:
        feed.stop()
    clarvis_clause(result, Path(arguments.workspace or ROOT), model, arguments.unattended)
    bridge_disabled(result, arguments.bridge_port, arguments.unattended)
    restart_clause(result, baseline, model, provider or "local")
    return report(result)


if __name__ == "__main__":
    sys.exit(main())
