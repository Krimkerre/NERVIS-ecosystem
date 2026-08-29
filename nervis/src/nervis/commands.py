"""Turning what the person asked for into one of a closed set of operations.

**"Have SIRVIS bench qwen3-4b" is an instruction, and NERVIS should be able to
take it.** The whole point of a control plane you can talk to is that talking to
it does something. But §12 is equally clear about how: *"Supervision offers an
enumerated set of operations against registered service instances — there is no
free-form command, script or process-selection path"*, and an operation not in
the set does not exist rather than failing at validation.

**The model is not what decides.** §11.5 says nothing a model returns from
reading NERVIS's evidence may become an action, and that rule is not weaker
because the evidence arrived as a chat turn. So the parsing below reads the
*person's own words*, deterministically, before the model has seen anything —
the model is told an offer exists so it can mention it, and cannot make, change
or take one. Two properties fall out of that:

* a prompt injected through a service's error message cannot propose anything,
  because proposals are not made from model output at all; and
* what runs is what the person confirmed, spelled out in front of them, rather
  than whatever a sentence was interpreted to mean.

**Nothing here executes anything.** This module returns a `Proposal` — a value
describing what could be done. The confirmation and the call live at the edge
that already holds the credential for it, which for benchmarks is the dashboard:
it holds a `benchmark`-scoped SIRVIS token the operator pasted, and NERVIS
deliberately holds none. A control plane that cannot be made to act by talking
to it is a control plane whose authority is the operator's, which is the whole
of §12's intent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class Operation:
    """One thing NERVIS knows how to offer, and the only kind of thing it can.

    Written out as data rather than discovered from what happens to be wired,
    for the reason §4.1 gives about capabilities: a set derived from the code
    advertises whatever got built, which is the opposite test.
    """

    id: str
    service: str
    summary: str
    """How the offer reads to the person, with `{target}` filled in."""
    action: str = "Run"
    """What the button says. The verb lives here rather than in `summary`,
    because the summary is handed to a model and a verb in it comes back
    conjugated into a claim that the thing has already happened."""


OPERATIONS: tuple[Operation, ...] = (
    Operation(
        id="sirvis.benchmark.cancel",
        service="sirvis",
        summary="the benchmark {target}",
        action="Cancel",
    ),
    Operation(
        id="sirvis.benchmark.submit",
        service="sirvis",
        # **No verb in the tense that becomes a lie.** This sentence is handed
        # to the model, and "queue a benchmark of X" came back as "a benchmark
        # of X has been queued" — the model was not disobeying an instruction,
        # it was conjugating the one verb in the text it was given. Naming the
        # thing rather than the act leaves nothing to conjugate.
        summary="a benchmark of {target} on SIRVIS",
    ),
)

BY_ID = {operation.id: operation for operation in OPERATIONS}

# Stopping one. Narrower than the submit pattern on purpose: "cancel" and "stop"
# are ordinary words, so they only count when a benchmark or a job id is named
# in the same breath. "stop" on its own is what somebody types to interrupt a
# *reply*, and that button is already on the screen.
CANCEL = re.compile(
    r"\b(?:cancel|stop|abort|kill)\b[^.?!]*?\b(?:bench|benchmark|job|run|"
    r"(?P<job>bj_[0-9a-f]{6,}))",
    re.IGNORECASE,
)

# What a benchmark request looks like in a sentence. Deliberately narrow: the
# cost of missing one phrasing is that nothing is offered and the person says it
# again; the cost of matching too eagerly is an offer to occupy the machine for
# ten minutes because somebody used the word "benchmark" in passing.
BENCHMARK = re.compile(
    r"\b(?:bench|benchmark)(?:\s+(?:the|a|an))?(?:\s+model)?\s+"
    r"[\"'“‘]?(?P<target>[A-Za-z0-9][\w\-./:]*)",
    re.IGNORECASE,
)

# Words that follow the verb but are not a model. "benchmark it", "benchmark
# that one" — a pronoun is a reference this module cannot resolve, and guessing
# which model it meant is exactly the kind of interpretation that must not sit
# between a sentence and a machine doing work.
NOT_A_MODEL = frozenset({
    "it", "that", "this", "these", "those", "them", "one", "something",
    "everything", "again", "please", "now", "the", "model", "models",
})

MAX_CANDIDATES = 6

# §4.2's job states that are still stoppable. A terminal job cannot be
# cancelled, and offering to cancel one is offering something that will be
# refused — the same reason an unknown model never becomes an offer.
STOPPABLE = ("queued", "running")


@dataclass(frozen=True)
class Proposal:
    """An offer, and everything needed to show it before anything happens.

    `ready` is false for an offer that cannot be carried out as asked — an
    unknown model, or several that match. It is still returned, because "I could
    not find that model, did you mean one of these" is an answer and silence is
    not.
    """

    operation: str
    service: str
    target: str
    summary: str
    ready: bool
    detail: str = ""
    candidates: tuple[str, ...] = ()
    action: str = "Run"

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "service": self.service,
            "target": self.target,
            "summary": self.summary,
            "ready": self.ready,
            "detail": self.detail,
            "candidates": list(self.candidates),
            "action": self.action,
        }


def propose(
    question: str,
    models: Sequence[Mapping[str, Any]],
    jobs: Sequence[Mapping[str, Any]] = (),
) -> Proposal | None:
    """What the person's words ask for, if it is something NERVIS offers.

    `None` for anything else, which is almost everything — this is a control
    surface with two operations on it, not an intent classifier.

    **Cancel is tried first.** "cancel the benchmark of qwen3-4b" contains a
    perfectly good submit request inside it, and reading it as one would answer
    "stop that" by starting another.
    """
    if not question:
        return None
    stopping = CANCEL.search(question)
    if stopping:
        return _cancel_proposal(stopping.group("job") or "", jobs)
    found = BENCHMARK.search(question)
    if not found:
        return None
    asked = found.group("target").strip("\"'“”‘’.,!?")
    if not asked or asked.lower() in NOT_A_MODEL:
        return None
    return _benchmark_proposal(asked, models)


def _benchmark_proposal(asked: str, models: Sequence[Mapping[str, Any]]) -> Proposal:
    """Resolve the name against what is actually on this machine.

    **Local models only.** A benchmark loads the model and measures it here;
    offering to benchmark something that runs in somebody else's data centre is
    offering a measurement of a network. SIRVIS would refuse it, and refusing it
    here means the offer is never made rather than made and then broken.
    """
    operation = BY_ID["sirvis.benchmark.submit"]
    local = [_name(model) for model in models if model.get("local") is True]
    local = [name for name in local if name]
    exact = [name for name in local if name.lower() == asked.lower()]
    near = exact or [name for name in local if asked.lower() in name.lower()]

    if len(near) == 1:
        target = near[0]
        return Proposal(
            operation=operation.id, service=operation.service, target=target,
            summary=operation.summary.format(target=target), ready=True,
        )
    if not near:
        return Proposal(
            operation=operation.id, service=operation.service, target=asked,
            summary=operation.summary.format(target=asked), ready=False,
            detail=(
                f"no model on this machine matches {asked!r}"
                if local else
                "the model catalogue could not be read, so nothing can be matched"
            ),
            candidates=tuple(sorted(local)[:MAX_CANDIDATES]),
        )
    return Proposal(
        operation=operation.id, service=operation.service, target=asked,
        summary=operation.summary.format(target=asked), ready=False,
        detail=f"{len(near)} models match {asked!r} — say which",
        candidates=tuple(sorted(near)[:MAX_CANDIDATES]),
    )


def _cancel_proposal(
    job_id: str, jobs: Sequence[Mapping[str, Any]]
) -> Proposal:
    """Which benchmark to stop, named rather than assumed.

    **The person almost never says the job id**, and that is the interesting
    case: "cancel the benchmark" means the one that is running, and NERVIS knows
    which that is. When it is not one — nothing running, or two of them — the
    offer is not ready and says which, because guessing here stops work somebody
    is waiting on.
    """
    operation = BY_ID["sirvis.benchmark.cancel"]
    live = [job for job in jobs if str(job.get("state") or "") in STOPPABLE]
    if job_id:
        named = [job for job in live if str(job.get("job_id") or "") == job_id]
        if not named:
            return Proposal(
                operation=operation.id, service=operation.service, target=job_id,
                summary=operation.summary.format(target=job_id), ready=False,
                action=operation.action,
                detail=f"no queued or running job with the id {job_id!r}",
                candidates=tuple(_label(job) for job in live[:MAX_CANDIDATES]),
            )
        live = named
    if not live:
        return Proposal(
            operation=operation.id, service=operation.service, target="", ready=False,
            summary="a benchmark", action=operation.action,
            detail="no benchmark is queued or running, so there is nothing to stop",
        )
    if len(live) > 1:
        return Proposal(
            operation=operation.id, service=operation.service, target="", ready=False,
            summary="a benchmark", action=operation.action,
            detail=f"{len(live)} benchmarks are queued or running — say which",
            candidates=tuple(_label(job) for job in live[:MAX_CANDIDATES]),
        )
    target = str(live[0].get("job_id") or "")
    return Proposal(
        operation=operation.id, service=operation.service, target=target,
        summary=operation.summary.format(target=_label(live[0])), ready=True,
        action=operation.action,
    )


def _label(job: Mapping[str, Any]) -> str:
    """A job as a person would recognise it: its id, its model and its state."""
    parts = [str(job.get("job_id") or "?")]
    model = str(job.get("model") or "")
    if model:
        parts.append(model)
    parts.append(str(job.get("state") or "?"))
    return " · ".join(parts)


def _name(model: Mapping[str, Any]) -> str:
    """A model's id under either spelling RAVIS uses."""
    return str(model.get("model_id") or model.get("id") or "")


