# What was planned, and what became of it

This file began as the list of what was planned and not built. **Most of it is built now**, and each
section says so in its heading or first line; what is still only planned says "planned" or "not
built". When somebody asks, say plainly which — a milestone described in the present tense is
indistinguishable from a shipped one, and this whole system is built on not confusing the two.

**On 18 September 2026 the owner struck the unbuilt parts** that only pay off for heavy benchmarking
or for hardware this stack doesn't have (more than one SIRVIS feeding one RAVIS, serverless GPUs,
comparison analytics, configuration sweeps). **Still open, as of 19 September 2026:** re-grading
the Code tab (the editor in the browser) through NERVIS's own proxy; the rest of RAVIS's
concurrency awareness; Clarvis's release regression check (rollback proven, the upgrade comparison
under way); and a direct link between the two machines' SIRVISes (offered and left for later, after
two small fixes around LM Link).

## An assistant that grows with the user

Five steps, in this order, each useful on its own and each what the next one
reads from. **The order is also the order of risk**: the first three change
nothing about what NERVIS is allowed to do.

The reason this can be built at all without loosening anything: the human gate
is about *acts*, and learning is about *knowledge*. The operation set stays
closed and a person still presses the button; what improves is how good the
proposal is before they see it.

### A notification centre — built, 1 September

**This one is no longer planned; it is on the Notifications tab.** A durable
place for things NERVIS wants to say — a service that changed state today, and
from later milestones a finished task or a question. Spoken announcements vanish
the moment they are said; a note survives a reload and a restart.

Every note carries why it exists, and NERVIS refuses to file one that cannot say
so. A note written by a model must also carry which model and what it cost —
which the unattended background work (built, see *Thinking when nobody is watching*) does.

The important part is who writes it. The voice speaks from the browser and the
note is written by the service, so muting, closing the tab, or reading another
screen loses the announcement and never the record. The badge in the top-right
corner is the same count seen from wherever somebody happens to be.

Dismissing is one note at a time on purpose — there is no mark-all-read and no
per-kind mute, because silencing a class is how the one that mattered gets
missed. A dismissed note is hidden rather than deleted, and swept a month later.
The first sweep after a restart is not announced at all: everything "changing"
from nothing-known is a roll call, not news.

### Proposal outcomes — built, 1 September

**Also no longer planned.** When NERVIS offers to do something, what you did
with the offer is now written down: you took it, you declined it, or you changed
it — and if you changed it, what you changed it to, which is the part worth
keeping. The next time the same offer comes up it says what happened before:
"you have declined this twice".

Three things about it are deliberate. **Silence is not a decline** — an offer you
ignored leaves no record at all, because closing a tab is not refusing, so there
is a "No thanks" button and that is the only way a refusal becomes a fact.
**What is remembered is shown, never applied** — the sentence appears beside the
offer and the offer is unchanged, because a preference you cannot see is one you
cannot argue with. And **clearing it is exact**: proposals are composed without
ever consulting the record, so emptying it leaves NERVIS offering precisely what
it offered before it learned anything. The record and its clear button are under
Settings.

### Learned notes — built, 1 September

**This one is built.** NERVIS keeps a file called `learned.md` next to these
hand-written notes, holding things it has been told, each with the date and the
sentence that prompted it. It is searched by exactly the same index as every
other note here, because it sits in the same directory — there is no separate
path to keep in step.

Three ways to add one: say *"remember that…"* in chat and press the button, type
one into the field under Settings, or open the file in any editor. The first two
go through one enumerated operation and a confirmation, like every other change
NERVIS makes. What gets stored is the person's own sentence — nothing a model
wrote, and nothing NERVIS decided on its own was worth keeping.

Where something NERVIS was told disagrees with one of the hand-written notes on
the same subject, the hand-written one is used and the learned one is still
shown, marked *overruled*. Exactly one of the two is wrong and only a person can
say which, so neither is hidden. Notes can be forgotten one at a time or all at
once, and the file can simply be deleted — retrieval then behaves exactly as it
did before any of this existed.

### Planning — built, 1 September

**Built.** Say two things joined by *then* — "remember that the box is on the
desk then export this conversation" — and NERVIS offers the whole sequence as a
plan, drawn in full before any of it runs. One button confirms the order.

What that button buys is the *ordering*, not permission. Every step is an offer
NERVIS would have made on its own from a clause you typed, so there is nothing
in a plan you could not have confirmed one at a time. Only *then* splits a plan:
"benchmark the qwen3-4b and granite builds" is one request naming two models,
not two steps. And if any clause names nothing NERVIS can do, there is no plan
at all rather than a plan with a gap — running the half it understood would be
worse than asking again.

