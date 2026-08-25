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
.venv/bin/pytest                      # part of 918 tests, no network, no live service
.venv/bin/ravis conformance clarvis   # the §8.9 release gate — 16 checks
```

The other three packages are checked the same way, from their own directories:

```bash
cd protocol && ../ravis/.venv/bin/python -m pytest -q   # 17 tests
cd sirvis   && ../ravis/.venv/bin/python -m pytest -q   # 348 tests
cd nervis   && ../ravis/.venv/bin/python -m pytest -q   # 29 tests
```

**`ecosystem-protocol` must be installed first.** It is a local path dependency
and pip will not find it on PyPI, because it does not live there.

Expected: all clean, 918 passing across the four, conformance `PASS`. CI runs the same four on
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
| 29 | **RAVIS M8** | The LM Studio and Ollama adapters, and `upstream_kind` selecting between them and the generic one. **Both verified live, 2026-08-24** — LM Studio's catalogue turns 12 of this machine's 20 builds from `UNKNOWN` into `ADVERTISED` tool support and gives every one a context window; Ollama's array proved to enumerate, so absence within it is now read as denial. It also produced the corpus's first catalogue-versus-measurement disagreement — settled below. Plural upstreams landed the same day — `RAVIS_UPSTREAMS`, per-upstream adapters and registries, name-addressing, and a collision rule three code paths share |
| 30 | **RAVIS M10** | Credentials and the provider UI — a `Secret` type that refuses to render itself, an OS-agnostic 0600 credential file with Keychain and environment behind it, provider enable/disable that actually stops a provider being routed to, and health per provider. **Stage 2 closed with it.** Settled below |

**Stages 0, 2, 3 and 4 are complete. Stage 1 is not**, and this file claimed it
was. The runbook's Stage 1 requires the metadata endpoints "in SIRVIS, RAVIS and
**NERVIS**", and exits when all three "pass live MEP conformance at one pinned
protocol version". SIRVIS and RAVIS do. **NERVIS has no MEP surface at all** —
NERVIS M0 has never been built, and `nervis/` is one HTML file. Two services out
of three is not a stage. Stage 2 completed as of 2026-08-24, when M10 landed. This file claimed Stage 2 was complete for some time before that, and
was wrong: the stage mapping puts **RAVIS M10** in Stage 2, *"since an upstream
needing a credential cannot be reached without it"*, and M10 had never been
built. No gate caught it; a question about where an operator would type an API
key did. Stage 4's last piece was SIRVIS M15; the evidence plane measures,
stores, serves and recommends.

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

### The swap answer, and the reproducibility problem it turned up

The pair re-run to get the baseline the previous run could not sample. Run
`run_f8eedea030ba457d`, `sirvis/results/exp_ba3c125d1bd24f9a`.

```text
condition        available     swap used
alone              7.79 GB        184 MB
sequential         5.89 GB        184 MB
alternating        5.78 GB        184 MB
concurrent         5.80 GB        184 MB
```

**Swap does not move.** 184 MB alone and 184 MB in every co-resident condition,
against a machine that already had 183.5 MB in swap with nothing loaded — so
that figure is pre-existing and not caused. Co-residency costs about **2 GB of
available memory and no swap at all**, which completes the sentence this
repository has been carrying since before it could check it: memory behaved as
arithmetic predicted, and behaviour did not.

**The more useful finding is that the pair does not reproduce.** The same set,
same suite, same evening:

```text
                       run 1    run 2    drift
agent alone             68.1     58.0   -14.8%
agent sequential        68.2     58.1   -14.8%
chat  alone             49.8     49.7    -0.2%
agent concurrent        44.6     45.7    +2.3%

agent concurrent degradation   +34.4%   +21.2%
chat  concurrent degradation   +22.1%   +21.4%
```

The agent's **alone** figure dropped 14.8% between runs while everything else
held within about 2%, and degradation is computed *against* alone — so its
contention penalty swung thirteen points without contention changing at all. On
a fanless machine that is thermal drift, and §11.7 already records ~32%
run-to-run variation with an earlier sweep bracketing itself 26% apart. The
lesson was in this file; the arithmetic had not been told.

So M15 no longer takes the newest matrix. It uses **every** run of the pair and
reports the spread.

**A third run settled which figure to use, and it is not the worst one:**

```text
                       run 1    run 2    run 3   spread
agent alone             68.1     58.0     57.6   18.2%
agent sequential        68.2     58.1     58.7   17.4%
agent alternating       59.8     59.1     59.2    1.2%
agent concurrent        44.6     45.7     45.3    2.3%
chat  (every condition)                          <1.1%

agent concurrent degradation   34.4%    21.2%    21.3%
chat  concurrent degradation   22.1%    21.4%    21.5%
```

Runs 2 and 3 agree to a tenth of a point. Run 1 is eighteen percent adrift and
**only on the unconstrained conditions** — its concurrent and alternating
figures sit with the others. So the outlier is in the *baseline*, not in the
contention, and a degradation computed against it inherits the error whole.

The penalty is therefore the **median**, which is §11.7's convention for exactly
this reason. Taking the worst would anchor a recommendation on the one
measurement its other two runs contradict: the combination score is `1.785` on a
21.5% median rather than `1.656` on a 34.4% outlier.

**A fourth run carried the first thermal readings, and they answered a
different question than the one asked.**

```text
                            run 1    run 2    run 3    run 4   median
agent alone                  68.1     58.0     57.6     58.9     58.4
agent sequential             68.2     58.1     58.7     58.6     58.7
agent alternating            59.8     59.1     59.2     57.6     59.1
agent concurrent             44.6     45.7     45.3     45.2     45.2
chat  (every condition)                        within 2.5% throughout

run 4 thermal   baseline nominal · alone nominal · sequential nominal
                alternating fair · concurrent fair
```

Run 4's **alone** was measured at `nominal` and came back **58.9** — so thermal
does not explain run 1's 68.1, and that outlier remains unaccounted for. What
the readings did show is worse than an outlier, because it is systematic:

**the machine crossed from `nominal` to `fair` in the middle of every run.** The
control is measured first, on a machine that has just been idle; concurrent
generation is measured last, after every other mode has run. So every
degradation figure compares a nominal measurement against a fair one, and the
contention number carries whatever drift accumulated in between.

That is a bias with a direction rather than noise: the control is always taken
under the better conditions, so contention is **overstated** by whatever the
machine lost along the way. §11.8 asks for exactly this to be flagged and never
silently discarded, so a run whose thermal state moves now says so in its
validity notes. Flagged rather than corrected — the correction would be a guess.

**The gap that let this hide for four runs.** §11.8 asks for thermal capture and M6 records two readings per run; M10
discarded the reader under a comment saying it happened "per mode below", where
it never did. So the first three runs of a real pair recorded no thermal state
at all. M10 now takes a reading per condition — baseline, alone, and each mode —
because a degradation percentage compares two conditions and one reading for the
whole run cannot say which of them was measured warm. The very first run that
carried them found the drift described above.

**Two bugs found in the process, both in the counting rather than the
measuring.** The matrix collector deduplicated the *raw* matrix against an
already-enriched list, so a run whose matrix needed enriching was counted twice
— and the recommendation reported "3 runs" where there were two, in the one
field that exists to say how much measurement is behind a number. And a test
fixture pinned the chat row at a constant, which floored every spread assertion
at that value until one came back 22.1 where 21.2 was expected.

### The fifth run — contaminated, and retracted

The one condition the first four never had: a machine that had been left alone
for nine hours. Run on 2026-08-24 at 21:49 CEST, with the evidence captured
before anything loaded — uptime 5 days with no reboot, no thermal warning level
recorded, nothing resident, 175 MB of swap in use.

```text
                       run1   run2   run3   run4    cold
  agent/alone          68.1   58.0   57.6   58.9    57.6
  agent/sequential     68.2   58.1   58.7   58.6    57.4
  agent/alternating    59.8   59.1   59.2   57.6    59.5
  agent/concurrent     44.6   45.7   45.3   45.2    45.3
  chat/alone                                        49.3
  chat/concurrent                                   38.9
