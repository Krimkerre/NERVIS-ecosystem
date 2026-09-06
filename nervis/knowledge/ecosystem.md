# The ecosystem — how the four fit together

Four services, one machine, each with one job.

**SIRVIS knows.** It measures models and publishes evidence.
**RAVIS chooses.** It routes each request to a model.
**CLARVIS acts.** It is the coding agent inside VS Code.
**NERVIS connects.** It is the control plane, dashboard and chat.

## The shape of it

    Clarvis ──┐
              ├──► RAVIS ──► providers (local runtimes, OpenRouter, Anthropic, OpenAI, Google)
    NERVIS ───┘      ▲
       │             │ evidence shapes pool membership
       │          SIRVIS
       └── watches all three over published contracts

NERVIS is a *consumer* of the others, never a peer that reaches into them. It
reads RAVIS and SIRVIS over HTTP and holds no copy of their databases.

## What they share

Every service publishes the same metadata surface — identity, health,
capabilities, version, events — so an operator can ask all three the same
question and compare the answers. A capability that is not fully working is
declared degraded or unavailable **with a reason**, rather than being advertised
and failing at the moment of use.

Requests carry a trace so one question can be followed across services, and a
routing decision is stored rather than recomputed.

Each of the three Python services also shares a backup story: a database
backs itself up automatically before every migration and can be put back —
`nervis restore-database`, `ravis restore-database`, `sirvis restore-database`,
each taking an optional `--version`, all confirmed working rather than only
read from the code. Clarvis rolls back a different way, by reinstalling a
prior `.vsix`; that was proven safe for real on 6 September 2026 against a
live workspace, both the conversation history and the stored provider key
surviving a version downgrade byte-for-byte. Full detail, plus what to do for
each of §10's failure conditions, is in `OPERATOR_RUNBOOK.md` — this file
explains what the ecosystem is; that one explains what to do when a piece of
it is misbehaving.

## The rule the whole thing rests on

**Never invent an endpoint, a number or a capability.** A service that cannot
answer says so. A figure that was not measured is absent rather than zero, and a
zero that looks measured is worse than a gap, because a gap is visibly a gap.
