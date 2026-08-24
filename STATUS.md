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
.venv/bin/pytest                      # part of 725 tests, no network, no live service
.venv/bin/ravis conformance clarvis   # the §8.9 release gate — 16 checks
```

The other two packages are checked the same way, from their own directories:

```bash
cd protocol && ../ravis/.venv/bin/python -m pytest -q   # 15 tests
cd sirvis   && ../ravis/.venv/bin/python -m pytest -q   # 213 tests
```

**`ecosystem-protocol` must be installed first.** It is a local path dependency
and pip will not find it on PyPI, because it does not live there.

Expected: all clean, 725 passing across the three, conformance `PASS`. CI runs the same four on
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
| 18 | **The four gaps that blocked M6** | Results storage (§17), streamed generation so time-to-first-token exists, memory sampling light enough for §11.8's eight points, §11.9's raw result directory — and the trivial fifth, a YAML parser |
| 19 | **SIRVIS M6** | The single-model benchmark engine: §11.2's lifecycle end to end, warmups, repetitions, raw response capture, TTFT, throughput, memory. **Run live against all 19 installed builds**, done 2026-08-23, in a cooled and control-bracketed sweep — numbers below |
| 20 | **RAVIS M3b** | The translated execution path (§6, Path B): normalized events rendered as an OpenAI stream, the fork decided once by the addressed provider, and `execution_path` on every route decision. Transparent route still passes conformance — M3's exit criterion, verbatim |
| 21 | **RAVIS fast-switch hardening** | A reproduced livelock against a dead upstream, the pre-commit refusal gate — three signals that arrive while a model can still be swapped and were being thrown away — and the §8.7 probe poisoning that fixing them exposed, which needed a change in Clarvis's repository as well as this one |
| 22 | **RAVIS M4** | The Anthropic native adapter — the first real provider on Path B. Request and response translation, streamed tool calls, capabilities read from the model catalogue, and four mappings decided deliberately rather than by default. Settled below |
| 23 | **SIRVIS M9** | Runtime Sets — §10's versioned multi-model target. Definitions, revisions that only move when the definition does, role-addressable members, sessions opened against a set under one lease, and a fit estimate that can refuse but never approve. Settled below |
| 24 | **SIRVIS M10** | Multi-model benchmarks — §11.3's sequential, alternating and concurrent modes over a stored set, each member measured alone first as the control, and the interaction matrix with degradation percentages. §10's gate — a simultaneous-load failure is a result, never separate-model success — is code and test, not policy. **Run live against a GGUF/MLX pair, 2026-08-24** — the first measured pair in the corpus, numbers below |
| 25 | **SIRVIS M16** | The RAVIS evidence API — §15.1's question as an endpoint, with every filter it names, cursor paging, staleness, stable references, and candidate runtime keys resolved by SIRVIS so RAVIS never infers equivalence. Nothing ranks. Found the role-vocabulary seam, below |
| 26 | **SIRVIS M12 + M13** | Clarvis's benchmark assets inspected and **wrapped, not rewritten** — the eight phrasings, the streamed index-keyed assembly, and the F17 follow-up turn, all from `clarvis-firstrun/tools/suite2.py`. Evidence is now filed under `clarvis-chat` and `clarvis-agent`, and `TrialRate` has a producer for the first time since M7 declared it. Settled below |
| 27 | **RAVIS M13** | SIRVIS evidence consumption — §13's identity kept whole, §13.2's two-axis threshold applied, §13.3's provenance never upgraded, and §13.4's seven pairwise fixtures each producing their own answer. **Stage 5's exit criterion met**: a SIRVIS result changed a RAVIS preference. Settled below |
| 28 | **SIRVIS M15** | The recommendation engine, and Stage 4's last piece. §14.3's weighted score computed without breaking §12.2's prohibition — every score carries the **coverage** it rests on, and on this machine that is 45%. Settled below |

**Stages 0, 1, 2, 3 and 4 are complete.** Stage 4's last piece was SIRVIS M15; the evidence plane now measures, stores, serves, and recommends.

**Stage 5 has started out of order, and by now substantially.** M3b, M4 and RAVIS M13 all belong to it and are all done — the last of them met Stage 5's own exit criterion, a SIRVIS result changing a RAVIS preference, before Stage 4 finished. That is not drift: each was unblocked early and the reason is recorded under *Reorderings made during the build*.

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

### What the switchboard could actually see, and what it was throwing away

Started as a question rather than a milestone: *if RAVIS cannot tell whether a
tool call worked, how does it switch models before the client sees an error?*
The answer turned out to be that the switching window already existed and was
being wasted.

**The window.** From `chain.begin(model)` until the first chunk is read from
httpx, `_TryNext` walks the fallback chain and the client — holding a 200 status
line and zero bytes — sees only whichever model answers. The status is committed
before any upstream is contacted, so a switch is invisible at the HTTP level.
Nothing new was needed to switch silently.

**Widening it was tried and refuted.** A hold-back buffer, implemented against
the real relay, turns a *succeeding* stream into a client-visible error: to
switch it must discard its buffered bytes and raise `_TryNext`, which is
one-way, so a guess with no healthy fallback leaves the client with an error
frame and nothing else. It also degrades the shipped non-buffering conformance
check, and `RAVIS.md` §6 forbids buffering to completion in terms.

**And the tool-call signal is unavailable in principle.** This repository's own
recorded fixtures show the first frame of a perfect tool-calling stream and the
first frame of a plain-chat stream are the same bytes — `{"delta":{"role":
"assistant"}}`. Clarvis's parser says why: nothing marks a tool call finished
mid-stream, so its arguments are known whole only once the stream is. The one
measured incapacity on this machine — `granite-4.0-h-tiny` as MLX, 1/8, its
arguments eaten by the runtime's parser — is invisible before the commit point
by construction.

So the gate catches **refusals**, which is a smaller claim and an achievable
one. Three signals arrive inside the existing window and were discarded: a 200
whose body is an error object, a 200 whose first SSE frame carries one, and a
200 that closes without a byte — the last recorded as a *success*, leaving the
client waiting for a `[DONE]` that never came. The first chunk is now inspected
before it is forwarded: it is already in hand, so nothing is held back and no
latency is added.

The same hole existed on the non-streaming path, which is the worse half —
Clarvis's §8.7 tool probe is a non-streamed request that reads any 2xx as "this
model supports tools" and caches it for the session, so a lying 200 taught it
the opposite of the truth. Both paths now share one definition of a refusal.

**Learning from use is the other half, and it is post-commit.** Pre-commit
switching can only ever catch refusals; whether a model did the job well is
knowable only at the end of a stream, and only by Clarvis. `RAVIS.md` §13.5
already describes recording success and error rate from real traffic and already
names Clarvis reporting task outcomes — scheduled at M19, Stage 10, which is
last. If routing that improves with use is the point of the system rather than a
polish item, that ordering is worth re-deciding.

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

### The test suite was loading models on the developer's machine

Runbook §14.5 says no test reaches a live model, a network or a real service.
That was true of the HTTP client and quietly false everywhere else, and it cost
the user four unwanted model loads before anyone connected the two.

Three separate holes, and the shape of the mistake is the same each time — a
guarantee that held for the channel somebody was thinking about:

- **`Settings()` defaults `lmstudio_base_url` to the real LM Studio.** Any test
  building an app from defaults had a live adapter wired into it.
- **Lifecycle does not go over HTTP at all.** LM Studio exposes no load or
  unload, so the adapter shells out to the `lms` CLI — which means pointing the
  base URL at a dead port protects reads and nothing else. This was the hole
  that actually loaded the models.
- **The Resource Manager captures the adapter it is constructed with.** Tests
  built the app and *then* replaced `app.state.lmstudio`, leaving a live adapter
  inside the manager. One test posting to `/runtime/sessions` loaded a real
  model.

Closed three ways, on the principle that the guard should not depend on anyone
remembering it. An autouse fixture pins both channels for every test; `Settings`
gained `lmstudio_cli_path` so the second one is configurable at all; and
`create_app` now takes its runtime, so no moment exists at which a live adapter
could be left behind. The last of those is the belt: `_run_lms` is patched to
fail the test loudly — but only when a real binary would actually have been
invoked, because two tests exist to assert the adapter's own refusal when no CLI
is installed, and a guard that pre-empts the code under test replaces a real
assertion with its own.

**The first attempt at that guard patched `subprocess.run` on the module**,
which is global, and broke M1's machine detection — which shells out to `sysctl`
and has nothing to do with runtimes. A guard that disables unrelated code is a
guard that gets deleted.

### M6 — the engine, and the four things that had to exist first

The audit below named four gaps that blocked this milestone. Each is now built,
and each turned out to be load-bearing rather than bureaucratic:

- **Streamed generation.** M2's `generate` is a round trip and stays one.
  Time-to-first-token is §11.4's headline metric and is not recoverable from a
  response that arrives whole. The streaming path re-meets LM Studio's
  200-with-an-error-body trap in a new disguise: a refused request does not
  arrive as SSE at all, so a parser that skipped non-`data:` lines would report
  it as a model that produced nothing.
- **Memory sampling at a weight that does not distort what it measures.** M1's
  `detect_system` shells out four times and takes hundreds of milliseconds;
  §11.8 wants eight readings around one generation plus a poll while it runs.
  One `vm_stat` per sample, page size read from its own header, total memory
  cached because it does not change. It repeats RAVIS's arithmetic — free +
  inactive + speculative + purgeable — rather than importing it, because
  runbook §3 permits sharing only the protocol package.
- **Results storage.** `experiment`, `benchmark_run` and `benchmark_result`,
  with §17's cardinality enforced by a unique constraint rather than remembered:
  a run has exactly one result per target. Results and the run's terminal state
  commit in one transaction, which is §11.10's "no result is visible before its
  snapshot and provenance commit atomically" made mechanical.
- **§11.9's raw directory.** `results/<experiment-id>/` with the specification,
  the machine, the runtime, every response including warmups, telemetry and a
  log. Raw responses are what make a rescoring cost an afternoon instead of a
  week of GPU time.

**What the engine refuses to do is most of what it is.** A token count nobody
reported is never called measured — without `stream_options.include_usage` the
only available number is a count of stream chunks, so throughput derived that
way publishes at `ESTIMATED` and §12.1's lattice weakens the whole record. A
warm model publishes **no load time at all**, because timing an already-
satisfied acquire would produce a figure three orders of magnitude too fast that
would look like the best result in the table. A model that returns nothing is a
result with a warning rather than an error, because M2 found `qwen3-1.7b`
spending an entire budget on reasoning: the call succeeded and the model said
nothing, and those are two facts. A metric missing from even one repetition is
omitted rather than summarised over the ones that worked.

**Acquiring a warm model used to load a second copy of it, and a test found it.**
The Resource Manager tracked only what *it* had loaded, so acquiring something
LM Studio already held issued a load — and LM Studio does not reconfigure a
resident model, it loads another instance. That is the mechanism behind three
copies of one 7B model being resident during M9. Instances are now **adopted**:
tracked, counted against capacity, reported with `owned: false`, and never
unloaded here, which is §11.2's "unload **if owned**" read literally.

### What the live runs measured

**The corpus that counts is the cooled one.** Every earlier sweep mixed engine
versions, ran builds back to back on a fanless machine, and measured the cooling
system as much as the models. This one is 21 measurements on one engine in one
sitting: a control build first, all 19 installed builds, then the same control
again — each preceded by cooling to `nominal`, which took 50–110 seconds.

| Build | tok/s | TTFT | Load | Notes |
|---|---|---|---|---|
| `qwen3.5-2b-mlx` | **105.8** | 0.118 s | 3.51 s | loaded at 262144, not the 8192 asked for |
| `granite-4.0-h-tiny` mlx | 97.0 | 0.204 s | 3.77 s | |
| `lfm2.5-2.6b-mlx` | 93.8 | 3.146 s | 3.91 s | adapted; TTFT is mostly thinking |
| `qwen3-1.7b` | 66.1 | 0.225 s | 3.12 s | adapted |
| `granite-4.0-h-tiny` gguf | 59.9 | **0.049 s** | 1.47 s | the control build |
| `exaone-deep-2.4b` | 58.8 | 0.034 s | 0.92 s | measuring reasoning prose, not an answer |
| `phi-4-mini-instruct` | 55.4 | 0.169 s | 3.57 s | |
| `qwen3-4b-2507` | 52.6 | 0.177 s | 3.03 s | |
| `hunyuan-1.8b` | 52.0 | 0.090 s | 1.23 s | adapted |
| `smollm3-3b` | 50.2 | 0.035 s | 0.71 s | adapted |
| `qwen2.5-coder-7b` | 31.4 | 0.282 s | 2.96 s | |
| `meta-llama-3.1-8b` | 29.4 | 0.249 s | 3.51 s | |
| `ministral-8b` | 28.7 | 0.271 s | 4.10 s | |
| `qwen2.5-coder-14b` | 15.6 | 0.523 s | 4.38 s | |
| `devstral-small-2507` | 9.6 | 0.698 s | 7.51 s | 13.28 GB resident |
| `gemma-4-e2b`, `gemma-4-e4b` | — | — | 5.8 s | no answer; 253 of 256 tokens spent thinking |
| `bonsai-27b` | — | — | 6.45 s | no answer; 255 of 256 |
| `deepseek-r1-distill-1.5b` | — | — | 1.18 s | no answer; 254 of 256 |

**The control bracket is the most useful number here, and it says the opposite
of what was expected.** Opening control 56.0 ± 0.9, closing control **70.6 ±
10.6** — the machine finished 26% *faster*, and the closing run's own spread was
±15% against the opening one's ±1.6%. Drift on this hardware is not monotonic
decline that cooldowns drain away; it is instability whose magnitude itself
varies. Cooling to `nominal` first did not stop **9 of the 21 runs** crossing
`nominal → fair` during their own five repetitions, which is why the flag
records per-run rather than per-sweep.

So the reading rule survives the mitigation: **gaps of a few percent in this
table mean nothing.** 105.8 against 9.6 is real. 55.4 against 52.6 is not, and
neither is any pair inside about 25% of each other. Comparing builds at finer
resolution needs repeated experiments over time (M20), not more repetitions
inside one sitting.

**The format pair is the point, and it splits in opposite directions.** Same
family, same weights, two packagings: MLX generates at **1.9× the GGUF's rate**
and takes **3.4× longer to reach its first token**. Neither is "the faster
build". §12.2 makes format part of evidence identity, and this is why — the two
carry different evidence IDs and there is no honest way to average them.

Measured in the same sitting this time, so the comparison is between builds
rather than between machine states: **97.0 against 59.9 tokens/second**, and
**0.204 s against 0.049 s** to first token. MLX generates 1.6× faster; GGUF
reaches the first token 4.2× faster. Both directions held across every sweep
that has ever measured them, at magnitudes that did not.

It matters more than a speed table, because `clarvis/docs/benchmarks.md` has the
same pair at **8/8 GGUF against 1/8 MLX** on tool calls. The faster build is the
one that cannot be trusted with the agent role, which is §12.5's "a verdict
belongs to a build, a config and a role" arriving as data rather than as a
principle. Note the provenance difference: the throughput here is `MEASURED`,
while those tool-call rates stay `CONFIGURED` until M13 measures them.

**Two of the ten produced no answer at all**, and the raw responses say why.
`qwen3-1.7b` and `lfm2.5-2.6b-mlx` both finished on `length` having generated
**255 tokens and zero content chunks**: reasoning models that spent the whole
budget thinking and never began answering. M2 saw this at a cap of eight tokens
and assumed the cap; it survives at 256, so it is the models. Both runs are
`SUSPECT` with a note, and **no TTFT or throughput was published for either**,
because a metric missing from even one repetition is omitted rather than
summarised over the ones that happened to work.

Two things follow. The token counts prove the work happened, so "no content" is
not "no computation" — and this engine measures *answers*, so whatever those 255
tokens cost is real and unmeasured. Reasoning throughput is a metric §11.4 does
not name and these two builds need. And a 256-token cap cannot measure them at
all, which is a *different experiment* rather than a repair to this one:
§11.4's own performance workload says 256 output, so raising it silently would
make the numbers incomparable with every other row above.

**Both are measurable now, by asking differently — and the engine does it.**
A build that answers nothing is a diagnosis rather than a verdict, so when every
warmup comes back empty the engine tries each declared suppression once and
measures with whichever produces an answer. The warmups are the probe: they
already run, §11.7 already excludes them from the statistics, and a build that
said nothing in all of them is about to say nothing five more times.

| Build | As asked | Adapted | Suppression | Thinking tokens |
|---|---|---|---|---|
| `qwen3-1.7b` | no answer | 61.9 tok/s · 0.218 s | `no_think_suffix` | none — it stops |
| `lfm2.5-2.6b-mlx` | no answer | 78.6 tok/s · 3.351 s | `direct_system` | 228 per repetition |

The two rows are not the same result. `/no_think` genuinely stops `qwen3-1.7b`
thinking, so its content chunks account for its whole token count. The system
instruction only gets `lfm2.5-2.6b-mlx` *answering while it still thinks*: a
median of **228 tokens per repetition never arrive as content**, its 3.35 s
time-to-first-token is mostly thinking, and its throughput covers the answer
alone. Both facts are in the record; neither is inferred from a model's name.

Nothing is hidden by any of this. An adapted run is `SUSPECT`, carries a note
naming the suppression, and puts it **in the evidence identity** — §12.2 keys
evidence on the configuration a number came from, and a prompt that had to be
changed to get an answer is a different configuration. `suppress_thinking: none`
turns it off for anyone measuring a build strictly as asked.

`examples/no-think.yaml` remains the *declared* version of the same question —
a separate suite rather than a flag on `basic.yaml`, because §11.5 freezes a
suite's prompts once results exist and results now exist.

**And `lfm2.5-2.6b-mlx` cannot be told to stop, which is settled rather than
suspected.** Its chat template opens every assistant turn inside the thinking
block, unconditionally:

```jinja
{%- if add_generation_prompt -%}
    {{- "<|im_start|>assistant\n<think>" -}}
{%- endif -%}
```

Its one template variable, `preserve_thinking`, governs whether *earlier*
messages keep their thinking in the history — not whether the model thinks now.
There is no switch, and LM Studio ignores `chat_template_kwargs` regardless. Two
things do work and one only appears to: a larger budget lets it finish thinking
and answer (289 tokens, `finish=stop`), the system instruction gets it answering
inside 256, and **prefilling a closing `</think>` is a trap** — reasoning drops
to zero and content jumps to 979 characters, because the thinking is now landing
in the content field. Any metric counting content length would score that a
success.

Worth recording as a check on the schema: the two `performance-no-think` runs —
one warm, one cold — produced **the same evidence ID** and agreed to within
0.03% on both throughput and TTFT. Two results, one evidence identity, which is
exactly what §12.2 says should happen and the first time it has been observed
rather than asserted. The warm one is `SUSPECT`, because a warm acquire says
nothing about load time and the engine refuses to publish one.

**A 24B fits on 24 GB, and the reason it nearly did not is instructive.**
`devstral-small-2507` loads at 13.28 GB and runs at **8.9 tok/s with a 0.716 s
TTFT** — the slowest build measured here, below the 14B. It would not load until
active and inactive pages were freed, and **no amount of swapping would have
helped**: on Apple Silicon a model's weights are *wired*, so the machine went
from 2.75 GB wired at idle to 15.69 GB with the model resident, and wired memory
is the one kind macOS cannot page out. "It will swap the rest" is the intuition
to unlearn — the thing needing room is the thing that cannot be moved.

**That run was also the thermal flag's first live firing**, and the data agrees
with it. The warning says pressure went from `nominal` to `fair` during the run;
the throughput spread came out at **±11%** against the ±1–2% every other build
produced. A 24B heats this machine inside a single benchmark, and without the
flag the result would have read as a model with unusually erratic throughput
rather than a machine that got hot halfway through. It also confirmed the
Resource Manager's adoption rule against a real runtime: the model had been
loaded outside the benchmark, so the run tracked it as `owned: false`, published
no load time, and left it resident afterwards.

**It also settled the M9 mystery.** `qwen2.5-coder-7b-instruct` reaches its
first token in **0.296 s** here against the ~4 s measured during M9, and against
the 0.26 s in `clarvis/docs/benchmarks.md`. The difference was never the model:
M9 ran with three JIT-loaded instances of it resident, because RAVIS was told
the advertised 32768 context and LM Studio would not reconfigure the running
copy. See the open decision below, which this confirms rather than resolves.

### Which model families can be told to stop thinking

Asked because two builds on this machine could not be measured at all, and
answered by **reading chat templates rather than downloading weights** — a
template is 5 KB and settles the question for a whole family, where a model is
gigabytes and settles it for one build. Downloads were needed only where a
template *cannot* answer.

The mechanism turns out to be mechanical. Two lines of any template say which of
five cases a build is in:

| Template shape | Can thinking be turned off? |
|---|---|
| Opens the assistant turn **inside** `<think>` | **No.** The model has no choice — LFM2.5 |
| Opens it empty, injects a pre-closed `<think></think>` on request | A real switch, but LM Studio ignores `chat_template_kwargs`, so not that way |
| **Parses a marker out of the prompt** and writes the switch itself | **Yes, through any API** — GLM, SmolLM3, Nemotron v2 |
| No thinking logic at all | Nothing to turn off — Granite 4.0, Llama, Mistral, Phi-4-mini |
| A dedicated reasoning model | **No.** It is the product — R1-distill, EXAONE-Deep, GLM-Z1, Kimi-Thinking |

Probed here, every mechanism against every build — baseline, `/no_think`,
`/nothink`, a plain "answer directly" system prompt, Nemotron's `detailed
thinking off`, and the template kwarg:

| Build | Thinks? | What works |
|---|---|---|
| `qwen3-1.7b` | yes | `/no_think`, `/nothink`, either turn, **and a plain instruction** |
| `tencent/Hunyuan-1.8B` | yes | `/no_think` |
| `smollm3-3b` | yes | `/no_think` **in the system message only** |
| `gemma-4-e2b`, `gemma-4-e4b` | yes | nothing — 77 of 79 tokens spent thinking |
| `bonsai-27b` | yes | nothing |
| `exaone-deep-2.4b` | yes | nothing — and it does not delimit its thinking at all |
| `lfm2.5-2.6b-mlx` | yes | nothing stops it; an instruction gets it answering *while still thinking* |
| `deepseek-r1-distill-qwen-1.5b` | yes | **nothing** — all six failed, no content in any |
| `qwen3.5-2b`, `qwen3-4b-2507` | no | — |
| `granite-4.0-h-tiny`, `phi-4-mini`, `ministral-8b`, `llama-3.1-8b` | no | — |

**The same marker in the wrong turn does nothing.** SmolLM3's template looks
for `/no_think` in the *system* message and nowhere else —
`{%- if "/no_think" in system_message -%}` — so the user-turn suffix that works
on Qwen3 and Hunyuan is invisible to it. Putting `/think` there instead forces
thinking back on, which is how that reading was confirmed rather than assumed.
The two spellings are separate strategies in the engine for exactly that reason.

**And thinking arrives in three shapes, not two.** Some runtimes route it to
`reasoning_content`, where this engine never sees it. Some emit it as content
inside `<think>` tags, which the engine now splits off. `exaone-deep-2.4b` does
neither: it reasons in **undelimited prose** — *"Okay, I need to... Let me think
about how to approach this"* — with no field and no tag to separate it from the
answer. Its benchmark is therefore `VALID` and its 32.2 tok/s is a real token
rate, but the tokens are reasoning rather than an answer, and **no structural
check can tell**. Distinguishing them needs something that can judge whether the
output answers the question, which is §11.6's evaluators and M18. Stated as a
limit rather than papered over.

**A template default is not a model's behaviour.** Gemma-4's template sets
`enable_thinking` to `false` unless asked, which reads like a family that does
not reason — and produced the wrong prediction. Both Gemma-4 builds spend their
whole budget thinking: the probe returned `reasoning_content` beginning
*"Thinking Process: 1. Understand the Goal"* and a usage block reporting **77
reasoning tokens out of 79**. The default governs what the *template injects*,
not what the weights do. Reading a template settles what a runtime can be told;
only running the model settles what it does.

That probe also found a number this engine had been inferring. LM Studio reports
`usage.completion_tokens_details.reasoning_tokens` — exactly the quantity the
chunk-count threshold exists to approximate. It is used where offered and the
threshold stays for runtimes that say nothing, because `None` and `0` are
different claims and only the second licenses trusting the total.

**One correction worth keeping.** Qwen3's template never parses `/no_think` — it
knows only `enable_thinking`. The suffix works because the *model* was trained
to honour it, which is also why a plain instruction works just as well and why
GLM's `/nothink` spelling works on a Qwen model. A convention can live in the
template, in the weights, or in both, and only trying it tells you which.

Settled from templates without running anything: DeepSeek-V3.1 and SmolLM3 carry
real conditionals; Granite 3.3 has thinking **off** by default with a variable to
turn it on; MiniMax-M2, Kimi-K2-Thinking, GLM-Z1, EXAONE-Deep, Phi-4-reasoning
and Qwen3-Thinking always think. Nemotron needed two templates rather than one:
**4B v1.1** defaults its system message to `detailed thinking off`, and **9B v2**
strips `/think` and `/no_think` out of the content itself. Gemma 3, Llama 3.2,
Magistral and Cogito are gated or moved and could not be read.

`nvidia/Llama-3.1-Nemotron-Nano-4B-v1.1` in GGUF **will not load on this
machine** — `llama-server` exits before becoming healthy, an architecture this
LM Studio build does not have. Recorded because it is a real limit of this
setup, not of the model.

### Audited before M6 — what the specification asks for and does not have

Checked the built surface against `SIRVIS.md` rather than discovering these
mid-milestone. **Four block M6.**

| Gap | Section | Blocks M6 |
|---|---|---|
| ~~**No storage for results.**~~ **Fixed.** `experiment`, `benchmark_run` and `benchmark_result`, with §17's one-result-per-target as a unique constraint and §11.10's atomic commit in one function | §17 | done |
| ~~**No streaming generation.**~~ **Fixed.** `stream_generate`, with the 200-with-an-error-body trap handled in its streaming form | §11 | done |
| ~~**No lightweight memory sampling.**~~ **Fixed.** One `vm_stat` per sample, plus a watcher that polls while a generation is in flight — a peak sampled only at the ends is not a peak | §11.8 | done |
| ~~**No raw result directory.**~~ **Fixed.** §11.9's layout, including warmup responses: a warmup that failed explains a measured run that looks strange | §11.9 | done |
| ~~**No YAML.**~~ **Fixed.** `pyyaml` declared explicitly rather than relied on transitively through `uvicorn[standard]` | §18 | done |
| ~~**The error model is wrong.**~~ **Fixed.** §4.3's shape and its closed code list, with correlation IDs attached at the single translation point so no raiser can forget them | §4.3 | done |
| ~~**`sirvis doctor` never learned about M1.**~~ **Fixed.** Machine, memory, disk, thermal, database, results directory, and the runtime — which it now contacts, reporting an absent one as a finding rather than a failure (§15.4) | §18 | done |
| **CLI is still short of parity.** `benchmark run` and `results latest` exist now; §18's `models list`, `runtime list` and `runtime sessions` all have APIs and no command | §18 | no |
| ~~**Benchmark runs and results are stored and unserved.**~~ **Fixed.** `/benchmark-runs`, `/benchmark-runs/{id}` and `/benchmark-results/{id}` — §17 says these are stored *because* they are served, and until now they were not | §4.2 | done |
| **`/api/v1/runtimes/{runtime_id}` and `/runtime-instances` are unbuilt**, and `/runtimes/{key}/models` is a path §4.2 does not list | §4.2 | no |
| **No job queue.** §11.10's coarse enum, its atomic commit, and — now — the unrecoverable half of "survive a restart or be marked unrecoverable" are built. The queue itself is not: no submit, pause, resume, retry or reorder, and nothing *resumes* an interrupted run | §11.10 | partly |
| **Parquet telemetry** is named for high-frequency data where SQLite becomes unsuitable | §17 | no — not at one-run scale |

Everything shipped-and-wrong on that list has been fixed. What remains is
absent, which is the cheaper kind of gap: nobody is building on top of it.

### What M9 settled, and one thing it deliberately cannot do yet

§10's gate is three claims — *two models stay loaded and independently
addressable; two revisions distinguishable; old results retain their revision* —
and the third is the one that is easy to believe and hard to keep.

**A revision row is written once and never updated.** There is no `UPDATE`
against `runtime_set_revision` anywhere, and adding one would silently rewrite
history that has already been cited. Everything else follows from that: `save`
does not mean "write these fields", it means *find the revision whose definition
matches, or add the next one*. So re-saving an unchanged definition returns the
revision it already had and writes nothing — a UI that saves on every keystroke,
or a script re-applying the same YAML, must not walk the number upward while the
combination stays the same. **A version that increments for no reason is a
version nobody reads.**

The other decisions, each of which had an obvious alternative that is wrong:

- **A set's identity is its name; the revision is which definition it currently
  means.** `clarvis-balanced` keeps one `runtime_set_id` while its membership
  changes, which is what makes "how has this set performed over time" a question
  with an answer. Hashing the membership into the ID would make every revision a
  different set.
- **Two members cannot claim one role.** Refused at definition time, not
  discovered at load time with half the set already resident. "Independently
  addressable" is exactly what a duplicate role breaks: which one is *the* chat
  model?
- **Load order defaults to declaration order and is part of the hash.** Largest
  first would allocate more predictably, but §11.2 loads role A then role B and
  the order changes what a multi-model run measures — so chat-then-agent and
  agent-then-chat are different definitions, not two spellings of one.
- **A session resolves the revision once.** §10 calls a set immutable *at use*;
  editing the set while a session is open must not change what that session is a
  session of, so the revision is pinned when the session opens and reported back.
- **The fit estimate can refuse and can never approve.** `REFUSED` means the
  weights *alone* already exceed the machine, which is arithmetic. `PLAUSIBLE`
  means only that this one obstacle is absent — it is never permission, because
  §10.1 is precisely the warning that separate fits do not compose. One unknown
  size makes the whole total unknown rather than smaller: a sum missing a term is
  not a sum, and treating an unrecorded size as zero would approve a set that
  cannot load.

**And the thing it cannot do yet, stated plainly because the code looks like it
can.** Nothing in SIRVIS records an installed size — `LocalModel.installed_size_bytes`
is declared and never populated, because LM Studio's catalogue does not report
one — so on this machine `estimate_fit` returns `UNKNOWN` for every set that
exists. The refusal path is real and unit-tested against known weights; it is
inert against the live inventory. That is why a session is *not* blocked by an
estimate that cannot be made: refusing on an absent number would make every set
unloadable on the strength of arithmetic nobody could do. Whatever captures
installed sizes makes this live, and until then the endpoint honestly answers
`{"verdict": "UNKNOWN", "basis": "estimate"}`.

Two smaller limits worth knowing before writing a set: the Resource Manager
holds **two** models by default (`DEFAULT_MAX_LOADED`), so a three-member set is
a `RESOURCE_BUSY` rather than a load; and "old results retain their revision" is
so far only half-demonstrated — the storage guarantees it, and no result cites a
set until M10 produces one.

**Verified live on 2026-08-24**, by M10's first real run. Both members of
`clarvis-balanced` were resident at once, each addressable by its role, each
loaded at the 8192 context its member declared — which is the one thing the fake
could never prove, because a fake accepts whatever configuration it is handed.
The numbers are under M10 below.

### What M10 settled

§10's gate has two clauses and only one of them is about producing something:

    Interaction matrix produced; a simultaneous-load failure is recorded as a
    result, not converted into separate-model success.

**The second clause is the hard one, and the reason is timing.** The alone phase
runs first and succeeds. So at the moment co-residency fails, there is a
directory full of good measurements sitting there, and the wrong behaviour is
not a crash — it is a *plausible success*. A run that reported those numbers
would be stating that the combination works, on the strength of evidence that
each model works by itself, which is precisely the inference §10.1 exists to
forbid. `_load_together` refuses it: the failure is persisted as a result, the
alone measurements are kept and labelled alone-only, the matrix reports
`complete: false` with the co-resident columns *absent* rather than filled, and
the run finishes FAILED. Three tests hold each half of that.

The decisions that shaped the rest:

- **Alone is measured in the same run, not borrowed from M6's corpus.** Reusing
  those numbers would be cheaper and wrong: they were taken on another day, at
  another thermal state, against another background, so a degradation
  percentage computed across them measures the week rather than the
  co-residency. The run pays three times the generation cost to make its own
  comparison internally valid, and the matrix says `basis: measured in this run`
  so nobody has to guess which it was.
- **Co-residency is part of the evidence identity.** It rides in
  `runtime_configuration`, which §12.2 already makes identifying, so the same
  build measured alone and measured beside another model produce different
  `evidence_id`s. Without that they would be one key holding two different
  numbers — the corpus disagreeing with itself, which is the failure this
  repository already had once when the identity recorded what was asked for
  rather than what ran.
- **A result is keyed by role, not by model.** §17 allows one result per
  ExperimentTarget, and for a set the target is the role: two roles could name
  one build, and two results about "that build" would collide on the run's
  unique constraint for a reason invisible from outside.
- **Concurrent mode gathers rather than task-groups.** A set where the agent
  falls over under concurrent load while chat keeps answering is exactly the
  finding the mode exists to produce; cancelling the survivor would destroy the
  evidence for it. The failure becomes a warning and the survivor is measured.
- **Alternating interleaves by repetition**, not by member. Running each
  member's repetitions in a block would let the runtime settle between switches
  and measure nothing about switching, which is the whole cost the mode is for.
- **Every measurement primitive is M6's.** A member carries a real
  `ExperimentSpec`, so a role's alone phase is *literally* a single-model run —
  warmups, thinking suppression, TTFT splitting and token accounting are the
  same code, not a second implementation free to drift.

### The first measured pair, and what it found

Run live on **2026-08-24**, `clarvis-balanced` revision 1 on the 24 GB machine:
`qwen2.5-coder-7b-instruct` (MLX 4-bit) as **chat** and
`lmstudio-community/granite-4.0-h-tiny` (GGUF Q4_K_M) as **agent**, both at 8192
context, two warmups and five measured repetitions per condition. Run
`run_76509c018d5d4bd6`, raw material in `sirvis/results/exp_f7b0dea6b9af4889`.
This is §21.1's second vertical slice, and the corpus's first pair.

```text
                     Alone    Sequential   Alternating   Concurrent
