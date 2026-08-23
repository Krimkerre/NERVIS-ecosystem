# SIRVIS

*Silicon Inference Runtime Validation & Intelligence System — the evidence plane.*

SIRVIS measures local models and says what it found. RAVIS routes on what SIRVIS
says, which is why the rule that shapes every module here is §12.1's provenance
invariant: **a value's provenance is never upgraded.** Something `ESTIMATED` does
not become `MEASURED` because it was copied, averaged or stored; something
`UNKNOWN` does not become a zero because a column needed filling.

`../SIRVIS.md` is the specification. `../STATUS.md` says how far this has got.

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -e ../protocol -e ".[dev]"
.venv/bin/sirvis doctor     # configuration and migrations, contacting nothing
.venv/bin/sirvis serve      # http://127.0.0.1:8721
```

`ecosystem-protocol` first: it is a local path dependency and pip will not find
it on PyPI.

## What exists

**M0 only.** The `/ecosystem/*` MEP surface, a migrated SQLite database, the CLI
and the configuration checks. No runtime adapter, no benchmarks, no evidence.

The exit criterion worth naming is that **it starts with no runtime present**.
Stage 1 exits at M0 and Stage 4 cannot begin until it does, so a SIRVIS that
needed LM Studio running in order to start would block the whole ecosystem
schedule on an application being open. §15.4 requires standalone behaviour, and
a laptop with nothing loaded is the ordinary case rather than a fault — which is
why the LM Studio address is configuration that `doctor` prints and deliberately
does not probe.

Every capability is advertised `unavailable` with the milestone that will change
it, including `sirvis.evidence.query@1`, the surface RAVIS is waiting on. That
one is declared absent from the start on purpose: a peer can act on "not yet,
because M16" and cannot act on silence.

## Two things it deliberately does not share

The MEP surface comes from `../protocol`, because a health endpoint that means
something slightly different per service is worse than none.

Storage does not. `ECOSYSTEM_RUNBOOK.md` §3 permits sharing generated transport
types from the protocol package and nothing else — "shared database tables,
provider clients, routing engines and benchmark logic are not". So
`storage/database.py` is a sibling of RAVIS's rather than an import of it, and
the two hard-won details in it — thread-local connections, and `":memory:"`
giving every connection a *private* database — travel as comments because the
code cannot.
