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

from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request

from nervis import chat, commands, learned, pdf, transcript
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
    if operation == "nervis.document.write":
        return _write_document(request, target, str(body.get("conversation_id") or ""))
    if operation == "nervis.conversation.export":
        return _export_conversation(request, target, str(body.get("conversation_id") or ""))
    if operation == "nervis.knowledge.learn":
        return _learn(request, target, str(body.get("prompted_by") or ""))
    if operation == "sirvis.benchmark.cancel":
        return await _cancel_benchmark(request, target)
    if operation == "sirvis.result.delete":
        return await _delete_result(request, target, str(body.get("reason") or ""))
    return await _submit_benchmark(request, target)


def _learn(request: Request, note: str, prompted_by: str) -> dict[str, Any]:
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


def _peer(request: Request, credential: str = "benchmark") -> tuple[RegistryEntry, Any]:
    """SIRVIS, negotiated and credentialled, or a refusal saying which is missing.

    Shared by both operations because both fail the same two ways, and a second
    copy of this is how one of them ends up skipping the capability check.
    """
    settings = request.app.state.settings
    entry: RegistryEntry | None = request.app.state.registry.get("sirvis")
    verdict = negotiate(
        Operation("benchmark", "sirvis", JOBS_CAPABILITY, "Benchmark queue"), entry
    )
    if not may_attempt(verdict, entry) or entry is None:
        raise InvalidConfigurationError(
            verdict.reason or "SIRVIS cannot take a benchmark right now",
            availability=verdict.availability.value,
        )
    if credential == "admin" and not settings.sirvis_admin_credential:
        # The same switch in its off position, for the other family. Deleting a
        # measurement needs `admin` on SIRVIS and NERVIS holds that separately
        # from its benchmark token on purpose — so this install can queue work
        # and still be unable to erase the results of it.
        raise InvalidConfigurationError(
            "NERVIS holds no admin credential for SIRVIS, so it cannot delete a "
            "result. The launcher mints an `admin`-scoped token at start; if "
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
            headers={"Authorization": f"Bearer {settings.sirvis_client_credential}"},
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
    entry, settings = _peer(request, "admin")
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


def _body_of(answered: httpx.Response) -> dict[str, Any]:
    """SIRVIS's body, or an empty one — a refusal without JSON is still a refusal."""
    try:
        found = answered.json()
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


# The outcomes that mean the operation happened. Anything else — refused,
# unreachable, malformed — is worth a warning.
_SUCCEEDED = frozenset({"queued", "cancelled", "deleted"})


def _audit(
    request: Request, target: str, outcome: str, detail: str, verb: str = "submit"
) -> None:
    """Every attempt, published — §12 asks for audit and this is the whole of it.

    Failures too, and for the more important reason: a control surface that
    records only what worked cannot answer "did something try to do this", which
    is the question an audit trail exists for.
    """
    request.app.state.hub.emit(
        "nervis.command.attempted",
        # An operation that did what it was asked is not a warning. Only
        # `queued` was treated as success, so a cancel that worked — and, once
        # this file grew a third operation, a delete that worked — published at
        # warning and landed in the error log the operator reads for problems.
        severity="info" if outcome in _SUCCEEDED else "warning",
        subject={"type": "service", "id": "sirvis"},
        data={
            # `delete` is not in the benchmark family — it acts on a result
            # rather than on the queue — so the id it audits under says so.
            "operation": "sirvis.result.delete" if verb == "delete"
            else f"sirvis.benchmark.{verb}",
            "target": target,
            "outcome": outcome,
            "detail": detail,
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


def _export_conversation(request: Request, named: str, conversation_id: str) -> dict[str, Any]:
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
    return _write_into_workspace(
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


def _write_document(request: Request, named: str, conversation_id: str) -> dict[str, Any]:
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
    return _write_into_workspace(request, root, named, written[-1].content)


def _workspace(request: Request) -> str:
    """Where chat may write, or a refusal naming the setting that turns it on."""
    root = str(getattr(request.app.state.settings, "workspace_path", "") or "").strip()
    if not root:
        raise InvalidConfigurationError(
            "NERVIS has no workspace configured, so it cannot write a file. "
            "Set NERVIS_WORKSPACE_PATH to the directory chat may read and write."
        )
    return root


def _write_into_workspace(
    request: Request, root: str, named: str, text: str, turns: int = 0,
    conversation: tuple[str, str, list[Any]] | None = None,
) -> dict[str, Any]:
    """One text, one filename, one boundary.

    **Shared by both writers deliberately.** Saving a reply and exporting a
    conversation differ only in what they assemble; the path comparison, the
    renderer, the audit line and the shape of the answer are the same act. Two
    copies of a boundary disagree eventually, and this is the boundary.
    """
    try:
        resolved = resolve_in_workspace(Path(root), named)
    except OutsideWorkspaceError as refusal:
        raise InvalidConfigurationError(str(refusal)) from refusal

    if resolved.path.suffix.lower() == ".pdf":
        rendered = (
            pdf.render_conversation(*conversation) if conversation
            else pdf.render(resolved.shown, text)
        )
        payload, detail = rendered.data, f"{rendered.pages} page(s)"
        if rendered.unsupported:
            # Said rather than silently substituted: a `?` where a character
            # should be is a defect the reader cannot see and the writer can.
            detail += f"; {len(rendered.unsupported)} character(s) Latin-1 could not carry"
    else:
        payload, detail = text.encode("utf-8"), f"{len(text):,} characters"
    if turns:
        detail += f"; {turns} turn(s)"

    resolved.path.parent.mkdir(parents=True, exist_ok=True)
    resolved.path.write_bytes(payload)
    _audit(request, resolved.shown, "written", detail)
    return {"file": {"name": resolved.shown, "bytes": len(payload), "detail": detail}}