agent tok/s           67.0          67.0          57.4         45.6
chat  tok/s           30.0          29.7          29.2         17.2
agent TTFT            0.05s         0.05s         0.13s        0.06s
chat  TTFT            0.29s         0.30s         0.37s        0.46s
```

**Co-residency itself is free.** Sequential — both models resident, one running
at a time — costs agent **-0.0%** and chat **+0.9%** throughput. That is the
result worth having, because it is the one a single-model benchmark cannot
produce and the one an operator most needs: keeping a second model warm does not
tax the first.

**Concurrency is not.** Running both at once costs chat **42.7%** of its
throughput and agent **31.9%**, with time-to-first-token up 57% and 37%. The
asymmetry is consistent — the slower model gives up more — and it is the figure
a router should care about, because it is the difference between "both fit" and
"both work".

**The switch has its own price, and it lands on latency rather than
throughput.** Alternating cost agent only 14% of its throughput but tripled its
time-to-first-token — **+196%**, 0.05s to 0.13s. A chat/agent handoff pattern
pays that on every turn while barely showing up in tokens per second, which is
exactly the kind of cost a throughput-only benchmark reports as nothing.

**Memory behaved as arithmetic predicted, and behaviour did not.** Available
memory fell from 11.3 GB to 3.6 GB with both resident — roughly the sum of the
two — and **swap never moved**, holding at 1.9 GB from baseline to post-run.
Nothing about the memory picture would have predicted a 43% throughput loss.
That sentence was the Runtime Sets screen's subtitle before anything had been
measured; it is now a finding rather than a claim, and it is the whole argument
for §10.1 existing.

**One gap in this run, and it is in the record rather than the measurement.**
§11.3's matrix carries Peak RAM and Swap rows; the engine sampled both and the
matrix did not carry them, so this run's `result.json` has no memory rows. They
were added immediately afterwards and are tested, and nothing was lost — the
figures above come from this run's own `telemetry/measurements.jsonl`. The next
run's matrix carries them inline.

### What M16 settled, and the seam it exposed

§15.1 states the question in RAVIS's own words, and the whole surface is shaped
by three clauses inside it:

> Give me the **best** measured evidence for role `clarvis-agent` on **this
> machine** for **these candidate builds**, under these runtime configuration
> constraints.

**"Best" is not computed, and there is nowhere for it to live.** §12.2 forbids
evidence keyed as model → score, and a `best=true` flag or an `ORDER BY score`
would be that rule broken by a different spelling. So the endpoint filters,
orders by *recency* — a fact about the record rather than a judgement about the
model — and hands back every metric with its provenance, validity, sample count
and versioned method. One test asserts structurally that no response field is
named `score`, `rank`, `best`, `recommended` or `winner`, because the pressure
to add one will come from a caller who finds choosing inconvenient, and choosing
is RAVIS's job.

**"These candidate builds" arrive as runtime keys and leave as variants.** That
is §15.1's *do not force RAVIS to infer equivalence across builds* made
mechanical: RAVIS knows a runtime key and nothing else, evidence is filed by
variant, and the mapping needs §6's inventory — which is SIRVIS's. A RAVIS
resolving it would be matching on names, which §15.1 forbids and §6 exists to
replace. Two failure modes are kept apart rather than collapsed: a candidate
this machine does not have comes back in `unresolved_candidates`, because *not
installed* and *measured, no evidence* are different findings and an empty list
for both would report a missing build as a disappointing one. An unreachable
runtime resolves nothing and says so, rather than answering confidently from an
empty inventory (§15.4).

**Tombstones are reported as an empty list, and that is not a stub.** §15.1 asks
for them; nothing in SIRVIS deletes a result, so there is no mechanism that
could produce one. The field ships empty so a consumer can code against it
before deletion exists, and so whoever adds retention knows exactly what they
have to start filling.

**The seam this milestone found is a missing milestone, not a defect.** RAVIS
names its pools `ravis/clarvis-chat` and `ravis/clarvis-agent`. Every piece of
evidence on this machine is filed under `agent`, `chat` or `general`, because
those are the role names M6's suite and M9's Runtime Set members used. So a
faithful RAVIS query for `clarvis-agent` returns nothing — and the correct
response is emphatically *not* a rule mapping one vocabulary to the other, which
would be the same equivalence-inference §15.1 forbids, merely performed by the
other side. **SIRVIS M13 is the milestone that produces evidence under the
`clarvis-chat` and `clarvis-agent` roles**, and until it runs the absence is
real rather than a translation problem. What M16 does do is stop the mismatch
lying: a role filter that matches nothing comes back with `available_roles`, so
"nobody has agreed what this role is called" cannot present as "this build was
measured and found wanting".

Verified against the real corpus rather than only fixtures: `role=agent`,
`evidence_type=MEASURED`, `config.context_length=8192` resolves to the M10
co-residency record for `granite-4.0-h-tiny` GGUF — median 66.96 tokens/second
across five repetitions with min, max, p10, p90, stddev, every repetition
preserved, `MEASURED` provenance carrying the method version
`stream.generation_tokens_per_second.v1`, and no single number anywhere that
could be mistaken for a score.


**A second pass deleted what the screens were inventing, and added the controls
they were missing.** The audit that prompted it counted 98 explanatory footnotes
against 0 selects and 0 toggles, and found the action surface exactly inverted:
nine buttons for operations that do not exist, none for the four that do.

Deleted: RAVIS Settings carried a €200/month budget with a spend ladder, and a
four-level privacy setting with one marked active. RAVIS has no cost concept in
its source at all, and privacy is a *pool* property (`ravis/private`) rather
than a service setting. The same screen headed a card **"What a client can
change · has an endpoint"** listing five actions, while a card three below it
correctly said those things have no endpoint — the screen contradicted itself,
and the version with the working-looking buttons was the false one.

Every remaining control now goes through `control()`, which renders a button
only when its endpoint exists and otherwise a disabled one naming what it waits
for. **A button that raises a toast and changes nothing is worse than an absent
control**: the operator learns the system does something it does not, and finds
out otherwise at the moment it matters.

One correction to that audit, recorded because the audit was wrong about it:
"no toggles" is not a defect on RAVIS Settings. RAVIS publishes nine reads and
no writes, so a toggle there would assert an endpoint that does not exist — and
the screen already said so, in a card arguing that a settings page is exactly
where that temptation is strongest.


**The Runtime screen's first real load, and the two defects it surfaced.**
`qwen/qwen3-1.7b` was loaded through the screen's own form on 2026-08-24 — model
selected, context 4096, lease 600s — and every field reached the runtime: LM
Studio reported it resident at 4096, and residency showed one holding, one
reference, `owner: nervis-dashboard`, `owned: true`. Renew and release both
worked, and the release unloaded it.

Neither defect was findable without doing it. The dashboard's *own copy* of the
runtime-set members table rendered a literal `null` in the follow-up chip — the
Runtime sets screen had been guarded and this second copy had not, and the load
is what put a rendered page in front of someone.

The second is the interesting one. `CORS_METHODS` read `GET, HEAD, OPTIONS`
under a comment saying reads only, and inviting whoever needed a cross-origin
mutation to decide explicitly. **It never held.** POST is a CORS-safelisted
method, so a browser preflight accepts it whether or not it is listed: the
dashboard could open a runtime session and renew a lease — the two expensive
mutations — and could only not *release* one. Memory could be spent and not
reclaimed, which is the worst available asymmetry, and the log showed it exactly:
a 204 preflight for the DELETE with no DELETE after it.

The list now names every mutation this service serves. That does not widen the
boundary, because the header was never the boundary: an origin must be in the
operator's allowlist (empty by default), present a token with the right scope,
and use a non-simple content type. The test that guarded the old line asserted
POST was not advertised — it is replaced by one asserting what actually protects
anything, that an unlisted origin gets no CORS headers at all.

### What M12 and M13 settled

M12's instruction is *inspect Clarvis's existing benchmark assets — do not
rewrite* — and the classification it produced is short, because three things in
`clarvis-firstrun/tools/suite2.py` are `REUSE` and each encodes a failure that
cost a real debugging session:

- **Eight phrasings, not one.** `granite-4.0-h-tiny` passed a single-prompt
  tool-call check three times out of three and lost the filename on every one of
  the eight. A harness with one prompt measures whether that phrasing works.
- **Assembled from the stream, by index.** The original harness sent
  `stream: false` and read `message.tool_calls` off a finished response. Clarvis
  never does that. The two modes **disagree**: the same build returns a
  well-formed call unstreamed and streams one whose arguments never arrive, so
  the old harness scored 3/3 for a build that fails every realistic request.
- **The follow-up turn.** One-shot code generation cannot see the failure where
  a model malforms a path and then retries the dead path four times, twice after
  being told plainly to use a different tool. The model that did that writes
  perfectly good Python.

M13 wrapped those and added the evidence contract around them. **`TrialRate` has
had no producer since M7 declared it** — the shape was right and nothing filled
it — and it is filled now: eight phrasings become `tool_call_well_formed`, the
follow-up becomes `tool_followup_used_result`, and both carry versioned method
names so a rate from `clarvis.tool_call.streamed.v1` is never silently compared
with a later one.

Three decisions worth the veto:

- **A rate, never a measurement.** A median over ones and zeros is meaningless.
  `passed` and `total` both travel, because 6/8 and 60/80 are different amounts
  of evidence for the same rate.
- **A runtime failure is a failed attempt, not a skipped one.** A build that
  makes the runtime fall over on two of eight prompts has a reliability of six
  in eight; dropping those two would publish eight in eight for it.
- **Absence is not zero.** A run without trials carries *no* tool rate at all
  rather than `0/0`. That field is what a router reads to decide whether a build
  can call tools, and a rate nobody measured sitting in it is the exact
  metadata-only claim M13's acceptance forbids.

**The role is spelled the way RAVIS names its pool.** M16 found that evidence
filed under `agent` cannot answer a query for `clarvis-agent`; the fix is to
measure the role RAVIS asks about, not to teach either side a mapping — which
would be the equivalence-inference §15.1 forbids, performed by the other side.
That seam is closed by construction.

### The first role run, and the claim it turned into a measurement

`lmstudio-community/granite-4.0-h-tiny` through the `clarvis-agent` role on
2026-08-24. Run `run_1b21d770a2594395`, evidence `ev_188a3fd324194eff`, VALID,
raw material in `sirvis/results/exp_315e650ee11f4d7c`.

```text
tool_call_well_formed          8/8   (100%)   clarvis.tool_call.streamed.v1
tool_followup_used_result      1/1   (100%)   outcome: used-result
generation_tokens_per_second   60.282 ± 1.878  (n=6)
time_to_first_token_seconds    0.048 ± 0.006   (n=6)
```

**Eight of eight, every phrasing, with a parsed path on every one** — and the
follow-up turn came back `used-result`: told its path was wrong and to use
`listFiles`, it used `listFiles`.

This repository has carried the figure "its GGUF packaging delivered 8 in 8"
since before SIRVIS existed, transcribed from `clarvis-firstrun`. It is now
**measured through SIRVIS's own pipeline** — streamed, assembled by index, filed
under the role RAVIS asks for, with `MEASURED` provenance and a versioned method
— rather than quoted. The two agreeing is the useful outcome, because the whole
point of M12 was that the old harness was worth wrapping.

It is also the ecosystem's **first tool-capability claim that came from
attempts** rather than a catalogue. `ravis/clarvis-agent` is 0 of 20 today
because RAVIS holds no such claim for any build; this is the first one that
exists to be carried, and carrying it is RAVIS M13.

### Two packagings of one model, and the case against model → score

`mlx-community/granite-4.0-h-tiny` through the same role on 2026-08-24. Run
`run_de31b9c876c84943`, evidence `ev_73e542e82096af09`, VALID. Identical weights
to the GGUF build above, identical suite, same machine, same evening.

```text
                          GGUF Q4_K_M      MLX 4bit
