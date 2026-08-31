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

### A notification centre

A durable place for things NERVIS wants to say — a
finished task, a service that changed state, a question. Today's spoken
announcements vanish the moment they are said; a note survives a reload. Every
note carries why it exists, and one written by a model also carries which model
and what it cost. A question can open a chat where the answer is just the next
thing said.

### Proposal outcomes

Nothing currently records whether an offer was accepted,
declined or edited, and that gap is the whole difference between proposing and
learning what somebody wants. A preference learned this way is shown in the
proposal that uses it — "you declined this twice, so this suggests the local
model" — never applied quietly. Silence is not a decline: somebody who closed
the tab did not refuse anything.

### Learned notes

A file NERVIS adds to, beside these hand-written ones and
searched the same way, holding corrections and preferences with the date and
what prompted them. It is a file a person can read, edit and delete. Where a
learned note disagrees with a hand-written one, the hand-written one wins and
the disagreement is shown.

### Planning

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

So the planned pool has a low but non-zero ceiling, landing on a small hosted
model that genuinely runs in parallel. Its spend is attributable by pool, so
what unattended work costs is a question with an answer.

The same trap sits in the protocol's "background call" marker, which refuses any
provider not known to be free — on a machine like this one, free means local
means competing. The plan says not to use it for this.