While it runs you can stop it. The stop lands at the next step, never in the
middle of one, and the plan then says how far it got. If a step fails, the rest
does not run: the sequence had a premise and the premise is gone. Finished,
stopped and halted are reported as three different things, because they are.

The older description of this idea follows.

Proposing an ordered sequence of operations that are already
allowed, shown in full, confirmed once, and stoppable at any step. Each step
stays individually bounded and reversible, so the single confirmation is a
decision about *order* rather than a blanket approval — one confirmation for six
steps is a weaker gate than six confirmations, however similar the rule looks.

### Background thinking — built (see *Thinking when nobody is watching*)

The only part that needs a model to run with nobody
watching, and the only genuinely new ability. It still produces notes and
proposals rather than actions, so the gate is untouched; what it needs is a
spending ceiling and an interval rather than permission.

## The pool that makes it run alongside chat — built as `ravis/free-api`

**Resolved.** `ravis/free-api` (RAVIS M28) is that pool: it uses only models that cost nothing and
run on somebody else's hardware, so background work never loads a model onto the machine chat is
using. Background calls (titles, naming, unattended work) go there first, and this machine's own
models are only the fallback. RAVIS M26, the plan below, was closed on 18 September 2026 as covered
by it. The reasoning that led there:

Unattended work needs its own pool in the gateway, and the obvious choice is the
worst one available. `ravis/cheap` allows nothing above zero cost, so it can only
choose models on this machine — which means an unattended job loads a model onto
the machine chat is already using and the two compete for it. That is not a
worry, it is something that happened: a background job to name a conversation
loaded a cold local model and produced nothing usable.

The rule that fixes it is **"must not contend"**, and deliberately not "must be
hosted" — because that is an answer rather than the question, and it is wrong on
a different machine. A workstation with an idle graphics card runs a local model
fast, for free, competing with nothing; a hosted-only rule would spend money
there to avoid hardware that was sitting unused. A laptop with a cold runtime is
the opposite case.

So the ceiling is something an operator sets per machine, and the pool expresses
its intent through what is already measured: prefer a model already loaded,
refuse to pay for loading one when memory is tight. The same gateway then
reaches opposite conclusions on two machines from configuration alone, and says
which and why.

The same trap sits in the protocol's "background call" marker, which refuses any
provider not known to be free. That is the same baked-in answer in another
place, and it resolves to a local model on exactly the machine where local is
the wrong choice. The plan says not to use it for this.

## Running without every service

**The ecosystem does not need every service to run.** RAVIS works with no
benchmarking service at all — provider metadata and its own observations carry
routing, and the missing half is labelled rather than hidden. A machine with no
graphics card runs the gateway and the control plane perfectly well and simply
has nothing to benchmark. The one thing to watch is a model whose tool support
was never measured and is not declared by its provider: that fails closed, and
an operator setting it is what resolves it.

## A separate box for the models

Sketched, not built as a design of its own. A cheap machine carrying a real graphics card runs the
models and the service that measures them; a laptop runs the gateway and the control plane and
reaches them over the network.

**What exists today instead** (19 September 2026): the owner runs the whole stack on two machines,
a Mac and a ThinkPad with CachyOS, and LM Studio's **LM Link** lets one use the other's models. SIRVIS
lists those models apart under the other machine's name, doesn't count them against this machine's
loaded-model limit, and refuses to benchmark them here; the owner decided they count as local for
private and local-only requests. The two SIRVISes don't talk to each other: that bigger link — one
dashboard for both machines, shared benchmark results, loads on the other machine by its own rules —
was offered and left for later, after two small fixes.

Several things about this are already settled. The measuring service belongs on
the machine with the models — it samples memory around every generation, and one
that measured a remote machine while reading its own would mark an exhausted run
healthy and a clean run suspect. The gateway runs perfectly well with no
measuring service at all, reporting what it therefore cannot know. And a model
on another computer reached by its network address counts as remote, so a pool that promises never
to leave this machine correctly refuses it. A model reached through LM Link is different: LM Studio
serves it from this machine's own address, and the owner decided on 19 September 2026 that it counts
as local, because both machines are theirs.

One consequence is pleasant: on a laptop with a capable machine on the network,
cheapest and fastest stop being opposite choices.

### Two measuring services