tool calls well-formed        8/8            1/8
follow-up turn          used-result   lost-arguments
generation tok/s             60.3          110.5
time to first token         0.048s         0.210s
```

**The MLX packaging is 83% faster and cannot be used as an agent.** Seven of its
eight calls arrived with the right tool name and *empty arguments* — the runtime
discards them — and the one that worked is the only prompt naming a bare
filename with no path. The follow-up turn scored `lost-arguments` for the same
reason.

This is §12.2's argument, measured rather than asserted. Evidence keyed as
**model → score** would rank the MLX build above the GGUF one on every number a
score would be computed from, and route `ravis/clarvis-agent` to the packaging
that fails every realistic tool request. The two are different evidence under
§12.2's identity — different `model_format`, different `quantization`, different
`evidence_id` — and that separation is the only thing standing between a router
and that mistake.

It is also why §15.1 forbids making RAVIS infer equivalence across builds. These
two share a family name, a parameter count and an architecture. Nothing
observable from the name distinguishes them, and everything that matters does.

**And it is the streamed harness earning its place.** An unstreamed check reads
`message.tool_calls` off a finished response, where this build returns a
well-formed call — the original harness scored it 3/3. Assembled from the stream
the way Clarvis assembles it, it scores 1/8. The mode the product does not use
measures nothing.

Running it found the reporting gap either printer would have hidden: `metrics`
carries measurements and trial rates together, the listing discriminated on
`"median" in body`, and every rate fell through it silently. An agent run
printed its throughput and not the 8-in-8 that was the point of it. Both
printers now render rates as `passed/total`.

### What RAVIS M13 settled

§13.1 opens with a prohibition — *never reduce SIRVIS results to model → score*
— so nothing in this milestone computes one. What crosses the boundary is a
**capability claim**: this build satisfies a pool invariant, or does not, or
nobody knows. Ranking stays with RAVIS, and one test asserts structurally that
no field anywhere is named `score`, `rank` or `rating`.

**The threshold is two axes and the second one is load-bearing.** SIRVIS.md
§13.2 sets it: `tool_call_pass_rate ≥ 0.95` over **≥ 8 phrasings × ≥ 3
repetitions**. A build clearing the rate on too few attempts establishes
*nothing* — which is a different answer from failing, and the surface says which.
That is not hypothetical: the first role run on this machine scored 8/8 over 8
attempts, which is a perfect rate over a third of the required sample, and RAVIS
correctly declines to admit it.

**The pool opened, and only for the build that earned it.** Both packagings were
re-measured at three repetitions — 24 attempts each, the sample §13.2 asks for —
on 2026-08-24:

```text
lmstudio-community/granite-4.0-h-tiny  SUPPORTED    24/24 over 8 phrasings
mlx-community/granite-4.0-h-tiny       UNSUPPORTED   3/24 — below the 95% threshold

