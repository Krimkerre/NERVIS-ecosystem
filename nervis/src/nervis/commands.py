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
from collections.abc import Callable
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
        id="nervis.chat.profile",
        service="nervis",
        summary="this conversation to {target}",
        action="Switch",
    ),
    Operation(
        id="sirvis.benchmark.cancel",
        service="sirvis",
        summary="the benchmark {target}",
        action="Cancel",
    ),
    # **In the set, and deliberately without a phrase that reaches it.** Every
    # other operation here is matched from something a person typed; this one is
    # only ever named by a button on the Results screen, next to the record it
    # would delete. A phrase matcher would mean "delete the gemma results" could
    # be *proposed* from a sentence, and a measurement is not a thing to offer
    # to destroy on the strength of a parse.
    Operation(
        id="sirvis.result.delete",
        service="sirvis",
        summary="the benchmark result {target}",
        action="Delete",
    ),
    # **Writing is an effect, so it is an operation rather than a tool.**
    # Reading a document is a source — NERVIS opens it before the model sees
    # anything and the model chooses nothing. Writing one changes the machine,
    # so it takes the path every other change takes: a proposal, a button
    # somebody presses, an attempt published to the hub. §11.5 is the same rule
    # read from the other end — nothing a model returns may become an action,
    # and a filename it suggested is exactly that until a person confirms it.
    Operation(
        id="nervis.document.write",
        service="nervis",
        summary="the last reply to {target}",
        action="Save",
    ),
    # **The attached document with this reply's comments placed in it**, and its
    # own operation for the same reason export is: a different act. Save keeps
    # what the model wrote; this keeps what the *person* attached — every page
    # of it, untouched — and puts the model's comments beside the passages they
    # quote. The model is asked for comments only, never to retype the document
    # (`annotate.py` says why), so what it writes is content placed by NERVIS
    # against the person's own file, exactly as a save's text is.
    #
    # **Three ways, each its own operation**, because each is its own act with
    # its own button — and because the choice belongs to the person, not to a
    # flag a model might set. Sticky notes are the soft default: offered when no
    # style was named, with the other two a word away. A PDF cannot be
    # reflowed, so "in the text" is never literally possible — the margin copy
    # is the closest a fixed page allows, and the inline copy is a re-rendering
    # that gives the design up.
    Operation(
        id="nervis.document.annotate.notes",
        service="nervis",
        summary="a copy of the attachment with this reply's comments as sticky notes, as {target}",
        action="Add notes",
    ),
    Operation(
        id="nervis.document.annotate.margin",
        service="nervis",
        summary="a reviewer's copy of the attachment, comments beside the text, as {target}",
        action="Annotate",
    ),
    Operation(
        id="nervis.document.annotate.inline",
        service="nervis",
        summary="the attachment re-rendered, this reply's comments under each passage, as {target}",
        action="Annotate inline",
    ),
    # The whole conversation rather than the last reply, and its own operation
    # because it is its own act: one saves an answer somebody liked, the other
    # keeps a record of an exchange. Sharing an id would make the confirm button
    # ambiguous about which is about to happen.
    Operation(
        id="nervis.conversation.export",
        service="nervis",
        summary="this conversation to {target}",
        action="Export",
    ),
    # **Somebody's own sentence, kept where the shipped notes are** (M23).
    # NERVIS does not decide what is worth remembering and nothing a model
    # returns is written: the text stored is what the person typed, and it is
    # stored because they pressed a button saying so.
    Operation(
        id="nervis.knowledge.learn",
        service="nervis",
        summary="“{target}” to what you have told NERVIS",
        action="Remember",
    ),
    # **A file, not a command** (M27). NERVIS writes a task brief into the
    # workspace it already writes to; Clarvis reads it with the flow that reads
    # any plan, a person approves it in the editor, and every tool call passes
    # the gates it always did. `CLARVIS.md` §6.7 forbids NERVIS invoking a tool
    # or resolving a gate, and nothing here does either — the test that keeps
    # the two apart is that with the Bridge stopped this still works, because
    # the interface is a document.
    Operation(
        id="nervis.clarvis.task",
        service="nervis",
        summary="“{target}” to Clarvis, as a task in the workspace",
        action="Hand over",
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

# Switching this conversation's pool. Two shapes, because both are how people
# ask: naming the pool ("use ravis/coding", "switch to the local pool") and
# naming what they want from it ("route this through reasoning"). The pool has
# to be named either way — NERVIS does not interpret "make it better".
# The verb says a route change is wanted. *Which* pool is asked of the pool
# list, for the same reason the benchmark target is asked of the inventory:
# RAVIS publishes fifteen of them and their names are the vocabulary.
#
# **The old pattern required a pool-shaped token** — `ravis/x`, `x pool` or
# `pool x` — and its own comment claimed it handled *"route this through
# reasoning"*, which it did not: that sentence names a pool with no marker
# around it, and nothing matched. A comment describing behaviour the code does
# not have is worse than no comment, so the code now has it.
SWITCH = re.compile(r"\b(?:use|switch|change|route|move|set)\b", re.IGNORECASE)

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
# **The verb only says what kind of request this is. The model name is found by
# looking for models.**
#
# This pattern used to capture the target too — "the word after the verb" — and
# that is where two reported bugs lived: a preposition became the model in
# *"queue a benchmark for qwen3-4b"*, and no word order but one ever worked.
# Every guard bolted on afterwards was compensating for reading English instead
# of reading the inventory.
#
# What replaced it is `_models_named`: the machine already knows exactly which
# models exist, and their names are distinctive strings. Matching against that
# closed set is both stricter and looser in the right directions — it cannot
# invent a model called `for`, and it does not care where in the sentence the
# name appears.
BENCHMARK = re.compile(r"\b(?:bench|benchmark|benchmarks|benchmarking)\b", re.IGNORECASE)

# Saving the last reply to a file. A name written here is used verbatim;
# "save that" names none, and `_saving_proposal` falls back to a name derived
# from the conversation's own title and date — never one a model invented,
# which is the distinction §12 actually draws.
WRITE = re.compile(
    r"\b(?:save|write|export|put)\b[^.?!]{0,60}?"
    r"(?:\bas\b|\bto\b|\binto\b)?\s*"
    r"[\"'`]?(?P<file>[\w./\-]{1,120}\.(?:pdf|txt|md))[\"'`]?"
    # **"call it X" earns a longer reach than sixty characters.** Observed:
    # "save me a new file that has the original policy text with your changes
    # applied to it, call it revised-policy.pdf" — seventy-eight characters
    # between the verb and the name, so the offer above fell back to a derived
    # one and the person's own filename was dropped without a word.
    #
    # The cap exists to stop a filename being lifted out of an unrelated
    # clause, and "call it" removes exactly that ambiguity: it is a phrase
    # whose only job is to name the thing being made. The verb is still
    # required, so a sentence merely *mentioning* what something was called
    # proposes nothing.
    r"|\b(?:save|write|export|put|download)\b[^.?!]{0,200}?"
    r"\b(?:call|name)\s+(?:it|them|this|the\s+file)\s+"
    r"[\"'`]?(?P<called>[\w./\-]{1,120}\.(?:pdf|txt|md))[\"'`]?",
    re.IGNORECASE,
)

#: Whether the person asked for a PDF specifically, when they named no file.
#: `_as_text_default` reads it: "save this as a pdf" with no filename used to
#: derive a `.md` name and say nothing about the mismatch, so the reply
#: promised a PDF while the button would have written markdown.
WANTS_PDF = re.compile(r"\bpdfs?\b", re.IGNORECASE)

#: The same intent with no filename at all — "save it", "could you ... download
#: it" — where WRITE has nothing to capture. Enough to reach the fallback
#: default name; still no filename this pattern itself supplies.
SAVE_VERB = re.compile(r"\b(?:save|write|export|put|download)\b", re.IGNORECASE)

# Exporting the conversation itself.
#
# **A filename is optional here, unlike `WRITE`.** That rule exists because
# "save that" names no file and a target NERVIS invented is what §12 forbids —
# but "export this conversation" is not vague in the same way. It names its
# target exactly: *this conversation*, which NERVIS is holding and which carries
# its own stored title. The filename is derived from that title, not conjured to
# fill a gap, and the person still confirms it on the button.
EXPORT_CONVERSATION = re.compile(
    # A verb and a word for the conversation, in either order …
    r"\b(?:export|save|write|print|download)\b[^.?!]{0,50}?"
    r"\b(?:conversation|chat|transcript|discussion|thread)\b"
    r"|\b(?:conversation|chat|transcript|discussion|thread)\b[^.?!]{0,40}?"
    r"\b(?:export|save|write|print|download)\b"
    # … or the word **export** on its own. It means one thing here, and
    # requiring a second word missed "lets try the export again" — a follow-up
    # to an offer, which is exactly when somebody is least likely to repeat the
    # noun they used a moment ago.
    r"|\bexports?\b|\bexporting\b",
    re.IGNORECASE,
)

# The shortest run of characters allowed to name a model on its own.
#
# Three would let `r1` and `4b` match half the catalogue, and one-word English
# collides with model names below four: checked against 53 common words, only
# `small` (devstral-**small**) and `still` (di**still**) overlapped at all, and
# `still` is not a name *segment* — which is why matching is by segment rather
# than by raw substring.
MIN_NAME_FRAGMENT = 4

# **A question mark is the signal. The word list is the fallback.**
#
# This was the other way round and it cost two reported bugs. The list held
# `do`, `does`, `did`, `show` and `tell` — verbs, not question words — so
# *"do a benchmark on the deepseek model"* was read as interrogative and
# silently made no offer. Narrowing `do` to `do you`/`do we` then let
# *"did the benchmark for qwen3-4b finish?"* through as a request to run one.
# Both were the same mistake: inferring a question from a word that opens
# plenty of instructions.
#
# Punctuation says it outright. Everything below is only for the sentences
# people write without it — and those are genuine interrogatives that cannot
# open an imperative, which is why the verbs are gone rather than qualified.
QUESTION_MARK = re.compile(r"\?\s*$")

ASKING = re.compile(
    r"^\s*(?:so\s+)?(?:how|what|whats|what's|why|when|where|which|who|whose)\b",
    re.IGNORECASE,
)

# …unless it is a question that asks for the work to be done. "can you bench X"
# and "could you benchmark X" are requests wearing a question mark.
ASKING_FOR = re.compile(r"\b(?:can|could|would|will)\s+(?:you|we)\b", re.IGNORECASE)

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
    # **Minted when the offer is made, not when it is answered** (M22). An
    # answer has to have something to be filed against, and an offer nobody
    # answers simply never produces a row — which is the record M22 wants,
    # because a person who closed the tab did not decline.
    proposal_id: str = ""
    # What became of this exact offer before, attached by the API layer rather
    # than composed here. `propose` stays a pure function of the person's words:
    # history only ever decorates an offer, never builds one, which is what
    # makes clearing the record restore the unlearned proposal exactly.
    history: Mapping[str, Any] | None = None
    #: **Other operations this same offer could have been**, as (operation,
    #: button) pairs — the two annotated copies not chosen when no style was
    #: named. Shown as chips beside the button by NERVIS's own screen, because
    #: the model was asked twice, in two placements, to name them in its reply
    #: and did not either time. A choice the person is owed is stated by the
    #: system that owes it, not left to a sentence a model may drop.
    alternatives: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "history": dict(self.history) if self.history else None,
            "operation": self.operation,
            "service": self.service,
            "target": self.target,
            "summary": self.summary,
            "ready": self.ready,
            "detail": self.detail,
            "candidates": list(self.candidates),
            "action": self.action,
            "alternatives": [
                {"operation": operation, "action": action}
                for operation, action in self.alternatives
            ],
        }


