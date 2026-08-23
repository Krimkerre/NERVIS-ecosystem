# Where the build is

**Read this before doing anything else.** It is the only file that says what is
finished and what is next. Everything else describes what the system *should* be;
this describes what it *is*, as of the last commit that touched it.

Keeping it honest is part of finishing a milestone, not a separate chore: a
status file that drifts is worse than none, because it is believed.

**That is enforced, not merely asked for.** `tools/check_status.py` runs in CI
and fails the build when the numbers here stop matching the repository, when a
path named here stops existing, or when a milestone appears as both done and
next. It rests on one observation — finishing a milestone always adds tests — so
an asserted test count doubles as a check that this file was updated when the
last one landed.

---

## Verify this yourself — do not take it on faith

Four commands. If any disagrees with what follows, this file is wrong and the
commands are right.

```bash
cd ravis && python3 -m venv .venv && .venv/bin/pip install -e ../protocol -e ".[dev]"
.venv/bin/ruff check src tests        # lint, imports, naming, complexity ≤ 8
.venv/bin/mypy                        # strict types
.venv/bin/pytest                      # part of 385 tests, no network, no live service
.venv/bin/ravis conformance clarvis   # the §8.9 release gate — 16 checks
```

The other two packages are checked the same way, from their own directories:

```bash
cd protocol && ../ravis/.venv/bin/python -m pytest -q   # 15 tests
cd sirvis   && ../ravis/.venv/bin/python -m pytest -q   # 110 tests
```

**`ecosystem-protocol` must be installed first.** It is a local path dependency
and pip will not find it on PyPI, because it does not live there.

Expected: all clean, 385 passing across the three, conformance `PASS`. CI runs the same four on
every push (`.github/workflows/checks.yml`), plus `nervis/tools/check.py`.

See it actually work, against a real model:

```bash
RAVIS_UPSTREAM_BASE_URL=http://127.0.0.1:1234 .venv/bin/ravis doctor
RAVIS_UPSTREAM_BASE_URL=http://127.0.0.1:1234 .venv/bin/ravis preflight clarvis
RAVIS_UPSTREAM_BASE_URL=http://127.0.0.1:1234 .venv/bin/ravis serve
curl -s localhost:8731/api/v1/pools | python3 -m json.tool | head -30
```

`preflight` is the one to run before M9. It prints the VS Code settings to paste
and then resolves both Clarvis pools against the live catalogue, so the two
failure modes that read as "Clarvis is broken" — a base URL with `/v1` on the
end, and an agent pool with no tool-capable candidate — are named before anyone
opens an editor. On this machine, against LM Studio's twelve models, chat
resolves and **the agent pool does not**, because nothing has declared tool
support yet. That is §5.2 working as written, and it is the next thing to fix.

---

## The build order, unambiguously

**Milestone numbers are identifiers, not a schedule.** M4 is numbered before M9
and executes after it. This has caused real confusion, so the honest ordering is
written out once, here, rather than reconstructed from three mapping tables.

The runbook's stages (`ECOSYSTEM_RUNBOOK.md` §6.2) are the schedule. RAVIS's
milestones map onto them in `RAVIS.md` §20.1. This is that mapping, flattened
into the order work actually happens.

### Done