Struck on 18 September 2026 (RAVIS M27), with the rest of what needs hardware this stack doesn't
have; the reasoning is kept. The gateway reads measurements from exactly one
measuring service today, and the shelf it files them on is labelled by model and
job — not by machine. With two machines reporting, the same model measured on a
fast card and on a slow laptop would land on one shelf and the newer answer
would quietly push out the older, after which traffic could be sent to either
machine on the strength of a number taken on the other.

The fix is to label the shelf with the machine as well, which the records almost
support already: each one carries the machine it came from, and the address of a
model runtime says which machine it is without anyone configuring it. Worth
doing when a second machine's SIRVIS reports to the same RAVIS — not before. LM Link gives a second
machine's models without that (see above), and SIRVIS refuses to benchmark a linked model, so no
number from the other machine lands on this one's shelf.

## Handing a coding task to Clarvis — built, 2 September

**Built.** Describe a coding task in chat,
press the button, and NERVIS writes it into the shared workspace as a file —
in a new folder of its own under `clarvis/nervis-tasks/`, so every task keeps
its own plan. The folder is named for the task — `pomodoro-timer` — and if the
task is too vague to name, the button asks you what to call it before anything
is written. Pressing it also switches to the Code tab and opens that folder in
the editor, as that task's own workspace.
Clarvis then starts its planning interview with your task already typed into
the first answer: change it or send it as it is, and it asks questions to flesh
the task out before writing a plan for you to approve. Since Clarvis 0.17.23 a small, clear task is
offered a short way that skips the questions, every question has **Draft it now**, and a task too
small for a plan goes straight to an offer to build it (see *Planning asks less* in the Clarvis
notes). Nothing is built until you say so, and every tool call still asks permission the way it
always did. Seen working end to end, with real Codex builds, on 19 September 2026. Progress comes back as events Clarvis already
publishes.

**The reason it is shaped that way is that it needs no new permissions at all.**
NERVIS can already write files into that directory when you confirm it, so this
is that same writer with a different template. NERVIS never runs a tool, never
answers a permission prompt for you, and never starts the run — you open the
editor and start it. The rule that forbids the other version is Clarvis's §6.7,
and the test that keeps the two apart is simple: with the connection between the
two programs switched off, this still works, because the interface is a document
rather than a link.

The task file says it came from NERVIS, so whoever approves it knows to read it
with the right amount of suspicion — and Clarvis leads with that rather than
tucking it at the end.

**How to ask for one.** Say it the way you would to a person — *get Clarvis to
add a retry*, *hand this to Clarvis: add a retry*, *can you ask Clarvis to add a
retry?* — and a Hand over button appears under the reply. **Nothing is written
until that button is pressed**, and until then the task is not in the workspace
and Clarvis does not have it. On 10 September 2026 *"Can you hand a small coding
task to clarvis?"* produced no button because that phrasing was not recognised,
and chat told the person the task had been written anyway. The phrasing is
recognised now; if a request ever produces no button, nothing was handed over. Until NERVIS 0.34.53
(19 September 2026) a task starting with "write", "save" or "export" — *get Clarvis to write a
script…* — got a Save button for chat's reply instead of Hand over; a sentence addressed to Clarvis
now always offers the handover.

**Where it opens, since 10 September 2026.** The editor opens at its usual
address with the task's folder attached, so its saved keys, history and
settings are all still there — only which folder is open changes, and only
because the button was pressed. Clarvis then starts its planning interview with
the task typed into the first answer, as described above. The offer no longer checks for an open editor window first: each
task's folder is new, so no window open beforehand could be looking at it.
One limit: if the editor is set up behind NERVIS's own proxy (off by default),
the tab opens its configured workspace instead, and you open the task's folder
yourself.

## Thinking when nobody is watching — built, 1 September

NERVIS can now think when nobody is watching, and it is **off until you switch it
on** under Settings. It spends money without being asked to, which is the one
kind of feature that may not default to on.

What it does: notices that a service has stayed unwell across several checks, or
once a day summarises what the event hub recorded, and writes a short note into
Notifications. Each note says which model wrote it and what that cost.

What it cannot do: anything. Every output is a note — it may observe, draft and
ask, and an operation still needs the same button press it needs when you are
watching. There is no path from unattended work to an action.

Every run is recorded, including the ones that decided there was nothing worth
saying, because those are the ones you want to see when wondering what it has
been spending. Each trigger can be switched off on its own, and switching one off
stops future runs of it while leaving the notes it already filed alone.

It runs on `ravis/free-api` by default, the pool that cannot compete with the chat you are having
(see *The pool that makes it run alongside chat*); which pool it uses stays your choice, and the card
says which.