#: What separates one step from the next. **`then` and nothing else**, which is
#: the whole discipline of this feature: a plan is an *ordering*, and `then` is
#: the word that expresses one. Splitting on `and` would turn "benchmark the
#: qwen3-4b and granite builds" — a single request naming two models — into two
#: steps NERVIS invented, and splitting on a comma would do the same to every
#: list anybody writes.
STEP = re.compile(r"\s*,?\s*\b(?:and\s+)?then\b\s*", re.IGNORECASE)

#: A plan longer than this is not an ordering somebody is holding in their head.
#: The cap is not about cost — every step is bounded on its own — it is about
#: the confirmation being meaningful: a person cannot read fifteen steps and
#: mean all of them.
MAX_STEPS = 6


@dataclass(frozen=True)
class Plan:
    """An ordered sequence of offers, confirmed once as an order.

    **One confirmation, and it buys ordering rather than authority.** Every step
    is a `Proposal` built by `propose` from one clause of what the person typed,
    so each is an operation §12 already allows with a target derived from their
    own words. Pressing the button says *do these, in this order*; it does not
    say *and anything else that follows from them*, because nothing follows —
    there is no step here that could not have been offered on its own.

    `ready` is false when any step is, and the plan is still returned: "I could
    prepare two of these three" is an answer, and a plan that silently dropped
    the step it could not build would run something other than what was read.
    """

    steps: tuple[Proposal, ...]
    plan_id: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.steps) and all(step.ready for step in self.steps)

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "ready": self.ready,
            "steps": [step.as_dict() for step in self.steps],
        }


