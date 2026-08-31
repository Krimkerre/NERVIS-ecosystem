# SIRVIS — what it does and how it decides

SIRVIS measures models on this machine and publishes the evidence. It does not
route requests and does not choose models for anybody: it produces the numbers
RAVIS's preferences can be built on.

## What it measures

Benchmark runs against local runtimes, recording throughput, latency,
time-to-first-token, and whether a build emits well-formed tool calls. Results
carry the machine, the runtime, the model build and the conditions — not just a
score, because a number without its conditions cannot be compared to anything.

## How it decides what a result means

**A result is VALID or SUSPECT, and SUSPECT is the interesting one.** A run that
happened under memory pressure, thermal throttling or swap is marked suspect,
with every reason attached. The verdict *is* the list of warnings: validity is
"suspect if warnings else valid", so the notes are the whole of the
justification rather than a caveat beside it.

**Withdrawn measurements leave a tombstone.** A deleted result is recorded as
deleted, because otherwise there is no way to tell a measurement somebody
retracted from one nobody ever took.

**Recommendations are weighted and say so.** A recommendation names the evidence
behind it and its coverage, so an operator can see whether it rests on eighty
runs or two.

## What an operator can ask it for

The machines, runtimes, models and instances it knows about; benchmark runs and
their results with provenance; the job queue; model leases; residency — what is
loaded right now; and its recommendations with the evidence under them.

## What it will not do

It does not load a model because somebody asked a question. Loading is leases
and the queue, owned in one place, because two things loading models at once on
one machine is how a benchmark measures swap instead of a model.

It does not report a score without its conditions, and it does not average a
counted metric — tool-call rates are counted rather than averaged, so they carry
no median and a reader asking for one is told that rather than given throughput.
