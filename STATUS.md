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
.venv/bin/pytest                      # part of 484 tests, no network, no live service
.venv/bin/ravis conformance clarvis   # the §8.9 release gate — 16 checks
```

The other two packages are checked the same way, from their own directories:

```bash
cd protocol && ../ravis/.venv/bin/python -m pytest -q   # 15 tests
cd sirvis   && ../ravis/.venv/bin/python -m pytest -q   # 188 tests
```

**`ecosystem-protocol` must be installed first.** It is a local path dependency
and pip will not find it on PyPI, because it does not live there.

Expected: all clean, 484 passing across the three, conformance `PASS`. CI runs the same four on
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
| 19 | **SIRVIS M6** | The single-model benchmark engine: §11.2's lifecycle end to end, warmups, repetitions, raw response capture, TTFT, throughput, memory. **Run live against six builds**, done 2026-08-23 — numbers below |
| 20 | **RAVIS fast-switch hardening** | A reproduced livelock against a dead upstream, the pre-commit refusal gate — three signals that arrive while a model can still be swapped and were being thrown away — and the §8.7 probe poisoning that fixing them exposed, which needed a change in Clarvis's repository as well as this one |

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

### What the first live runs measured

**Every installed build, ten of them**, one prompt, 2 warmups and 5 measured
repetitions each at a requested 8192 context, on 2026-08-23. All at `MEASURED`
provenance with real token counts — LM Studio reports usage, so nothing here is
derived from counting stream chunks.

| Build | Format | TTFT | tok/s | Load |
|---|---|---|---|---|
| `granite-4.0-h-tiny` | mlx 4bit | 0.191 s | **101.0** | 3.79 s |
| `granite-4.0-h-tiny` | gguf Q4_K_M | **0.056 s** | 52.7 | 1.45 s |
| `phi-4-mini-instruct` | mlx 4bit | 0.479 s | 31.0 | 3.80 s |
| `qwen2.5-coder-7b-instruct` | mlx 4bit | 0.296 s | 29.8 | 4.67 s |
| `ministral-8b-instruct-2410` | mlx 4bit | 0.274 s | 27.9 | 4.17 s |
| `qwen3-4b-2507` | mlx 4bit | 0.468 s | 27.2 | 3.47 s |
| `meta-llama-3.1-8b-instruct` | mlx 4bit | 0.382 s | 18.6 | 4.47 s |
| `qwen2.5-coder-14b-instruct-mlx` | mlx 4bit | 0.630 s | 10.8 | 4.87 s |
| `qwen3-1.7b` | mlx 8bit | *no answer* | *no answer* | 3.31 s |
| `lfm2.5-2.6b-mlx` | mlx 4bit | *no answer* | *no answer* | 4.61 s |

*No answer* means exactly that: the build ran, generated tokens and emitted no
content, so there was no first token to time and no answer to divide by. It is
not a gap in the measurement. **Every row above is the `performance-basic`
suite**, and the `performance-no-think` number further down belongs to a
different suite with a different prompt — the two are not comparable and are
never listed together, which is the whole reason §11.5 versions a suite by its
prompts.

**The format pair is the point, and it splits in opposite directions.** Same
family, same weights, two packagings: MLX generates at **1.9× the GGUF's rate**
and takes **3.4× longer to reach its first token**. Neither is "the faster
build". §12.2 makes format part of evidence identity, and this is why — the two
carry different evidence IDs and there is no honest way to average them.

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
| `qwen3-1.7b` | yes | `/no_think`, `/nothink`, **and a plain instruction** |
| `tencent/Hunyuan-1.8B` | yes | `/no_think` |
| `lfm2.5-2.6b-mlx` | yes | nothing stops it; an instruction gets it answering *while still thinking* |
| `deepseek-r1-distill-qwen-1.5b` | yes | **nothing** — all six failed, no content in any |
| `qwen3.5-2b`, `qwen3-4b-2507` | no | — |
| `granite-4.0-h-tiny`, `phi-4-mini`, `ministral-8b`, `llama-3.1-8b` | no | — |

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
| **`/api/v1/runtimes/{runtime_id}` and `/runtime-instances` are unbuilt**, and `/runtimes/{key}/models` is a path §4.2 does not list | §4.2 | no |
| **No job state machine.** §11.10's coarse published enum and its atomic commit are built; the *queue* behind them — pause, resume, retry, reorder, and a job that survives a restart — is not | §11.10 | partly |
| **Parquet telemetry** is named for high-frequency data where SQLite becomes unsuitable | §17 | no — not at one-run scale |

Everything shipped-and-wrong on that list has been fixed. What remains is
absent, which is the cheaper kind of gap: nobody is building on top of it.

### The rule about loading — still read this first

M6 is the first milestone that **loads models to do its job**, and an earlier
session loaded four onto the developer's machine without asking. The cause is
fixed and `sirvis benchmark run` now names what it is about to load and waits
for an answer — refusing outright when stdin is not a terminal, because an
unattended script that meant to do this can pass `--yes` and one that did not
should not find out by discovering a 14 GB model resident an hour later.

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

### Next — in this order

| # | Milestone | Why here |
|---|---|---|
| 21 | **M3b + M4 (RAVIS)** | The translated execution path and the Anthropic adapter. Permitted now that the transparent path is proven by something other than fixtures — and this is where tool-call framing actually gets hard. Runs in parallel; Stage 5 needs Stage 4 finished |
| 22 | **SIRVIS M9 + M10** | Runtime Sets and multi-model benchmarks — §21.1's *second* vertical slice, and the only way to answer the question §10.1 asks: two models that each fit do not necessarily work together |
| 23 | **SIRVIS M16** | The RAVIS evidence API. Inside Stage 4, not after it: Stage 5 exits on a SIRVIS result changing a RAVIS preference, and that needs a real producer rather than a test double |

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
ravis/                 the routing gateway (M0–M18a, M12, M9)
sirvis/                the evidence plane (M0–M4, M6, M7, M8)
```

Clarvis lives in its own repository (`../clarvis`) — different language, runtime
and release cadence, and `clarvis/plan.md` is normative for it.