def plan(
    question: str,
    models: Sequence[Mapping[str, Any]],
    jobs: Sequence[Mapping[str, Any]] = (),
    pools: Sequence[Mapping[str, Any]] = (),
    default_name: str = "",
    attachment: str = "",
) -> Plan | None:
    """Several offers from one sentence, or `None` if it is not a sequence.

    **Composed from `propose`, never around it.** Each clause goes through
    exactly the function that would have handled it alone, so a plan cannot
    contain an operation that is not offerable on its own — which is what makes
    the single confirmation an ordering decision. If that ever stops being true,
    it will be because somebody added a second way to build a step.
    """
    clauses = [part.strip() for part in STEP.split(question) if part.strip()]
    if len(clauses) < 2:
        return None
    if len(clauses) > MAX_STEPS:
        return None
    steps = [
        propose(clause, models, jobs, pools, default_name, attachment=attachment)
        for clause in clauses
    ]
    # **All or nothing.** A sentence where only some clauses name an operation is
    # not a plan with gaps, it is a sentence that was not a plan — and running
    # the half NERVIS understood is the failure this whole design exists to
    # avoid. Falls back to the single-offer path, which reads the first clause.
    if any(step is None for step in steps):
        return None
    return Plan(steps=tuple(step for step in steps if step is not None))