| # | Milestone | What it delivered |
|---|---|---|
| 1 | **M0** | Package, config, SQLite + migrations, structured logging, CLI, `/ecosystem/*` MEP surface, §4.4 admission control, §9.6.0 identity |
| 2 | **M1** | Transparent `/v1/models` and `/v1/chat/completions`, streaming, cancellation |
| 3 | **M2** | Clarvis wire-contract suite — `ravis conformance clarvis`, 12 Stage 2 checks (16 today; M12 added §8.8's Stage 3 four) |
| 4 | **M3a** | `ProviderAdapter` protocols, normalized request/response/stream shapes, capability discovery |
| 5 | **M5** | The 13 virtual pools, route resolution, route explanations |
| 6 | **M14** *(observation half)* | Residency preference, memory-pressure route change |
| 7 | **M6** | Request-derived hard constraints — tools, vision, context, schema, streaming |
| 8 | **M18a** | Read-only management API — pools, models, providers, profiles, policies, route decisions, usage |
| 9 | **M12** | Failure classification, provider and model health, circuit breakers, retry budget, and the fallback chain — plus the §8.8 Stage 3 conformance scenarios the earlier milestones had left unwritten |
| 10 | **M9 groundwork** | `ravis preflight clarvis`, and CORS on the read surface so a browser dashboard can reach it |
| 11 | **M9** | **Live Clarvis ↔ RAVIS**, done 2026-08-23. Evidence below |
| 12 | **`ecosystem-protocol`, and SIRVIS M0** | The MEP surface extracted to the shared package the runbook has always named, and the second service standing on it |
| 13 | **SIRVIS M1 + M2** | Machine detection with honest gaps, and the LM Studio adapter. Verified live: discover → load → generate → unload |
| 14 | **SIRVIS M3** | The four-concept model domain, and the `runtime_key` lookup RAVIS needs |
| 15 | **SIRVIS M4** | Token scopes, origin validation, and the mutating endpoints that let a script drive a model *through* SIRVIS |
| 16 | **SIRVIS M7** | The evidence schema — **Stage 4's exit criterion verbatim** |
| 17 | **SIRVIS M8** | The Resource Manager: reference counts, leases, conflict policy, and §9's rule that *all* load and unload flows through one owner |

**Stages 0, 1, 2 and 3 are complete. Stage 4 has started.**

### The protocol package, extracted when the second consumer arrived

`ECOSYSTEM_RUNBOOK.md` §12 puts `ecosystem-protocol` first in the release
sequence and §3 permits services to share *only* its transport types — "shared
database tables, provider clients, routing engines and benchmark logic are not".
It was never extracted, because RAVIS was the only consumer and a package with
one consumer is just a directory.

SIRVIS M0 needs the same five `/ecosystem/*` endpoints. One copy is a package;
two copies is a protocol nobody can rely on, so it was extracted before the
second copy existed rather than after. `protocol/` now holds the endpoints, the
capability envelope, the version rule and — because runbook §4.3 fixes the
correlation vocabulary and §9's redaction list is a security control — the
structured logging. Each service still supplies its own identity, its own health
checks and its own capability declarations, which is everything that ought to
differ and nothing that ought not.

The package's tests mount the router on a bare FastAPI app with an invented
service type, deliberately: if they needed RAVIS to run, it would be RAVIS's
router with extra steps and the first SIRVIS-shaped assumption would go
unnoticed until SIRVIS tripped over it.

SIRVIS storage is *not* shared, and that is the same rule read the other way.

### SIRVIS M0

`sirvis doctor` and `sirvis serve` work, the database migrates, and the service
starts and answers with **no runtime present** — which is M0's load-bearing exit
criterion, because Stage 1 exits here and Stage 4 cannot begin until it does. A
SIRVIS that needed LM Studio open in order to start would block the ecosystem
schedule on an application being open.

Every capability is `unavailable` and each names the milestone that will change
it, including `sirvis.evidence.query@1` — the one RAVIS is waiting on, declared
absent from the start because a peer can act on "not yet, because M16" and
cannot act on silence.

### M9, and what it actually proved

An unmodified Clarvis was pointed at a running RAVIS through **its own provider
UI**, not by editing `settings.json`. No Clarvis source change was made and none
was needed, which is the claim `ECOSYSTEM_RUNBOOK.md` §6.1 rests the whole build
order on — the riskiest unknown was proxy wire-compatibility, and it is now
retired against real traffic rather than fixtures.

Nineteen route decisions in one session, against LM Studio's twelve models with
`measured-capabilities.json` loaded:

| | |
|---|---|
| `ravis/clarvis-chat` | 7 succeeded, 4 cancelled |
| `ravis/clarvis-agent` | 5 succeeded — a real write, five sequential tool-calling steps |
| Failures of any class | none |
| Interrupted streams | none |
| Circuit state | all `CLOSED` throughout |

Against Stage 3's exit criteria: chat streams; agent tool calls survive
fragmentation; Stop cancels upstream work and never triggers a fallback; the two
pools resolve independently; the agent pool never routed to a model without
tools; and no Clarvis source was modified.

**Two honest gaps in that.** Both pools resolve to `qwen2.5-coder-7b-instruct`,
so "different models" is unproven — not because independence failed but because
this catalogue's best model wins both roles, and forcing a split would mean
choosing worse. And **fallback never ran live**: nothing failed all session, so
no chain walked past its primary. It is covered by `ravis conformance clarvis`
against a genuinely-failing primary, and by nothing else.

**Running it early paid for itself in defects.** Three bugs surfaced in an hour
that 251 tests had not, all in code that only matters during an incident: a
cancelled stream recorded nothing, every warning discarded its reason, and a
typo in a pool ID silently bypassed routing altogether. Each is in the sections
below.

### What M1 and M2 turned up

Four facts about this machine's runtime, each established by probing it rather
than by reading documentation, and each now encoded somewhere that will fail if
it stops being true.

- **LM Studio answers `200` for endpoints it does not implement**, with an error
  body. A randomly invented path answers exactly like a plausible one. An
  adapter trusting the status code reports an unsupported operation as success,
  which is worse than failing because it is silent. Every response is checked
  for an `error` key.
- **There is no HTTP load or unload.** Lifecycle lives in the `lms` CLI, which
  §7 permits precisely because no API exists for it — "CLI tooling may exist as
  a debug fallback but must not be the primary abstraction while an API exists".
  Everything LM Studio *does* expose over HTTP is read over HTTP.
- **`loaded_context_length` is routinely smaller than `max_context_length`**,
  which is §7.1's requested-versus-effective distinction in the wild rather than
  in principle. See the open decision below, because it has already bitten.
- **A reasoning model can return empty content** at a low token cap: `qwen3-1.7b`
  spent all eight on thinking. Not an adapter fault, and exactly what
  `clarvis/docs/benchmarks.md` screened for — but it means "the call succeeded"
  and "the model said something" are separate checks, which M6 will need.

### What M3 settled

§6's four concepts, kept apart, and each separation earns itself on this machine
rather than in principle:

- **`granite-4.0-h-tiny` is one family and two variants.** Same weights, GGUF
  and MLX, and §12.2 makes format and quantization part of evidence identity —
  which is why they must not merge: the two reach 8/8 and 1/8 on the same
  tool-call trial.
- **Their architectures disagree** — `granitehybrid` against `granitemoehybrid` —
  and that is *recorded*, not resolved. §6 forbids asserting equivalence from
  names; silently believing one packaging would be inventing agreement, and
  refusing to link them would lose a relation that is real.
- **Identifiers are derived, not generated.** An ID is a hash of the attributes
  that identify a build, so a fresh inventory on an empty database reproduces
  it. A random UUID would need a table to survive a restart, and a wiped
  database would orphan every result referencing one.
- **No table was added.** The inventory is derived on read, because everything
  in it can be re-derived from what the runtime reports. Storage arrives when
  something needs to record what cannot be — an installed size, a download
  source, a benchmark's reference to a build since deleted.

`GET /api/v1/models?runtime_key=…` is live and resolves a **cold** model, which
is the point: the load-or-don't decision needs evidence about a build precisely
when it is not loaded. An unknown key is a 404 and nothing falls back to a
nearest match, because a fuzzy hit would be a guess wearing an identity's
clothes and RAVIS would route on it.

### The size tiebreak was ranking pools it had no business ranking

Spotted by a reader asking why the same model kept being selected. It was worse
than it looked: **eight of the thirteen pools resolved to `qwen/qwen3-1.7b`** —
the one model `clarvis/docs/benchmarks.md` explicitly never measured — and
`ravis/balanced` was selecting the smallest installed thing available.

The flaw was in the justification, not the arithmetic. "Among candidates a pool
already considers identical, the smaller is faster and cheaper" assumes the pool
has *expressed* something for them to be equal on. A pool with an empty `prefer`
considers everything equal, so size stopped being the last word and became the
entire ranking.

Now applied only where a pool declared a preference. `fast`, `performance`,
`coding` and the two Clarvis pools are unaffected and still select what they
should. The six that declare nothing — `auto`, `balanced`, `cheap`, `local`,
`api`, `private` — fall back to alphabetical order, which is meaningless and is
the honest state: they have no basis to distinguish on until M13, and
alphabetical is at least not systematically biased towards whatever is smallest.

**They still all select the same model**, and that is not fixed — it is
correctly reported. Inventing preferences their §5 descriptions do not imply
would be the opaque magic §9.4 forbids, in exchange for looking better.

### M7 — four rules, enforced by construction rather than convention

Stage 4's exit criterion is this milestone's acceptance, word for word, so each
clause is a property of the types rather than a promise about how they are used.

- **No scalar-only canonical score.** A `Measurement` is built from its
  repetitions and derives the headline. There is no constructor that accepts
  `38.4` and forgets where it came from, so no code path can produce a number
  whose samples were discarded. `EvidenceRecord` has no `score` field and no
  place to add one.
- **Every repetition is preserved.** §11.7 exists because Clarvis's own work saw
  identical prompt variants differ by ~32% and concluded a single take can
  measure noise rather than a difference. The samples publish alongside the
  summary, always.
- **Provenance can only weaken.** `combine` is a lattice: all measured stays
  `MEASURED`; any mixture becomes `PARTIALLY_MEASURED`; nothing measured falls
  to `ESTIMATED` or `UNKNOWN`. A record's provenance is *derived*, not a field,
  because a field could be written `MEASURED` onto a record holding an estimate.
  Aggregating nothing is `UNKNOWN`, not `MEASURED` — the vacuous-truth reading
  is the bug that function exists to prevent.
- **Absence never becomes zero.** A single take has a median and `spread: null`,
  because reporting `0.0` would claim perfect consistency measured once.
  Percentiles are absent below five samples, since with four takes a P10
  describes the sample rather than the thing sampled.

Two separations that would be easy to collapse and cost something if they were.
**Validity is not provenance**: a number can be genuinely `MEASURED` and still
`SUSPECT` because the machine was throttling, and merging them leaves a consumer
unable to tell "not measured" from "measured uselessly". **A verdict belongs to
a build, a config and a role** (§12.5), never to a family — the type carries a
variant's identity and there is no constructor that takes a family.

**No table and no endpoint were added.** M6 produces evidence and M16 serves it
to RAVIS; a schema with no writer is a claim nobody is keeping.

### What M8 settled, and one test that had to be thrown away

§9's exit names nine ways state can go wrong — concurrent, warm-reuse,
cold-start, load-failure, hung-inference, crash, stale-lease, cancellation,
exhaustion — and each is a test. The manager's whole value is being right when
something fails; when nothing fails, a bare adapter would have done.

Two decisions worth naming. **No lock is held across a load**: the runtime call
happens outside the critical section so a multi-minute load does not freeze
every other acquire, and what makes that safe is registering the in-flight load
*inside* the lock so a second acquire joins it rather than starting a second.
And **the load is shielded from cancellation**, because two clients can wait on
one load and the first giving up must not take the second down with it.

**`preempt` cannot currently do anything, and the first version of its test
hid that.** §9 names the policy, and the code reclaims an unreferenced holding —
but no such holding can exist, because release unloads at zero references. The
original test manufactured one by reaching into the manager's internals, which
made an unreachable branch look covered. A green tick over a state the system
cannot enter is worse than an admitted gap: it is the gap, plus a reason not to
look for it. The test now asserts the refusal that actually happens.

**`/api/v1/runtime/load` was removed, and it should never have existed.** §4.2
lists `/api/v1/runtime/sessions` and no such path; M4 invented one. The
ecosystem's governing rule forbids inventing another component's API, and
inventing one's own where the specification already names a shape is the same
mistake with a shorter blast radius.

### Audited before M6 — what the specification asks for and does not have

Checked the built surface against `SIRVIS.md` rather than discovering these
mid-milestone. **Four block M6.**

| Gap | Section | Blocks M6 |
|---|---|---|
| **No storage for results.** M7 built the schema and no table; §17 names `BenchmarkRun` and `BenchmarkResult` with cardinality — an Experiment has many runs, a run has one result per target | §17 | **yes** — M6's exit is "persists a valid result" |
| **No streaming generation.** M2's `generate` is deliberately a round trip; time-to-first-token cannot be measured without the first token's arrival | §11 | **yes** — TTFT is the headline metric |
| **No lightweight memory sampling.** M1's `detect_system` shells out and is far too heavy to run at §11.8's eight points around a single generation | §11.8 | **yes** |
| **No raw result directory.** §11.9's `results/<experiment-id>/` with responses preserved, so a rescoring does not need a rerun | §11.9 | **yes** |
| **No YAML.** `sirvis benchmark run basic.yaml` needs a parser this package does not depend on | §18 | yes, trivially |
| **The error model is wrong.** §4.3 specifies `{"error": {code, message, details, request_id, trace_id}}` with a named code list; the API currently returns FastAPI's default `{"detail": …}` | §4.3 | no — but it is shipped and wrong |
| **`sirvis doctor` never learned about M1.** §18 says it checks Apple Silicon, RAM, disk, LM Studio, installed models and the results directory. It reports configuration and migrations only — M1 landed and doctor was not revisited | §18 | no |
| **CLI is far from parity.** §18's `models list`, `runtime list`, `runtime sessions`, `results latest` all have APIs and no command | §18 | no |
| **`/api/v1/runtimes/{runtime_id}` and `/runtime-instances` are unbuilt**, and `/runtimes/{key}/models` is a path §4.2 does not list | §4.2 | no |
| **No job state machine.** §11.10's internal phases and coarse published enum, and "no result is visible before its snapshot and provenance commit atomically" | §11.10 | partly |
| **Parquet telemetry** is named for high-frequency data where SQLite becomes unsuitable | §17 | no — not at one-run scale |

The two that are shipped-and-wrong rather than merely absent — the error model
and doctor — are worth fixing before more surface is built on top of them.

### Next — in this order

| # | Milestone | Why here |
|---|---|---|
| 18 | **The gaps below** | Found by auditing the built surface against the specification. Four of them block M6 |
| 19 | **SIRVIS M6** | The benchmark engine. The first milestone that must load models to do its job |
| 15 | **SIRVIS M7** | The evidence schema — **its acceptance is verbatim Stage 4's exit criterion** |
| 16 | **M3b + M4 (RAVIS)** | The translated execution path and the Anthropic adapter. Permitted now that the transparent path is proven by something other than fixtures — and this is where tool-call framing actually gets hard. Runs in parallel; Stage 5 needs Stage 4 finished |

### After that

Stage 5 is the rest of RAVIS intelligence: **M7** (Google, OpenRouter), **M8**
(LM Studio, Ollama, generic adapters), **M13** (SIRVIS evidence), the rest of
**M14**, **M16** (policy). Stage 6 is NERVIS core — **M11** + **M15**.

**M3b and M4 are no longer blocked.** They were held back because §20.2 says
translation comes only after the transparent Clarvis slice works, and the
transparent path was the control — its correctness proven by nothing but
fixtures. That is no longer true as of M9.

---

## Reorderings made during the build

Each is recorded in `RAVIS.md` §20.1 with its reason. Listed together here
because a fresh reader will otherwise find only the result and wonder.

- **M3 split.** M3a is the adapter interface and capability discovery; M3b is the
  translated execution path. M6 cannot filter on capabilities without something
  that discovers them, so M3a is a Stage 3 prerequisite while M3b waits for
  Stage 5.
- **M14 split, observation half pulled to Stage 3.** Routing with no view of what
  is loaded picks alphabetically, and during M5 testing that evicted a
  deliberately-loaded model. Preferring what is already loaded needs no ownership
  of the runtime's lifecycle, which is why that half could move. The
  load-versus-don't tradeoff still waits for M11 and M13.
- **M18 split, read-only half pulled to Stage 3.** The prototype's Routes and
  Pools screens read exactly those endpoints, and a real route decision on screen
  is how M9 gets debugged. Deferring it meant nothing was observable until after
  the hardest integration was finished.
- **A slice of M8 pulled forward** — the LM Studio residency probe only, kept in
  `runtime/` rather than `providers/` so it is not mistaken for the adapter. M14
  is inert without a runtime that reports residency.

---

## Work that no milestone names

Three things here were built because something else needed them, not because a
milestone called for them. Listed separately from the deviations below because
they are not disagreements with the specification — they are additions to it,
and an addition nobody wrote down is how a plan quietly stops describing the
build.

- **CORS on the read surface.** Stage 3's *visible increment* is the prototype
  reading RAVIS's management API, and a browser cannot read a cross-origin
  response without `Access-Control-Allow-Origin`. The increment is in the
  runbook; the mechanism it requires was not.
- **`ravis preflight clarvis`.** M9's two failure modes are both silent from
  inside VS Code, and both cost an hour to diagnose the first time. Ten seconds
  of command beats an hour of confusion, but no milestone asked for it.
- **`model_capabilities_path`, and `ravis/measured-capabilities.json`.** §5.2
  makes operator declaration the only capability truth until M13, and the
  setting to do it already existed — but only as an environment variable. The
  honest version of this data records *where each claim came from* and needs to
  be diffable, which a shell blob is not.

Each is small, tested, and reversible. If any looks like scope creep rather than
groundwork, delete it — nothing in the milestone list depends on it.

---

## Deliberate deviations from the specification

These are decisions, not drift. Each is defensible and each is reversible; a
reviewer who disagrees should say so rather than assume it was an accident.

- **§7's `ProviderAdapter` was split in two.** Implemented literally, every
  transparent adapter would carry `complete` and `stream` that nothing calls —
  §6 forbids re-serialising an already-compatible stream — so they would raise
  `NotImplementedError`. Split into `ProviderAdapter` (discovery, every adapter)
  and `TranslatingAdapter` (adds the two, Path B only). *If this is wrong, change
  §7 rather than the code.*
- **Pool resolution rewrites the request's `model` field.** The only place the
  transparent path modifies what the client sent. A pool ID is not a model any
  upstream knows. The response stream is never touched.
- **A request's *estimated* context requirement does not fail closed on an
  unknown window, while a pool's *declared* minimum does.** A generic
  OpenAI-compatible endpoint publishes no windows, so failing closed on RAVIS's
  own arithmetic would make every large request unroutable. Surfaced as
  `unverified` in the route explanation rather than passed silently.
- **§10's circuit breaker is scoped by failure class, not only by provider.**
  §10 says "provider health", and with one provider that reading takes every
  model out of service the first time one model OOMs. So each failure class
  declares its own scope: a refused connection opens the *provider* circuit, an
  unloaded model or a local OOM opens only that *model's*. This is what makes
  falling back to the next candidate on the same upstream possible at all.
- **An authentication failure opens nothing.** It is scoped `NONE`, which looks
  wrong until you follow it through: opening the provider circuit converts a
  fixable 401 — which names the problem — into a 422 no-route, which does not.
  It is still counted in the health record; only the breaker ignores it.
- **A context overflow is rejected rather than escalated.** §10 says context is
  handled by routing to a larger-context model *or* rejecting, and the routing
  half is M6's pre-flight filter. The fallback chain is ordered by pool
  preference, not by context size, so the next candidate is not known to be
  larger — falling back to it would be a guess dressed as a recovery.
- **A directly named model never falls back, but its circuit is still
  honoured.** §5.3 puts an explicit request above inference, so the breaker is
  not consulted while *choosing* — substituting a different model would be
  exactly the second-guessing §5.3 forbids. It is consulted before *calling*,
  which is how a named model with an open circuit fails fast (503) instead of
  paying another timeout to rediscover what is already known.
- **SIRVIS's CSRF token is deferred, and the gap is stated rather than stubbed.**
  §4.5 asks for an origin check, a non-simple content type *and* a CSRF token on
  mutations. The first two are built and tested; the third needs a session to
  bind to, and SIRVIS has no sessions until the dashboard at M14. A token bound
  to nothing would look like protection and provide none, so it is absent and
  recorded. §4.5's own gate — unauthenticated refused, wrong-origin refused —
  is met.
- **SIRVIS's load and unload bypass a Resource Manager that does not exist.**
  §9 requires every load and unload to flow through it; M8 builds it. Until
  then these are the primitives it will wrap, with no reference counting and no
  ownership: an unload does not ask whether another client is mid-request. That
  is why both endpoints require the `runtime` scope rather than being open.
- **CORS is reads-only and never sends credentials.** A browser dashboard cannot
  read `/api/v1/*` without `Access-Control-Allow-Origin`, so the middleware now
  emits it — for an allow-listed origin only, echoed rather than `*`, and with
  `Access-Control-Allow-Credentials` deliberately absent, because that header
  plus an echoed origin is what lets a hostile page make *authenticated* reads.
  `GET`/`HEAD`/`OPTIONS` only: every `/api/v1` endpoint that exists is a read,
  and whoever adds the first mutation should decide about it deliberately rather
  than inherit permission from this line. One allowlist, read by both halves of
  the decision, so refusal and permission cannot drift apart.
- **`/api/v1/health` gained a `targets` array.** §15.1 lists the endpoint; this
  is what it now carries. Two kinds of health are reported separately on
  purpose: `upstream_reachable` is a live probe, `targets` is observed history.
  A provider can answer a probe instantly while failing every completion, and a
  single boolean would have to pick one of those to report.

---

## Open decisions — yours, not mine

- **A model's advertised context is not the context it is loaded with, and RAVIS
  is currently told the advertised one.** `ravis/measured-capabilities.json`
  declares `context_window: 32768` for `qwen2.5-coder-7b-instruct` — its
  maximum. LM Studio had it *loaded* at 8192. RAVIS's `clarvis-agent` pool
  requires a 32768 minimum, so it routed agent traffic there on the strength of
  a number describing a configuration that was not running.
  Nothing broke, and the reason is worth knowing: **LM Studio JIT-loaded two
  further instances at 32768** rather than reconfiguring the first, so three
  copies of one 7B model are resident. That is the runtime quietly spending
  memory to cover a mismatch, and it is what §9's Resource Manager (M8) exists
  to make visible and deliberate. It is also the most likely explanation for the
  4-second time-to-first-token measured during M9 against a benchmark figure of
  0.26s.
  The honest fix is M3 + M13: SIRVIS reports the *effective* configuration of a
  loaded instance, and RAVIS filters on that rather than on a model card. Until
  then, declaring 8192 would under-route and declaring 32768 over-promises.

- **Vision.** Clarvis has no image handling and NERVIS defers images to "Later".
  RAVIS filters on vision because it came free with the generic mechanism. An
  image-bearing request currently fails closed with a 422, which will look like a
  bug to whoever first pastes an image. Escape hatch: declare
  `"vision": "SUPPORTED"` in `RAVIS_MODEL_CAPABILITIES`.
- **Capability configuration is the only source of truth today.** Nothing probes
  (§8.7) and SIRVIS evidence is M13, so `ravis/clarvis-agent` is unroutable until
  an operator declares tools and a context window for at least one model. This is
  §5.2 working as written, not a defect, but it will surprise.
  **Answered for this machine** by `ravis/measured-capabilities.json`, derived
  from `clarvis/docs/benchmarks.md` — real executed tool-call trials, not LM
  Studio's `tool_use` flag, which disagrees with the measurements in *both*
  directions. Three models advertise nothing and score 3/3; `granite-4.0-h-tiny`
  advertises tool support in both packagings and scores 8/8 as GGUF against 1/8
  as MLX. RAVIS records all of it at CONFIGURED provenance, which is a
  *downgrade* from MEASURED and therefore safe — it never claims to have
  measured what it was told (§13.3 forbids the upgrade, not the downgrade). M13
  should replace this file rather than sit beside it.
- **Ranking now breaks ties on size rather than on the alphabet.** Both pools
  used to resolve to measurably poor choices — `clarvis-agent` took the 14B over
  a 7B that scores identically at three times the rate, and `clarvis-chat` took
  the model that needs 5.5s to reach a first token — and both were alphabetical
  accidents rather than judgements. Fixed in two places, neither of which
  invents a quality signal:
  **`clarvis-chat` declares a preference at all.** It had none, so selection was
  pure alphabetical order. §5.1 asks the pool for instruction following, so
  `prefer=("instruct", "chat")` is what the description already said. It narrows
  the field to a defensible class and deliberately does not rank inside it.
  **The last-resort tiebreak is now parameter count, smallest first.** §9.2
  lists "prefer fast" and "prefer cheap" among the soft preferences, and among
  candidates a pool already considers identical the smaller one is both —
  whereas alphabetical order carries no meaning at all. It is consulted after
  declared preference and after residency, never instead of them, the route
  explanation names it and calls it a tiebreak on cost rather than quality, and
  a name carrying no size sorts *last* rather than counting as zero. Mixture-of-
  experts names are read by their active parameters, since that is what decides
  how fast the thing answers.
  Both pools now select `qwen2.5-coder-7b-instruct`, which is what
  `clarvis/docs/benchmarks.md` recommends for both roles — reached from the
  pools' own stated intent rather than by copying the result, which is the only
  reason it is worth anything. Declaring a good model's rival tool-incapable to
  force the same ordering would have been lying about capability to buy a
  ranking, and remains the one thing not to do.
- **Where the orchestration layer lives.** Alexander Keisse's router does
  prompt-shaping, multi-pass and RAG that this ecosystem currently has nowhere.
  The proposal on the table is that it becomes a client *of* RAVIS rather than
  part of it. Undecided.

---

## Things that bit, so they do not bite twice

- `python -m pytest` puts the working directory on `sys.path`; bare `pytest` does
  not. CI runs it bare. `pythonpath = ["."]` in `pyproject.toml` now makes the two
  equivalent — CI was red for six merges before anyone noticed.
- `top`'s `PhysMem … unused` is **not** available memory on macOS. It once read
  619 MB against 8.6 GB genuinely free. `runtime/resources.py` sums the
  reclaimable page classes instead, and the comment explains why.
- A single SQLite connection cannot be used from FastAPI's threadpool, and
  `":memory:"` gives each connection a *private* database. Both are handled in
  `storage/database.py`; the tests that caught them are still there.
- `TestClient` buffers a whole response, so it cannot express a mid-stream
  disconnect. The cancellation test drives the relay generator directly, which is
  what Starlette actually does.
- `TestClient` must be used as a context manager or the lifespan never runs, the
  model catalogue is never warmed, and **every pool resolves to "no models
  available"** — a failure that looks like a routing bug and is a harness bug.
  `tests/test_fallback.py` says so where it builds its client.
- A lookup that creates. `HealthRegistry.of()` creates a record on first sight,
  which is right when something is about to be called and wrong when routing is
  merely *asking* about every model in the catalogue — the health snapshot
  filled up with rows for models nobody had ever called. `allows()` and
  `refusal()` are the non-creating pair, and the query/command split (§14.2) is
  the rule that was being broken.
- **A capability list went stale for four milestones.** Everything RAVIS
  advertised was written `unavailable` at M0 with the milestone that would
  change it, and then M1, M2, M5 and M18a shipped without anyone coming back.
  RAVIS spent Stage 3 telling every peer it could not do things it demonstrably
  could. Nothing broke, because understating is the safe direction — but a
  capability list that lags the build is one nobody can act on, and §4.1 exists
  so peers can. The test that should have caught it asserted `states ==
  {"unavailable"}`, which encoded M0's *situation* rather than the *rule*; it
  now asserts that `available` requires conformance. Two entries are `degraded`,
  which is a state worth keeping: `management` has its reads and none of its
  mutations, and `usage_cost` counts real traffic and knows no prices.
- **A typo in RAVIS's own namespace used to succeed.** `ravis/chat` is not a
  pool and has no second slash, so it was neither a pool ID nor a direct
  address — and fell through to the plain-model-name path, which forwards
  verbatim. LM Studio answered with whatever was loaded. The request *succeeded*
  with no pool, no capability filtering, no tool invariant and no fallback, and
  looked entirely fine; it also invented a health record for a model nobody has.
  Now a 422 naming the near-miss. §5.3's "an explicit request outranks
  inference" does not cover it, because `ravis/` is RAVIS's own namespace and no
  upstream serves a model called `ravis/chat`. The suggestion uses containment
  before edit distance: the real mistake is a dropped qualifier, and edit
  distance confidently proposed `ravis/cheap` for `chat`.
- **A cancelled stream used to leave its route decision looking unfinished.**
  Found by pointing the real Clarvis at a running gateway, not by a test. A
  mid-stream disconnect unwound out of the relay generator without running
  `finish()`, so `execution` stayed `null` — the same thing an in-flight request
  shows, which made "the user pressed Stop" and "this has been hanging for four
  minutes" indistinguishable on a dashboard. Now recorded as a `cancelled`
  attempt and re-raised, never handled: §10 still forbids the retry, and §8.6
  still needs the exception to propagate so the upstream stops generating.
  Neither call on that path awaits, which matters — an async generator that
  awaits after catching `GeneratorExit` raises `RuntimeError` instead of
  closing.
- **A streaming response the client never reads a byte of records nothing at
  all**, and cannot. Closing a not-yet-started async generator never runs its
  body, so no code inside it can observe the disconnect. `execution` stays
  `null`, correctly; the field's documentation says so rather than implying the
  request is still running.
- **Clarvis's `custom` provider probes with a placeholder model.** Before a
  model is chosen it sends `local-model`, which is `defaultModel` in
  `clarvis/src/model/providers.ts`. RAVIS answers with the upstream's 400,
  classified `model_unavailable`, and Clarvis carries on to list the catalogue —
  so this is noise rather than a fault, but it appears in the decision log and
  looks like a failure until you know what it is.
- **Clarvis's base URL must not end in `/v1`.** It appends the path itself —
  `${baseUrl}/v1/models`, `${baseUrl}/v1/chat/completions`, verified in
  `clarvis/src/model/OpenAiCompatibleProvider.ts`. A base of
  `http://127.0.0.1:8731/v1` produces `/v1/v1/models`, a 404, and a provider
  Clarvis reports as **offline** — a message naming nothing. `ravis preflight
  clarvis` prints the correct string so nobody has to remember this.
- One base URL serves both Clarvis roles. `ModelService.baseUrl` reads
  `chat.baseUrl.${spec.id}` — keyed by *provider*, not by role — so the
  chat/agent split is two model settings against one endpoint. That is what
  makes M9 configuration rather than a code change.
- `httpx.ConnectTimeout` is both a `TimeoutException` and a connection error, so
  `isinstance` order decides its classification. Reading it as a connection
  failure would retry the same target — §10 only permits that for a request that
  provably never arrived, and a connect *timeout* may well have arrived.

---

## Map of the repository

```text
ECOSYSTEM_RUNBOOK.md   cross-product authority — protocol, build order, gates, §14 engineering standards
RAVIS.md SIRVIS.md     per-product build plans; the runbook wins on anything crossing a boundary
NERVIS.md CLARVIS.md
ECOSYSTEM_OVERVIEW.md  conceptual, no contracts
nervis/                the prototype — every screen, wired to mocks shaped like the real responses
protocol/              ecosystem-protocol — the MEP surface and the logging vocabulary, shared
ravis/                 the routing gateway (M0–M18a, M12, M9)
sirvis/                the evidence plane (M0–M4, M7, M8)
```

Clarvis lives in its own repository (`../clarvis`) — different language, runtime
and release cadence, and `clarvis/plan.md` is normative for it.