ravis/clarvis-agent                    available: true, members: 1
                                       · lmstudio-community/granite-4.0-h-tiny
```

The MLX packaging reproduced its failure at three times the sample — 3/24 is the
same 12.5% as 1/8 — and is excluded while being **83% faster** than the build
that was admitted. `ravis/clarvis-agent` had been unavailable since it was
declared, because RAVIS held no tool-capability claim for anything. It is now
available with one member, and both the admission and the exclusion trace to
measurements with references back to the runs that produced them.

**Stage 5's exit criterion is met**: a SIRVIS result changed a RAVIS preference.

**Getting there needed the other invariant too, which the first attempt missed.**
`clarvis-agent` requires tools *and* 32K context, and RAVIS knew no context
window for any build — so the pool stayed closed even with the tools claim
established. §13 lists "model fit" among what RAVIS asks SIRVIS for, and the
store now reads declared ceilings from SIRVIS's inventory alongside the
evidence. Declared, not measured, and labelled as such: it answers whether a
minimum is *possible*, not whether the build performs well there. The claim
detail also records the context the trial actually ran at — §12.2 keys evidence
on the configuration it was produced under, so a rate measured at 8K admitted to
a pool needing 32K is a small inference, and it is visible rather than hidden.

**And a bug caught before it cost model time.** The CLI's `--repetitions` set
the prose repetitions and never reached the tool trials, which were fixed at
one — so no run from the command line could ever satisfy the threshold's second
axis, and the first two role runs produced 8 attempts where 24 were needed. The
count now reaches the trials, which is why these runs have 24.

The decisions worth the veto:

- **Absence, staleness and unreachability are three findings, not one.** Never
  measured is UNKNOWN. Measured too long ago is UNKNOWN *and* degrades the
  source. A SIRVIS that will not answer drops every claim and says so — serving
  the last read on would be presenting a cached measurement as a current one,
  which §13.3 forbids outright.
- **Provenance is never upgraded.** SIRVIS's `PARTIALLY_MEASURED` arrives as
  `ESTIMATED` and cannot establish an invariant, per §13.3's own mapping.
- **An operator's declaration still outranks a measurement.** §9.5's ordering
  survives M13 intact: `CONFIGURED` beats `MEASURED`, because an operator knows
  things about their deployment RAVIS cannot observe.
- **An unsupported major is refused rather than parsed optimistically.** The
  fields might line up; that is not a reason to route on a shape nobody verified.

**Two gaps this milestone found in surfaces built for it.** M16's evidence
endpoint returned resolved variants but no mapping back to the runtime keys the
caller asked about — so a consumer holding several candidates could not tell
which record answered which question, and would have had to match on names,
which §15.1 forbids and that endpoint exists to prevent. Added as
`candidate_variants`.

And **SIRVIS was advertising every capability as unavailable.** M1, M2, M3, M6,
M7 and M16 had all shipped; the declaration still said `unavailable` for all of
them, including `sirvis.evidence.query@1` — the one RAVIS was built to
negotiate. The file's own docstring warned that a sibling service had let these
go stale for four milestones. Nothing fails when a service under-advertises: no
error, no failing test, just a peer that cannot integrate and no clue why. The
test that pinned this asserted "everything is unavailable" and went on passing
for six milestones after that stopped being true; it now pins the split, so a
milestone that makes a capability real has to say so.

### A request actually routed through the pool

`POST /v1/chat/completions` with `model: ravis/clarvis-agent`, 2026-08-24. It
answered in 2.1 seconds, and the recorded decision is the point:

```text
requested      ravis/clarvis-agent
selected       lmstudio-community/granite-4.0-h-tiny
requirements   tools REQUIRED · minimum context 32768
excluded       19 candidates
                 · 18 × "tools is UNKNOWN"        — never measured
                 · 1  × "tools is UNSUPPORTED"    — mlx-community/granite-4.0-h-tiny
