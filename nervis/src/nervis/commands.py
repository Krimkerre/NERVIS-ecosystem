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


OPERATIONS: tuple[Operation, ...] = (
    Operation(
        id="sirvis.benchmark.submit",
        service="sirvis",
        summary="queue a benchmark of {target} on SIRVIS",
    ),
)

BY_ID = {operation.id: operation for operation in OPERATIONS}

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

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "service": self.service,
            "target": self.target,
            "summary": self.summary,
            "ready": self.ready,
            "detail": self.detail,
            "candidates": list(self.candidates),
        }


def propose(question: str, models: Sequence[Mapping[str, Any]]) -> Proposal | None:
    """What the person's words ask for, if it is something NERVIS offers.

    `None` for anything else, which is almost everything — this is a control
    surface with one operation on it, not an intent classifier.
    """
    if not question:
        return None
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
        return (
            f"The person asked for something NERVIS can offer: {proposal.summary}. "
            "NERVIS has put a button under your reply for them to confirm it. "
            "Nothing has run and nothing will run unless they press it — say it is "
            "ready and waiting for them, and never say it has started, been queued "
            "or finished."
        )
    return (
        f"The person asked for something NERVIS can offer — {proposal.summary} — but "
        f"it cannot be prepared: {proposal.detail}. No button has been offered. "
        "Tell them why, and name the alternatives if any are listed in the reading."
    )