def propose(
    question: str,
    models: Sequence[Mapping[str, Any]],
    jobs: Sequence[Mapping[str, Any]] = (),
    pools: Sequence[Mapping[str, Any]] = (),
    default_name: str = "",
    clarvis: Mapping[str, Any] | None = None,
    attachment: str = "",
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
    # **The question test comes first, for every operation.** It used to sit
    # between cancel and submit, so *"did you cancel the benchmark?"* reached
    # the cancel branch and proposed one — a Cancel button offered in reply to a
    # question about the past, which is the same fault as offering to run a
    # benchmark when asked how the last one went. Whether a sentence is a
    # question has nothing to do with which operation it mentions.
    if (ASKING.search(question) or QUESTION_MARK.search(question)) \
            and not ASKING_FOR.search(question):
        return None
    # **Tried in order, and the order is the whole of the disambiguation.**
    # Each entry is a branch this used to hold inline; as a list it stays flat
    # while the set grows, and the reason one comes before another is written
    # where the pair actually matters rather than implied by nesting.
    #
    # Learning sits above benchmarking because *"remember that qwen3-4b is the
    # fast one"* names a model, and read the other way round it becomes an offer
    # to measure one.
    # Typed as what it is. A lambda carries no annotations and cannot be given
    # any, so calling one is a call into an untyped function — which this
    # package's own settings refuse. Naming the tuple's type puts the signature
    # back where the reader and the checker can both see it.
    attempts: tuple[Callable[[], Proposal | None], ...] = (
        lambda: _switch_proposal(question, pools) if SWITCH.search(question) and pools else None,
        lambda: _cancel_from(question, jobs),
        lambda: _saving_proposal(question, default_name, attachment),
        # Before learning, because "get clarvis to remember the port" is a
        # handoff whose task happens to contain the word `remember`.
        lambda: _handoff_proposal(question, clarvis),
        lambda: _learning_proposal(question),
        lambda: _benchmark_proposal(question, models) if BENCHMARK.search(question) else None,
    )
    for attempt in attempts:
        found = attempt()
        if found is not None:
            return found
    return None


def _cancel_from(question: str, jobs: Sequence[Mapping[str, Any]]) -> Proposal | None:
    """Cancel, which unlike the others matches on a group rather than a flag."""
    stopping = CANCEL.search(question)
    if not stopping:
        return None
    return _cancel_proposal(stopping.group("job") or "", jobs)


#: *remember that…*, *note that…*, *keep in mind…*. The trailing clause is the
#: note; everything before it is the request to keep one.
#:
#: **`that` is optional and the colon form is included**, because "remember: the
#: box is on the desk" is how people actually write this down. What is *not*
#: matched is a bare "remember" with nothing after it — there is no note in that
#: sentence, and offering to store an empty one is worse than not offering.
LEARN = re.compile(
    r"\b(?:remember|note|keep in mind|don.t forget)\b"
    r"(?:\s+that)?\s*[:,]?\s+(?P<note>\S.*)",
    re.IGNORECASE | re.DOTALL,
)

#: How much of a note becomes its heading. A heading is an index entry and it
#: counts triple in retrieval, so it wants the distinctive words — but it is
#: also what the person reads on the button, so it is their own words in their
#: own order rather than a bag of terms.
HEADING_WORDS = 8

#: Words that are all that remains when the phrase has consumed the sentence.
#: Not a stop-word list — these are the three tokens the pattern itself can
#: leave behind, and nothing else belongs here.
_FILLER = frozenset({"that", "this", "it"})


#: *get clarvis to…*, *have clarvis…*, *ask clarvis to…*. The trailing clause is
#: the task.
#:
#: **Clarvis has to be named.** Every other operation here is matched from what
#: the sentence asks for; this one is matched from *who it asks*, because
#: "fix the export bug" is a request to whoever is listening and only "get
#: clarvis to fix the export bug" says where it should go.
HANDOFF = re.compile(
    r"\b(?:get|have|ask|tell)\s+clarvis\s+(?:to\s+)?(?P<task>\S.*)",
    re.IGNORECASE | re.DOTALL,
)


def _handoff_proposal(question: str, clarvis: Mapping[str, Any] | None) -> Proposal | None:
    """*"Get Clarvis to add a retry to the uploader"* — an offer to write it down.

    `clarvis` is what NERVIS knows about the editor: whether one has registered
    and, if the operator turned labelling on, which workspace it has open. Both
    shape the offer rather than only its text — a task written where no editor
    is looking is the failure this whole design is trying to avoid, and the
    honest answer when NERVIS cannot tell is to say so on the button.
    """
    said = HANDOFF.search(question)
    if not said:
        return None
    task = " ".join(said.group("task").split()).strip(" .")
    if len(task) < 3 or QUESTION_MARK.search(task) or task.lower() in _FILLER:
        return None
    operation = BY_ID["nervis.clarvis.task"]
    registered = bool(clarvis and clarvis.get("registered"))
    label = str((clarvis or {}).get("workspace_label") or "")
    mine = str((clarvis or {}).get("nervis_workspace") or "")

    # **Three answers, and only one of them is a refusal.** No editor at all is
    # a task nobody will read. A label that disagrees is a task landing in the
    # wrong project. No label — `CLARVIS.md` §6.1's default, where the raw path
    # and name are private and `workspace_id` is salted — is not knowing, and
    # not knowing is said rather than resolved either way.
    if not registered:
        detail = ("no Clarvis window has registered, so nothing would read this. "
                  "Open the editor and ask again.")
        ready = False
    elif label and mine and label != mine:
        detail = (f"Clarvis has {label} open and NERVIS writes into {mine}, so this "
                  "would be written where that window is not looking")
        ready = False
    elif label:
        detail = f"Clarvis has {label} open, which is where this would be written"
        ready = True
    else:
        detail = ("Clarvis does not publish which workspace it has open — the path "
                  "is private by default — so NERVIS cannot confirm this lands where "
                  "that window is looking. It will be written into NERVIS's own "
                  "workspace either way.")
        ready = True
    return Proposal(
        operation=operation.id, service=operation.service, target=task,
        summary=operation.summary.format(target=_heading_of(task)),
        ready=ready, action=operation.action, detail=detail,
    )


def _learning_proposal(question: str) -> Proposal | None:
    """*"Remember that the GPU box has an RX 6800"* — an offer to write it down.

    The whole sentence is the note and the first few words are its heading, so
    what the button says is what the file will contain. Nothing is summarised:
    a heading NERVIS invented would be NERVIS deciding what somebody meant, and
    the point of this operation is that it does not.
    """
    said = LEARN.search(question)
    if not said:
        return None
    note = " ".join(said.group("note").split()).strip(" .")
    # **What is left when the phrase eats itself.** "remember that" has nothing
    # after it, so the optional `that` in the pattern backtracks and the word
    # itself becomes the note — an offer to file the word "that" as something
    # NERVIS had been told. The same happens with "note this" and "remember it".
    if len(note) < 3 or note.lower() in _FILLER:
        return None
    # **A question about remembering is not a note.** `ASKING_FOR` treats "can
    # you…" as a request wearing a question mark, which is right for "can you
    # benchmark qwen3-4b" and wrong here: *"can you remember what I said?"*
    # reached this and offered to file "what I said?" as a thing NERVIS had been
    # told. The difference is that every other operation names a target that
    # exists whether or not the sentence is a question, and this one takes the
    # rest of the sentence as its content — so a question mark makes the content
    # a question, and there is nothing to store.
    if QUESTION_MARK.search(note):
        return None
    operation = BY_ID["nervis.knowledge.learn"]
    return Proposal(
        operation=operation.id, service=operation.service, target=note,
        summary=operation.summary.format(target=_heading_of(note)),
        ready=True, action=operation.action,
        detail="kept beside the notes NERVIS shipped with, and overruled by them "
               "where the two disagree",
    )


def _heading_of(note: str) -> str:
    """The first few words, which is what the note is filed under."""
    words = note.split()
    if len(words) <= HEADING_WORDS:
        return note
    return " ".join(words[:HEADING_WORDS]) + "…"


def _segments(name: str) -> set[str]:
    """A model name broken where its punctuation breaks it.

    `deepseek-r1-distill-qwen-1.5b` yields `deepseek`, `distill`, `qwen` and the
    rest. Segments rather than raw substrings because "is it still running"
    contains `still`, which sits inside `distill` and is not a name — a substring
    test would offer to benchmark a model because somebody used an adverb.
    """
    return {piece for piece in re.split(r"[/:._\-]", name.lower())
            if len(piece) >= MIN_NAME_FRAGMENT}


def _models_named(question: str, local: Sequence[str]) -> list[str]:
    """Which local models this sentence names, in any position.

    Three ways to name one, in decreasing confidence: the whole id written out,
    a word matching one of its segments, or a word appearing inside it — the
    last covers `qwen3` against `qwen3-4b-2507`, which no segment split produces.

    Word order is not consulted at all. *"qwen3-4b, benchmark it please"* names a
    model as plainly as *"benchmark qwen3-4b"*, and a reader who has to put the
    words in a particular sequence has been asked to learn a syntax.
    """
    said = question.lower()
    # Hyphens stay inside the token. Splitting on them turned `qwen3-4b` into
    # `qwen3`, which names every qwen3 build on the machine — the request was
    # specific and the match was not.
    words = {word for word in re.findall(r"[a-z0-9][\w.\-]*", said)
             if len(word) >= MIN_NAME_FRAGMENT}

    # How specifically each model was named, by the longest word that reached
    # it. `qwen3-4b` reaches `qwen/qwen3-4b-2507` with eight characters and
    # `qwen/qwen3-8b` with five, so the two are not equally named.
    reach: dict[str, int] = {}
    for name in local:
        low = name.lower()
        # A segment match, or a *compound* word found inside the id. The
        # compound test is what lets `qwen3-4b` reach `qwen/qwen3-4b-2507`,
        # which no segment split produces — and restricting it to words
        # carrying a separator is what stops `still` reaching `distill`. A
        # plain English word must be a whole segment or it is not a name.
        compound = [word for word in words
                    if any(mark in word for mark in "-/._") and word in low]
        matched = [word for word in words if word in _segments(name)] + compound
        if low in said:
            matched.append(low)
        if matched:
            reach[name] = max(len(word) for word in matched)
    if not reach:
        return []

    # Only the most specifically named survive. Without this every ambiguity is
    # reported to somebody who was not ambiguous, and "say which" is a strange
    # answer to a sentence that already said which.
    best = max(reach.values())
    return sorted(name for name, length in reach.items() if length == best)


def _write_proposal(named: str) -> Proposal:
    """Save the last reply to a file the person named.

    Ready as soon as a name exists: unlike a benchmark, there is nothing to
    resolve against an inventory — the target is a filename, and whether it sits
    inside the workspace is decided when the button is pressed, by the same path
    comparison that governs reading. Deciding it here as well would put the
    boundary in two places, and two copies of a boundary disagree eventually.
    """
    operation = BY_ID["nervis.document.write"]
    return Proposal(
        operation=operation.id, service=operation.service, target=named,
        summary=operation.summary.format(target=named), ready=True,
        action=operation.action,
    )


#: Wanting the attached document back *with* the comments in it, rather than
#: the comments on their own. The words that say so, and nothing looser:
#: "notes", "review" and "feedback" were left out because "save your review"
#: with a document attached is at least as often a request for the review by
#: itself, and the plain save is what that has always produced.
ANNOTATING = re.compile(
    r"\b(?:comments?|annotat\w*|findings|remarks|original)\b"
    r"|\b(?:into|in)\s+(?:the|my|this|that)\s+(?:document|file|pdf|attachment)\b",
    re.IGNORECASE,
)

#: "Insert your findings into the document" names no save at all. These are the
#: verbs that request placing one thing inside another, and they only count
#: beside `ANNOTATING` with a file actually attached.
MERGING = re.compile(
    r"\b(?:merge|insert|add|place|incorporate|weave|thread|apply)\b", re.IGNORECASE
)

#: Which of the three annotated copies was asked for, if the person said.
STYLE_MARGIN = re.compile(r"\bmargin", re.IGNORECASE)
STYLE_INLINE = re.compile(
    r"\b(?:inline|in-line|re-?render\w*|plain\s+text|under\s+each)\b", re.IGNORECASE
)
STYLE_NOTES = re.compile(r"\b(?:sticky|post-?its?|notes?\s+only|as\s+notes)\b", re.IGNORECASE)

#: A message that is nothing but the choice — "margin notes", "sticky notes
#: only", "inline please". With a document attached, that is the answer to the
#: question the previous offer invited, and not something anybody types for
#: any other reason.
STYLE_ONLY = re.compile(
    r"^\W*(?:the\s+|go\s+with\s+(?:the\s+)?|do\s+(?:the\s+)?|make\s+it\s+|use\s+)?"
    r"(?:margin(?:\s+notes)?|sticky(?:\s+notes)?(?:\s+only)?|notes\s+only|inline|in-line)"
    r"(?:\s+(?:one|version|please|then|copy|notes))*\W*$",
    re.IGNORECASE,
)


def _style_of(question: str) -> str:
    """"margin", "inline", "notes" — or "" when no style was named.

    Margin first: "sticky notes in the margin" is the margin copy, which
    carries sticky notes anyway. Inline before notes for the same reason a
    person saying "inline notes" means inline.
    """
    if STYLE_MARGIN.search(question):
        return "margin"
    if STYLE_INLINE.search(question):
        return "inline"
    if STYLE_NOTES.search(question):
        return "notes"
    return ""


def _saving_proposal(
    question: str, default_name: str, attachment: str = ""
) -> Proposal | None:
    """Writing a file: the whole conversation, the attachment with this reply's
    comments placed in it, or the last reply on its own.

    **The conversation is checked first**, because "export this conversation as
    notes.pdf" matches both patterns and only one of them is what was asked for.
    **Annotating is checked before a plain save** for the same reason: "save
    the original with your comments" is a save request too, and read as one it
    would write the comments alone — which is what happened, and what produced
    a file of `[Original intact]` placeholders where the document should be.

    Split out of `propose` for the complexity gate, which is doing its job here:
    the branches share a filename and differ in what they write, and reading
    them side by side is how the precedence stays visible.
    """
    writing = WRITE.search(question)
    if EXPORT_CONVERSATION.search(question):
        named = _named_by(writing) if writing else default_name
        return _export_proposal(named) if named else None
    wants_to_write = bool(writing or SAVE_VERB.search(question))
    chosen = _style_of(question)
    style = chosen or "notes"
    if attachment and STYLE_ONLY.match(question):
        named = _annotated_default(attachment, default_name)
        return _annotate_proposal(named, style, bool(chosen)) if named else None
    # A named style is intent enough on its own: "re-render the document with
    # your comments inline" names no save and no merge, and means one thing.
    if attachment and ANNOTATING.search(question) and (
        wants_to_write or MERGING.search(question) or _style_of(question)
    ):
        named = _named_by(writing) if writing else _annotated_default(attachment, default_name)
        return _annotate_proposal(named, style, bool(chosen)) if named else None
    if not wants_to_write:
        return None
    named = _named_by(writing) if writing else _as_text_default(default_name, question)
    return _write_proposal(named) if named else None


def _annotated_default(attachment: str, default_name: str) -> str:
    """The derived name for an annotated copy, in the original's own format.

    `default_name` already carries the attachment's stem, "-annotated" and the
    date, ending `.pdf`. A PDF original keeps that: its pages are copied, so
    the copy is a PDF whatever else is true. A text original is merged as
    text, so the copy takes the original's suffix rather than being rendered
    into a format the person never had it in.
    """
    if not default_name:
        return ""
    found = re.search(r"\.\w{1,8}$", attachment or "")
    suffix = found.group(0) if found else ""
    if not suffix or suffix.lower() == ".pdf":
        return default_name
    return re.sub(r"\.pdf$", suffix, default_name, flags=re.IGNORECASE)


def _annotate_proposal(named: str, style: str, chosen: bool) -> Proposal:
    """The attached document with this reply's comments in it, one of three ways.

    When the style was not the person's choice, the other two travel with the
    offer as alternatives, so the default is soft on the screen and not only
    in a sentence.
    """
    operation = BY_ID[f"nervis.document.annotate.{style}"]
    others = tuple(
        (other.id, other.action) for other in OPERATIONS
        if other.id.startswith("nervis.document.annotate.") and other.id != operation.id
    )
    return Proposal(
        operation=operation.id, service=operation.service, target=named,
        summary=operation.summary.format(target=named), ready=True,
        action=operation.action, alternatives=() if chosen else others,
    )


def _named_by(writing: re.Match[str]) -> str:
    """The filename `WRITE` captured, from whichever of its shapes matched.

    Two groups rather than one because `re` will not let both alternatives
    share a name, and one `or` here is cheaper than a second pattern to keep
    in step with the first.
    """
    return writing.group("file") or writing.group("called") or ""


def _as_text_default(default_name: str, question: str) -> str:
    """The export offer's own derived name, as text unless a PDF was asked for.

    Still derived, not invented — the same title-and-date string `propose`
    was already handed, changed only in its suffix. A single reply is prose
    NERVIS already holds as text, so text is the default this reaches for.

    **Except when the person said "pdf" and named no file.** That sentence
    used to derive a `.md` name anyway, which made the reply promise a PDF
    while the button would have written markdown — the offer contradicting
    the sentence that produced it, silently, in the one direction nobody
    checks.
    """
    if not default_name:
        return ""
    if WANTS_PDF.search(question or ""):
        return default_name
    return re.sub(r"\.pdf$", ".md", default_name, flags=re.IGNORECASE)


def _export_proposal(named: str) -> Proposal:
    """Export the whole conversation to a file.

    Ready immediately, like the single-reply write: the target is a filename and
    whether it sits inside the workspace is decided when the button is pressed,
    by the one path comparison that governs every write.
    """
    operation = BY_ID["nervis.conversation.export"]
    return Proposal(
        operation=operation.id, service=operation.service, target=named,
        summary=operation.summary.format(target=named), ready=True,
        action=operation.action,
    )


def _benchmark_proposal(question: str,
                        models: Sequence[Mapping[str, Any]]) -> Proposal | None:
    """Resolve the name against what is actually on this machine.

    **Local models only.** A benchmark loads the model and measures it here;
    offering to benchmark something that runs in somebody else's data centre is
    offering a measurement of a network. SIRVIS would refuse it, and refusing it
    here means the offer is never made rather than made and then broken.
    """
    operation = BY_ID["sirvis.benchmark.submit"]
    local = [_name(model) for model in models if model.get("local") is True]
    local = [name for name in local if name]
    near = _models_named(question, local)

    if len(near) == 1:
        target = near[0]
        return Proposal(
            operation=operation.id, service=operation.service, target=target,
            summary=operation.summary.format(target=target), ready=True,
        )
    # **Nothing named means no offer at all**, rather than an offer of the whole
    # catalogue. "do we have benchmark results" mentions benchmarking and names
    # no model, and proposing one there answers a question about the past by
    # offering to start work — the same class of mistake as the old
    # "benchmark go". The model is told on every turn (`capabilities_line`) that
    # a missing offer means naming the model, and it holds the catalogue to
    # answer with, so this loses nothing a person sees.
    if not near:
        return None
    # Several named, which is an answerable question rather than a refusal —
    # the candidates go back so the reply can list them instead of saying "be
    # more specific" to somebody who does not know the full ids.
    return Proposal(
        operation=operation.id, service=operation.service, target="",
        summary=operation.summary.format(target="a model"), ready=False,
        detail=f"{len(near)} models on this machine match — say which",
        candidates=tuple(sorted(near)[:MAX_CANDIDATES]),
    )


def _pools_named(question: str, known: Sequence[str]) -> list[str]:
    """Which published pools this sentence names, in any position.

    A pool id is `ravis/<name>`, and people say the name three ways: written out
    in full, as *"the cheap pool"*, or bare — *"route this through reasoning"*.
    All three name the same thing, so all three are matched against the list
    RAVIS publishes rather than recognised by their shape.

    The bare form is why this reads the last word of the id rather than the
    whole of it: nobody types `ravis/` in a sentence unless they are quoting.
    """
    # Lowercased, not squashed. `_squash` removes spaces as well as punctuation,
    # so "the cheap pool" becomes "thecheappool" and a word-boundary match can
    # never fire — which is exactly what happened on the first attempt here.
    said = question.lower()
    named: list[tuple[int, str]] = []
    for pool in known:
        tail = pool.rsplit("/", 1)[-1].lower()
        if not tail:
            continue
        # A hyphen in an id is a space in a sentence: people write
        # "clarvis chat" for `ravis/clarvis-chat`. Whole words either way, so
        # `ravis/fast` is not named by "fastest" in an unrelated clause.
        pattern = r"[\s\-]?".join(re.escape(part) for part in tail.split("-"))
        if re.search(rf"\b{pattern}\b", said):
            named.append((len(tail), pool))
    if not named:
        return []
    # The most specifically named wins, exactly as it does for models:
    # `ravis/clarvis-chat` contains the whole of `chat` with a word boundary in
    # front of it, so both are named and only one was meant.
    best = max(length for length, _ in named)
    return sorted(pool for length, pool in named if length == best)


def _switch_proposal(
    asked: str, pools: Sequence[Mapping[str, Any]]
) -> Proposal | None:
    """Which pool this conversation should use next, from what was named.

    **Only a pool RAVIS publishes.** §7 has NERVIS address the pools RAVIS
    already publishes and never invent one, so an unrecognised name is not an
    offer — it is a list of what exists. `None` rather than a refusal when
    nothing resembles a pool at all, because "use the other one" is a sentence
    about something else far more often than it is a routing instruction.
    """
    operation = BY_ID["nervis.chat.profile"]
    known = [str(pool.get("pool_id") or "") for pool in pools]
    known = [name for name in known if name]
    near = _pools_named(asked, known)
    if len(near) == 1:
        target = near[0]
        return Proposal(
            operation=operation.id, service=operation.service, target=target,
            summary=operation.summary.format(target=target), ready=True,
            action=operation.action,
        )
    if not near:
        return None
    return Proposal(
        operation=operation.id, service=operation.service, target="", ready=False,
        summary="this conversation", action=operation.action,
        detail=f"{len(near)} pools match — say which",
        candidates=tuple(sorted(near)[:MAX_CANDIDATES]),
    )


def _squash(text: str) -> str:
    """Lowercased letters and digits only, so spelling stops mattering.

    "ravis/coding", "the coding pool" and "Coding" are one request. The same
    idea as `situation.named_in`'s flattening and deliberately a separate copy:
    that one matches service names against a registry, this one matches pool
    names against RAVIS's list, and a shared helper would tie two unrelated
    vocabularies to one definition of "close enough".
    """
    return "".join(character for character in text.lower() if character.isalnum())


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


def capabilities_line() -> str:
    """What NERVIS can be asked to do, stated whether or not an offer was made.

    **Because a model with no offer in front of it invents a reason.** Asked to
    *"do a benchmark on the deepseek model"* — a request the matcher was
    swallowing at the time — the reply was *"I can't queue that, NERVIS doesn't
    have a benchmark endpoint. You'd need to hit the hub's benchmark API
    yourself."* Every clause of that is false: the operation is in this module's
    own set, NERVIS holds a benchmark-scoped token, and SIRVIS advertises the
    capability as available.

    A matcher that misses a phrasing is a bug somebody reports. A system that
    denies a power it has is worse, because the person stops asking — so what
    NERVIS can do is stated on every turn rather than only when a phrase happened
    to match.

    Generated from `OPERATIONS` rather than written out, so an operation added
    later cannot be one the model still denies.
    """
    offerable = sorted(
        operation.action.lower() + " " + operation.summary.replace("{target}", "…")
        for operation in OPERATIONS
        if operation.id != "sirvis.result.delete"
    )
    return (
        "NERVIS can offer these, as buttons under your reply, when the person names "
        "a specific target: " + "; ".join(offerable) + ". Saving a reply, "
        "exporting a conversation and annotating an attached document are the "
        "exceptions — no name is required, NERVIS fills one in on its own. You "
        "never perform any of these and "
        "there is no endpoint for the person to call instead. If you cannot see an "
        "offer, the request did not resolve to one thing — say that you need the "
        "exact model or job named (or, for the two exceptions, that the phrasing "
        "did not register). Never say NERVIS lacks the ability, and never "
        "send them elsewhere to do it."
    )


def told(proposal: Proposal | None) -> str:
    """What the model is told about the offer — and what it must not claim.

    Deliberately a statement of fact rather than an instruction to act on: the
    model's job is to mention that the button is there. A model that says "done,
    I've started it" about work nobody confirmed is the failure this whole split
    exists to prevent, so it is named here rather than left to inference.
    """
    if proposal is None:
        # **Said, rather than left to be inferred from silence.** The standing
        # capabilities line already tells the model what to do when it cannot
        # see an offer — and a model cannot reliably notice that something is
        # absent. Asked "lets try the export again", one answered "the Export
        # button is still there under my last reply", about a reply that had no
        # button under it and never had: it knew the operation existed, nothing
        # told it none had been offered, and it filled the gap.
        #
        # Describing a control that is not on the screen is the same failure as
        # quoting a figure nobody measured, and it is worse in one way — the
        # person goes looking for it.
        return (
            "No offer accompanies this reply, so there is no button under it. "
            "Do not say there is one, do not refer to a button from an earlier "
            "reply as though it were still on this one, and do not describe "
            "where to click. If they are asking for something you can offer, "
            "ask them to name the target and it will appear on the next reply — "
            "**except saving a reply or exporting a conversation, which need no "
            "name at all.** A missing filename there is filled in automatically, "
            "from an attachment, the reply's own opening line, or the "
            "conversation's title, so saying 'save this' or 'export this "
            "conversation' should already have produced a button. Never say you "
            "cannot create a file or a download link for either — that is the "
            "standing capabilities line above, contradicted; if you truly see no "
            "offer for one, say the phrasing did not register and suggest trying "
            "it plainly, not that a name is missing. "
            "**Never say a save, write, export or any other operation already "
            "happened** — not this turn, not an earlier one — unless NERVIS's "
            "own reading says so. You have no way to know one occurred beyond "
            "that reading, and 'it must have gone through' is a guess wearing "
            "the words of a fact. If you are not sure whether something "
            "happened, say exactly that."
        )
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
            f"{proposal.action} button under **this** reply — the one you are "
            "writing now, not an earlier one. It is the only thing that "
            "can do this, and it has not been pressed, so nothing has changed yet — "
            "it is an offer on screen and nothing more. Tell them the button is "
            "there and that it is theirs to press. Describe it in the future tense "
            "only, and never point at a button under a previous reply: buttons do "
            "not persist, and sending somebody back up the page to look for one is "
            "the same as describing a control that is not there."
            + _ready_addendum(proposal)
        )
    return (
        f"The person asked for something NERVIS can offer — {proposal.summary} — but "
        f"it cannot be prepared: {proposal.detail}. No button has been offered. "
        "Tell them why, and name the alternatives if any are listed in the reading."
    )