```

**The two exclusion reasons are the whole of M13 in one line.** Eighteen builds
are out because nobody has asked them; the MLX packaging is out because somebody
did. A router that collapsed those would report the same word for a build
awaiting measurement and a build that failed one — and only one of those is
fixed by running a benchmark.

**The explanation contradicted itself, and running this is what showed it.** The
ranking sentence said *"No benchmark evidence or cost data is available yet"* —
unconditionally, written before M13 — so it appeared inside a decision that was
only possible **because** evidence existed. It now says the narrower thing that
is actually true: evidence decides eligibility rather than order, nothing ranks
one admitted build above another on quality, and cost is genuinely absent until
M15. The test that pinned the old wording asserted `"No benchmark evidence"` and
would have gone on passing indefinitely.

**And the request left a model resident that nothing owned.** RAVIS forwards on
the transparent path; LM Studio JIT-loaded the build to serve it; §9 gives
lifecycle to SIRVIS, which did not load it and will not unload it. That is
exactly the `foreign` column on the Runtime screen — memory SIRVIS accounts for
and does not own — arriving here for the first time from an ordinary request
rather than a benchmark.

### What M15 settled

§14.3 asks for a weighted score. §12.2 forbids evidence keyed as `model →
score`. The line between them is the whole milestone:

    A score is a **function output**, carried with the weights, the inputs and
    the algorithm version that produced it. It is never a property of a model.

Nothing writes a number back onto evidence. A recommendation is an opinion with
its derivation attached, it expires, and §14.3 closes by saying what it is not —
*evidence-backed suggestions, not routing commands*.

**Coverage is the field that makes the score readable.** The `clarvis-agent`
profile puts 35% of its weight on coding and 15% on reasoning; no suite measures
either (M18). Another 5% is memory, and nothing records an installed size. So a
score on this machine rests on **45%** of its own profile, and every
recommendation says so:

**M15's acceptance, met.** Two builds were measured under the `clarvis-chat`
role on 2026-08-24, and the engine recommends a pair:

```text
clarvis-chat    1. qwen/qwen3-4b-2507            score 1.00 · coverage 0.50
                2. meta-llama-3.1-8b-instruct    score 0.53 · coverage 0.50
