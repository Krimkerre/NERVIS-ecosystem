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

**STATUS.md tracks this**, and CI checks it against the repository. In short: SIRVIS
measures models, stores the evidence, serves it, and recommends from it.

- **Inventory (§6)** — machines, runtimes, model artifacts and configurations, with
  identity kept as family + variant + runtime + config rather than a model name.
- **Benchmarks (§11)** — the single-model engine with warmups, repetitions, streamed
  timing and memory sampling, plus §11.3's multi-model modes over a Runtime Set:
  each member measured alone as a control, then co-resident.
- **Evidence (§12)** — the schema, the query API RAVIS reads, and §12.2's prohibition
  enforced by construction: nothing is ever keyed as model → score.
- **Recommendations (§14.3)** — a weighted score that carries the **coverage** it rests
  on, so a confident number and a thinly-evidenced one are distinguishable.
- **Resource Manager (§9)** — reference counts and leases, with every load and unload
  flowing through one owner. Two clients holding one model is the ordinary case.
- **Clarvis role suites (M12/M13)** — Clarvis's own benchmark assets wrapped rather than
  rewritten, so evidence is filed under `clarvis-chat` and `clarvis-agent`.

The exit criterion worth naming is still that **it starts with no runtime present**.
§15.4 requires standalone behaviour, and a laptop with nothing loaded is the ordinary
case rather than a fault. `doctor` does contact the runtime and reports what it finds —
an absent runtime is a finding, not a failure, which is the guarantee that matters.

Capabilities are advertised with the milestone behind each one. `sirvis.events@1` is the
remaining `unavailable` entry, and it names M21.

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
