# What is planned but not built

These are decided and written into the build plan; none of them exists yet. When
somebody asks about them, say plainly that they are planned — a milestone
described in the present tense is indistinguishable from a shipped one, and this
whole system is built on not confusing the two.

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
nothing writes one yet, because unattended work is a later milestone, but the
rule is already enforced rather than merely intended.

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

### Background thinking

The only part that needs a model to run with nobody
watching, and the only genuinely new ability. It still produces notes and
proposals rather than actions, so the gate is untouched; what it needs is a
spending ceiling and an interval rather than permission.

## The pool that makes it run alongside chat

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

## A window manager, as a stretch idea

Qtile is a tiling window manager configured in Python, and NERVIS is already an
HTTP service — so joining them is a request from a widget rather than an
integration layer. A workspace that houses the dashboard is a matching rule and
nothing more; a bar showing ecosystem health reads the same endpoint the
dashboard reads; a keybinding drops into chat.

Whether NERVIS may *move windows* is a separate decision and deliberately not
part of it. The window manager would allow it; that would arrive as operations
in the closed set with a confirmation, the same as everything else, rather than
as an assistant holding a window manager.

**The ecosystem does not need every service to run.** RAVIS works with no
benchmarking service at all — provider metadata and its own observations carry
routing, and the missing half is labelled rather than hidden. A machine with no
graphics card runs the gateway and the control plane perfectly well and simply
has nothing to benchmark. The one thing to watch is a model whose tool support
was never measured and is not declared by its provider: that fails closed, and
an operator setting it is what resolves it.

## A separate box for the models

Sketched, not built. A cheap machine carrying a real graphics card runs the
models and the service that measures them; a laptop runs the gateway and the
control plane and reaches them over the network.

Several things about this are already settled. The measuring service belongs on
the machine with the models — it samples memory around every generation, and one
that measured a remote machine while reading its own would mark an exhausted run
healthy and a clean run suspect. The gateway runs perfectly well with no
measuring service at all, reporting what it therefore cannot know. And a model
on another computer counts as remote, so a pool that promises never to leave
this machine correctly refuses it — which also means the strictest privacy pool
refuses the household's own box, and whether that deserves its own tier is an
open question rather than an oversight.

One consequence is pleasant: on a laptop with a capable machine on the network,
cheapest and fastest stop being opposite choices.

### Two measuring services

Planned, not built (RAVIS M27). The gateway reads measurements from exactly one
measuring service today, and the shelf it files them on is labelled by model and
job — not by machine. With two machines reporting, the same model measured on a
fast card and on a slow laptop would land on one shelf and the newer answer
would quietly push out the older, after which traffic could be sent to either
machine on the strength of a number taken on the other.

The fix is to label the shelf with the machine as well, which the records almost
support already: each one carries the machine it came from, and the address of a
model runtime says which machine it is without anyone configuring it. Worth
doing when a second machine really serves models — not before.

## Handing a coding task to Clarvis — built, 2 September

**Built.** Describe a coding task in chat,
press the button, and NERVIS writes it into the shared workspace as a file —
in a new folder of its own under `clarvis/nervis-tasks/`, which you open in
Clarvis as that task's workspace, so every task keeps its own plan.
Clarvis picks it up with the same flow it uses for its own plans, shows you the
task before anything runs so you can edit it, and every tool call still asks
permission the way it always did. Progress comes back as events Clarvis already
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

One honest limit. Both programs have to be pointed at the same folder, and
NERVIS usually cannot tell whether they are: Clarvis keeps its workspace path
private by default, which is the right default. So the offer refuses when no
editor has registered, refuses when Clarvis publishes a folder label that
disagrees, and otherwise says plainly that it cannot confirm the task lands
where the editor is looking.

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

One honest gap: it is supposed to run on a pool that cannot compete with the
chat you are having, and that pool does not exist yet. Until it does, which pool
it uses is your choice, and the card says so rather than implying a guarantee.