clarvis-agent   1. granite-4.0-h-tiny · GGUF     score 1.00 · coverage 0.45
excluded        granite-4.0-h-tiny · MLX — tool_use is UNSUPPORTED
combination     2.0, with no joint term: this pair has never been run together
```

`qwen3-4b-2507` wins chat on throughput — 52.4 tok/s against llama's 28.7, at
0.18s to first token against 0.28s — and both score on **half** their profile,
because reasoning and memory have no evidence. The agent side is unchanged and
its exclusion is still the useful one: the faster packaging of the same weights
is out on a measurement, not on ignorance.

The decisions worth the veto:

- **The score divides by covered weight, not the profile total.** Dividing by
  the total would mark every candidate down for suites that do not exist, which
  is a fact about SIRVIS rather than about any model — and would move every
  candidate identically, which is no information at all.
- **An excluded candidate does not set the normalisation scale.** Found by
  running it: while the MLX packaging was in the denominator, the admitted build
  scored **0.55** on throughput for being beaten by something that fails every
  realistic tool call. A recommendation ranks the builds that qualify, so
  "fastest" means fastest among those.
- **Eligibility is separate from ranking.** A build that cannot call tools is
  not a low-scoring agent; it is not an agent. Weighing it would let a fast
  enough model out-score its own disqualification.
- **The joint term is zero, not optimistic.** A pair nobody has run together has
  no joint evidence, and inventing one asserts exactly what §10.1 denies.
- **Every admitted candidate is ranked, not only the winner.** Found by running
  the chat role against two builds: reporting the top of each role made the
  second-placed build vanish — absent from the ranking and from the exclusions,
  as though nobody had looked at it. §14.3 asks for ranked candidates, plural.
- **`fast` proposes nothing.** §14.3 lets `verified` propose further benchmarks
  and neither mode starts one: expensive here means gigabytes of somebody's
  memory and a model resident for the duration.

**Two things it exposed.** The threshold §13.2 sets was nowhere in SIRVIS's own
code — it lived in this specification's prose and in RAVIS's consumer, and the
service that owns it had no copy. It is defined here now, and still duplicated
across two services that share no code; whoever tires of that should publish it
beside the rates so the consumer reads it rather than restating it.

And `POST /api/v1/recommendations` shipped behind `Scope.READ`, which is the
same mistake M9 and M16 made — except the test pinning that convention only
walks **GET** routes, so it did not catch a POST. §14.3 specifies a POST because
its inputs are a body, and it stores nothing: it is a read that happens to need
a request body. The token check is gone and the CSRF checks are not, which is
the distinction dropping `require` wholesale would have lost.

### The recommended pair, measured together

The pair M15 recommended, run through M10 on 2026-08-24. Run
`run_d024b6f271674585`, set `clarvis-recommended@1`, raw material in
`sirvis/results/exp_4b6797ae30eb4caf`.

```text
                     alone   sequential  alternating  concurrent
agent tok/s           68.1        68.2         59.8        44.6
chat  tok/s           49.8        49.8         49.3        38.8
agent TTFT           0.051       0.052        0.110       0.063
chat  TTFT           0.218       0.219        0.254       0.260