def told(proposal: Proposal | None) -> str:
    """What the model is told about the offer — and what it must not claim.

    Deliberately a statement of fact rather than an instruction to act on: the
    model's job is to mention that the button is there. A model that says "done,
    I've started it" about work nobody confirmed is the failure this whole split
    exists to prevent, so it is named here rather than left to inference.
    """
    if proposal is None:
        return ""
    if proposal.ready:
        # **The prohibition comes first, and names the words.** Told to mention
        # a button and not to claim the work had started, an 8B build answered
        # "a benchmark of qwen/qwen3-4b-2507 has been queued and is waiting for
        # your confirmation" — both halves in one sentence, the false half
        # first. A rule stated after the thing it restricts is a rule a small
        # model reads as an afterthought, and "never say X" leaves it composing
        # a synonym.
        return (
            f"The person asked about {proposal.summary}. There is an unpressed "
            f"{proposal.action} button under your reply. It is the only thing that "
            "can do this, and it has not been pressed, so nothing has changed yet — "
            "it is an offer on screen and nothing more. Tell them the button is "
            "there and that it is theirs to press. Describe it in the future tense "
            "only."
        )
    return (
        f"The person asked for something NERVIS can offer — {proposal.summary} — but "
        f"it cannot be prepared: {proposal.detail}. No button has been offered. "
        "Tell them why, and name the alternatives if any are listed in the reading."
    )