```

**This run is void, and the conclusion first drawn from it is withdrawn.** Two
things were running during it, neither visible to the pre-run evidence capture
and both reported by the operator afterwards: a **Time Machine backup**, and an
application that had put macOS into **Game Mode**. Game Mode deprioritises
background processes and a backup competes for I/O — both depress a benchmark
driven from a CLI.

So 57.6 is a floor rather than a measurement. It was briefly recorded here as
ruling cold out, on the grounds that `agent/alone` came back the *lowest* of the
five rather than the highest. That inference is wrong: a handicapped run coming
back low says nothing about whether a cold machine runs fast. **Cold remains
untested**, and run 1's 68.1 remains unexplained for the same reason it always
was.

The pre-run capture — uptime, load average, thermal level, resident models — saw
none of it. A backup is I/O-bound and shows no CPU in a `ps` snapshot, and Game
Mode is a scheduler policy with no reading of its own. That is a real gap in what
"is this machine quiet?" was being asked, and the honest fix is a checklist item
for the operator rather than another probe, because the two things that mattered
here are both invisible from inside the process.

**It did stay `nominal` throughout** — baseline, alone, sequential, alternating
and concurrent — where run 4 crossed to `fair` between sequential and
alternating. That reading is probably trustworthy on its own terms: a
deprioritised, I/O-starved run generates less heat, which is consistent with
never leaving nominal and is not evidence that the thermal bias is absent.

Its degradation figures — agent 21.4%, chat 21.2% — were briefly offered as weak
support for that bias. They are withdrawn too. The four conditions were not
necessarily contaminated *equally*: backup I/O is bursty, so a ratio between two
conditions measured minutes apart is not obviously safer than the absolute
numbers. The bias argument stands on the ordering of the phases, which is
structural; it gains nothing from this run.

**What has not changed** is the baseline load near 2.0 on every run including
this one — the desktop client rendering the session that drives the benchmark.
No run in this corpus was taken on a genuinely idle machine, and that is a
constant across all five rather than a difference between them.

**What a real cold run still needs**: no backup, no Game Mode, nothing else
competing — checked by the person at the keyboard, because neither is visible
from inside the process. The scheduled task written to do this unattended was
deleted after this run; a replacement should say so in its pre-flight and stop
rather than measure through it.

### M10 — somewhere to put the key

**The question that found it:** *"does the UI have a menu where we can enter our
API details?"* It did not, and nothing built so far could have reached Google or
OpenRouter, because there was nowhere to put a credential. M7 would have shipped
two providers that could be described and never used.

**Secrets absent from all outputs and logs, carried by a type.** `Secret`
refuses to render itself — `repr`, `str`, f-strings, `format()` and `%` each
yield a redaction naming the source and length but never the value, and each
path is tested separately because a guarantee that holds for `repr` but not for
`%` is not a guarantee. `json.dumps` raises rather than serialising. The value
leaves only through `reveal()`, named so that `grep` is the audit.
`CredentialStatus` is the stronger promise for anything a screen touches: not a
value it declines to show, but no field able to hold one.

**A file, because the store had to work on every OS.** Mode `0600` in the user's
config directory — `%APPDATA%` on Windows, XDG elsewhere — which is what the AWS
CLI, Docker, `gh` and npm all do. Stdlib only. A credential-vault dependency
buys encryption at rest and costs a native build requirement on three
platforms, and the key that would decrypt it has to be readable by RAVIS
unattended, so anyone with the same file access reads it too: the problem moves
rather than resolves.

**macOS Keychain is a read source and deliberately not a write target.**
`security` takes the password as a command-line argument, which publishes it in
the process list to everything running as that user. There is no stdin form —
tested, and `-w` swallows the following argument instead. Writing to a file has
no such window.

**Order: file, Keychain, environment.** The file is first because it is what the
UI writes and the most recent explicit action should win. Environment variables
keep working, so every deployment written before M10 is unaffected — but the
source travels with the value, which is what lets a screen say *where* a
credential came from without saying what it is.

**`PUT`, not `POST`, and the CORS line that had to move.** Setting a named
credential is idempotent, which is what M18b's `Idempotency-Key` exists to
reconstruct for operations that lack it. `CORS_METHODS` was reads-only with a
comment deferring the decision to whoever landed the first mutation; this is
that mutation. PUT and DELETE are now allowed, and the reasoning is recorded
where the old comment was: `allowed_origins` is empty by default so no origin
gains anything until an operator names one, and PUT *always* preflights where a
simple POST does not — so a page that was never allow-listed cannot slip a
credential write through.

**Two claims in the dashboard were false and are corrected.** It advertised
`store:'macOS Keychain', plaintext_fallback:false` — a security property the
system did not have — and stated credentials were never editable from a screen.
Both were written before there was an endpoint. `RAVIS → Credentials` is now a
real screen.

**Verified live end to end**, 2026-08-24: a value entered in the dashboard,
written to a `-rw-------` file, and then found in none of the served page, the
API responses, or the service log. The test value and its file were deleted
afterwards, and no real configuration directory was created.

**Enable/disable, and what a toggle has to mean.** A switch that changes a badge
and nothing else is worse than no switch, so a disabled provider is not probed,
not advertised in `/v1/models`, not a routing candidate, and returns 503 when
addressed directly. Verified live: disabling the upstream took `/v1/models` from
20 models to 0 — the 13 pools stayed, correctly, because those are RAVIS's own —
and re-enabling brought them back.

State lives in `providers.json` beside the credential file but **not** at mode
`0600`: nothing in it is secret, and a private file would be a small lie about
what it holds. A provider absent from the file is **on**, so the file records
decisions rather than state and nothing needs migrating when a provider is
added. Both directions are written rather than deleting the entry on enable, so
an operator can tell "someone turned this back on" from "nobody has ever touched
this". A corrupt file leaves everything **enabled** — the permissive direction,
which is wrong for a security control and right for this: a mangled file should
not silently turn a gateway into one that serves nothing.

**Disabling is not deleting the key**, and the UI says so. Disabled means "not
right now" and is one click back; removing the credential says this deployment
no longer holds one. A UI offering only the second would make an operator
destroy configuration to achieve a pause.

**One ordering bug, caught by its own test.** The refusal for a disabled
provider sat below the "no upstream is configured" guard, so addressing a
disabled *translated* provider on a deployment with no transparent upstream
answered "set RAVIS_UPSTREAM_BASE_URL" — pointing at a setting that had nothing
to do with it. The specific refusal now precedes the general one.

**`/api/v1/providers` listed one provider until now.** It reported the single
primary adapter, which predates M8: a deployment reaching three upstreams showed
one row. It now lists every transparent upstream and every translated provider,
probes their health concurrently, and skips the probe for anything disabled.

### Model filters — 417 models, and the elegant way out

**The number that forced this.** OpenRouter's public catalogue was fetched and
counted: **417 models**. Without a filter every one lands in `/v1/models`, which
Clarvis renders as a picker, and every one becomes a routing candidate evaluated
on each request. A provider that floods the catalogue makes the whole gateway
worse to use.

**Globs, and deliberately nothing more.** Filtering on capability belongs with
M16's policy engine and filtering on price needs a cost model, which is M15 —
inventing one here would be §14's "presenting an estimate as an invoice" in a
different costume. Patterns solve the actual problem. Measured against the real
catalogue:

```text
  no filter                         417 / 417
  include anthropic/*                28 / 417
  include anthropic/* + openai/*    121 / 417
  …minus previews, betas and :free  120 / 417
  one pinned model id                 1 / 417
```

**Exclude wins over include**, so `include: openai/*` with `exclude: *-preview*`
means "all of OpenAI except the previews" — the other precedence would make the
exclusion unreachable. **An empty include means everything, not nothing**, so an
exclude-only filter is useful on its own. Matching is case-sensitive: model ids
are opaque strings in a URL path, where `GPT-4` and `gpt-4` are not
interchangeable however a human reads them.

**No filter means no filtering.** Not a cap, not a sample, not a truncation with
a warning — a provider with no filter behaves exactly as it did before this
existed. Silent truncation would read as "covered everything" while hiding
models, which is the failure this is meant to prevent rather than commit. The
preview endpoint reports `matched_total` separately from a capped `sample` for
the same reason.

**The same rule in three places.** `merged_catalogue`, `merged_candidates` and
`resolve` apply it identically, so the list a client reads, the candidates the
router picks from, and the upstream the forwarder targets can never disagree
about whether a model exists — the same discipline declaration order gets as the
one collision rule.

**Filtered before capabilities are assembled**, not after: against OpenRouter
that is the difference between building 417 capability records per routing pass
and building the handful an operator asked for.

Verified live against LM Studio: a filter took the advertised catalogue from 20
models to 3, and `/v1/models` agreed. The count in the UI is a link that opens
the pattern editor for that provider.

**M10 is complete.** Stage 2 is complete with it.

### The outlier: closed, unexplained, and deliberately so

**Decision, 2026-08-24: stop chasing run 1.** Four valid runs put `agent/alone`
between 57.6 and 58.9 — a spread of 1.3 tok/s. Run 1 sits at 68.1 and nothing
accounts for it. Thermal was ruled out by run 4, cold was never successfully
tested, and two attempts to test it produced a contaminated run and an aborted
one.

**What made this safe to close rather than merely convenient.** Every figure
downstream of these runs is a **median**, not a mean — `_summarise` in
`sirvis/src/sirvis/benchmarks/multi.py` and `_median` in
`sirvis/src/sirvis/core/recommendations.py`. A single high outlier among four or
five points cannot move a median, so §14.3's recommendation, the interaction
matrix and every degradation percentage read the same with run 1 present or
absent. The outlier is a curiosity in the record rather than a term in any
answer.

**What it cost to find that out**, worth keeping because the cost was the
lesson: three separate contaminants, none visible to the pre-run capture. A
Time Machine backup and macOS Game Mode during the fifth run — both reported by
the operator, neither detectable from inside the process. Then Spotlight
reindexing, apparently triggered by cancelling that backup, which drove load
from 2.0 to 6.85 and made the sixth attempt not worth taking; the cleanup was a
larger contaminant than the thing cleaned up.

**If someone reopens this**, the bar is a machine with no backup, no Game Mode
and a settled Spotlight index — checked by a person, since the first two have no
probe and the third only shows up as unexplained load. And one clean run would
still not settle it: run 1 is a single observation, and no single re-run can
separate "cold matters" from "run 1 was a fluke".

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

### What M8's first half settled, and the disagreement it found

The adapters are thin on purpose. LM Studio and Ollama both speak the OpenAI
protocol, so both subclass `GenericOpenAiAdapter` and override exactly one
method: `capabilities`. Reaching an upstream was already solved and a second
implementation of it would be a third place to drift.

What changes is what RAVIS can *learn*. Against this machine's running LM Studio,
`/api/v0/models` turns **12 of 20 builds** from `UNKNOWN` tool support into
`ADVERTISED`, and gives all 20 a real context window where `/v1/models` gave
none. Before this, an agent pool against a local runtime was unavailable unless
an operator declared capabilities by hand.

**Then the catalogue was caught being wrong.** LM Studio advertises `tool_use`
for *both* packagings of `granite-4.0-h-tiny`. SIRVIS measured the GGUF build
passing 24 of 24 tool trials and the MLX build passing 3 of 24. The
advertisement is false for one of them, and the two builds are indistinguishable
from the catalogue alone.

This is the first time the provenance ordering has had a real disagreement to
resolve rather than a fixture. `MEASURED` outranks `ADVERTISED`, so the MLX build
is excluded from `ravis/clarvis-agent` on evidence while the GGUF build is
admitted — and `test_measured_evidence_overturns_the_catalogue_for_the_mlx_build`
pins that with the real identifiers and the real counts. §9.5 was written on the
argument that catalogues have been observed to be wrong in both directions.
It now has a local instance of that.

**Three restraints, each of which could have been over-claimed.**

A *missing* `capabilities` array is left as `UNKNOWN`, not recorded as
unsupported. It is absent for 8 of the 20 builds here, and silence is not a
denial — `UNKNOWN` already fails closed under §9.1, so nothing is lost by
declining to invent the stronger claim. An *empty* array is treated identically,
because a field defaulted to empty and a field populated with nothing are the
same bytes.

LM Studio's `type` is claimed in both directions, unlike the capability array,
because it is always present and closed — `vlm` means vision, `embeddings` means
not a chat model. That last one is claimed explicitly rather than left alone:
the protocol default had already recorded `TEXT` as supported, and leaving it
standing would have put an embedding endpoint in a text pool.

**The Ollama adapter shipped under-claiming, and a live run earned the stronger
reading.** It was written to the documented `/api/show` shape with mock tests
only, so it made *positive claims only* — where the LM Studio adapter says
UNSUPPORTED, it stayed silent. Ollama 0.32.3 was then started and asked, and two
models settled it: `llama3.2:3b` reports `["completion", "tools"]`, and
`all-minilm` reports `["embedding"]` **alone**, declining even `completion`. An
embedding model that will not claim to complete is an array that enumerates
rather than annotates, so absence within it is a denial and is now recorded as
UNSUPPORTED.

**The reading is closed over Ollama's vocabulary and no further.** The five
tokens it uses — completion, tools, vision, embedding, thinking — are denied
when absent. Structured output, parallel tools, prompt caching and audio are
not: an array that was never going to mention them says nothing about them, and
reading that silence as a denial would cost a pool a candidate on the strength
of a subject Ollama does not discuss. `test_a_capability_ollama_has_no_word_
for_is_never_denied` pins the boundary.

The embedding case is the one with teeth. Without the closed reading, `TEXT`
would still be SUPPORTED on `all-minilm` from the protocol default, and an
embedding endpoint would sit in a text pool waiting to be handed a chat request.

`/api/show` reads the manifest and loads nothing — `ollama ps` stayed empty
throughout, so none of this cost a model load.

**A defect the first half introduced, and the comment that predicted it.**
`ravis/src/ravis/api/openai/chat.py` assembles capabilities per request, and said so with a warning: it was
cheap because the generic adapter answers without I/O, and *"the moment an
adapter needs a network call to answer, this is the line that has to change"*.
Both vendor adapters need one. `candidates_with_evidence` asks per model, so a
single chat completion against LM Studio cost one GET per installed build — 20
of them here, sequentially — and configuring a second upstream would have
doubled it.

Both adapters now cache: LM Studio the whole catalogue, Ollama per model,
because `/api/show` is a POST taking one model and cannot be batched. Sixty
seconds, on the grounds that what is read describes a *build* and does not
change while it sits on disk. Residency does change and is deliberately not
served from this cache — `runtime/lmstudio.py` still reads the endpoint itself.

**A failed read is not cached**, and that is the part worth keeping. Every model
looks capability-less while the catalogue is unreadable, and capability-less
fails closed under §9.1 — so holding a transient outage for the whole window
would empty every pool for a minute instead of for a request.

### What the plural half settled

`RAVIS_UPSTREAMS` declares a JSON list; the singular settings still work and
still mean one upstream named `default`, which is what every deployment written
before this is. Each declared upstream gets its own adapter and its own
registry, and every catalogue is refreshed concurrently at startup.

**Names are addresses.** An upstream's name occupies the same
`ravis/<name>/<model>` slot a translating provider's does, so a client asking
for a specific place does not have to know whether that place needs
translation — and it is the only way to reach a model two upstreams both serve.

**One collision rule, in three places.** Declaration order breaks a tie, and
`resolve`, `merged_catalogue` and `merged_candidates` all use it. Three
agreeing matters more than any one being clever: the list a client reads, the
candidate the router picks and the URL the forwarder sends to must not disagree
about which of two identically-named models is *the* one.

**A fixed target would have sent fallbacks to the wrong host.** `_Call` carried
one URL and one header set for the whole request, while §10's chain walks
candidates — so a fallback on a different upstream would have gone to the
primary's address with the primary's credential, and failed in a way that
looked like the fallback model being broken. Target and headers are now
resolved per attempt.

**The residency merge nearly invented a fact.** `ResidencySnapshot.state_of`
returns COLD for an absent entry once *any* upstream reports residency. LM
Studio reports it and Ollama does not, so a naive merge would have made every
Ollama model read COLD and ranked it below a genuinely hot one on the strength
of a default. Models behind a non-reporting upstream are now recorded UNKNOWN
explicitly. Verified live: with both runtimes configured, `known=True` while
`llama3.2:3b` reads UNKNOWN.

**Verified live with both runtimes up, 2026-08-24.** `/v1/models` returned 35
entries — 13 pools listed once, 20 LM Studio models and 2 Ollama models — each
model resolved to the runtime that actually holds it, and
`ravis/ollama/<an LM Studio model>` overrode the catalogue as designed.

**M8's acceptance criterion, met live on 2026-08-24.** A completion was routed
through the second upstream, twice:

```text
resolve("llama3.2:3b")  ->  ollama @ 127.0.0.1:11434, OllamaAdapter
non-streamed    200   content "ROUTED"   usage 31 + 3 = 34
streamed+tool   200   finish_reason=tool_calls
                      name "get_weather"  args {"city":"Amsterdam"}
recorded        execution_path TRANSPARENT_OPENAI
```

The second call is the half the criterion actually names — *transparent local
streams preserve tool semantics*. The tool call arrived as stream deltas and
reassembled into an intact name and complete JSON arguments, with
`finish_reason` set to `tool_calls` rather than `stop`. Nothing in RAVIS
normalised it: `execution_path` records `TRANSPARENT_OPENAI`, so those bytes were
forwarded under §6's Path A, which is what makes preservation a property of the
forwarder rather than of a translation layer that happened to round-trip.

So the caveat recorded at M13 is now narrowed twice over:
`ravis/clarvis-agent` admits its member because a build **passed a
measurement**, and RAVIS can look at more than one runtime — though every
measurement in the corpus still came through LM Studio.

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
| 1 | **RAVIS M7** | Google and OpenRouter, the remaining native adapters. The last of Stage 5's provider work |
| 2 | **RAVIS M16** | Policy. The route explanations already carry everything it needs to decide on |
| 3 | **The rest of M14** | The load-versus-don't tradeoff. Blocked on M11 for expected session length |
| 4 | **Stage 6 — NERVIS core** | M11 + M15. M14's remaining half is waiting on M11 anyway |

### After that

What is left of Stage 5 is RAVIS's remaining intelligence: **M7** (Google,
OpenRouter), the rest of **M14**, and **M16** (policy). **M8 is done** — it was
still listed here eleven lines above the section that declares it finished. Stage 6 is NERVIS core — **M11** + **M15**.

**M3b, M4 and M13 are done, out of stage order**, and all three for the same
reason: §20.2 holds translation back until the transparent Clarvis slice works,
and that slice was the control whose correctness rested on nothing but fixtures.
M9 ended that. So the translated path was built, given a real provider to drive
it, and then — once SIRVIS could measure a Clarvis role — handed real evidence
to route on.

**M8 is done.** Both adapters, plural upstreams, and a real completion —
streamed, with a tool — routed through the second one. Verified live against LM
Studio and Ollama running side by side. Every *measurement* in the corpus still
came through LM Studio, which is a fact about the evidence rather than about
RAVIS's reach.

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

## The sweep: what the dashboard actually reads

Every screen was classified mechanically rather than by eye — each view function
matched against the data sources it touches, then every endpoint it asks for
probed against a running service.

**What it found.**

| | Screens |
|---|---|
| Read from a running service | SIRVIS Models, Runtime, Runtime sets, Results, Recommendations · RAVIS Pools, Credentials |
| Partly real | SIRVIS Dashboard · RAVIS Dashboard, Routes, Providers, Evidence |
| Entirely invented | SIRVIS Discover, Benchmarks, Downloads · RAVIS Policies, Logs, Diagnostics, Settings · every NERVIS screen · every CLARVIS screen |

Twenty-four of the twenty-eight `API.*` methods return invented data. Four try a
real endpoint. The **service dashboards were the worst of it and the last to be
caught**: SIRVIS's renders from nine sources of which two are live, RAVIS's from
four of which one is — and a landing screen full of confident invented numbers
is the most misleading page in the build, because it is the first one anyone
sees.

**Faded, not hidden.** Prototype cards drop to 40% opacity, desaturate, take a
dashed border and a `PROTOTYPE` label beside their heading, and come back to
readable on hover. Hiding them would lose the thing that *is* real about those
screens — the layout is the design for something not yet built. The point is
"do not trust this number", not "you may not look".

`.card.live` is the opt-out, so a mixed screen fades only its invented half. One
flag per screen would have meant either fading a working table or leaving a
fabricated one bright.

This is the ecosystem rule — *a control that implies an endpoint exists is the
one thing the dashboard must not do* — applied to **numbers** rather than to
buttons. It was already enforced for controls; the sweep found it had never been
enforced for data.

### Two real bugs the sweep turned up

**The launcher was orphaning every database.** Both services default to a
*relative* database path and the launcher runs them from the repository root, so
a first run created empty databases beside the launcher and came up healthy,
empty, and disconnected from **81 benchmark runs and 2 runtime sets**. It looks
exactly like a working install with no data. Both paths — and SIRVIS's §11.9
results directory — are now absolute.

**RAVIS was never told where SIRVIS is.** It started with an evidence store
reporting "configured: false", so every routing decision fell back to advertised
capabilities and the Evidence screen read zero records — which looks like SIRVIS
having no evidence rather than nobody having introduced them. The launcher now
sets `RAVIS_SIRVIS_BASE_URL`, and defaults RAVIS's upstream to LM Studio when
the operator has named none, because RAVIS with no upstream has no models,
therefore no candidates, therefore nothing to ask SIRVIS about — an emptiness
three steps removed from what it displays. After both fixes a single launch
brings up 20 models, 13 pools and 20 evidence records.

## Clicking an installed build

The rows on SIRVIS's dashboard were already clickable and did nothing —
`selectOne` highlighted the row and raised a toast reading *"Selection
updated"*. Nothing had been updated. That is the same defect the fade sweep
addressed one screen over: **a control must not imply an effect it does not
have**, and a toast is how a control claims it worked. The toast is gone.

Three things a click now does, all against endpoints that already existed:

```text
GET  /api/v1/models/{local_model_id}        what this build is
GET  /api/v1/evidence?variant={variant_id}  what has been measured of it
POST /api/v1/runtime/sessions               load it, under a lease
```

**The evidence query is a variant query, not a model lookup**, and that is
§12.2 showing through rather than an inconvenience. Evidence is never keyed by
model, so "this build's measurements" is asked by `variant` — the id that
encodes family, format and quantization together. Verified against the corpus,
and the two granite builds are why it matters:

```text
GGUF   context 1048576   lmstudio-community   agent 6 · clarvis-agent 2 · general 9
MLX    context  131072   mlx-community                 clarvis-agent 2 · general 3
                                              + family and variant disagree
```

One model name, two builds, different measurements — the same distinction that
admitted the GGUF build to `ravis/clarvis-agent` on 24 of 24 tool trials and
excluded the MLX build on 3. The panel says so in a line under the numbers,
because a reader otherwise wonders why it did not simply ask for the model.

**Load is gated on the runtime-scoped token** and says so in the button's title
rather than failing when pressed. It reuses `RUNTIME._send`, the call the
Runtime screen already drives, rather than a second implementation of the same
lease mutation. Releasing a lease that holds **more than one model** asks first
— §9's reference counting means releasing on one owner's behalf can unload
another's.

**Verified live, 2026-08-25.** `smollm3-3b` loaded through the panel under a
lease, then released through it. The run happened to produce §9's argument in
miniature, because a probe held a second lease on the same model at the time:

```text
after load      leases 2 (probe, nervis-dashboard)   refs 2   resident
release ours    leases 1 (probe)                     refs 1   still resident
release probe   leases 0                             refs 0   unloaded
```

Releasing one owner's lease did not unload a model another owner still held —
which is the whole reason the reference count exists.

**Two bugs the live run found, both invisible until pressed.**

The Load and Release buttons were built with `JSON.stringify`, whose double
quotes terminated the `onclick` attribute early and left the browser parsing a
truncated expression. The click did nothing, silently — no error, no toast. The
row handler had the same bug and had already been fixed; the buttons had not.
Calling `BUILD.load()` from the console worked, which is what isolated it.

**The panel offered to release a lease belonging to somebody else.** It took the
first lease matching the model, and a model can be held by several owners at
once. With the probe holding one lease and the dashboard the other, the button
was wired to the probe's. That is the dashboard unloading another client's model
on their behalf — precisely the fight §9's reference counting exists to prevent.
It now releases only a lease owned by `nervis-dashboard`, and names the other
holders instead of acting on them.

**Still no endpoint:** starting a benchmark run from the UI. `API.sirvis.jobs()` reads real runs
from `/api/v1/benchmark-runs` — it is not one of the invented set, and calling
it so was wrong. What is missing is an endpoint to **start** a run: nothing
submits one, so running a benchmark is CLI-only.

## Getting a runtime token, without a terminal

The screen used to say *"Mint one with `sirvis tokens --mint runtime`"*. There
is no `tokens` subcommand and the scopes do not go there — anyone following it
got an error, and it had been there since M4. That is what an instruction nobody
exercises becomes.

Fixing the sentence was not the fix. **Getting a token meant opening a terminal,
changing directory, running a CLI, copying 43 characters and pasting them into a
field** — in an application whose entire premise is double-clicking one file.

**The launcher now does it.** A `read runtime` token is minted on first start,
cached at mode `0600` beside the logs, reused on later starts, and handed to the
page in the URL **fragment**. Nothing is typed and no token is ever shown.

**Why the fragment rather than a query string.** Everything after `#` is never
sent to a server — not to the file server that serves the dashboard, not in a
`Referer`, not into an access log. The page reads it before the first render,
keeps it in memory for that tab only, and erases it with `replaceState`, so a
reload, a bookmark or a shared URL does not carry it. Verified: after the
handoff the address bar reads `…/index.html?v=…` with nothing after it, and the
Load button is armed with no manual step.

This changes who mints the credential, not what it protects against. It is a
loopback-only, runtime-scoped token for a service the person double-clicking the
launcher already controls, and §4.5's reason for requiring it is untouched:

* Loading a model spends gigabytes and can **evict a model another client is
  holding** — demonstrated, with two owners on one build and a reference count
  of 2.
* Loopback keeps the LAN out and does nothing about another process on the same
  machine, which is why a scope is required *even on loopback*.
* The origin check does not replace it. A browser attaches credentials
  automatically, so origin alone cannot tell a click from a page the user
  happened to visit — §4.5 requires both.
* Reads stay open deliberately: Clarvis probes `/v1/models` with a two-second
  timeout and reads any 401 as *provider offline*.

The manual command survives as the documented fallback, now correct and with the
directory it must run from — the CLI's database path is relative, so minting
from the wrong one writes to a different database and yields a token that
authenticates against nothing.

**One incidental fix.** The launcher opens the dashboard with `?v=<mtime>`. A
cached `index.html` had been served after an edit while this very handoff was
being written — new code served, old code running — and cost a debugging detour.

**Cold start, verified end to end.** `.run/` deleted, the token row deleted, no
services running, then `./start-macos.command`:

```text
services      SIRVIS · RAVIS · NERVIS   all ready, ppid 1, no controlling tty
token         minted fresh, cached 0600, scopes "read runtime"
data          20 models · 81 benchmark runs · 2 runtime sets · 20 evidence records
dashboard     token picked up, fragment cleared from the address bar
load          OPTIONS 204 → POST 200, resident, lease owned by nervis-dashboard
release       OPTIONS 204 → DELETE 200, unloaded, 0 leases
```

Nothing typed at any point.

**The cold start found a real bug that the earlier test had missed.** Clearing
the fragment during parsing is not final: the browser still has it to process
for anchor scrolling once the document finishes, and it reappeared in the
address bar afterwards. The token had been picked up — so the code *had* run —
which is exactly what made it look like it had worked. It now scrubs on
`DOMContentLoaded` and `load` as well, and re-checks before acting.

**First-run virtualenv creation, tested by removing the virtualenv.** Moved
aside rather than deleted, so a pip failure was recoverable. The launcher
rebuilt it and brought everything up in **9.4 seconds** — "a minute or so" is
pessimistic with a warm pip cache.

**It shipped an environment in which none of the documented checks existed.**
The rebuild installed runtime dependencies only: 51 MB, with `pytest`, `ruff`
and `mypy` all absent — every command in this file's own *"verify this
yourself"* block. The application worked perfectly; a person who had just cloned
the repository and double-clicked the launcher could not have run a single check
against it. Fixed by installing the `dev` extra: one virtualenv, one path, and
the documented commands present in it. 162 MB, which is nothing beside the
models this thing loads.

Verified on a virtualenv built **entirely by the launcher**: ruff, mypy strict,
Clarvis conformance, the nervis check, the STATUS gate, and 862 tests
(509 + 338 + 15).

**One flaky test, found because the machine was busy.** The rebuild's pip run
saturated the CPU, and `test_the_matrix_reports_degradation_against_alone`
failed once and then passed alone and in two clean full runs. The cause is real
rather than cosmic: the fake runtime halves the *tokens* generated per extra
resident model, but tokens-per-second is computed from elapsed **wall-clock**
time, so on a busy machine a co-resident measurement can come out no slower than
its control. The test's own docstring already conceded the split — "the exact
percentage arithmetic is proven in the handcrafted test below, where the timings
are chosen rather than measured".

`run_multi_experiment` already accepted an injectable `clock` and the test
helper had never passed one. It does now, so elapsed time per repetition is
identical and the halved token count is the only thing that moves — which is
what the test was always trying to assert. Confirmed by re-running the M10 suite
under three CPU-saturating processes: 25 passed. Same reasoning the helper
already applied to thermal and memory one argument along.

## Superseded: the token card said how, and said it wrong

Asked how to get a runtime token, and the answer turned out to be that the
screen already told you — incorrectly. It read *"Mint one with `sirvis tokens
--mint runtime`"*. There is no `tokens` subcommand and the scopes do not go
there; the command is `sirvis token --mint <label> --scopes "read runtime"`.
Anyone following the instruction got an error, which is worse than no
instruction, and it had been sitting there since M4.

The card now carries the working command, the directory it has to run from —
the CLI's database path is relative, so minting from the wrong one writes into a
different database and produces a token that authenticates against nothing, the
same trap that orphaned the benchmark corpus — and, newly, **why a token exists
at all**, which is what was actually being asked:

* Loading a model spends gigabytes and can **evict a model another client is
  holding**. That is not hypothetical here: the panel test ran with two owners
  on one build and a reference count of 2.
* §4.5 requires a scope on every mutation *even on loopback*, because loopback
  keeps the LAN out and does nothing about another process on the same machine.
* The origin check does not replace it. A browser attaches your credentials
  automatically, so origin alone cannot distinguish your click from a page you
  happened to visit — which is why §4.5 requires both.
* Reads stay open deliberately: Clarvis probes `/v1/models` with a two-second
  timeout and reads any 401 as *provider offline*.

## Cheap wins: what could be wired without building anything

A second pass over the sweep, asking a narrower question — not *what is
invented*, but *what is invented while a working endpoint sits next to it*.

**Wired.**

`services()` — the **Service Registry on the Overview**, the first card on the
first screen anyone sees. It was inventing build numbers (`0.4.1`, `0.3.0`)
while §1's `/ecosystem/identity` and `/ecosystem/capabilities` published the
real ones. RAVIS and SIRVIS now read live: build `0.0.1`, protocol `1.0.0`, and
the capability shown is the first each reports as **available** rather than the
first it declares — a service publishing five and offering one should read as
offering one, and the count travels beside it. Clarvis and LM Studio publish no
MEP surface, so their rows stay declared, and the card says which half is which
instead of implying all four are read.

`profiles()` — 13 profiles from `/api/v1/profiles`, with RAVIS's own
descriptions and revisions. The mock asserted revisions of 4, 3 and 2; every
real one is 1. `default` is derived here rather than invented upstream, because
§9.3 makes `ravis/auto` the profile an unspecified request resolves to and RAVIS
publishes no such flag.

**Not cheap, and why — recorded so the next pass does not re-derive it.**

`machine()` was recorded as not cheap and then done, because the blocker turned
out to be one missing field rather than a missing capability. The System screen
computes `memory_gb - available_gb`, and SIRVIS reported no available memory —
so a live wire produced `NaN`. But `/api/v1/system` **already detects on read**
and already carries volatile fields: free disk, used swap, thermal state. A
reclaimable-memory reading is the same class of fact, taken with the same
`MemoryProbe` the benchmark engine samples with, so the two cannot disagree
about what "available" means. Added to the snapshot, not to a new endpoint.

Capacity and availability stay separate claims: the machine has its unified
memory whatever is running, and how much is free is a statement about a moment.
`None` is preserved over `0` — ignorance and a machine with nothing left to
reclaim are different, and only one is a crisis.

**"Model: not detected" turned out to be a gap rather than a limit.** §5.1 names
"Mac model" among the metadata that belongs on a snapshot — SIRVIS had simply
never read it. `hw.model` gives `Mac17,3`, the board identifier. The *marketing*
name stays absent deliberately: it needs a lookup table that is wrong the week a
new machine ships, and an exact identifier beats a stale name.

**The hostname is recorded too, and labelled.** §5.1's "never a hostname alone"
governs the machine **ID**, which stays a locally generated UUID — it does not
forbid a hostname as snapshot metadata, and the same section requires that
transport and display *"label sensitivity and support redaction"*. So the
snapshot carries `sensitive_fields: ["hostname"]`, and the label travels in the
payload rather than living in a consumer's head: anything forwarding a snapshot
knows which key to drop without having to recognise it by name. The screen shows
the name with a **personal** marker beside it. It is worth recording at all
because a corpus spanning two machines needs something a person recognises, and
an opaque UUID is exactly what nobody does — this machine answers `Govert`.

**Two fields remain genuinely undetectable and say so.**
`system_baseline_gb` — the memory this machine uses at rest with LM Studio open
before any model loads, which was **measured by hand** and is computed nowhere.
`model_budget_gb` — derived from that baseline, so it inherits the absence. Both
render as *"not detected"* through one shared helper, because a fit decision made
against an invented budget is worse than one nobody made.

The runtime list came from a second endpoint rather than being nulled:
`/api/v1/runtimes` publishes it, and nulling it crashed the screen — which is
how the omission was found.

**The screen is now live end to end**, and was un-marked from the prototype list
card by card. One over-correction was caught in the process: `Conditions` was
faded as invented, and it reads only `thermal_state`, `swap_gb` and
`available_gb` — all three real. The prose around them is *documentation*, not
fabricated data, and fading it would have been as misleading as leaving a mock
bright.

`policies()` and `usage()` have endpoints that answer 200 and are **deliberately
empty**: policies land at M16, cost at M15, and `/api/v1/usage` says so in a
`cost_detail` field. Wiring them would replace invented numbers with honest
blanks — worth doing, but it is a screen redesign rather than an adapter, since
the cards are built around numbers that will not exist until those milestones.

`route-decisions` is live and empty until something routes. It fills by itself
the first time a request goes through, so it needs no work — only traffic.

## Traces and Events, and what each honestly is

**Traces now reads RAVIS's route decisions.** `/api/v1/traces` does not exist,
but `/api/v1/route-decisions` does, and a route decision *is* the record of what
happened to one request: what was asked for, the pool it resolved to, what was
selected, the fallbacks behind it, every candidate considered, every exclusion
with its reason, and an execution block naming each attempt and its outcome.

The screen shows it and refuses to overstate it. **It is not a distributed
trace** — no timeline correlates Clarvis, RAVIS, SIRVIS and the runtime, because
that needs event publication. The span card says so, naming the milestone each
service declares. Drawing a timeline from one service's record would invent the
other three rows.

Empty and unreachable are answered separately: nothing routed yet says so and
explains that decisions live in memory only — §17 does not list them as stored
state, so a restart loses them and that is the intended trade. RAVIS not
answering gets no remembered traces at all, because a transcribed trace would
describe a request nobody made.

**Events is marked unavailable, from the services' own mouths.** There is no
feed: `/api/v1/events` 404s on both. So the screen reads
`/ecosystem/capabilities` and reports what each service declares about itself —
RAVIS `ravis.events@1 unavailable, "event publication lands at M18b (runbook
Stage 7)"`, SIRVIS `sirvis.events@1 unavailable, "…at M21"`. That is a live fact
about events, and it is the honest one.

The envelope, ordering, duplicate and redaction rules stay on the screen,
labelled **specified, not built** — they are the contract rather than a
description of running code. A third card points at what does answer the
question someone opened this screen to ask: routing decisions on Traces,
benchmark history on Results, both live.

One vocabulary bug, caught by looking: a successful attempt reports
`succeeded`, and the outcome chip was testing for `ok`/`success`/`routed` — so
the one decision that worked rendered as a warning.

## NERVIS off macOS

`detect_system()` returned the platform name and nothing else on anything but
Darwin, reasoning that SIRVIS targets Apple Silicon (§3) and inventing a Linux
box's GPU core count would be fabrication. The first half is right and the
second overshot: **refusing to read what a machine plainly reports is not the
same as refusing to guess at what it does not.** A Linux machine appeared
entirely unknown, which described SIRVIS's reticence rather than the machine.

Cores, memory, disk, hostname and the OS name are available everywhere through
the standard library — `os.cpu_count`, `shutil.disk_usage`, `platform.node`,
`sysconf` on POSIX and a `GlobalMemoryStatusEx` call through `ctypes` on
Windows, which is a good deal less than a dependency for one number. Linux gets
its distribution's own `PRETTY_NAME` from `/etc/os-release`, falling back to the
kernel version rather than to nothing.

What has no portable equivalent stays absent and says so in `unknown_fields`:
chip name, GPU core count, the performance/efficiency split, thermal state. All
four are `sysctl` reads with nothing to read them from elsewhere.

The column reads **OS** rather than **macOS**, and carries the full description
— `macOS 27.0`, `Ubuntu 24.04`, `Windows 11` — because `27.0` alone means
nothing in a corpus spanning machines. `platform.mac_ver()` is used rather than
`platform.release()`: the product version is 27.0 where the kernel is 25.0.0,
and only one of those is a number anybody recognises.

**Verified on macOS only.** The Linux and Windows paths are exercised by tests
that call them directly, not on real hardware of either kind.

## Chat and the Ecosystem map

**The chat screen could not have held a conversation.** Its send button was
wired to `sendChat`, a function defined nowhere in the file — pressing it threw
a `ReferenceError`. So it was not merely showing a transcribed exchange; it had
no path to a real one.

It is now what its own subtitle always claimed: a plain client of RAVIS's
OpenAI-compatible API. The profile chips come from `/api/v1/profiles` — 13 of
them, real revisions — and **they now set the profile** instead of only
announcing it in a toast, which was the same lie the build has been removing
everywhere else. Messages live for the tab and are written nowhere, which is
what NERVIS.md §7 asks of a client that is not a workspace.

**Each reply is correlated to the decision that produced it, exactly.** The
completion returns `x-request-id` and a route decision carries the same
`request_id`, so the link is precise rather than "the most recent decision" —
which would be wrong the moment two requests overlap. Verified live: request
`a70ac098abe0` → decision `64983b4d42e3`, served by
`deepseek-r1-distill-qwen-1.5b` through `ravis/auto`.

The route card is built from that record and **drops what a decision does not
carry**: cost and time-to-first-token are absent rather than zeroed, because the
cost engine is M15 and a `€0.00` reads as measured and free.

**One CORS line was the actual blocker.** POST was not in RAVIS's allowed
methods, so every completion from the dashboard would have failed at the
preflight — and reported as *RAVIS unreachable*, which is the wrong diagnosis of
an allow-list. POST is now permitted, and the reason sits beside the note
explaining why credentials deliberately use PUT: a JSON body always preflights,
so this admits an allow-listed origin and nobody else.

**The map asks the Clarvis bridge rather than inventing workspaces.** It was
asserting two editor hosts; the bridge is a localhost surface that starts and
stops with an editor window, so "not running" is the ordinary case. The KPI now
reads **0 — Clarvis Bridge is not running**. Everything else on that screen was
already live once the registry was, since every node is a registry row.

## The routing explanation, out of the transcript

A route card under every reply crowded the conversation with reference
material. It is now a small marker at the top-right of each assistant bubble,
opening the same card as a panel.

**`<details>` rather than a hover tooltip.** Hover has no answer on a touch
screen, cannot be reached from a keyboard, and vanishes when the pointer crosses
a gap — and this panel is a table somebody may want to *read* rather than
glance at. `details` brings the toggle, focus handling and Escape with it, and
needs no outside-click handler. A `toggle` listener closes any other open panel
and places the one being opened.

**Three attempts at positioning, and the first two were wrong.**

Absolute positioning inside the bubble was clipped by the transcript, which is a
scroll container — the panel came out two rows tall with the rest cut off.
Making `.messages` overflow visible would have fixed the clip and broken the
scrolling the transcript depends on, including the `scrollTop` the send path
drives. Rejected.

Fixed positioning computed from the panel's own `offsetHeight` was worse: the
height is read while the element is still mid-flow and is not the height it will
have once placed, so the panel landed off the bottom of the screen.

What works measures nothing about the panel. Opening downward pins `top` to the
marker's bottom edge; opening upward pins `bottom` to the marker's top edge.
Neither needs a height, and `max-height` is set to whatever space remains in
that direction, so it cannot overrun either way.

**The viewport is clamped rather than merely defaulted.** A collapsed or
embedded pane can report a height of zero — observed repeatedly during testing,
with the same pane alternating between 720 and 0 across consecutive reads — and
a small-but-nonzero reading breaks the arithmetic just as thoroughly.

**The chat frame fills the page.** It was capped at `min(560px, 72vh)`, leaving
dead space beneath and a transcript shorter than it needed to be — which the
panel made obvious by having less room than it wanted. The height is now
measured from the card's own top offset on render and on resize, because that
offset depends on a heading that wraps differently at narrow widths.

## Chat parameters

There was no way to set a system prompt, a temperature or a token budget — the
screen sent a fixed `max_tokens: 512` and nothing else. A collapsible panel now
carries **system prompt, temperature, max tokens and top&nbsp;p**, held for the
tab like the rest of the conversation.

**An empty field is not sent at all.** No default is filled in here, because
picking a common value like `0.7` would silently override a choice the model or
the runtime had already made. The same instinct as §14's rule about estimates:
do not present a guess as a choice. The closed summary shows what is actually in
force — `system prompt · temp 0.1 · max 40`, or *model defaults* — so the panel
does not hide the answer it exists to give.

The panel's open state survives a re-render, because `render()` runs after every
message and a panel that snapped shut each time would be unusable exactly while
being adjusted. A system prompt applies from the next message onward and the
screen says so: the messages already above were not sent with it.

**"(empty response)" was hiding a cause the payload was carrying.** A reply with
no content is almost never an empty reply on this corpus — it is a reasoning
model spending its whole budget thinking, which SIRVIS measured directly on both
Gemma-4 builds at 253 of 256 tokens. The response says so if anyone reads it, so
now it is read: an empty answer reports the reasoning tokens, the total, and why
generation stopped.

Seen immediately, with a 40-token budget: *"No answer. 38 of 40 tokens went to
reasoning and generation stopped at the token limit — raise Max tokens, or pick
a profile that avoids reasoning models."*

## Conversations: saved, listed, deletable — and where they actually live

There was no memory of any kind. The transcript lived in a tab and died with it,
**"New chat" was a button that raised a toast and did nothing**, and the screen
told the reader *"Conversation cv_… is stored locally and can be deleted"*,
which was false on both halves.

The spec is not silent about this. NERVIS **M4** requires "history persists
locally", and **§7.2** names exactly what to keep: *"conversation ID, title,
timestamps, messages, RAVIS route IDs. Allow deletion."* The domain lists
`ChatConversation · ChatMessage` and says to use database migrations.

**The reason it was never built is bigger than the feature.** §7.2's store
belongs to NERVIS **M0** — package, FastAPI, config, SQLite, migrations,
`nervis serve` — and `nervis/` contains one HTML file, docs and avatars. There
is no NERVIS service. The launcher serves the dashboard with
`python -m http.server`. M4's "persists locally" had nowhere to persist to.

So conversations now live in `localStorage`, which satisfies what §7.2 actually
requires — local by default, deletable, surviving a reload — and the screen says
plainly what that means: **this browser only**, not the machine, not any
service; another browser or a private window sees none of it. The real store
arrives with M0.

* **Saved after every exchange**, not on an explicit action. The failure being
  prevented is closing a tab and losing the conversation, and nobody presses
  save before closing a tab.
* **New chat saves first.** A button that discarded what was on screen would be
  a data-loss control wearing a friendly label.
* **Parameters travel with the conversation.** Reopening a chat that used a
  system prompt and finding it silently gone would make the next reply
  inexplicable.
* **Route decision ids are kept**, because §7.2 names them — a stored reply can
  still explain why that model answered, as long as the decision is still in
  RAVIS's in-memory window.
* A corrupt or unavailable store yields no history rather than throwing. Private
  browsing refuses `localStorage` outright, and a chat screen that cannot open
  is worse than one that cannot remember.

**Still absent, and worth stating plainly:** there is no *memory* in the sense of
recall across conversations — no summarisation, no retrieval, nothing carried
between sessions. What exists is history: the messages of one conversation,
re-sent as context when that conversation continues.

## A diagnostics screen, and the two stale capabilities it found

`/api/v1/diagnostics` is a 404 on both services and the Clarvis conformance
suite is a CLI command with no HTTP surface — so there is no diagnostics
endpoint to wire. But every service publishes `/ecosystem/health` and
`/ecosystem/capabilities`, which is enough to build the screen out of surfaces
that already exist: status, live, ready, each named check, and every capability
a service declares **with its own reason**. A capability reported as *not yet*
naming its milestone is more useful than a red light.

It earned its place immediately by surfacing two declarations that had gone
stale — the failure mode this file already records happening twice before.

**`sirvis.recommendations@1` said "the recommendation engine lands at M15"**
while the endpoint returned real recommendations. M15 shipped. SIRVIS's bar is
whether a thing is *built*, which its own test name states, so this is now
available.

**`ravis.providers.native@1` said "translated execution path is M3b"**, which
became false the moment M3b landed and read as though the work were pending.
Getting this one right took two attempts, and the mistake is worth recording:
the first fix assumed §4.1 imposed a single conformance bar, and `ravis
conformance` has one suite covering the *transparent* route only — so it was
reverted as over-advertising.

That was wrong. **§4.1 sets a condition per capability, not one bar for all of
them**: `chat_completions@1` says *"conformance passes"*, and this one says
*"a translated adapter ships"*. Anthropic's shipped at M4 and the local adapters
at M8, with a tool-calling completion routed through Ollama and back. The
condition is met, and the test pinning it `unavailable` was the stale artefact.

Both errors point the same way. Under-advertising fails **silently**: no error,
no wrong route, just a peer that never negotiates a surface that works. Only an
assertion or somebody reading the list catches it — which is now a screen.

## Planned: NERVIS M20, conversation memory

Recorded in `NERVIS.md` rather than left as a conversation. Recall across
conversations, distinct from history within one, with the exit conditions that
matter: what was recalled is **shown with its source conversation** rather than
silently injected, turning it off leaves ordinary chat unchanged, and a recalled
passage is **fenced before it re-enters a prompt** (§11.5) — a stored assistant
reply is model output, and re-admitting it unfenced is the same trust mistake in
a longer loop. Stage 10.

## Diagnostics and Settings: reporting effects, not values

None of these four screens has an endpoint to wire. `/api/v1/diagnostics`,
`/api/v1/settings` and `/api/v1/config` are all 404, `ravis conformance clarvis`
is a CLI command with no HTTP surface, and NERVIS has no service at all. So each
screen reports what can be **observed** rather than what is configured.

**RAVIS Diagnostics** reads `/api/v1/health` and the capability surface: status,
whether the upstream answers and how fast, how many models are visible, the
build and protocol version, and each unavailable capability with its own reason.
The conformance table below it is labelled **"shape, not a run"** — the suite's
sixteen checks are real and pass, but nothing renders a *result* because nothing
serves one.

**RAVIS Settings** reports the configuration's effects. The address is the one
thing the dashboard knows for certain — it is talking to RAVIS, so it knows
where RAVIS is, which beats a transcribed listen address that would be a guess
about a setting nobody publishes. Beside it: the upstream's actual answer, each
provider's enabled state, credential source and reachability, and a link to the
Credentials screen that does allow a change.

**NERVIS Settings** is the shortest, because the honest answer is that **NERVIS
has no service to configure.** M0 — package, FastAPI, SQLite, migrations,
`nervis serve` — has never been built; `nervis/` is one HTML file served
statically. The endpoints this dashboard talks to *are* its configuration, so
each is listed with whether it actually answered, alongside the two pieces of
state it does hold: conversations in this browser, and a runtime token for this
tab.

The pattern across all four is the same one the fade sweep established. Where a
value cannot be read, the screen does not invent it — it reports the effect that
can be seen, names the milestone that would publish the value, and leaves the
cards describing unbuilt settings marked as prototype.

## The breaker was keyed on one label for every upstream

`AttemptChain.provider` was a single string, chosen when RAVIS talked to one
upstream, and the constant beside it said so: *"M8 makes this plural, at which
point the adapter supplies the name."* M8 shipped and it did not.

That is a routing bug rather than a naming one. A **provider-scoped circuit
takes out every model behind that provider at once** — which is the point of
scoping — so one shared label meant a single failing local runtime opened the
breaker for the entire catalogue, including models on a healthy upstream that
had never been called.

Measured, with LM Studio failing and Ollama fine:

```text
one shared label     4 of 4 candidates blocked
resolved per model   2 of 4 blocked — granite, qwen. Ollama's models keep serving.
```

Every lifecycle method on the chain already received the target, so the scope
could always have been resolved from the model rather than assumed for the
request. `provider` now accepts a resolver, `HealthRegistry.unavailable` takes
one too, and `ravis/src/ravis/api/openai/chat.py` supplies one that asks `resolve()`
which upstream owns a model — the same collision rule the catalogue, the candidate set and the
forwarder already share.

**The health target for a singular deployment is now `default`, not
`upstream`.** That is a visible rename on `/api/v1/health`, taken deliberately:
`default` is the name `upstreams.py` already assigns when the singular settings
are used, and keeping "upstream" for that case while named upstreams reported
their real names would leave two vocabularies for one thing.

Two regression tests pin it: one upstream's open circuit leaves another's models
routable, and a chain crossing upstreams attributes each failure to the provider
that produced it rather than to whichever one the request started on.

## `ravis doctor` was printing an empty table and blaming M8

`resolve_provider_map()` was `del settings; return []`, with a docstring saying
the emptiness was honest because no provider configuration existed before M8.
M8 shipped in the meantime — upstreams, filters, an enable/disable file — and
the function kept returning nothing, so the one command M0 requires to answer
*"why did it pick that one"* answered "none configured" on a fully configured
gateway.

The constraint that makes it useful is that it may not contact an upstream, and
that rules out listing concrete models: which models exist is a network call.
So the rows describe **the rules rather than their outcome** — which addresses
reach which provider, which patterns a filter admits, which it removes — printed
in the order the router applies them:

```text
model                    provider               decided by
ravis/anthropic/*        anthropic (translated) RAVIS_ANTHROPIC_API_KEY
ravis/lmstudio/*         lmstudio               upstream 'lmstudio' → http://127.0.0.1:1234/v1
*                        lmstudio               upstream 'lmstudio' → http://127.0.0.1:1234/v1
ravis/openrouter/*       openrouter             upstream 'openrouter' → https://openrouter.ai/api/v1
*-preview                — no route —           models.json exclude for 'openrouter'
anthropic/*              openrouter             models.json include for 'openrouter'
openai/gpt-4*            openrouter             models.json include for 'openrouter'
```

Three orderings in that table are load-bearing rather than cosmetic. Upstreams
appear in **declaration order**, because that is the tie-break
`ravis/src/ravis/transparent.py` uses when two of them serve the same model id —
listing them alphabetically would name the wrong winner in exactly the situation
someone runs this to understand. A provider's **exclusions print above its
inclusions**, because exclude wins in `ModelFilter.matches`. And a translating
provider appears **only as a direct address**, because §6's Path B is
unreachable from a pool, so a bare pattern would claim a route that does not
exist.

A malformed `RAVIS_UPSTREAMS` becomes a row rather than an exception. `doctor`
is what an operator runs *because* the configuration is broken, so the one
command that can explain a bad declaration must not be the one that dies on it.

Eight tests, several pointing the configuration at `127.0.0.1:9`, where nothing
listens: if this ever starts probing for real models they fail rather than hang.

## Seven audit findings, and what each one turned out to be

The audit found eight things the code claimed and did not do. One was a routing
bug and is recorded above. The rest were **claims** — docstrings, comments and
capability declarations asserting behaviour that did not exist — which is a
category worth naming, because nothing fails when a comment is wrong and so
nothing catches it.

### The shorthand was going out on the wire

§4.1 says `<id>@<major>` "is never a wire value": the `id` field carries the
identifier alone and `version` carries the semantic version, because `@<major>`
names a compatibility boundary rather than part of a name. Every capability in
both services was declared under a dict key like `ravis.events@1`, and that key
went straight into `id`. A consumer negotiating on the pair would have seen a
name no registry it compares against would match.

Stripped at the boundary in `protocol/src/ecosystem_protocol/capabilities.py`,
not in the declarations — everything inside a service still keys on the form its
prose uses, and no service can forget.

The same change added a consistency check: a shorthand whose `@<major>`
disagrees with the major in `version` now raises. **It caught a live one on its
first run** — this project's own protocol fixture declared `example.other@1` at
version `2.1.0`, which is a service advertising a major-1 contract while running
major-2 code.

### SIRVIS published seven capability names it had invented

SIRVIS.md §4.1 publishes a table of eight. `sirvis/src/sirvis/ecosystem.py`
declared seven under different names, of which two matched by coincidence:

| §4.1 says | the code said |
|---|---|
| `sirvis.inventory.read@1` | `sirvis.models.inventory@1` + `sirvis.system.snapshot@1` |
| `sirvis.runtime.state.read@1` | — |
| `sirvis.runtime.control@1` | `sirvis.runtime.lmstudio@1` |
| `sirvis.benchmarks.jobs@1` | — |
| `sirvis.benchmarks.results@1` | `sirvis.evidence.query@1` + `sirvis.benchmarks.single_model@1` |
| `sirvis.runtime_sets@1` | — |
| `sirvis.recommendations@1` | ✓ |
| `sirvis.events@1` | ✓ |

A capability name is the thing a peer negotiates on, so this is not a cosmetic
divergence: it advertised a contract no consumer written against the
specification would look for, and hid the eight it would. The declarations are
now §4.1's table verbatim. `sirvis.benchmarks.jobs@1` is **unavailable** —
benchmarks run, but §4.1 means submit/poll/cancel by "jobs", and declaring it
available because a neighbouring operation works is §4.1's exact prohibition.

### RAVIS was reading SIRVIS without negotiating anything

A comment in `ravis/src/ravis/evidence/sirvis.py` said the evidence capability
"is checked separately — a peer that stops advertising it stops being read."
Nothing checked it. RAVIS read `/api/v1/evidence` from whatever answered.
`UnsupportedProtocolVersionError` had been defined for the same purpose and was
never raised anywhere in the repository.

`_negotiate()` is that sentence made true. Before evidence is read, RAVIS checks
the protocol major and the capability state. Both §4.2 and §13.4 apply and they
pull in opposite directions — one requires rejecting an unsupported major with a
structured error, the other requires SIRVIS being unusable never to stop RAVIS
routing — so the refusal is **raised** where it is detected and **applied as a
degraded source** at the call site.

A 404 on `/ecosystem/*` is tolerated: that is a SIRVIS build older than the
shared protocol package, and §13.4 makes evidence optional. Answering-and-saying-no
is a refusal; not answering is not one.

### `ravis.virtual_profiles@1` was advertising a revision that does not exist

§4.1 sets a condition per capability, and this one's is *"profiles are versioned
and revisioned"*. `VirtualModelPool` has a `pool_id`, a label and its
requirements — no version, no revision. Thirteen pools route, so `unavailable`
would make a peer hide a working feature; but a consumer reading `available` is
entitled to pin a revision and be told when it changes. Now `degraded`, with the
versioning work added to RAVIS M16.

### `POST /api/v1/recommendations` documented four inputs and read two

The docstring listed §14.3's body — profile, roles, constraints, mode. The
handler read `roles` and `mode`. §14.3's own printed example carries
`{"avoid_swap": true}`, and dropping it returns a recommendation that may swap,
to a caller who asked for one that would not, with nothing in the response
saying so.

`constraints` is now refused with `UNSUPPORTED_PARAMETER` rather than ignored —
§7.1's distinction between "you asked for something I cannot do" and "your
request was malformed", where only the first means the answer would have
described a configuration nobody chose. An unknown `profile` is refused for the
same reason: one family is defined, and scoring with `clarvis` weights under
another name answers a question nobody asked.

### The `Recommendation` docstring claimed §14.3's output "whole"

It said *"every field there is a field here"*. Two of the fields it named as
proof — `coverage` and `supporting_evidence` — are not on that record at all;
they live on `Utility`, one level down, and the second is called `evidence_ids`.
Four of §14.3's outputs are genuinely absent: Runtime Set recommendation,
expected memory, performance and quality as named outputs, and evidence level.

The docstring now says which is which, and the four are scheduled as SIRVIS
**M15b**. That is the whole lesson of this section in one example: the docstring
read as a completeness guarantee, so nobody checked it for eleven milestones.

## NERVIS M0 — the third service exists

Until today NERVIS was a 4,351-line HTML file served by `python -m http.server`.
That is why chat history lived in `localStorage`, why the Settings screen had
nothing to write to, and why **Stage 1 could not close**: its exit requires every
service — NERVIS included — to answer identity, health, capabilities and version
before anything probes anyone.

`nervis serve` now does. Same port, same URL, same dashboard file. What changed
is that the thing serving it has a database, a stable identity and an
`/ecosystem/*` surface:

```text
$ curl -s localhost:8790/ecosystem/identity
{"service_id": "9c0d6924…", "service_type": "nervis",
 "instance_id": "7e04c33b…", "machine_id": "b2a10526…",
 "protocol_version": "1.0.0", "build_version": "0.0.1"}
```

### What it declares, and why almost all of it is `unavailable`

§3.1 prints ten capability names. All ten are published and **nine are
`unavailable`**, each naming the milestone that will change it. The tenth,
`nervis.dashboard@1`, is `degraded` — the shell is genuinely served, and every
figure on it is still fetched by the browser from RAVIS and SIRVIS directly, so
NERVIS is the page's host and not yet its source.

The registry card reads that live and says so:

```text
NERVIS  0.0.1  proto 1.0.0  none available   0/10
RAVIS   0.0.1  proto 1.0.0  ravis.openai_…   3/8
SIRVIS  0.0.1  proto 1.0.0  sirvis.benchm…   6/8
```

**NERVIS reads itself same-origin** — `BASE.nervis` is the empty string, because
the service serving the page is the one being asked.

### Three decisions worth stating rather than discovering later

**The dashboard is served, not rewritten.** NERVIS.md §3 names HTMX and
server-rendered HTML, and that remains the direction for screens NERVIS supplies
data for from M1 onward. `index.html` was built screen by screen against two
live services; replacing it with a server-rendered skeleton to satisfy a
milestone's wording would have thrown away working software to produce a worse
version of it.

**Not SQLAlchemy and not Alembic**, which §3 also names. Both sibling services
migrate with the standard library, the schema is a handful of tables, and adding
an ORM plus a migration framework to a third service to describe them would buy
nothing the other two found they needed.

**No CORS middleware.** NERVIS serves the dashboard and the dashboard's own API
from one origin, so nothing it answers is ever cross-origin. A third copy of the
allowlist would be a header no browser ever sends a request past.

### `machine_id` comes out of the database, not a constructor

§4.1 requires it to be locally generated, opaque, non-hardware-derived and
resettable — never a hostname, serial number or MAC address. A `uuid4()` in
`create_app` would satisfy every clause and still be wrong, because it would
give a peer a different answer after every restart. It is generated once into an
`installation` table and read back thereafter. `instance_id` is the one that
changes per process, which is what distinguishes a restart from a second
instance.

### A crash the wiring exposed, and its actual cause

Adding a NERVIS row to the registry broke the Overview and Ecosystem map
screens: LM Studio's row had been carrying `key: 'nervis'` as a placeholder, and
giving NERVIS a row of its own meant LM Studio needed a key that has no entry in
the dashboard's fallback `SERVICES` map.

Four call sites looked up `SERVICES[row.key].state` and every one of them threw.
The fix is not four guards. **Rows read live now carry their own state**, which
is where it belongs — three of the five are read from `/ecosystem/*`, and mixing
a live read with a hard-coded map was the defect. `usable()` also stopped
treating an unknown key as fatal: a dashboard whose entire job is rendering
partial information should not have a lookup that raises on "I have never heard
of this".

Verified afterwards by rendering **all 27 screens across the three apps** and
asserting none throws.

### What this does not do

No peer is probed — that is M2, and NERVIS is deliberately ready with RAVIS,
SIRVIS and Clarvis all absent. A control plane that reported itself broken when
the things it watches are broken could not be used to find out why.

`capable(service, capability)` exists in the dashboard and **nothing calls it**;
the comment above `SERVICES` claimed controls were bound to capabilities, and
they are bound to whether their service answered. Corrected in place rather than
quietly, and binding each control is what M2's registry is for.

## Starting the thing

Six launchers — start and stop, for macOS, Linux and Windows — each three lines
calling `tools/run.py`. One file behind six doors, so the sequence cannot drift
between platforms.

**Detached, which was the requirement.** The services are spawned into their own
session with `start_new_session` (POSIX) or `DETACHED_PROCESS` (Windows) and
their stdio redirected to `.run/*.log`, so closing the terminal does not take
them with it. Verified: after the launcher exits the three processes show
**ppid 1** and **no controlling terminal**, so a window close sends them no
SIGHUP.

That has a consequence the design has to answer for rather than ignore: nothing
stops them when the window closes, so there is a `stop` launcher and a PID file.

**The PID file is not trusted on its own.** Each record stores a marker — a
distinctive fragment of the command line — and `stop` confirms the recorded PID
still belongs to that command before signalling it. PIDs are reused, and a file
on disk claiming a number was ours is not a reason to kill whatever holds it
now. Tested directly: a recycled PID pointing at an unrelated process is
reported "was not running" and left alone.

**`start` consults health, not the PID file**, when deciding what to launch.
A PID file says what was started; a health check says what is serving, and when
they disagree the health check is right.

**Ports are not a choice made in the launcher.** 8721 and 8731 are the services'
own defaults *and* the addresses hard-coded in `nervis/index.html`; picking
others would produce a dashboard reporting everything offline. Each service is
started with the dashboard's origin allow-listed and nothing else, because both
default to an empty list and the screens would otherwise silently show nothing.

**It starts nothing it does not own.** LM Studio, Ollama and Clarvis are
reported, never launched — §9 puts model loading behind SIRVIS's Resource
Manager, and "nothing is routing" and "no runtime is running" are the same
symptom with different fixes.

**No automated tests.** The launcher spawns detached processes and signals them;
the verification was done live and is described above — start, status,
idempotent re-start, stop, no strays, and the PID-reuse guard. Recorded as a gap
rather than implied to be covered.

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