available memory      8.5 GB                   6.3 GB co-resident
```

**Co-residency is free again**, on a second and different pair: −0.2% and +0.1%.
That is now a finding rather than an observation — two pairs, four builds, the
same answer. **Concurrency costs 34.4% and 22.1%**, which is *less* than the
first pair's 42.7% and 31.9%: the pair a recommendation picked degrades less
under load than the one picked by hand, which is the first time this corpus has
been able to compare two combinations at all.

**And the recommendation now stands on it.** M15's combination score moved from
`2.0` to **`1.6557`** — the measured 34.4% subtracted as §14.3's contention
penalty — and its uncertainty line changed from *"this pair has not been
measured together"* to *"measured together: concurrent generation costs up to
34.4% of throughput (clarvis-recommended@1)"*. The joint term was the one number
in that output standing in for a measurement nobody had made.

**Three gaps the run exposed, all in surfaces built for it.**

`_interaction_matrices` iterated `list_runs(...)` directly, and `list_runs`
returns `(runs, cursor)` — so the loop read a list and a cursor string as
though both were runs. mypy caught it before it ran.

The matrix keyed its rows by role and named no builds, so a consumer holding one
could not tell which pair it described — and §10.1 is precisely that
co-residency behaviour does not transfer between combinations. It carries
`members` now, and matrices written before that field are resolved from the set
definition at their pinned revision, which is immutable by construction.

And **the alone condition had no peak memory sample of its own.** Only the
co-residency modes emitted one, so swap could be read while two models were
resident and not while one was — leaving §10.1's central comparison, what the
second model costs, unanswerable for the figure most likely to answer it.

### The rule about loading — still read this first

M6 is the first milestone that **loads models to do its job**, and an earlier
session loaded four onto the developer's machine without asking. `sirvis
benchmark run` now names what it is about to load and waits — refusing outright
when stdin is not a terminal, because an unattended script that meant to do this
can pass `--yes` and one that did not should not find out by discovering a 14 GB
model resident an hour later.

**It happened twice more anyway, in the session that built that prompt**, and
how is the part worth keeping. Both were diagnostics rather than benchmarks — a
model loaded to test a thermal API, another to re-check a throughput figure —
and neither felt like "loading a model" at the time, which is exactly the
failure mode described below. The user caught both.

**The prompt did not help, because the same session passed `--yes` on every
invocation.** A guard routinely bypassed is not a guard; it is a step that has
been optimised away. The prompt protects an operator typing the command. It
cannot protect anyone from an agent scripting it, and nothing in the code can.
The only thing that works is asking in chat and waiting for an answer — for
probes and one-off checks too, not just for deliberate benchmarks.

**A confirmation prompt is not the same as the habit**, and the habit is what
actually prevents this:

> **Say what you are about to load, and wait.** Not only for a deliberate load —
> the four that happened were all indirect: a test suite reaching a live
> service, a `generate` call JIT-loading, a CLI shelling out. None of them
> registered as "loading a model" at the time.
>
> **Check residency after anything that could reach a runtime**, rather than
> reasoning about whether it should have. `curl -s localhost:1234/api/v0/models`
> shows the `:2`/`:3` suffixed instances LM Studio creates by itself, which are
> the easy ones to miss.
>
> **Never attribute an unexplained load to the user.** It was mine three times
> out of three.

And the approach the runbook asks for, which is still ahead and is easy to skip:
**wrap `clarvis-firstrun/tools/suite2.py` before replacing it.** That tooling produced
the measurements in `ravis/measured-capabilities.json`; §21.1's first vertical
slice is a *comparison* — one GGUF and one MLX build, benchmarked and compared —
not one model measured well.

### What M4 settled

The first real provider on Path B, and the value was never the plumbing — M3b
had already proven that with a test double. It was the **four mappings that had
no obvious right answer**, each of which produces a plausible-looking response
when it is wrong, which is why each is written down here rather than only in a
comment.

- **A stream index is not a tool index.** Anthropic numbers *content blocks* and
  text blocks take numbers; OpenAI numbers *tool calls*. A response that opens
  with a sentence and then calls a tool is Anthropic block 1 and OpenAI tool 0.
  Passing the block index through would file every fragment under a call that
  does not exist, and the client would see a tool call with no name and no id.
- **Parallel tool results must arrive in one message.** OpenAI sends one
  `role: "tool"` message per result; Anthropic takes them as `tool_result`
  blocks inside a single user turn. One turn per result is valid JSON, is
  accepted, and quietly trains the model to stop calling tools in parallel —
  a regression that would look like the model getting worse.
- **`stop_reason: "refusal"` maps to `content_filter`.** It is a real terminal
  state — HTTP 200, usually no content, a `stop_details.category` naming the
  classifier — and OpenAI has no word for it. `content_filter` is the only
  finish reason in that vocabulary meaning *the provider declined*, which is
  what happened. The category has no equivalent at all, so it is logged and put
  on the event's `raw`, never invented onto the wire. `pause_turn` and anything
  unrecognised become `null` rather than `stop`: publishing "stop" would assert
  the model finished when nobody established that.
- **A trailing assistant message is refused, not forwarded.** That is a prefill,
  and current Anthropic models reject it with a 400. Refusing locally costs
  prefill on Anthropic's older models — a real loss, taken deliberately over
  keeping a model-version table that would be wrong the week it was written —
  and buys an error that names the cause instead of a provider 400 two layers
  from the request that caused it.

**`temperature` is dropped, and the drop is logged.** Sampling parameters are
rejected outright by every current Anthropic model, and most OpenAI clients set
one by default — so forwarding it turns ordinary traffic into a hard failure.
§7 says an unsupported feature must never silently disappear, and a log line is
how that promise is kept without refusing requests RAVIS exists to serve. It is
listed under the deviations below because it is the weaker half of a rule.

**The adapter speaks HTTP rather than the `anthropic` SDK.** RAVIS already owns
retries and the retry budget, circuit breakers, timeouts, and §8.6's rule that
closing a generator stops the upstream — an SDK would have to be switched off on
each of those to avoid retrying a request the chain has already decided not to
retry, and its typed stream events are a re-serialisation of the frames this
adapter needs verbatim. M7 and M8 add four more providers behind the same
interface, and five vendor SDKs in a gateway is a gateway that has stopped being
one HTTP client. The translation itself is pure and tested without a socket:
`providers/anthropic_wire.py`, 38 tests, no event loop.

**What M4 deliberately does not claim is tool support.** `capabilities()`
records what the model catalogue advertises — context window, vision,
structured output, reasoning — and Anthropic's catalogue documents no
tool-support key. §5.1's hard invariant is that every member of
`ravis/clarvis-agent` satisfies the tool requirement, so the claim stays
`UNKNOWN` and any pool requiring tools fails closed until an operator declares
it or M13's evidence arrives. This will look like a bug and is §5.2 working.
Direct addressing — `ravis/anthropic/<model>` — is unaffected, and that is what
M4's acceptance criterion exercises: an unmodified OpenAI client, through
Anthropic, unable to tell from the wire which of §6's two paths ran.

**One bug was found by writing the tests rather than by running them.** A
translation RAVIS refuses was being recorded as `UNKNOWN` — a class that counts
against the provider — so one client's malformed body would have walked
Anthropic's circuit breaker toward open and taken the provider offline for every
other caller on the machine. `TranslationError` now lives on the adapter
contract in `providers/base.py`, is classified `INVALID_REQUEST`, and returns a
400 naming the cause instead of a 502 saying every candidate failed.

### Which prototype screens read real services

`nervis/index.html` renders every screen against mocks. Three now read live
services instead, and the rest still should not — a screen wired to an endpoint
that does not exist is worse than a screen on mocks, because it looks finished.

| Screen | Source | Notes |
|---|---|---|
| SIRVIS **Results** | live | `/api/v1/benchmark-runs`, one row per evidence identity |
| SIRVIS **Benchmarks** | live | runs mapped onto the job record; there is no queue, and the screen says so |
| SIRVIS **Dashboard** | live | the run-detail card and the build list |
| SIRVIS **Models** | live | `/api/v1/models`; size, residency, advertised and measured capability all render absent, because the inventory carries none of them |
| SIRVIS **Runtime sets** | live | `/api/v1/runtime-sets` joined to the M10 matrix on **name and revision**; peak memory and follow-up render absent |
| RAVIS **Routes** | live | `/api/v1/route-decisions` |
| RAVIS **Pools** | live | `/api/v1/pools` + `/api/v1/models`; every build reads *out · tool support unknown — fails closed*, which is true |
| SIRVIS **Runtime** | live | `/api/v1/runtime/residency`, and the four session mutations — the first controls on this page that reach a service |
| RAVIS **Evidence** | live | `/api/v1/evidence` — what SIRVIS said and what RAVIS concluded, with the reason a build was refused |
| SIRVIS **Recommendations** | live | `POST /api/v1/recommendations` — ranked, excluded with reasons, and the coverage each score rests on |
| SIRVIS **Discover** | **mocks — no endpoint** | there is no `/api/v1/catalog`. M11 builds it |
| Recommendations, Downloads | mocks | need M15 and M11 |

**Wiring a screen is part of finishing a milestone from now on.** M9, M10 and
M16 each shipped a producer and wired no consumer, and this table drifted from
three live screens to a growing list of *endpoint exists, nothing reads it*. The
argument against that was already in this file — a queue view counts states and
a log does not — and the repository was not following it.

**What wiring found, none of which the tests had.** Five reads shipped behind
`Scope.READ` while every other read on SIRVIS is open; the browser sends no
token, `live()` treats a 401 as "service absent", and both new screens would
have rendered mocks while looking wired. A `reduce` with no seed on the SIRVIS
dashboard threw on the first live payload and took the whole app's render with
it. `null.toLocaleString()` blanked the Pools screen. And three separate cells
turned an absent value into a confident negative — `tools_advertised: null`
printing as **none** across every row is an assertion that no build advertises
tool use, which is false. A passing test against a fixture cannot find any of
these, because a fixture is never absent.

**The Runtime Sets screen was blocked and is not any more, and the history is
worth keeping** because it is the clearest case of the rule this table exists
for. It reads measured *pairs* — peak co-resident memory, first token under
co-residency, follow-up behaviour per pairing — and until 2026-08-24 there were
only two ways to produce those, both forbidden: inventing
`/api/v1/runtime-sets`, or synthesising pairs from single-model runs, which
would assert exactly what §10.1 exists to deny. The screen's own subtitle says
*memory behaved as arithmetic predicted and behaviour did not*, so fabricated
rows would have made the page contradict the finding it was built to show.

M9 gave it an endpoint and M10 gave it a measured pair — and the subtitle turned
out to be right, which is the first time it has been checkable. It is now
unwired rather than unwirable, and that is a NERVIS task.

Live wiring is why the reconciliation above exists, and that is the argument for
doing it early rather than last: a queue view counts states, and a log does not.

### Next — in this order

| # | Milestone | Why here |
|---|---|---|

### After that

What is left of Stage 5 is RAVIS's remaining intelligence: **M7** (Google,
OpenRouter), **M8** (LM Studio, Ollama, generic adapters), the rest of **M14**,
and **M16** (policy). Stage 6 is NERVIS core — **M11** + **M15**.

**M3b, M4 and M13 are done, out of stage order**, and all three for the same
reason: §20.2 holds translation back until the transparent Clarvis slice works,
and that slice was the control whose correctness rested on nothing but fixtures.
M9 ended that. So the translated path was built, given a real provider to drive
it, and then — once SIRVIS could measure a Clarvis role — handed real evidence
to route on.

**M8 is the one to watch in what remains.** Every measurement in the corpus came
through LM Studio, and RAVIS reaches exactly one upstream today. Until M8 lands,
`ravis/clarvis-agent` has one eligible member because one runtime has been
looked at — not because one build passed.

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
- **RAVIS M13 pulled ahead of the rest of Stage 5**, and it is the reordering
  with the strongest case: SIRVIS M13 had just produced the first
  tool-capability claim in this ecosystem that came from attempts rather than a
  catalogue, and nothing carried it across. Leaving it would have meant two
  services passing their own suites and no evidence that the contract between
  them worked — while `ravis/clarvis-agent` stayed unroutable for want of a
  claim that already existed. Building it immediately also meant the integration
  was exercised against evidence measured hours earlier rather than against a
  fixture written to match the consumer, which is how the two gaps it found —
  M16's missing runtime-key mapping and SIRVIS's stale capability declarations —
  were found at all.

---

## Work that no milestone names

Three things here were built because something else needed them, not because a
milestone called for them. Listed separately from the deviations below because
they are not disagreements with the specification — they are additions to it,
and an addition nobody wrote down is how a plan quietly stops describing the
build.

- **CORS on SIRVIS's read surface**, mirroring the RAVIS decision above and for
  the same reason, discovered the same way: the Results screen was wired to the
  new endpoints and could not read one of them. §4.5's origin check already
  owned an allowlist and nothing emitted `Access-Control-Allow-Origin`, so the
  endpoints were servable and unreadable at once. Allow-listed origin echoed
  rather than `*`, credentials deliberately absent, `GET`/`HEAD`/`OPTIONS` only,
  and the same allowlist drives permission and refusal so they cannot drift.
- **A list endpoint for benchmark runs, and §4.2 amended to name it.** §4.2 gave
  this group two *detail* paths and separately defined a list envelope taking
  `limit` and `cursor` — a contract for a response nobody could request. The
  endpoint was built and **the specification was corrected rather than deviated
  from**, which is §1's third step: amend the plan, do not fake the expected
  value. Paged on `rowid` rather than `started_at`, because two runs begun in
  the same second sort arbitrarily by timestamp and a paging key that can tie
  eventually drops a row or serves it twice — rarely, silently, unreproducibly.
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
- **`temperature` is dropped on the Anthropic path rather than forwarded or
  refused.** §7 says an unsupported feature must never silently disappear, and
  this is the weakest form of keeping that promise: the parameter is left off
  the request and the drop is logged, because every current Anthropic model
  rejects sampling parameters with a 400 and most OpenAI clients set one by
  default. Forwarding it would fail ordinary traffic; refusing it would refuse
  the clients RAVIS exists to serve. *If a route explanation ever carries
  per-request notes, the drop belongs there instead of only in a log.*
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
- **A translated request never falls back, and a pooled request never reaches
  Path B.** Two limits of M3b, both deliberate and both cheap to mistake for
  bugs. §10's chain assumes every candidate is reachable the same way; falling
  back from a translated provider to a transparent one runs the next attempt
  down a different path with a different failure vocabulary, and deciding that
  inside an exception handler is how a fallback starts producing answers nobody
  can account for. And a pool resolves to a *model*, not to a provider, until
  M8's provider table exists — so only a direct `ravis/<provider>/<model>`
  address can select a translating adapter today. `execution_path` is recorded
  on every request, including the transparent ones, precisely so this is
  visible rather than inferred.
- **A twelfth failure class, where §10 names eleven.**
  `INVALID_UPSTREAM_RESPONSE` covers a success status that carried no usable
  response — specifically a stream that closed without a single byte. Folding it
  into an existing class would have meant a label that is a guess, and the
  configured upstream answers 200 for endpoints it does not implement, so "the
  status said fine" is not evidence that anything worked. Its policy permits a
  fallback and scopes the circuit to the model. *If the eleven are meant to be
  closed, say so in §10 and this becomes `UNKNOWN` with a worse explanation.*
- **A benchmark's telemetry is JSON Lines, not Parquet.** §11.9's tree names
  `telemetry/measurements.parquet`, and §17 scopes Parquet to "high-frequency
  data where SQLite becomes unsuitable". A single-model run produces a few dozen
  samples, so a columnar format and a pyarrow dependency would answer a scale
  problem that does not exist yet. One JSON object per line, so M10 can change
  the format when concurrent runs justify it without any reader here having
  assumed a schema.
- **Validity is `SUSPECT`, where §11.8 writes `VALID_WITH_WARNINGS`.** The same
  three-state idea under a shorter name, chosen at M7 and kept rather than
  renamed now that results are written against it. *If the specification's name
  is preferred, change both together.*
- **The Resource Manager adopts instances it did not load.** §9 gives it
  ownership of every load and unload; it now also *tracks* a model the runtime
  already holds, without owning it. The alternative was worse than a deviation:
  issuing a load for something already resident makes LM Studio create a second
  instance rather than reuse the first, which is how three copies of one 7B
  model ended up in memory during M9. An adopted instance counts against
  capacity, is reported with `owned: false`, and is never unloaded here —
  §11.2's "unload **if owned**", read literally.
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
  **M6's live runs confirm the diagnosis.** The same build, loaded once at 8192
  by a benchmark that owns its lifecycle, reaches its first token in 0.296 s —
  an order of magnitude better than M9's ~4 s, and in line with the 0.26 s in
  `clarvis/docs/benchmarks.md`. The 4 s was the cost of three co-resident copies
  of one 7B model, not a property of the model. That makes this decision more
  urgent rather than less: the current configuration is not merely imprecise, it
  is measurably expensive.

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
- **A tool refusal still fails the request instead of trying the next model,
  and the one-line fix is a regression.** `TOOL_INCOMPATIBILITY` carries
  `(retry=False, fallback=False, scope=NONE)`, grouped with `INVALID_REQUEST`
  under "the request itself is the problem". That reasoning is false here: tool
  support is a property of the *model*, and every fallback in the chain is
  tool-capable by construction, so the pool's next candidate would very likely
  work. Reaching this failure at all means a capability claim was wrong.
  **Flipping `may_fall_back` alone makes it worse.** With scope still `NONE` no
  circuit opens, the router re-picks the same primary forever, and each of
  Clarvis's per-turn POSTs — up to 25 in one task — becomes a silent double
  upstream call while the upstream's own diagnostic is swallowed once a fallback
  succeeds. And `HealthScope.MODEL` is barred: the health registry is keyed on
  `(scope, target)` with no pool dimension, so it would evict the model from
  `ravis/clarvis-chat` too, contradicting `ECOSYSTEM_RUNBOOK.md` §2.1's
  requirement that a tool-failing model stay eligible for a chat pool.
  What it needs is a **(model, capability)-scoped, time-boxed suppression**,
  which is a scope the registry does not have. The pressure for it is §8.7
  rather than §10 — §10 names the class once and assigns it no rule at all.
- **§8.7's literal instruction is still not followed: RAVIS routes tool probes
  to cold candidates.** The poisoning it caused is fixed from both ends (below),
  but §8.7 does not say "answer the probe carefully" — it says *do not route a
  tiny tool probe to a cold or unsupported candidate and thereby make the pool
  appear incapable*, and calls it the sharpest wire-level constraint in the
  integration. Doing that needs residency-aware pre-flight, and M14's
  observation half already reports residency, so the missing part is small. Not
  done, because it is a routing change rather than an error-handling one and it
  deserves its own pass.
- **Where the orchestration layer lives.** Alexander Keisse's router does
  prompt-shaping, multi-pass and RAG that this ecosystem currently has nowhere.
  The proposal on the table is that it becomes a client *of* RAVIS rather than
  part of it. Undecided.

---

## Things that bit, so they do not bite twice

**Five of the defects below are the same defect.** Worth naming the shape before
the list, because recognising it is faster than rediscovering it: *a value that
changed what was measured was recorded in prose instead of in the key, or a
signal could only say yes.*

- An adapted prompt was noted in a warning while the evidence identity still
  said the suite's declared question.
- The effective context was noted in a warning while the identity still said the
  requested one.
- The thermal probe mapped "no warning has been recorded" to `nominal`, so it
  could report health and never its absence.
- Clarvis's tool probe returned a boolean for a three-valued question, so "could
  not tell" had to arrive as an exception or not at all.
- The pre-commit gate had no failure class permitting a fallback, so a detected
  refusal could abandon a model and then give up.

The test for it: **can this thing express a negative, and does anything that
changes the measurement reach the key rather than a note beside it?** A warning
is prose; whoever compares two evidence IDs never reads it.


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
- **The one failure class allowed to retry the same target retried it forever.**
  A refused connection is classified `CONNECTION`, the only class permitted a
  same-target retry — and `next_target()` returned that retry *before* consulting
  the budget, while `failed()` re-armed it on every failure. Against a closed LM
  Studio the chain re-offered the primary indefinitely: the second candidate was
  never reached, `max_attempts` was never enforced, and the request never
  returned. A switchboard that cannot fail over is worse than one that fails.
  The test that should have caught it is named `..._retries_the_same_target_once`
  and asserted the *first* retry, never asking what came after — the same
  situation-not-rule mistake as the capability list above. Reproduced in twelve
  lines before anything was changed, which is why the fix is trustworthy.
- **A half-open circuit could latch permanently, and the new failure class made
  it likely.** An attempt is claimed on *both* the model's and the provider's
  health record, so both can be left `probing`; a failure blames only one of
  them, and the other stayed probing forever — `allows()` is false while
  probing, only a success clears it, and no success can arrive while `allows()`
  is false. Every model behind that provider then reads unavailable and every
  request is a 422. Pre-existing, and reachable most easily on the *recovery*
  path: a half-open probe is by definition the first request to a provider that
  was just down, which is exactly when a runtime answers "model unloaded" while
  it comes back up. Both records are now released; only one is blamed.
- **Defaulting an unrecognised error to a fallback-eligible class was a fail-open
  bug, not a convenience.** The first version of `classify_error_body` returned a
  class permitting fallback when the marker table matched nothing, on the
  reasoning that `UNKNOWN` would abandon a model and then give up. That makes the
  *same* refusal produce opposite decisions depending on which status the
  upstream attached, with the permissive branch being the 2xx one — and §10's
  "do not route around a safety refusal" carries no status qualifier. It fails
  closed now, and the marker table was widened so the shapes that actually occur
  are recognised rather than defaulted.
- **A gate that reads only the first line is defeated by punctuation.** A
  provider's own `: ping` keep-alive, or an `event:` field, carried a refusal
  straight past the first version. It skips SSE framing now — which is not the
  same as parsing the stream: the original bytes are still forwarded untouched.
- **A run killed mid-flight used to stay `running` for ever.** §11.10 asks that
  a job either survive a restart or be truthfully marked unrecoverable; a run
  did neither. `start_run` writes `RUNNING` before the work, which is right — a
  run first recorded when it succeeds could not be recovered at all — and
  nothing put the row right if the process died. Two such rows sat in this
  machine's database for a day. The engine noticed nothing; **the dashboard
  did**, the first time the Benchmarks screen was pointed at real data, because
  a queue view counts states and a log does not. That is the argument for wiring
  a screen early rather than last.
  Reconciled at startup now, in the service and the CLI alike: whatever is still
  unfinished belonged to a process that no longer exists. `FAILED`, not
  `CANCELLED` — cancelled means a client asked to stop, a different fact about a
  different actor — and not `PARTIAL`, which §11.10 reserves for a run that kept
  *some* results, where these kept none. **The assumption to re-check is one
  line in that function**: it reads "unfinished" as "abandoned", which is true
  of one local service and false the moment two share a database.
- **The evidence identity recorded what was *asked for*, not what ran.** §12.2
  keys evidence on the configuration a number was produced under, and the engine
  put `spec.load` — the request — into that key. It went unnoticed while every
  runtime honoured the request, and stopped being true the moment a build did
  not: `lms load --context-length 8192` is applied for ordinary GGUF builds and
  **ignored by LM Studio's vision models**, which load at their own default, so
  `gemma-4-e2b` ran at 131072 and hashed to the same evidence ID as a run that
  genuinely ran at 8192. The mismatch was reported as a validity warning
  throughout — §7.1 working — but a warning is prose, and anyone comparing
  evidence IDs saw two identical keys. The identity now carries the effective
  values where the runtime reports them, and where it reports nothing the
  request stands without pretending it was confirmed. The general lesson is the
  one already learned about adapted prompts, arriving a second time: **anything
  that changes what was measured belongs in the key, not in a note beside it.**
- **The thermal reading said `nominal` through a 48% throttle.** A fanless
  MacBook Air, asked to load and benchmark eighteen models back to back, slows
  down — and the same build measured **38.4 tokens/second heat-soaked against
  56.8 after ten minutes of rest**, on byte-identical work. That is most of the
  variance recorded above, and the machine had been reporting `nominal`
  throughout, because `pmset -g therm` prints "No thermal warning level has been
  recorded" on Apple Silicon whatever is happening and the probe read that as
  good news. A reading that cannot say *no* is not a reading.
  `NSProcessInfo.thermalState` is documented, unprivileged and does move —
  `nominal` for 64 seconds under sustained inference, `fair` at ~72. §11.8's
  thermal flag is built on it now, sampled at both ends of the measured work
  because the case that matters is a machine that became compromised *while*
  being measured. Unknown is deliberately not treated as compromised: silence
  from a platform that does not implement this is not evidence of heat.
  **Absolute temperature is not available** without the private
  `IOHIDEventSystemClient` API, and pressure answers the question anyway — the
  question is whether a number was taken under duress, not how many degrees it
  was.
- **Cooling between runs did not make a fanless machine repeatable.** The
  cooled sweep waited for `nominal` before every build — 50 to 110 seconds each
  — and bracketed the whole thing with the same control build. The control came
  out at 56.0 ± 0.9 at the start and **70.6 ± 10.6 at the end**: 26% *faster*,
  with fifteen times the spread. The expectation was monotonic decline that
  cooldowns would drain away; what the bracket found was instability whose
  magnitude itself varies, and 9 of the 21 runs still crossed `nominal → fair`
  inside their own five repetitions. The mitigation was worth building — it is
  the difference between a corpus with a stated ±25% ruler and one with an
  unexamined one — but **the bracket, not the cooldown, is what made the limit
  knowable.** A control measured twice costs one extra run and converts an
  invisible confounder into a number.
- **The spread a benchmark publishes is not the spread it has.** Five
  repetitions of `granite-4.0-h-tiny` agreed to ±1.6% within a run, twice — and
  the two runs disagreed with each other by **16%**, on byte-identical work:
  same 256 content chunks, same 256 reported tokens, different wall clock. The
  published number is a within-sitting consistency figure and says nothing about
  whether the same build measured tomorrow lands in the same place. It was
  caught by re-running one build to check that an engine change was neutral,
  which it was; the machine was not. The consequence is a reading rule rather
  than a fix: wide gaps in a comparison table are real, adjacent rows are not,
  and closing that properly needs repeated experiments over time (M20) rather
  than more repetitions inside one.
- **A model can do its thinking *inside* the content stream, and be measured as
  though it were answering.** Some runtimes route thinking into
  `reasoning_content`, where this engine never sees it. `tencent/Hunyuan-1.8B`
  does not: its `<think>` block arrives as ordinary content, and a benchmark of
  it reported a **0.043 s time-to-first-token and 55 tokens/second over 256
  tokens containing no answer at all** — while looking clean, because from the
  stream's point of view the model was talking, so the empty-content check could
  not fire. The engine now finds where the answer starts, times from there, and
  keeps the thinking separately. The three-valued `answer_offset` is the load-
  bearing part: a first chunk of `<th` is neither a think block nor an answer
  until more of it arrives, and deciding early either way mis-times the token
  that the whole metric rests on.
- **A throughput of 767 tokens/second, at `MEASURED` provenance, from
  arithmetic that was correct.** Generation throughput divides output tokens by
  the window after the first token — everything before it is the runtime reading
  the prompt, everything after is it writing. A reasoning model breaks that
  split: `lfm2.5-2.6b-mlx` spent 3.36 s generating 228 tokens of *thinking* and
  then 0.33 s producing 27 tokens of answer, and dividing all 255 by the answer
  window published an impossible number for a 2.6B model on a laptop. Nothing
  was wrong with the formula; it was being handed tokens the content window
  never saw. The rate is now computed from tokens that actually arrived as
  content, published at `ESTIMATED` when the runtime's count disagrees with the
  stream, and the gap is reported. The threshold for "disagrees" was measured
  rather than chosen — ordinary models show content for 93–100% of their
  reported tokens, this one for 11%. **It surfaced only because a live run
  produced a number a person could see was impossible**, which no test asserted
  and no gate would have caught.
- **LM Studio accepts `chat_template_kwargs` and ignores it.** Probed while
  looking for a way to switch a reasoning model's thinking off:
  `{"enable_thinking": false}` produced a response byte-identical to the
  baseline — zero characters of content, 284 of reasoning. Same shape as the
  200-with-an-error-body trap, and the reason §7.1 insists an unsupported
  setting is reported rather than dropped. It is also why the benchmark spec
  parser refuses generation settings it cannot honour instead of passing them
  through hopefully: a specification carrying that key would have produced a
  thinking-on measurement labelled thinking-off. The switch that does work for
  Qwen is `/no_think` in the prompt, which the spec already carries and §11.5
  already versions.
- **A three-valued answer stored in a two-valued type, twice.** Clarvis's §8.7
  tool probe asks whether a model can call tools and can receive three answers —
  yes, no, or *could not tell*. `supportsTools` returns a boolean and the result
  is cached for the session, so "could not tell" was expressible only by
  throwing, and the throw was gated on a list of three status codes. Every
  status nobody had thought of was recorded as a definitive **no**. Clarvis had
  already been burned by this once, with a bad credential, and had fixed the
  *instance* — 401, 403, 429 — rather than the rule, which is exactly why RAVIS
  answering 502 brought it straight back.
  Fixed from both ends. Clarvis now decides by what a status *means*: a 4xx is
  the server rejecting the request, and the only unusual thing in a probe is its
  `tools` parameter, so that is an answer; anything else — a 502, a 503, a
  timeout, a refused connection — is the server failing to answer, and is never
  remembered. And RAVIS stopped sending a 4xx for a condition that resolves
  itself: a pool whose candidates are all in breaker cooldown answers **503**,
  while a pool nothing satisfies stays **422**. The distinction is carried
  structurally on the route decision rather than recovered from prose, because
  a status derived by matching strings is one that breaks when a message is
  reworded.
- **Acquiring a model that was already loaded used to load it a second time.**
  Found by a test asserting that a warm benchmark performs no load at all. The
  Resource Manager tracked only what *it* had loaded, so a model LM Studio held
  — JIT-loaded, or loaded by hand — looked cold to it. LM Studio does not
  reconfigure a resident model to satisfy a request; it loads another instance,
  so the manager would have been accounting for one copy while the machine held
  two. It now adopts what is already there. The lesson is the one M8 was already
  built around and this still slipped past: **ownership and residency are
  different questions**, and code that knows only its own bookkeeping will
  happily duplicate the machine's.
- **A `Measurement` that omits a metric beats one that averages what survived.**
  The engine drops a metric entirely if even one repetition could not produce it
  — a median over "the three takes that happened to report token counts" has a
  sample size decided by coincidence, and §11.7's whole argument is that a
  headline means something only in relation to the samples behind it.
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
ravis/                 the routing gateway (M0–M18a, M12, M9, M3b, M4)
sirvis/                the evidence plane (M0–M4, M6–M10)
```

Clarvis lives in its own repository (`../clarvis`) — different language, runtime
and release cadence, and `clarvis/plan.md` is normative for it.