def _ready_addendum(proposal: Proposal) -> str:
    """What pressing a ready button actually does, beyond "it is ready" —
    one clause per operation whose mechanics would otherwise surprise
    someone, each one added because a real reply got it wrong, not guessed
    in advance.
    """
    if proposal.operation == "sirvis.benchmark.submit":
        # **Because "queued" was read as a waiting line.** SIRVIS runs one
        # benchmark at a time: with nothing else running it starts
        # immediately and loads the model, and only otherwise does it wait.
        # Somebody who expects a queue and gets a busy machine has been
        # misled by one word.
        return (
            " Pressing it starts the benchmark straight away unless another "
            "is already running, in which case it waits for that one. It is "
            "not put in a queue to run later."
        )
    if proposal.operation.startswith("nervis.document.annotate."):
        # **The model is asked for comments, never for the document.** The
        # saved file that started this was placeholders — `[Original intact]`
        # — because a reply cannot hold ninety-seven thousand characters and
        # a model asked to retype them writes a stand-in instead. NERVIS holds
        # the document; the reply only has to say *where* each comment goes,
        # and a quoted line is something a model reproduces exactly where a
        # whole page is something it does not.
        return (
            # **The choice comes first, because a sentence at the end of a
            # long instruction is the sentence a model drops.** Observed live:
            # the soft-default paragraph sat last, and the reply described
            # the button at length and never named the other two copies.
            (
                " **Open with the choice, in one sentence, before anything"
                " else:** this button adds the comments as sticky notes — pages"
                " untouched, each note clickable at its passage — and they can"
                " say **margin notes** for a reviewer's copy with the comments"
                " drawn beside the text, or **inline** for a plain re-rendering"
                " with each comment under its passage (the document's own"
                " design is lost that way). Naming one puts that button on the"
                " next reply instead. This sentence is required whenever they"
                " did not name a style themselves."
                if proposal.operation.endswith(".notes") else ""
            )
            + " It copies the attached document — every page of it, untouched —"
            " and places comments beside the passages they quote. **The"
            " comments it places are the ones written in a reply, in this"
            " exact shape: a line beginning with `> ` that quotes a short"
            " phrase copied exactly from the document — a heading, or the"
            " opening words of the passage — then the comment on the lines"
            " below it. One `>` line per comment.** If your comments already"
            " exist in an earlier reply in that shape, say the button will"
            " place them. If they exist but are not in that shape, or exist"
            " only in your head, write them out here now, in that shape, in"
            " full — a reply that only describes what the button will do"
            " gives it nothing to place. Never retype or summarise the document"
            " itself; NERVIS holds it. Anything written without a quote still"
            " lands in the copy, at the end under its own heading. Nothing has"
            " been placed until the button is pressed."
            # **The choice is the person's, and the default is soft.** With no
            # style named, the sticky-notes button is offered straight away
            # rather than a question asked first — the operator wanted to be
            # asked, and wanted not to be blocked, and this is both: the button
            # is there, and the other two are one word away.
            + (
                " If they did not say which kind of copy they want, say that"
                " this button adds the comments as sticky notes — the pages"
                " untouched, each note clickable at the passage it is about —"
                " and, in one sentence, that they can say **margin notes** for a"
                " reviewer's copy with the comments drawn beside the text, or"
                " **inline** for a plain re-rendering with each comment under"
                " its passage (the document's own design is lost that way)."
                " Naming one puts that button on the next reply instead."
                if proposal.operation.endswith(".notes") else ""
            )
        )
    if proposal.operation != "nervis.document.write":
        return ""
    # **Pressing it saves this reply's own text, verbatim — nothing else.**
    # Reported from use: asked to save "the annotated document" after a long
    # back-and-forth revising one, this reply was a short remark about being
    # ready, and that is what got offered to save. If the person wants the
    # full, up-to-date document and this reply does not already contain it
    # in full, write it out completely in this same reply before mentioning
    # the button — a short reply plus a button saves the short reply.
    addendum = (
        " It saves the text of this reply exactly as written, nothing "
        "assembled from earlier turns. If what they want saved is not "
        "already written out in full above, write it out in full here "
        "first — the button saves whatever this reply actually says."
    )
    if proposal.target.lower().endswith(".pdf"):
        # **Said here because "can you check it looks right" is a question
        # this exact offer invites**, and the honest answer is yes — a
        # PDF save already gets an automatic glance, and a model with no way
        # to know that either denies a power NERVIS has (`capabilities_line`'s
        # own reasoning) or promises a guarantee this cannot make, since it
        # depends on a vision-capable model being reachable at the moment.
        addendum += (
            " Saving it as a PDF also gets the result a quick automatic "
            "glance afterward — whether the page actually rendered "
            "correctly, never whether the writing is good — and a real "
            "defect would be named in the confirmation once it is saved. "
            "This is not guaranteed to find anything: it depends on a "
            "vision-capable model being available right now, and the save "
            "completes either way regardless. If asked whether you can "
            "check a saved PDF looks right, say yes, this already happens "
            "automatically — never say you have no way to see the result."
        )
    return addendum
