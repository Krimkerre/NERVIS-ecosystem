"""Carrying out the one thing a person confirmed (§12).

**This is the only place in NERVIS that acts on another service.** Everything
else reads. §12 governs what that may look like: an enumerated set of
operations, no free-form command or process-selection path, a switch per family
that defaults to off, and an audit trail. All four are visible below —
`commands.OPERATIONS` is the set, the request names an operation by id rather
than describing one, the credential setting is the switch, and every attempt is
published to the hub whether it succeeded or not.

**What arrives here is a confirmation, not an instruction.** The proposal was
built from the person's own words in `nervis.commands`, shown to them with what
it would do written on it, and reached this endpoint because they pressed the
button. Nothing a model produced is involved at any step — §11.5's rule that a
model's reading of NERVIS's evidence may never become an action holds because
there is no path from model output to this function.

**The specification is fixed here, and that is the point.** §9 forbids benchmark
logic in NERVIS, and this is not any: it is the same standard experiment the
Benchmarks screen has always submitted, named once so the operation is closed.
A version that forwarded whatever specification a caller supplied would be the
free-form path §12 exists to prevent, dressed as a parameter.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request

from nervis import (
    annotate,
    background,
    chat,
    commands,
    documents,
    handoff,
    handoff_git,
    learned,
    pdf,
    style,
    transcript,
    visual_check,
    workspace,
)
from nervis.api.chat_calls import _forwarded
from nervis.api.chat_titles import _title_from
from nervis.api.documents import SERVED
from nervis.clarvis import HANDOVER_OPERATION
from nervis.errors import InvalidConfigurationError
from nervis.negotiation import Operation, may_attempt, negotiate
from nervis.registry import RegistryEntry
from nervis.workspace import OutsideWorkspaceError, resolve_in_workspace

router = APIRouter(prefix="/api/v1/commands", tags=["commands"])

# Long enough for SIRVIS to write a queue row and answer, short enough that a
# wedged peer does not hold the button. A benchmark takes minutes; *queueing*
# one is a database insert, which is why §4.2 has it answer 202 rather than
# holding the connection for the run.
SUBMIT_TIMEOUT_SECONDS = 10.0

# The standard experiment, identical to the one the Benchmarks screen submits.
# Sent as JSON because §4.2 stores what a job was given rather than a path: a
# queued job must not depend on a file still existing on the machine that runs
# it.
BENCHMARK_SPECIFICATION: dict[str, Any] = {
    "id": "basic",
    "suite": "performance-basic",
    "suite_version": "1",
    "role": "general",
    "environment": "shared",
    "target": {"model": "", "load": {"context_length": 8192}},
    "warmups": 2,
    "repetitions": 5,
    "tests": [
        {
            "id": "short-prompt-256-out",
            "version": 1,
            "prompt": (
                "Write a Python function that merges two sorted lists into one "
                "sorted list. Return only the code."
            ),
            "generation": {"temperature": 0, "max_tokens": 256},
        }
    ],
}

JOBS_CAPABILITY = "sirvis.benchmarks.jobs"
# SIRVIS M11's downloads, and a longer wait than a submit: SIRVIS reads the model's
# file list from Hugging Face before it answers.
DOWNLOADS_CAPABILITY = "sirvis.downloads"
DOWNLOAD_TIMEOUT_SECONDS = 30.0

# **The chosen background pool first, then this one model, then this machine's
# own** (`background.route`). `qwen2.5vl:3b` is the operator's measured choice
# of a local model: asked to name a layout defect in one word followed by a
# sentence, a small captioning model answered with a paragraph about a page it
# was not shown, while this one followed the format exactly. It used to be asked
# first with `ravis/vision` behind it; now the pool picked under Settings →
# Unattended work goes first like every other background call, this model is the
# first fallback, and the last is local — never `ravis/vision`, which can reach a
# hosted model and would overrule an operator who chose a private pool.
#
# Marked `background` for the same reason a title generation is (§9.6.1): this
# is an aside the person did not directly ask a hosted model to run, not the
# primary reply, and RAVIS refuses to spend a non-free provider's money on a
# call that declares itself one.
VISUAL_CHECK_MODEL = "ravis/ollama/qwen2.5vl:3b"
VISUAL_CHECK_TIMEOUT_SECONDS = 30.0
VISUAL_CHECK_MAX_TOKENS = 60

# The folder a handed-over task goes into is named by a background call, and
# goes where every background call goes (`background.route`): the pool chosen
# under Settings → Unattended work, then this machine's own models. A shorter
# timeout than a title's, because the person is waiting on the button that asked.
# **400, the same room a title gets.** Replayed against the running RAVIS on 10 September
# 2026 with this exact prompt: at 24 tokens `ravis/cheap` answered 502, and the
# two live presses before it came back empty after 15 and 10 seconds; at 400 it
# routed to `qwen2.5vl:3b` and answered `pomodoro-timer` in 3.1 seconds; the
# next day `ravis/free-api` did the same — nothing at 24, `pomodoro-timer` in
# 1.5 seconds at 400. The name itself is a handful of tokens — this is a
# ceiling, not a spend.
FOLDER_NAME_MAX_TOKENS = 400
FOLDER_NAME_TIMEOUT_SECONDS = 15.0
FOLDER_NAME_PROMPT = (
    "Name the project folder for this coding task: one to three plain words, "
    "lowercase, joined by hyphens, like pomodoro-timer or uploader-retry. Reply "
    "with the name alone. If the task does not say clearly enough what is being "
    "built or changed to name it, reply UNCLEAR.\n\nTask: "
)
NAME_QUESTION = (
    "What should this task's folder be called? A couple of words is plenty — "
    "like pomodoro-timer."
)


@router.post("/run")
async def run(request: Request) -> dict[str, Any]:
    """Do the confirmed thing, and say exactly what happened.

    Returns SIRVIS's own answer rather than a rewritten one, for §9's reason:
    the queue is what knows the state of a job, and a second opinion assembled
    here would eventually disagree with the first.
    """
    body = await _json_body(request)
    operation = str(body.get("operation") or "")
    target = str(body.get("target") or "").strip()
    if operation not in commands.BY_ID:
        # Not a validation failure — an operation outside the set does not
        # exist, which is §12's wording and a deliberate distinction: it means
        # the surface cannot be enumerated by probing it for near-misses.
        raise InvalidConfigurationError(f"no such operation {operation!r}")
    if not target:
        raise InvalidConfigurationError("an operation needs a target")
    # **A table rather than a ladder.** Each new operation added a branch, and
    # the eighth took this past the complexity gate. The dispatch was never a
    # decision — it is a lookup that happened to be written as `if`s, and one
    # entry per operation is also how §12's closed set reads.
    conversation = str(body.get("conversation_id") or "")
    own = {
        "nervis.document.write": lambda: _write_document(request, target, conversation),
        "nervis.document.annotate.notes":
            lambda: _annotate_document(request, target, conversation, "notes"),
        "nervis.document.annotate.margin":
            lambda: _annotate_document(request, target, conversation, "margin"),
        "nervis.document.annotate.inline":
            lambda: _annotate_document(request, target, conversation, "inline"),
        "nervis.conversation.export": lambda: _export_conversation(request, target, conversation),
        "nervis.clarvis.task":
            lambda: _hand_over(request, target, conversation, str(body.get("name") or "")),
        "nervis.knowledge.learn":
            lambda: _learn(request, target, str(body.get("prompted_by") or "")),
    }
    if operation in own:
        return await own[operation]()
    if operation == "sirvis.download.start":
        return await _start_download(
            request, target, str(body.get("quantization") or ""), body.get("confirm") is True
        )
    if operation == "sirvis.benchmark.cancel":
        return await _cancel_benchmark(request, target)
    if operation == "sirvis.result.delete":
        return await _delete_result(request, target, str(body.get("reason") or ""))
    return await _submit_benchmark(request, target)


async def _hand_over(
    request: Request, task: str, conversation_id: str, name: str
) -> dict[str, Any]:
    """Write a coding task where Clarvis will find it (M27).

    **This writes a file and nothing else.** It does not start a run, resolve a
    gate or reach the editor — `CLARVIS.md` §6.7 forbids all three, and the
    property that keeps this on the right side of that line is that it works
    with the Bridge stopped. The person opens Clarvis, reads the task, edits it
    if they want to, and approves it there.
    """
    # **Written into the editor's own room, not the export room.** This file
    # exists to be opened in Clarvis; the export room is where things NERVIS
    # produced for a *person* go, and a task written there is one the editor
    # never sees.
    place = workspace.editor_room(request.app.state.settings)
    if place is None:
        raise InvalidConfigurationError(
            "NERVIS has no workspace configured, so it cannot hand a task to Clarvis. "
            "Set NERVIS_WORKSPACE_PATH to the directory the editor opens."
        )
    # **A folder name somebody can read, or a question.** The folder is what the
    # editor shows in its title bar and what the task is found by later, so it is
    # named for what the task is — `pomodoro-timer` — by a background model call.
    # A name the person typed is used as it stands. When neither gives one,
    # nothing is written and the card asks: a guessed name is a folder somebody
    # has to rename underneath an open editor.
    #
    # **A task that says nothing about what it is does not reach the model.**
    # "fix it" has no name in it to find, and the small model that answers this
    # pool names it `fix-it` rather than saying so — so the vagueness is judged
    # here, from the words, and the person is asked straight away.
    if name.strip():
        chosen = handoff.folder_name(name)
    elif handoff.says_what_it_is(task):
        chosen = await _suggest_folder_name(request, task)
    else:
        chosen = ""
    if not chosen:
        return {"needs_name": NAME_QUESTION}
    written = handoff.write(place, task, name=chosen, conversation=conversation_id)
    # **The new folder starts as a git repository** (0.29.3), because Codex saves
    # its work as commits and refuses a folder that is not one. Never a reason
    # for the hand-over to fail: when git cannot do it, the folder and brief are
    # already written and the answer says it is not a repository yet. Off the
    # event loop, since a stuck git may take its whole timeout.
    started = await asyncio.to_thread(handoff_git.start, place, written.folder)
    _audit(request, task, "written", f"handed to Clarvis in {written.folder}/"
           + ("" if started.repository else ", not a git repository yet"),
           verb="hand over",
           # What `/api/v1/handovers` joins Clarvis's `clarvis.task.*` events on.
           extra={"task_id": written.task_id, "folder": written.folder})
    detail = (f"waiting in {written.folder}/ — open that folder in Clarvis "
              "to read and approve it")
    if not started.repository:
        detail += f"; {started.detail}"
    return {
        "handoff": written.as_dict(),
        "file": {
            "name": handoff.TASK_FILE,
            # The task's own folder: what to open in Clarvis as its workspace.
            "folder": written.folder,
            # The same folder, absolute, so the page can open exactly this one in
            # the Code tab. Resolved the way `handoff.write` resolved it for its
            # containment check, so the editor session's root check compares
            # like with like.
            "workspace": str((place / written.folder).expanduser().resolve(strict=False)),
            "detail": detail,
            # Whether the folder is a git repository, and the sentence saying so.
            "repository": started.as_dict(),
        },
    }


async def _suggest_folder_name(request: Request, task: str) -> str:
    """A short folder name for a task, from a RAVIS background call — or nothing.

    Nothing means ask. Every failure lands there on purpose — no credential, no
    RAVIS, every model on the route refusing, timing out or answering with
    something that is not a name — because the alternative in each case is a
    guessed name, and the person is at the button.
    """
    settings = request.app.state.settings
    entry: RegistryEntry | None = request.app.state.registry.get("ravis")
    if not settings.ravis_client_credential or entry is None or not entry.is_usable:
        return ""
    config = background.settings(request.app.state.database)
    for model in background.route(config):
        name = await _folder_name_by(request, entry, model, task, background.marker(config))
        if name:
            return name
    return ""


async def _folder_name_by(
    request: Request, entry: RegistryEntry, model: str, task: str, said: dict[str, object],
) -> str:
    """One model's folder name for a task, or nothing when it had none to give."""
    client: httpx.AsyncClient = request.app.state.probe_client
    payload = {
        "model": model,
        "max_tokens": FOLDER_NAME_MAX_TOKENS,
        "metadata": said,
        "messages": [{"role": "user", "content": FOLDER_NAME_PROMPT + task[:600]}],
    }
    try:
        response = await client.post(
            entry.declaration.base_url + "/v1/chat/completions",
            json=payload,
            headers=_forwarded(
                getattr(request.state, "request_id", ""),
                getattr(request.state, "trace_id", ""),
                request.app.state.settings.ravis_client_credential,
            ),
            timeout=FOLDER_NAME_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            return ""
        return _folder_name_from(response.json())
    except (httpx.HTTPError, ValueError):
        return ""


def _folder_name_from(body: dict[str, Any]) -> str:
    """The folder name in a completion, or nothing when what came back is not one.

    Read with the title reader, which already strips a reasoning model's thinking
    and refuses a sentence. On top of that: `UNCLEAR` is the model saying it
    cannot name the task, more than four words is a description rather than a
    name, and a name made only of generic words (`fix-it`) names nothing.
    """
    line = _title_from(body)
    if not line or "unclear" in line.lower():
        return ""
    name = handoff.folder_name(line)
    if len(name.split("-")) > 4 or not handoff.says_what_it_is(name):
        return ""
    return name


async def _learn(request: Request, note: str, prompted_by: str) -> dict[str, Any]:
    """Write down something NERVIS was told (M23).

    **The person's own sentence, and nothing derived from it.** The heading is
    the first few words of the note in their order; NERVIS does not summarise,
    because a heading it invented would be it deciding what somebody meant.

    `prompted_by` falls back to the note itself. A note typed into Settings has
    no sentence that prompted it — it *is* the sentence — and recording an empty
    provenance would be worse than recording the obvious one.
    """
    written = learned.remember(
        commands._heading_of(note), note, prompted_by or note,
    )
    _audit(request, note, "written", f"filed under {written.heading!r}", verb="learn")
    return {
        "learned": written.as_dict(),
        "file": {"name": learned.LEARNED, "detail": f"filed under “{written.heading}”"},
    }


def _peer(
    request: Request,
    credential: str = "benchmark",
    capability: str = JOBS_CAPABILITY,
    purpose: str = "take a benchmark",
    label: tuple[str, str] = ("benchmark", "Benchmark queue"),
) -> tuple[RegistryEntry, Any]:
    """SIRVIS, negotiated and credentialled, or a refusal saying which is missing.

    Shared by every operation because they fail the same two ways, and a second
    copy of this is how one of them ends up skipping the capability check. The
    capability, the purpose and the label are the operation's own, so a download
    negotiates on `sirvis.downloads` and a refusal says what could not be done.
    """
    settings = request.app.state.settings
    entry: RegistryEntry | None = request.app.state.registry.get("sirvis")
    verdict = negotiate(Operation(label[0], "sirvis", capability, label[1]), entry)
    if not may_attempt(verdict, entry) or entry is None:
        raise InvalidConfigurationError(
            verdict.reason or f"SIRVIS cannot {purpose} right now",
            availability=verdict.availability.value,
        )
    if credential == "admin" and not settings.sirvis_admin_credential:
        # The same switch in its off position, for the other family. Deleting a
        # measurement needs `admin` on SIRVIS and NERVIS holds that separately
        # from its benchmark token on purpose — so this install can queue work
        # and still be unable to erase the results of it.
        raise InvalidConfigurationError(
            f"NERVIS holds no admin credential for SIRVIS, so it cannot {purpose}. "
            "The launcher mints an `admin`-scoped token at start; if "
            "SIRVIS was started another way, mint one with "
            '`sirvis token --mint nervis-admin --scopes "admin"` and set '
            "NERVIS_SIRVIS_ADMIN_CREDENTIAL."
        )
    if credential != "admin" and not settings.sirvis_client_credential:
        # §12's switch, in its off position, said out loud. The launcher mints
        # this token; an install that starts SIRVIS some other way has not been
        # given one, and saying so beats a 401 the person has to interpret.
        raise InvalidConfigurationError(
            "NERVIS holds no benchmark credential for SIRVIS, so it cannot act "
            "on the queue. The launcher mints a `benchmark`-scoped token at "
            "start; if SIRVIS was started another way, mint one with "
            '`sirvis token --mint nervis --scopes "benchmark"` and set '
            "NERVIS_SIRVIS_CLIENT_CREDENTIAL."
        )
    return entry, settings


async def _submit_benchmark(request: Request, model: str) -> dict[str, Any]:
    """Queue one benchmark of one model, with NERVIS's own narrow credential."""
    entry, settings = _peer(request)
    specification = {
        **BENCHMARK_SPECIFICATION,
        "target": {**BENCHMARK_SPECIFICATION["target"], "model": model},
    }
    client: httpx.AsyncClient = request.app.state.probe_client
    try:
        answered = await client.post(
            entry.declaration.base_url + "/api/v1/benchmark-jobs",
            json={"specification": specification, "model": model},
            # **The caller's trace, not just the credential.** SIRVIS reads the
            # inbound `traceparent` into the job it queues, so the run joins the
            # trace that asked for it — and this call sent authorization alone,
            # which meant a benchmark started from NERVIS's own command surface
            # opened a trace of its own. The one place a person can ask for a
            # measurement and then go looking for it was the one place it could
            # not be found.
            headers=_forwarded(
                getattr(request.state, "request_id", ""),
                getattr(request.state, "trace_id", ""),
                settings.sirvis_client_credential,
            ),
            timeout=SUBMIT_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as failure:
        _audit(request, model, "unreachable", type(failure).__name__)
        raise InvalidConfigurationError(f"SIRVIS did not answer: {failure}") from failure

    payload = _body_of(answered)
    if answered.status_code >= 400:
        detail = str((payload.get("error") or {}).get("message") or answered.status_code)
        _audit(request, model, "refused", detail)
        raise InvalidConfigurationError(f"SIRVIS refused it: {detail}")
    job = payload.get("job") or {}
    _audit(request, model, "queued", str(job.get("job_id") or ""))
    return {"job": job}


async def _cancel_benchmark(request: Request, job_id: str) -> dict[str, Any]:
    """Ask SIRVIS to stop one job, by the id it issued.

    **A cancel is a request, not a kill.** §4.2 has a running job wind itself
    down — `cancel_requested` is set and the worker stops at its next boundary —
    so what comes back is the job's state, which may still be `running` for a
    moment. Reporting it as stopped would be inventing an outcome; the queue's
    own answer travels instead.
    """
    entry, settings = _peer(request)
    client: httpx.AsyncClient = request.app.state.probe_client
    try:
        answered = await client.post(
            entry.declaration.base_url
            + f"/api/v1/benchmark-jobs/{quote(job_id, safe='')}/cancel",
            json={},
            headers={"Authorization": f"Bearer {settings.sirvis_client_credential}"},
            timeout=SUBMIT_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as failure:
        _audit(request, job_id, "unreachable", type(failure).__name__, "cancel")
        raise InvalidConfigurationError(f"SIRVIS did not answer: {failure}") from failure

    payload = _body_of(answered)
    if answered.status_code >= 400:
        detail = str((payload.get("error") or {}).get("message") or answered.status_code)
        _audit(request, job_id, "refused", detail, "cancel")
        raise InvalidConfigurationError(f"SIRVIS refused it: {detail}")
    job = payload.get("job") or {}
    _audit(request, job_id, "cancelled", str(job.get("state") or ""), "cancel")
    return {"job": job}


async def _delete_result(request: Request, result_id: str, reason: str) -> dict[str, Any]:
    """Delete one stored benchmark result, with NERVIS's admin credential.

    **The credential never reaches the browser.** The Results screen has no
    token and cannot get one; it names a result and NERVIS makes the call, which
    is the same shape as every other operation here and the reason §12 wants
    them enumerated rather than proxied.

    The reason travels because SIRVIS records it on the tombstone. Nothing here
    reads or requires it — an operator who deletes without explaining has still
    deleted something, and a mandatory field would only ever be filled with a
    dot.
    """
    entry, settings = _peer(request, "admin", purpose="delete a result")
    client: httpx.AsyncClient = request.app.state.probe_client
    try:
        answered = await client.request(
            "DELETE",
            entry.declaration.base_url
            + f"/api/v1/benchmark-results/{quote(result_id, safe='')}",
            json={"reason": reason[:500]},
            headers={"Authorization": f"Bearer {settings.sirvis_admin_credential}"},
            timeout=SUBMIT_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as failure:
        _audit(request, result_id, "unreachable", type(failure).__name__, "delete")
        raise InvalidConfigurationError(f"SIRVIS did not answer: {failure}") from failure

    payload = _body_of(answered)
    if answered.status_code >= 400:
        detail = str((payload.get("error") or {}).get("message") or answered.status_code)
        _audit(request, result_id, "refused", detail, "delete")
        raise InvalidConfigurationError(f"SIRVIS refused it: {detail}")
    _audit(request, result_id, "deleted", str(payload.get("evidence_id") or ""), "delete")
    return {"deleted": payload}


async def _start_download(
    request: Request, repo_id: str, quantization: str, confirm: bool
) -> dict[str, Any]:
    """Hand one download to SIRVIS, which checks the disk and asks LM Studio (M11).

    **Admin, and button-only.** SIRVIS requires `admin` for a download, because it
    spends disk that nothing gives back, and NERVIS holds that credential apart
    from its benchmark token. No phrase reaches this operation: only the Discover
    screen's button names it, next to the size and the disk check.

    **A refusal keeps SIRVIS's details.** A disk warning is not a failure to report
    and forget — the screen needs the warnings to put in front of the person and
    ask again with `confirm` — so SIRVIS's code and details travel on unchanged.
    """
    entry, settings = _peer(
        request, "admin", DOWNLOADS_CAPABILITY, "start a download", ("download", "Model downloads")
    )
    target = f"{repo_id}@{quantization}" if quantization else repo_id
    client: httpx.AsyncClient = request.app.state.probe_client
    try:
        answered = await client.post(
            entry.declaration.base_url + "/api/v1/downloads",
            json={"repo_id": repo_id, "quantization": quantization, "confirm": confirm},
            headers=_forwarded(
                getattr(request.state, "request_id", ""),
                getattr(request.state, "trace_id", ""),
                settings.sirvis_admin_credential,
            ),
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as failure:
        _audit(request, target, "unreachable", type(failure).__name__, "download")
        raise InvalidConfigurationError(f"SIRVIS did not answer: {failure}") from failure

    payload = _body_of(answered)
    if answered.status_code >= 400:
        raw_error = payload.get("error")
        error: dict[str, Any] = raw_error if isinstance(raw_error, dict) else {}
        detail = str(error.get("message") or answered.status_code)
        _audit(request, target, "refused", detail, "download")
        raw_details = error.get("details")
        details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}
        raise InvalidConfigurationError(
            f"SIRVIS refused it: {detail}", sirvis_code=str(error.get("code") or ""), **details
        )
    download = payload.get("download") or {}
    _audit(
        request, target, str(download.get("status") or "queued"),
        str(download.get("download_id") or ""), "download",
    )
    return {"download": download}


def _body_of(answered: httpx.Response) -> dict[str, Any]:
    """SIRVIS's body, or an empty one — a refusal without JSON is still a refusal."""
    try:
        found = answered.json()
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


# The outcomes that mean the operation happened. Anything else — refused,
# unreachable, malformed — is worth a warning.
_SUCCEEDED = frozenset({"queued", "cancelled", "deleted", "already_present"})

# The id an attempt is audited under, where it is not in the benchmark family:
# `delete` acts on a result and `download` on a model, not on the queue.
# **A handover is Clarvis's, not a SIRVIS benchmark.** `hand over` fell through to
# `sirvis.benchmark.hand over` about service `sirvis` until 16 September 2026, so no
# reader looking for handovers could have found one.
_AUDITED_AS = {"delete": "sirvis.result.delete", "download": "sirvis.download.start",
               "hand over": HANDOVER_OPERATION}
_SUBJECT_OF = {HANDOVER_OPERATION: "clarvis"}


def _audit(
    request: Request, target: str, outcome: str, detail: str, verb: str = "submit",
    extra: dict[str, Any] | None = None,
) -> None:
    """Every attempt, published — §12 asks for audit and this is the whole of it.

    Failures too, and for the more important reason: a control surface that
    records only what worked cannot answer "did something try to do this", which
    is the question an audit trail exists for.
    """
    operation = _AUDITED_AS.get(verb, f"sirvis.benchmark.{verb}")
    request.app.state.hub.emit(
        "nervis.command.attempted",
        # An operation that did what it was asked is not a warning. Only
        # `queued` was treated as success, so a cancel that worked — and, once
        # this file grew a third operation, a delete that worked — published at
        # warning and landed in the error log the operator reads for problems.
        severity="info" if outcome in _SUCCEEDED else "warning",
        subject={"type": "service", "id": _SUBJECT_OF.get(operation, "sirvis")},
        data={
            "operation": operation,
            "target": target,
            "outcome": outcome,
            "detail": detail,
            **(extra or {}),
        },
        trace_id=str(getattr(request.state, "trace_id", "") or ""),
    )


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        found = await request.json()
    except ValueError as failure:
        raise InvalidConfigurationError("the body must be JSON") from failure
    if not isinstance(found, dict):
        raise InvalidConfigurationError("the body must be a JSON object")
    return found


async def _export_conversation(
    request: Request, named: str, conversation_id: str
) -> dict[str, Any]:
    """Write the whole conversation to a file, rather than its last reply.

    Same boundary, same store, same renderer — only the content differs, and it
    differs completely: every turn in order, under its speaker, nothing dropped.
    A transcript is the one document whose whole value is being complete.

    The text still comes from NERVIS's own store and never from the request. A
    body that carried its own content would make this an arbitrary file-write
    endpoint wearing a chat operation's name, which is as true of an export as
    it is of a save.
    """
    root = _workspace(request)
    stored = chat.messages(request.app.state.database, conversation_id)
    if not stored:
        raise InvalidConfigurationError("this conversation has nothing to export yet")

    title = next(
        (str(row.get("title") or "") for row in chat.conversations(request.app.state.database)
         if row["conversation_id"] == conversation_id),
        "",
    )
    when = datetime.now().astimezone()
    return await _write_into_workspace(
        request, root, named,
        transcript.as_markdown(title, stored, when),
        turns=len(stored),
        # Drawn as the chat window for a PDF, and left as text for anything
        # else: a `.md` export is a file somebody will grep, and bubbles are not
        # a thing grep has an opinion about.
        conversation=(
            title or "Conversation",
            f"Exported {when:%d %B %Y at %H:%M}",
            transcript.as_turns(stored),
        ),
    )


async def _write_document(request: Request, named: str, conversation_id: str) -> dict[str, Any]:
    """Save the last reply of a conversation to a file the person named.

    **The boundary is enforced here, once.** `_write_proposal` deliberately does
    not check whether the name sits inside the workspace: a proposal is a
    suggestion and this is the act, and putting the same rule in both places
    gives two copies that disagree eventually. The path comparison that governs
    reading governs writing, and for the stronger reason — a write leaves
    something behind.

    The content is the conversation's own last assistant message, read from
    NERVIS's store. Never text the caller supplied: a request body that carried
    its own content would make this an arbitrary file-write endpoint wearing a
    chat operation's name.
    """
    root = _workspace(request)
    written = [
        message for message in chat.messages(request.app.state.database, conversation_id)
        if message.role == "clarvis" or message.role == "assistant"
    ]
    if not written:
        raise InvalidConfigurationError("this conversation has no reply to save yet")
    return await _write_into_workspace(
        request, root, named, written[-1].content,
        # The template is an *attachment*, so it is looked for in the import
        # room. `root` here is the export room — where the saved file goes —
        # and looking for the template there finds nothing, which reads as
        # "there was no template" rather than as looking in the wrong place.
        template_style=_template_style(_attachments_root(request), conversation_id),
    )


async def _annotate_document(
    request: Request, named: str, conversation_id: str, mode: str
) -> dict[str, Any]:
    """A copy of the newest attachment with this reply's comments placed in it.

    **The document is the person's own file, from the attachments directory;
    the comments are the reply, from NERVIS's store.** Neither comes from the
    request body, for the reason `_write_document` gives. And nothing was
    retyped: the model wrote comments anchored by quotes, and `annotate` did
    the placing — its module docstring says why that is the only shape that
    survives a forty-page document.

    `mode` is which of the three copies: `notes` and `margin` keep a PDF's
    pages and so produce a PDF whatever the target was called; `inline`
    re-renders the extracted text with the comments under their passages,
    which is text and so may be written as either.
    """
    root = _workspace(request)
    place = documents.attachment_dir(Path(_attachments_root(request)), conversation_id)
    found = documents.list_files(place) if place is not None else []
    if place is None or not found:
        raise InvalidConfigurationError("nothing is attached to this conversation to annotate")
    written = [
        message for message in chat.messages(request.app.state.database, conversation_id)
        if message.role == "clarvis" or message.role == "assistant"
    ]
    if not written:
        raise InvalidConfigurationError("this conversation has no reply to place as comments yet")
    # **The newest reply that actually carries anchored comments**, not the
    # newest reply. "Here are my findings" with quotes, then "put them in the
    # document", then "press Annotate" — the third reply is the newest and
    # places nothing; the first is what the person meant. Only when no reply
    # has a quote at all does the newest one stand, so what it says still
    # lands at the end rather than being dropped.
    anchored = [message for message in written if annotate.has_anchors(message.content)]
    comments = annotate.parse_comments((anchored or written)[-1].content)
    original = place / found[0].name
    is_pdf = original.suffix.lower() == ".pdf"
    if is_pdf and mode != "inline":
        if not named.lower().endswith(".pdf"):
            raise InvalidConfigurationError(
                f"an annotated copy of {found[0].name} keeps its pages, so it is a PDF —"
                f" name it with .pdf"
            )
        copied = annotate.annotate_pdf(
            original.read_bytes(), comments, style.extract_style(original), mode
        )
        return await _write_into_workspace(request, root, named, "", rendered=copied)
    text = (
        annotate.extracted_text(original.read_bytes()) if is_pdf
        else documents.read_document(place, found[0].name).text
    )
    return await _write_into_workspace(
        request, root, named, annotate.annotate_text(text, comments),
        template_style=style.extract_style(original) if is_pdf else None,
    )


def _attachments_root(request: Request) -> str:
    """The room attachments arrive in, which is not the room documents go to.

    Two rooms and two verbs: a file somebody handed over is read from `import`,
    and a file chat produces is written to `export`. Every lookup here that
    ends in `attachment_dir` wants the first one.
    """
    place = workspace.imported(request.app.state.settings)
    return str(place) if place is not None else ""


def _template_style(root: str, conversation_id: str) -> style.StyleProfile | None:
    """This conversation's newest attachment's own look, if it is a PDF.

    Mirrors `chat_titles._attachment_title`'s lookup exactly (`attachment_dir`
    then `list_files`, newest first) — the newest attachment is what "the
    template" means, the same way it is what "annotated" already means there.
    A newer, unrelated file ahead of an older PDF means there is no template,
    not that NERVIS should reach past it for one.
    """
    place = documents.attachment_dir(Path(root), conversation_id) if root else None
    if place is None:
        return None
    found = documents.list_files(place)
    if not found or Path(found[0].name).suffix.lower() != ".pdf":
        return None
    return style.extract_style(place / found[0].name)


def _workspace(request: Request) -> str:
    """Where chat may write, or a refusal naming the setting that turns it on.

    The export room: a file written here is one NERVIS produced — a document it
    was asked to save, a conversation rendered to PDF — which is a different
    thing from a file somebody handed it, and the two used to land in one heap.
    """
    place = workspace.exported(request.app.state.settings)
    root = str(place) if place is not None else ""
    if not root:
        raise InvalidConfigurationError(
            "NERVIS has no workspace configured, so it cannot write a file. "
            "Set NERVIS_WORKSPACE_PATH to the directory chat may read and write."
        )
    return root


async def _write_into_workspace(
    request: Request, root: str, named: str, text: str, turns: int = 0,
    conversation: tuple[str, str, list[Any]] | None = None,
    template_style: style.StyleProfile | None = None,
    rendered: pdf.Rendered | None = None,
) -> dict[str, Any]:
    """One text, one filename, one boundary.

    **Shared by both writers deliberately.** Saving a reply and exporting a
    conversation differ only in what they assemble; the path comparison, the
    renderer, the audit line and the shape of the answer are the same act. Two
    copies of a boundary disagree eventually, and this is the boundary.

    **`template_style` reaches only the plain-reply path.** `_export_conversation`
    never passes one, so `pdf.render_conversation` — a picture of the chat
    window, not a document with a look of its own to borrow — is untouched by
    this parameter existing at all, structurally rather than by a condition
    either caller has to remember to check.

    **The visual glance reaches the same path, and for the same reason.**
    `render_conversation` lays out a fixed chat bubble from bounded, already
    heavily tested drawing code; `pdf.render` lays out whatever markdown a
    model wrote, of any length and shape, which is where a stray code block
    or a deep list actually can misrender. Checking the export path too would
    mean double-checking a renderer that cannot produce the defects this looks
    for, on every export, for nothing.
    """
    try:
        resolved = resolve_in_workspace(Path(root), named)
    except OutsideWorkspaceError as refusal:
        raise InvalidConfigurationError(str(refusal)) from refusal

    if resolved.path.suffix.lower() == ".pdf":
        # `rendered` arrives already built when the bytes are not a rendering
        # of `text` at all — an annotated copy is the person's own PDF with
        # pages added, and there is no markdown it could be re-rendered from.
        # **Whether NERVIS laid this page out is what decides the glance
        # below**, so it is recorded before `rendered` is filled in.
        ours = rendered is None
        if rendered is None:
            rendered = (
                pdf.render_conversation(*conversation) if conversation
                else pdf.render(resolved.shown, text, template_style)
            )
        payload, detail = rendered.data, f"{rendered.pages} page(s)"
        if rendered.unsupported:
            # Said rather than silently substituted: a `?` where a character
            # should be is a defect the reader cannot see and the writer can.
            detail += f"; {len(rendered.unsupported)} character(s) Latin-1 could not carry"
        if ours and not conversation:
            # **Only pages NERVIS laid out.** An annotated copy's first page is
            # the person's own cover, and the glance duly reported the heading
            # on it as "crowded against the text below" — a small local model
            # reviewing somebody's design, on a page NERVIS did not draw and
            # would not change. Checking a copy is not checking work.
            defect = await _visual_defect(request, payload)
            if defect:
                detail += f"; looked over, and: {defect}"
    else:
        payload, detail = text.encode("utf-8"), f"{len(text):,} characters"
    if turns:
        detail += f"; {turns} turn(s)"

    resolved.path.parent.mkdir(parents=True, exist_ok=True)
    resolved.path.write_bytes(payload)
    _audit(request, resolved.shown, "written", detail)
    return {"file": {
        "name": resolved.shown, "bytes": len(payload), "detail": detail,
        "download": _download_address(request, resolved.path),
    }}


def _download_address(request: Request, written: Path) -> str | None:
    """Where `/api/v1/documents/` finds this file, or None when it can't serve it.

    **`name` is not that address.** `name` is counted from the export room, which is
    where the file was written, and the documents route counts from the top of the
    workspace. Since the four rooms landed (9 September 2026) every download link in
    a chat bubble asked for `chat.pdf` while the file sat at `export/chat.pdf`, and
    every one answered 404. So the address is worked out here from where the file
    really is, and is None rather than a dead link when the route can't hand it
    back: a suffix it doesn't serve, or an export room configured outside the
    workspace.
    """
    top = str(getattr(request.app.state.settings, "workspace_path", "") or "").strip()
    if not top or written.suffix.lower() not in SERVED:
        return None
    try:
        return written.resolve().relative_to(Path(top).expanduser().resolve()).as_posix()
    except ValueError:
        return None


async def _visual_defect(request: Request, pdf_bytes: bytes) -> str | None:
    """What a vision-capable model sees wrong on the first rendered page —
    or `None`, both when nothing is wrong and when there was no way to ask.

    **Mirrors `chat_titles._generate_title` exactly, on purpose.** Same gate
    (no credential, no registered RAVIS, RAVIS marked unusable — skip), same
    `background` marker (§9.6.1: an aside the person did not directly ask a
    hosted model to run must not spend a non-free provider's money), same
    broad `except` on the call itself. A save's success never depends on
    this: every failure mode here — RAVIS unreachable, no model this machine
    can see, a malformed reply — is silence, not a refusal, because telling
    someone their file did not save when it did would be worse than the
    missed glance.

    The chosen background pool is asked first, then the named model, then this
    machine's own — each only if the one before came back with nothing.
    """
    settings = request.app.state.settings
    entry: RegistryEntry | None = request.app.state.registry.get("ravis")
    if not settings.ravis_client_credential or entry is None or not entry.is_usable:
        return None
    snapshot = visual_check.first_page(pdf_bytes)
    if snapshot is None:
        return None
    config = background.settings(request.app.state.database)
    for model in background.route(config, VISUAL_CHECK_MODEL):
        answered, defect = await _looked_over(request, entry, snapshot, model,
                                              background.marker(config))
        if answered:
            return defect
    return None


async def _looked_over(
    request: Request, entry: RegistryEntry, snapshot: visual_check.Snapshot, model: str,
    said: dict[str, object],
) -> tuple[bool, str | None]:
    """One glance, by one model: whether it answered at all, and what it saw.

    **The two are separate returns because conflating them costs a call on
    every save.** "Nothing is wrong" and "this model could not be reached"
    both have no defect to report, but only the second is a reason to ask
    somebody else — and a clean page is the ordinary case, so folding them
    together would send every ordinary save on to the fallback pool, which is
    the hosted model this deliberately avoids reaching for.
    """
    client: httpx.AsyncClient = request.app.state.probe_client
    payload = {
        "model": model,
        "max_tokens": VISUAL_CHECK_MAX_TOKENS,
        "metadata": said,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": visual_check.PROMPT},
                {"type": "image_url", "image_url": {"url": snapshot.data_url}},
            ],
        }],
    }
    try:
        response = await client.post(
            entry.declaration.base_url + "/v1/chat/completions",
            json=payload,
            headers=_forwarded(
                getattr(request.state, "request_id", ""),
                getattr(request.state, "trace_id", ""),
                request.app.state.settings.ravis_client_credential,
            ),
            timeout=VISUAL_CHECK_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            return False, None
        body = response.json()
    except (httpx.HTTPError, ValueError):
        return False, None
    choices = body.get("choices") or []
    if not choices:
        return False, None
    message = choices[0].get("message") or {}
    return True, visual_check.verdict_of(str(message.get("content") or ""))
