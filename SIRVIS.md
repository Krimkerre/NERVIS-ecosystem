# SIRVIS

**Silicon Inference Runtime Validation & Intelligence System**

> Local model benchmarking, runtime intelligence, model management and hardware-aware
> recommendations. **SIRVIS knows.**

**Status:** Canonical SIRVIS specification — build plan and ecosystem contracts in one document
**Consolidates:** `SIRVIS — Complete Build Plan and Technical Specification.md`,
`SIRVIS_ECOSYSTEM_ADDENDUM.md`, `SIRVIS_ECOSYSTEM_ADDENDUM.md — Revised`
**Cross-references:** `ECOSYSTEM_RUNBOOK.md` for the shared protocol, build order and release gates

Detailed enough that a coding agent can execute it milestone by milestone without inventing
missing architecture. Where this document and the runbook disagree about a cross-product
contract, the runbook wins. Where they disagree about SIRVIS's internals, this document wins.

---

# 1. Non-negotiable rules

> **No agent may invent another ecosystem component's API, schema, capability or behaviour
> merely to complete its own milestone. If the required contract does not yet exist,
> implement against the canonical ecosystem contract where specified, use an explicitly
> labelled test double where appropriate, or stop at the integration gate and report the
> missing dependency.**

Observed runtime behaviour takes precedence over assumptions in this document. If LM Studio,
MLX, llama.cpp, macOS or another dependency behaves differently from this plan:

1. record the actual behaviour;
2. create a regression or compatibility test where possible;
3. amend the plan or the implementation contract;
4. **do not fake the expected value.**

SIRVIS owns benchmark truth. It does not own routing (RAVIS), workspace actions (Clarvis)
or ecosystem supervision (NERVIS). It must remain fully usable with none of them installed.

---

# 2. Product vision

A local-first macOS application and service for discovering, downloading, managing,
configuring, benchmarking, comparing and recommending local LLMs.

Primary target: **Apple Silicon macOS**. Initial runtime support is GGUF via
llama.cpp/LM Studio and MLX via LM Studio. The architecture stays runtime-independent enough
to add native llama.cpp, mlx-lm, Ollama and others later through adapters.

SIRVIS is not a benchmark webpage. It is a local model intelligence service, model manager,
benchmark engine, resource scheduler, recommendation engine, web dashboard, CLI and external
API — sharing one application layer.

## 2.1 Core questions it must answer

Which model runs fastest on this Mac? Which gives the best coding quality under 32 GB? Does
MLX beat GGUF for this family? What is the quality loss between Q8, Q5 and Q4? How much
memory does this model *actually* use? How much context before performance becomes
impractical? Which chat + agent pair works best when both stay loaded? Does simultaneous
inference cause swap or serious slowdown? Which models should Clarvis use? Did a runtime
update regress performance?

## 2.2 Naming

```text
Project:        SIRVIS          CLI:            sirvis
Python package: sirvis          Local service:  SIRVIS Service
API:            SIRVIS API      Web UI:         SIRVIS
TypeScript SDK: @sirvis/client  Python SDK:     sirvis-client
macOS app:      SIRVIS.app
```

---

# 3. Technology and architecture

**Backend:** Python 3.12+, FastAPI, Pydantic, asyncio, httpx, SQLAlchemy or SQLModel, SQLite,
Alembic, psutil, subprocess only where necessary, structured logging. Polars for analytics
where useful; Parquet for high-volume telemetry. Prefer explicit, boring architecture over
heavy frameworks.

**Frontend:** server-rendered HTML, HTMX, Alpine.js, ECharts or Plotly. Avoid React initially;
introduce it only if the UI genuinely develops complex client-side state.

**Packaging:** `sirvis serve` during development, `SIRVIS.app` later via a lightweight native
shell such as Tauri. The service must stay independently runnable.

```text
              Web UI  ·  CLI  ·  External apps
                            │
                   Versioned SIRVIS API
                            │
                   Application services
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
 Benchmark Engine     Model Manager     Recommendation Engine
        └───────────────────┼───────────────────┘
                            ▼
                     Resource Manager
                            ▼
                     Runtime Adapters
                     ┌──────┴──────┐
                     ▼             ▼
              LM Studio Adapter  Future adapters
                     ┌──────┴──────┐
                    GGUF          MLX
```

**Architectural rule.** Benchmark logic stays independent of FastAPI, HTML, the CLI, LM Studio
and SQLite models. Conceptually `result = await benchmark_service.run(experiment)` must work
identically from the Web UI, the CLI, RAVIS and tests. The frontend must never contain
benchmark logic unavailable to the API or CLI.

---

# 4. API surface

## 4.1 MEP endpoints (cross-service, canonical)

`GET /ecosystem/health`, `/ecosystem/identity`, `/ecosystem/capabilities`,
`/ecosystem/version`, `/ecosystem/events` — exactly as specified in `ECOSYSTEM_RUNBOOK.md` §4.
These are the endpoints NERVIS and RAVIS negotiate against.

Minimum published capabilities:

| Capability | Meaning |
|---|---|
| `sirvis.inventory.read@1` | machines, runtimes, instances, models |
| `sirvis.runtime.state.read@1` | current runtime and instance state |
| `sirvis.runtime.control@1` | load/unload — **only if implemented and authorized** |
| `sirvis.benchmarks.jobs@1` | submit, poll, cancel |
| `sirvis.benchmarks.results@1` | runs, results, evidence |
| `sirvis.runtime_sets@1` | versioned multi-model combinations |
| `sirvis.recommendations@1` | evidence-backed suggestions |
| `sirvis.events@1` | MEP event stream |

An unavailable capability means absent or explicitly unavailable — **never a stub returning
plausible data**.

## 4.2 SIRVIS API

Default base: `http://127.0.0.1:<port>/api/v1/`. Versioning is mandatory from the first
commit. Endpoint groups:

```text
/api/v1/health                 /api/v1/benchmark-jobs
/api/v1/system                 /api/v1/benchmark-jobs/{job_id}
/api/v1/machines/{machine_id}  /api/v1/benchmark-jobs/{job_id}/cancel
/api/v1/runtimes               /api/v1/benchmark-runs
/api/v1/runtimes/{runtime_id}  /api/v1/benchmark-runs/{run_id}
/api/v1/runtime-instances      /api/v1/benchmark-results/{result_id}
/api/v1/runtime/sessions       /api/v1/runtime-sets
/api/v1/models                 /api/v1/recommendations
/api/v1/models/{artifact_id}   /api/v1/profiles
/api/v1/catalog                /api/v1/evidence
/api/v1/downloads              /api/v1/events
```

List responses return `{items, next_cursor, snapshot_revision}` and accept `limit` and
`cursor`. Detail responses return the entity plus `snapshot_revision`. Any path or state
change requires a versioned contract change **before** consumer work begins.

**One deletion exists: `DELETE /api/v1/benchmark-results/{result_id}`.** It removes the
result and writes §15.1's tombstone in its place; the run row and §11.9's raw takes are kept,
and the response says so rather than leaving it to be assumed. It requires **`admin`**, not
`benchmark`: the scopes are graded by what they cost, deleting a measurement is irreversible,
and RAVIS admits and excludes builds on the evidence it removes — a client trusted to spend an
hour of machine time is not thereby trusted to erase what that hour produced. A missing result
is a 404 and stays one, so deleting the wrong id and deleting the same id twice are told apart.

`/api/v1/health` remains as a convenience alias carrying the same data as
`/ecosystem/health` plus identity and capability summaries. The `/ecosystem/*` endpoints are
canonical for negotiation.

`GET /api/v1/models` accepts **`runtime_key`**, resolving the name a runtime reports for a build
to that build's identity (§6, §12.2), for installed models whether or not they are loaded. This
is the lookup RAVIS uses instead of matching on names, which §15.1 forbids. It answers 404 when
no installed build carries that key, and 404 stays meaningfully different from a guess.

Cancellation is idempotent. **A successful HTTP request is not a successful benchmark.**

## 4.3 Error model

```json
{"error": {"code": "INSUFFICIENT_MEMORY",
           "message": "Requested Runtime Set cannot be loaded safely.",
           "details": {}, "request_id": "...", "trace_id": "..."}}
```

Codes: `MODEL_NOT_FOUND`, `MODEL_NOT_INSTALLED`, `RUNTIME_UNAVAILABLE`,
`INSUFFICIENT_MEMORY`, `RESOURCE_BUSY`, `INVALID_CONFIGURATION`, `UNSUPPORTED_PARAMETER`,
`LOAD_FAILED`, `BENCHMARK_NOT_FOUND`, `TIMEOUT`, `DOWNLOAD_FAILED`, plus the MEP codes.

## 4.4 SDKs and OpenAPI

Ship TypeScript and Python clients. TypeScript matters for VS Code and Node consumers.
Treat the generated OpenAPI document as a public contract — API docs, type generation,
client generation and breaking-change checks all derive from it.

## 4.5 Security and privacy

Bind `127.0.0.1` by default; never expose to the LAN by default. Benchmark prompts and results
stay local. External networking happens only for model discovery, model download, optional
external judges and updates. **Never upload benchmark results automatically.**

**A local API token is required, not optional.** SIRVIS's mutating surface starts
multi-gigabyte downloads, loads and evicts models other clients hold, and saturates the machine
with benchmark work; loopback is not a boundary against another local process, and §15.2's
management gate — "read-only users cannot invoke it" — cannot be exited by a service with no
concept of a user. Scopes are `read`, `benchmark`, `runtime` and `admin`. Every mutating
endpoint requires a scope even on loopback. Reads may be unauthenticated where a peer needs
them for negotiation, and `/ecosystem/*` follows the runbook.

**Origin and Host validation.** SIRVIS serves a browser dashboard from the same origin as those
mutating endpoints, so any page the user visits could otherwise POST to `127.0.0.1:8721`.
Reject a non-allow-listed `Origin` or `Host`, and require a non-simple content type on
mutations. A token check and an origin check answer different questions — the browser already
carries the user's credentials — so both are required.

**Amended 4 Sep (§16 item 5).** `Host` was not read anywhere until then: the origin check
runs inside `require`, which guards mutations, and the disclosure a rebound page performs is
a *read* of every model, benchmark and machine detail on the box. It is middleware now, ahead
of reads and writes alike.

The CSRF token this asked for is deliberately not built. It defends an *ambient* credential —
a cookie the browser attaches by itself — and SIRVIS has none: every mutation carries a
runtime-scoped token in an `Authorization` header, which a cross-origin page cannot set
without a preflight measured against an allowlist that is empty by default. The credential
does the token's work one layer earlier. `/ecosystem/events` is the shared heartbeat route and
carries no data yet; it gets these rules when §16 item 9 makes it a real stream.

**Retrieved content and the fencing rule.** Under runbook §9 the producer owns fencing. SIRVIS
has exactly one such path: **optional external judges**, where a model's generated output
becomes input to a judging prompt. That output is untrusted text by construction — it is the
thing under test — so it is fenced before it enters a judge prompt, and a judge verdict is
never accepted as an instruction to SIRVIS. Downloaded model metadata is fenced on the same
grounds wherever it reaches a prompt.

**Gate:** an unauthenticated mutation is refused; a wrong-`Origin` mutation is refused; a
generated output containing judge-directed text does not change its own verdict.

---

# 5. Identity

## 5.1 Machine identity

SIRVIS needs a stable local machine identity. It must be a locally generated installation
UUID — **never** a hostname alone, MAC address, externally exposed serial number, username
or any reversible hardware fingerprint. It must be opaque and resettable.

Hardware metadata is attached to the *snapshot*, not encoded in the ID: Mac model, chip, CPU
cores, GPU cores where available, unified memory, macOS version, disk capacity, free disk,
thermal state, runtime versions, SIRVIS version. Inventory may contain hardware facts needed
for compatibility, but transport and display must label sensitivity and support redaction.

Every benchmark references an immutable snapshot.

## 5.2 Domain identifiers

Opaque IDs for `machine`, `runtime`, `runtime_instance`, `model_artifact`,
`model_configuration`, `runtime_set`, `benchmark_job`, `benchmark_run`, `benchmark_result`
and `recommendation`. **Display names are never identifiers.** Stable IDs survive restart
while the underlying entity is unchanged; duplicate display names must not collide.

---

# 6. Model domain

Four separate concepts, never collapsed:

| Concept | Represents | Key fields |
|---|---|---|
| **ModelFamily** | A conceptual base model (e.g. `Qwen3-30B-A3B`) | `family_id`, display name, architecture, parameter count, active parameter count, declared context, source metadata |
| **ModelVariant** | A specific usable build/conversion (GGUF Q4_K_M, MLX 4-bit…) | `variant_id`, `family_id`, source repository, revision, runtime format, quantization, file info, size, architecture metadata |
| **LocalModel** | An installed variant | `local_model_id`, `variant_id`, storage path, installed size, download source, installed timestamp, **`runtime_key`** — the name the runtime itself reports for this build, whether or not it is loaded |
| **RuntimeModelInstance** | A currently loaded instance | `instance_id`, `local_model_id`, runtime, requested config, effective config, load time, loaded timestamp, runtime identifier |

**Family linking preserves uncertainty.** Link equivalent variants under one family, but never
assert equivalence solely because names look similar.

**`runtime_key` is the join RAVIS needs, and it belongs to the installed model rather than to a
loaded instance.** RAVIS sees only what a provider reports — `qwen3-30b-a3b-mlx` — and §15.1
forbids it inferring equivalence by name or reading SIRVIS's database, so without a lookup that
works on a *cold* model, exactly the case the load-or-don't decision needs evidence for, RAVIS
must route on `UNKNOWN`. Resolve it with `GET /api/v1/models?runtime_key=…`, which returns the
build identity (§12.2) or 404. **404 is a correct answer** and must stay distinguishable from a
guess.

One name for artifact identity, since four are currently in use: **`local_model_id`** is the
installed build; `artifact_id`, `model_artifact` and a bare `model_id` are not separate things
and should not appear.

Responses must distinguish installed artifact from loadable configuration from running
instance. Missing runtime and stale instance must be distinguishable. Do not claim tool use,
context capacity, structured output or multimodality from model-family defaults when probing
is required — capability-critical values carry provenance.

---

# 7. Runtime adapters

```python
class RuntimeAdapter:
    async def health(self): ...
    async def runtime_info(self): ...
    async def list_models(self): ...
    async def list_loaded_models(self): ...
    async def download(self, request): ...
    async def download_status(self, job_id): ...
    async def load(self, model, config): ...
    async def unload(self, instance_id): ...
    async def unload_all(self): ...
    async def generate(self, request): ...
```

First adapter: `LMStudioAdapter`. Later: `LlamaCppAdapter`, `MLXAdapter`, `OllamaAdapter`.

LM Studio is the initial runtime and orchestration backend. Its endpoint is configurable.
**Never hard-code an assumption where LM Studio can report actual capability or state.** CLI
tooling may exist as a debug fallback but must not be the primary abstraction while an API
exists.

Runtime state is the closed enum `stopped | starting | ready | busy | stopping | failed |
unknown`. Transitions carry timestamps and reasons.

**The endpoint is configurable; the machine is not.** A runtime URL means *a
runtime on this host* — another port, another container, another user — and
never a runtime on another computer. The field is configurable and therefore
invites the second reading, so the rule is written here rather than left to be
discovered.

SIRVIS pointed at a runtime across a network would work, which is the problem.
Generations would run and throughput would even be roughly right, because that
is observed over the wire. Everything *around* the number would be read from the
wrong machine: §11.8 samples memory eight times around each generation —
baseline, after each load, before generation, at peak prompt, at peak
generation, post-run, post-unload, and a poll during — and every one of those
would describe the observer rather than the subject. A run that exhausted the
remote machine's memory would be stamped `VALID` because this one was idle, and
an untroubled run would be `SUSPECT` because this one was busy. The result would
carry this machine's identity while describing another's hardware, and §9's
lease ceiling would be guarding memory that is not where the models are.

That is worse than declining to measure. **Evidence is a claim about a machine**,
and every discipline in this document — the VALID/SUSPECT verdict, the
conditions attached to a score, the provenance in §12.1 — rests on the measuring
and the measured being the same host. Split them and the numbers are confident
and unfounded, which is the one failure this service exists to prevent.

The lifecycle half is local by construction anyway: LM Studio exposes no HTTP
load or unload, so loading shells out to a binary on this machine. Aiming the
URL elsewhere protects reads and nothing else.

**So SIRVIS runs where the models run.** A machine that holds the models holds
the service that measures them; a machine that only *routes* to them needs
neither, because RAVIS runs without SIRVIS by §13.4 and reports the degradation
rather than hiding it.

## 7.1 Load configuration

```yaml
context_length: 16384
runtime: llama.cpp
gpu_offload: max
flash_attention: true
eval_batch_size: 512
offload_kv_cache_to_gpu: true
num_experts: null
ttl: null
```

Store **both** the requested and the effective configuration. **Never silently ignore an
unsupported value** — surface it.

---

# 8. Model browser, catalog and downloads

Navigation: Models → Installed / Discover / Downloads. The installed view shows family,
variant, runtime, quantization, file size, loaded state and last benchmark, with actions
Load, Unload, Benchmark, Compare, Inspect, Find Variants, Reveal, Delete.

```python
class ModelCatalog:
    async def search(...)
    async def get_model(...)
    async def variants(...)
```

Providers: `LMStudioCatalog` first, `HuggingFaceCatalog` later. Discover filters: search,
GGUF, MLX, parameter size, quantization, architecture, installed/not installed, family, file
size; later license, author, popularity, last update.

**Downloads are persistent jobs** tracking download ID, variant, source, status, bytes
downloaded, total bytes, start and completion time and failure. Statuses: `queued`,
`downloading`, `paused`, `completed`, `failed`, `cancelled`, `already_present`. A browser
reload must not lose state.

Before downloading, check model size against available disk and remaining disk after
download. Warn on insufficient disk, dangerously low remaining disk, or a very large
percentage consumption. **Never auto-delete models.**

---

# 9. Resource management

`ResourceManager` tracks loaded models, owners, references, sessions, leases and memory
reservations; coordinates benchmark exclusivity; prevents conflicting unloads. **All
load/unload operations flow through it.**

**Ownership.** A loaded model may be used by RAVIS, the benchmark runner, the web UI or an
external application at once. Do not unload because one owner releases it — unload when the
reference count reaches zero, unless explicitly force-unloaded with authority. Never unload a
resource owned by another client.

**Runtime sessions.** External applications request a set of loaded models:

```http
POST /api/v1/runtime/sessions
{"models": [{"role": "chat", "model_id": "..."},
            {"role": "agent", "model_id": "..."}],
 "lease_seconds": 3600}
```

Returns session ID, runtime instances, roles, lease and state.

**Leases** recover from crashed clients and prevent permanently stranded models. Conservative
defaults; renewal allowed.

**Load conflict policies:** `wait` (default), `reject`, `preempt`. **Preemption is never
implicit.**

**Amended 4 Sep (§16 item 7): two of those three are names, and the code says so.**
`wait` does not wait — `_make_room` reports exhaustion, because a queue that blocked
inside the resource lock would deadlock against every release that could free the
capacity it waits for, so bounded queueing belongs above that layer. `preempt`
reclaims only *unreferenced* holdings, and release unloads at zero, so no such
holding can currently exist: it refuses exactly as `reject` does. The branch is kept
because §9 names the policy and because it becomes reachable the moment models are
retained warm after their last release.

So all three currently refuse, and only `reject` refuses for the reason its name
gives. The manager's own docstrings state both facts plainly — *"saying so is better
than a `wait` that silently behaves like `reject`"* — and this clause did not, which
is the gap. Real waiting is a queue above the manager and is not built; when it is,
this sentence stops needing the paragraph under it.

Use per-runtime locking, bounded queues, capacity reservations, startup timeouts,
lease heartbeats, idempotent operations and cleanup after crash. Ownership is explicit so
RAVIS and NERVIS never fight over lifecycle.

---

# 10. Runtime Sets

A Runtime Set is a first-class multi-model target — a versioned definition, immutable at use,
of configurations intended to coexist or cooperate.

```yaml
name: clarvis-balanced
models:
  - role: chat
    model: model-a
    context_length: 16384
  - role: agent
    model: model-b
    context_length: 32768
```

Roles are arbitrary strings: `chat`, `agent`, `planner`, `coder`, `vision`, `embedder`,
`reranker`, `judge`.

Stored metadata: `runtime_set_id`, revision, name, purpose, members with roles and placement,
load order, requested and effective configs, concurrency and resource reservations, known
conflicts, combined memory, peak memory, swap, headroom, machine, measured combination
evidence, per-member evidence, compatibility constraints and unknowns.

**A Runtime Set has no routing semantics.** RAVIS owns route choice. SIRVIS reports whether a
combination was tested, under what workload, and the observed interference and capacity.

Gates: two revisions are distinguishable and old results retain the original revision; a
simultaneous-load failure is a *result*, never silently converted into separate-model success;
resource totals and contention metrics identify measurement versus estimate; RAVIS can refer
to a set or member without learning SIRVIS internals.

## 10.1 Why multi-model benchmarking matters

On a 64 GB machine a 24 GB chat model and a 30 GB agent model each appear to fit. Together
they need 54 GB of weights *plus* KV cache, runtime overhead, OS memory, Clarvis, RAVIS and
everything else — producing memory pressure, swap, reduced throughput and thermal effects.

> **Two models fitting separately does not prove they work well together.**

---

# 11. Benchmarking

## 11.1 Experiment model

Every execution is an Experiment storing: experiment ID, timestamp, system snapshot, runtime
snapshot, target, models, Runtime Set if applicable, load config, generation config, suite,
warmups, repetitions, seed, ordering, environment mode, telemetry policy, notes and tags.

Target types: single model, Runtime Set, model-family sweep, configuration matrix.

**Environment modes** are `controlled` (attempt exclusive resource access) and `shared`
(measure realistic contention). **Never silently compare them as equivalent.**

## 11.2 Lifecycles

**Single model:** validate → capture baseline → acquire resources → load → record load time →
verify effective config → warm up → measured repetitions → evaluate → finalize telemetry →
unload if owned → persist.

**Multi-model:** validate Runtime Set → acquire resources → capture baseline → load role A →
measure → load role B → measure → load remaining roles → verify all → warm up each →
individual-role tests → sequential → alternating → concurrent → joint workflow → evaluate →
unload owned resources → persist. **Load order is always recorded.**

## 11.3 Multi-model modes

| Mode | Behaviour | Measures |
|---|---|---|
| **Sequential** | All resident, one runs at a time | Resident memory impact, switching overhead, co-resident throughput |
| **Alternating** | chat → agent → chat → agent | Latency variability, cache and runtime switching effects |
| **Concurrent** | Simultaneous inference | TTFT and throughput degradation, GPU contention, memory pressure, swap, thermal state, combined completion latency |

Produce an interaction matrix with degradation percentages:

```text
                 Alone      Co-resident   Concurrent
Chat tok/s       42.1       39.8          28.7
Agent tok/s      35.8       33.9          24.2
Chat TTFT        .38s       .44s          .65s
Agent TTFT       .52s       .57s          .81s
Peak RAM         22 GB      48 GB         52 GB
Swap              0 GB       0 GB          1.8 GB
```

## 11.4 Categories

**Performance** — synthetic deterministic workloads (512 / 2K / 8K / 16K / 32K token prompts
→ 256 output), measuring load time, prompt throughput, TTFT, generation throughput, total
latency, memory and swap.

**Capability** — coding, reasoning, instruction following, structured output, information
extraction, long-context retrieval, summarization, tool use, planning, conversation.

**Stress** (optional, and always clearly labelled) — maximum practical context, long
generation, rapid switching, sustained concurrency, load/unload cycles, memory pressure.

## 11.5 Definitions and versioning

Benchmark specs live in version-controlled files:

```text
benchmarks/{performance,coding,reasoning,structured-output,long-context}/
benchmarks/integrations/clarvis/
```

```yaml
id: json-extraction-001
version: 1
prompt: |
  Extract...
generation:
  temperature: 0
  max_tokens: 256
evaluator:
  type: json_schema
```

Once results exist for `clarvis-agent@1.0`, its prompts and rules are frozen. Changes require
a new version.

## 11.6 Evaluation

Preference order: **executable tests → exact-answer grading → schema validation →
deterministic rules → LLM judge → human evaluation.** Do not use LLM judging where
deterministic evaluation exists.

LLM judging is optional. When used, store judge model, judge revision, judge prompt, rubric
version, parameters, raw judgment and parsed score — and keep objective and judge scores
separate.

**Generated-code safety.** Never execute generated code in the main SIRVIS process. Use
isolated execution with a timeout, a restricted working directory, network disabled by
default, no inherited secrets and resource limits where possible. Treat model-generated code
as untrusted.

## 11.7 Statistical method

Defaults: `warmups = 2`, `measured_runs = 5`. Store **every** repetition. The headline is
usually the **median**; also preserve mean, min, max, stddev, P10 and P90 where sample size
permits.

> **Do not treat a single benchmark take as authoritative.** Clarvis's own development work
> observed identical prompt variants differing by roughly 32%, and concluded that single-run
> comparisons can measure noise rather than a real difference. Benchmark conclusions require
> repeated measurements and visible spread.

Experiment ordering supports `fixed`, `randomized` and `balanced`, with ABBA ordering later.
Store the order and the random seed.

## 11.8 Telemetry, thermal and validity

**Memory telemetry** captures baseline, after each load, before generation, peak prompt, peak
generation, post-run, post-unload, and swap baseline/peak/final. For Runtime Sets, record
after every model load.

**Thermal** capture where possible: thermal pressure, CPU, GPU, RAM, swap. Optional policy:

```yaml
thermal_policy:
  cooldown_between_targets: true
  minimum_seconds: 30
```

Flag thermally compromised runs. **Do not silently discard them.**

**A validity warning carries what it undermines (§16 item 7, 4 Sep).** Flagging the
run was only half of "do not silently discard": a consumer holding one flag for the
whole record has two equally wrong options, trust it all or drop it all. On this
machine 44 of 93 records were `SUSPECT` and twenty-one of those only because it was
thermally throttled — a 48% swing in tokens/second, and no evidence at all about
whether the model formed well-formed tool calls.

Every note therefore declares a `ValidityScope`:

| Scope | What is in question | Producers |
|---|---|---|
| `TIMING` | the rate describes something other than the model | thermal, swap |
| `OUTPUT` | what came back — tokens that never arrived as content, unexpected stops | generation |
| `CONDITIONS` | the run answered a different question, so nothing on it is safe | configuration mismatch, prompt adaptation |

The scope belongs to the **warning**, not to the function that emits it: a single
producer emits warnings of different kinds. `_suppression_warnings` reports both
reasoning tokens (`TIMING` — the note itself says the throughput covers the answer
only and the time-to-first-token includes the thinking) and a suppressed prompt
(`CONDITIONS`). Attaching a scope per producer read the first as the second, which
would have demoted `gemma-4-e4b` from 24/24 well-formed tool calls. A warning that
cannot be placed is `CONDITIONS` — the conservative reading, tainting everything
rather than nothing.

Consumers read the scope against what they are claiming: RAVIS's tool-call verdict
refuses `OUTPUT` and `CONDITIONS` and accepts `TIMING`. **An empty scope list means
*not stated*, never *nothing affected*** — records written before this carry none,
and a reader must keep its previous handling for them rather than infer either way.

**Validity** is `VALID`, `VALID_WITH_WARNINGS` or `INVALID`. Warnings include swap, thermal
pressure, runtime changed, background contention, unexpected generation stop, effective
config mismatch, load instability. **Never hide an integrity problem.**

## 11.9 Raw result preservation

```text
results/<experiment-id>/
├── experiment.json
├── system.json
├── runtime.json
├── responses/
├── telemetry/measurements.parquet
└── logs/
```

Raw responses enable rescoring without rerunning inference.

## 11.10 Queue and failure handling

Internal job phases: `queued`, `waiting_for_resources`, `downloading`, `preparing`, `loading`,
`warming_up`, `running`, `evaluating`, `unloading`, `completed`, `failed`, `cancelled`.

The **published** job state is the coarse contract enum `queued | preparing | running |
succeeded | failed | cancelled | partial`, with the internal phase carried in a `detail`
field. Consumers switch on the coarse enum; the detail is for humans and diagnostics.

Expect and handle: LM Studio unavailable, runtime crash, OOM, load failure, API timeout,
download failure, disk full, malformed output, lost connection, sleep/wake, user cancellation.
**One failed benchmark must not block unrelated jobs.** Persist enough state for recovery.
Jobs survive a SIRVIS restart or are truthfully marked unrecoverable. No result is visible
before its snapshot and provenance commit atomically.

Supported queue operations: pause, resume, cancel, retry, reorder.

---

# 12. Evidence

## 12.1 Provenance invariant

Every performance, capacity, compatibility, quality, capability or recommendation input
carries provenance:

- **`MEASURED`** — produced by a recorded benchmark run on the identified
  machine/runtime/model/artifact/configuration.
- **`PARTIALLY_MEASURED`** — a composite where some inputs are measured and some are not.
- **`ESTIMATED`** — derived from a declared model, heuristic, interpolation, vendor claim or
  comparable measurement.
- **`UNKNOWN`** — no defensible value. It stays unknown; it never receives a default.

```json
{
  "kind": "MEASURED",
  "method": "sirvis.benchmark.tokens_per_second.v1",
  "observed_at": "2026-08-22T12:00:00Z",
  "source_run_id": "bench_...",
  "confidence": 0.98,
  "notes": null
}
```

For `ESTIMATED`, identify the estimation method, source evidence, assumptions and confidence.
For `UNKNOWN`, omit the value or use `null` and state the reason. **Never convert a missing
value to zero.**

**Aggregation cannot promote evidence.** A result derived partly from estimated data is at
most `ESTIMATED`. A result materially dependent on unknown data must disclose that
uncertainty. Consumers that do not understand `PARTIALLY_MEASURED` must treat it as
`ESTIMATED`, never as `MEASURED`.

## 12.2 Canonical evidence identity

This is the contract RAVIS depends on. Evidence must **never** be keyed as `model → score`.
Identity includes:

```text
machine_id
model_family · model_variant · source_repository · source_revision
format · quantization
runtime · runtime_version · runtime_configuration
role
benchmark_suite · benchmark_version
```

So this:

```text
Qwen family / mlx-community build / 4-bit / MLX runtime / 32K context
  / clarvis-agent / clarvis-agent@1
```

is *different evidence* from:

```text
same base model / GGUF Q4_K_M / llama.cpp / clarvis-agent
```

### 12.2.1 The runtime describes the wrong build

**LM Studio groups several builds under one entry and its HTTP API describes whichever the
app has selected, not the one that is loaded.** Measured on this machine, with an MLX and a
GGUF of `google/gemma-4-e4b` installed:

```text
lms ps           google/gemma-4-e4b@q4_k_m   gguf   Q4_K_M   (loaded)
/api/v0/models   google/gemma-4-e4b          mlx    4bit
```

It compounds it two ways. A completion addressed to `…@q4_k_m` is answered while
`/api/v0/models/…@q4_k_m` returns "not found" — so the runtime will measure a build it
refuses to describe. And a *non-selected* variant is not published at all unless it happens to
be loaded: unload the MLX and it vanishes from the catalogue while still installed, loadable
and benchmarkable.

Filing a GGUF measurement under an MLX identity is not cosmetic. On this machine those two
builds reach 21/24 and 24/24 on the same tool-call trial, and RAVIS admits and excludes on it.
So SIRVIS asks the runtime's own CLI, which does know; **refuses to record evidence when
nothing can confirm which build was measured**; enumerates installed builds from the CLI
additively, never replacing the catalogue with it (the CLI indexes fewer models overall); and
consults the CLI only for a loopback runtime, because it reads *this* machine's disk and an
adapter pointed at another host must not be handed these builds.

The domain keys an installed build on its qualified key for the same reason. Keyed on the
family, the two collapsed into one record and the first writer won: the machine held two
builds and reported one.

## 12.3 Evidence envelope

```json
{
  "evidence_type": "MEASURED",
  "machine_id": "...",
  "role": "clarvis-agent",
  "target": {
    "model_family": "qwen-example",
    "variant": "mlx-4bit",
    "runtime": "mlx",
    "runtime_version": "...",
    "runtime_config": {"context_length": 32768}
  },
  "suite": {"id": "clarvis-agent", "version": "1"},
  "samples": 5,
  "metrics": {
    "generation_tok_s": {"median": 38.4, "min": 35.9, "max": 40.8},
    "tool_call_pass_rate": {
      "phrasings": 8, "repetitions": 3,
      "passed": 23, "total": 24, "rate": 0.958
    }
  },
  "validity": "VALID"
}
```

Metrics name their unit, direction, aggregation, sample count and provenance.

## 12.4 Immutable snapshots

Benchmark results reference immutable snapshots of: machine and relevant hardware/OS state;
runtime product, version, build and launch settings; the exact model artifact, quantization,
hash where available, context and tool settings; workload, dataset and spec version and seed;
warmup, sample count, concurrency, power and thermal conditions where observable; Runtime Set
membership and scheduling policy; SIRVIS version and benchmark method version.

**Changes create new snapshots and new results. They never rewrite old evidence.**

## 12.5 Role-specific verdicts

A verdict belongs to a **build + runtime config + role**, never to a conceptual model:

```text
Qwen MLX 4-bit
  clarvis-chat:   PASS
  clarvis-agent:  FAIL  (tool-call reliability below threshold)
```

**Do not mark the whole family "bad."**

---

# 13. Clarvis benchmark integration

> **Wrap validated application-specific benchmarks before replacing them.**

Clarvis already has measured local models against its two roles. SIRVIS integrates that
through an adapter rather than rewriting it:

```text
Clarvis existing benchmark tool → SIRVIS ClarvisAdapter → canonical Experiment → canonical Result
```

## 13.1 Roles

Minimum `clarvis-chat` and `clarvis-agent`; later `clarvis-joint` and `clarvis-concurrency`.

These are **SIRVIS workload specifications**, not RAVIS virtual profile definitions, though
they may share stable references. Clarvis's separate chat and coding-model behaviour is
source-established. If a workload definition does not exist, **stop rather than guess**.

**Accepted, with the threshold recorded here.** These suites were previously marked "proposed,
require explicit acceptance", which left `clarvis-agent: FAIL` with no evaluable pass condition
while M13, M15 and M16 stated their exits unconditionally. The accepted threshold for the agent
role is:

```text
clarvis-agent  tool_call_pass_rate ≥ 0.95
               over ≥ 8 distinct prompt phrasings × ≥ 3 repetitions each
               at suite version 1
```

**Two axes, not one, and the phrasing axis is the one that catches failures.** An earlier
version of this threshold said "≥ 50 attempts" and was wrong in kind rather than in size: it
assumed repetition of a single prompt. The working harness this suite wraps
(`clarvis-firstrun/tools/suite2.py`, verified 2026-08-23: `RUNS = 3`, eight entries in
`TOOL_PROMPTS`) runs eight different ways of asking for the same file, precisely because
`granite-4.0-h-tiny` passed a single prompt three times out of three and then lost the filename
on all eight. Fifty repetitions of one phrasing would have scored it a clean pass. A model's
tool reliability is phrasing-sensitive, so a rate measured along one phrasing is not a rate.

**A call counts only when the path actually arrives.** A `readFile` emitted with no arguments is
worse than no call at all — the consumer cannot act on it and nothing explains why — so it
scores as a failure, not as a partial success.

The threshold is versioned with the suite, and the suite version is part of evidence identity
(§12.2), so changing it produces new evidence rather than silently reinterpreting old evidence.
A run that covers fewer phrasings or fewer repetitions than the minimum yields `UNKNOWN`, never
a pass. Any later role suite records its threshold on both axes the same way before a milestone
may depend on it.

**`clarvis-chat`** evaluates instruction following, clarity, conversation quality, code
explanation, diff explanation, summarization, planning quality, conversational latency and
streaming, and context behaviour.

**`clarvis-agent`** evaluates coding correctness, tool-call reliability, tool argument
correctness, file selection, repository reasoning, planning, patch generation, bug fixing,
test repair, structured calls, long-context code comprehension, multi-step completion,
cancellation responsiveness and context/tool limits.

**`clarvis-joint`** simulates: user task → chat model interprets → agent model works → chat
model explains. Measures task success, handoff quality, latency, tool correctness and final
explanation quality.

**`clarvis-concurrency`** simulates agent work plus chat generation, measuring chat slowdown,
agent slowdown, memory, swap and total task delay.

## 13.2 Tool-call pass rate is first-class

```json
{"tool_call_pass_rate": {
  "phrasings": 8, "repetitions": 3, "passed": 23, "total": 24, "rate": 0.958
}}
```

**Both axes are part of the measurement, not annotations on it.** A rate reported without them
is not comparable to one that carries them, because it does not say whether the model was asked
the same way twenty-four times or eight different ways three times each — and §13.1 exists
because those two produce different answers for the same model.

This matters more to RAVIS's `ravis/clarvis-agent` routing than generic chat quality does.

**Gate:** a model is never declared Clarvis-agent-capable from family metadata alone. Measured
probes, or explicitly labelled external/estimated evidence, are required.

---

# 14. Sweeps and recommendations

## 14.1 Configuration sweeps

```yaml
matrix:
  models: [qwen-mlx-4bit, qwen-gguf-q4]
  context: [8192, 16384, 32768]
  flash_attention: [true, false]
```

Show the expanded run count before execution. Warn on large matrices. For Runtime Set sweeps,
**do not benchmark the Cartesian product blindly** — use the search strategy:

```text
10 chat × 10 agent → single-role benchmarks → memory-fit filter → role-score filter
  → estimated contention ranking → top candidate combinations → measured pair benchmarks
```

Later, predict combined memory, swap likelihood and expected slowdown from individual memory,
runtime, context, throughput, machine memory bandwidth and model size. **Always label a
prediction `ESTIMATED`.**

## 14.2 Memory-fit estimator

Classify `IDEAL`, `GOOD`, `TIGHT`, `POOR`, `DOES_NOT_FIT`, `UNKNOWN`, considering weights,
runtime overhead, KV cache, context, batch size, quantization, MoE behaviour, OS headroom and
already-loaded models. Always distinguish measured from estimated.

```json
{"fit": "GOOD", "headroom_gb": 8.1, "swap_expected": false}
```

## 14.3 Recommendation engine

**Inputs:** machine, roles, constraints, profile weights, benchmark evidence, acceptable
evidence level, candidate scope.

**Output:** ranked candidates *and excluded candidates with reasons*, recommended model or
Runtime Set, fit tier, memory, expected performance, quality, evidence level, supporting
benchmark IDs, uncertainty, generated time, expiry/staleness policy, and the recommendation
algorithm version.

```http
POST /api/v1/recommendations
{"profile": "clarvis", "roles": ["chat", "agent"],
 "constraints": {"avoid_swap": true}, "mode": "fast"}
```

**Modes:** `fast` uses existing evidence only and starts no new benchmark. `verified` may
propose or run additional benchmarks for top candidates — never automatically starting
expensive work unless requested.

**Role profiles:**

```yaml
name: clarvis-agent
weights:
  coding: 0.35
  tool_use: 0.25
  reasoning: 0.15
  throughput: 0.10
  context: 0.10
  memory: 0.05
requirements:
  context: 32768
```

**Combination scoring** is `ChatUtility + AgentUtility + JointUtility − MemoryPenalty −
SwapPenalty − ContentionPenalty − ReliabilityPenalty`. **Do not simply pick the highest chat
model and the highest agent model independently.**

**Recommendations are evidence-backed suggestions, not routing commands.** SIRVIS must not
invent RAVIS policies or Clarvis requirements.

---

# 15. Ecosystem contracts

## 15.1 RAVIS consumption

RAVIS consumes versioned inventory snapshots, current runtime state, benchmark evidence,
Runtime Sets and recommendations. SIRVIS provides cursor- or revision-based incremental
reads, staleness timestamps and tombstones, stable evidence references resolvable during the
retention window, events for inventory/runtime/job/result/recommendation changes, and **no
RAVIS-specific mutation of historical evidence**.

**Tombstones are real, and this is what one is for.** The evidence surface published an empty
list for them until deletion existed. A tombstone records that a result was removed — its
result id, evidence id, target, role, when it was measured, when it was deleted and why — and
a consumer holding `ev_…` needs it because a build with no evidence and a build whose evidence
was withdrawn are indistinguishable on every other surface, and lead to different decisions.

A tombstone does **not** mean the evidence is gone. §12.2 makes two runs of one suite against
one build two results under one evidence identity, so a deleted result can sit beside a live
one; the record names the *result*, and the deletion response reports how many remain under
that identity. Tombstones on the evidence index are narrowed by the same candidates and role
the caller filtered evidence on — deliberately not by which records survived, since the case
that matters is a build whose only result was deleted and whose item list is therefore empty.

RAVIS must be able to ask:

> Give me the best measured evidence for role `clarvis-agent` on this machine for these
> candidate builds, under these runtime configuration constraints.

So filtering must support machine, role, model family, variant, runtime, runtime config,
suite and version, and evidence type. **Do not force RAVIS to infer equivalence across
builds.** RAVIS must never need SIRVIS's database.

RAVIS decides how evidence affects routes. SIRVIS never receives prompts, provider keys or
user content to satisfy this contract.

**Pairwise gate:** a real RAVIS instance imports a fixture and a live SIRVIS result, preserves
provenance and staleness, reacts to a runtime-state change, and rejects an unsupported major.

## 15.2 NERVIS management

NERVIS may read health, system, machine, models, downloads, runtime state, benchmark jobs,
results, recommendations, capabilities, versions, events and diagnostics. It **never** reads
the SIRVIS database.

Mutating operations are narrowly owned and separately authorized: refresh inventory,
submit/cancel benchmark, and start/stop a runtime **only if SIRVIS already owns and advertises
that operation**. Every control operation supports idempotency, actor identity, an audit
event, an explicit target, a precondition or version, and a structured result. **NERVIS may
not manufacture a "restart" by combining undocumented calls.**

**Management gate:** every enabled NERVIS control maps to an advertised capability and a
passing real-service contract test; read-only users cannot invoke it; a denial leaves state
unchanged.

## 15.3 Events and traces

MEP envelopes for at least:

```text
sirvis.inventory.changed            sirvis.benchmark.result.created
sirvis.runtime.state.changed        sirvis.recommendation.created
sirvis.benchmark.job.state_changed  sirvis.capability.changed
```

Plus the operational stream: `download.started/progress/completed`,
`model.loading/loaded/unloaded`, `benchmark.started/progress/completed/failed`,
`runtime.session.created/released`.

Events carry **references, not full sensitive inventories or results**. Spans cover API
request, queue wait, preparation, runtime load, benchmark execution, result persistence and
recommendation generation. Where invoked by RAVIS or NERVIS, preserve `trace_id`,
`request_id`, `session_id` and `job_id`.

**Telemetry export is optional, bounded, redacted, and never blocks a benchmark.**

## 15.4 Standalone behaviour

SIRVIS starts and operates without RAVIS, Clarvis, NERVIS or a trace collector. Missing peers
affect only integration capabilities. Runtime absence produces an unavailable state, not
service failure. Loss of event consumers does not grow an unbounded queue. Keychain, network,
disk and runtime failures expose truthful readiness and actionable structured errors.

---

# 16. Web dashboard

**`nervis/index.html` already renders the Models, Benchmarks, Runtime Sets and Results
screens**, against a transcription of a real benchmark run on a real machine — including the
provenance rendering this section insists on. It is the reference implementation, not a sketch,
and this stage's visible increment is wiring those screens to real endpoints rather than
building them (`ECOSYSTEM_RUNBOOK.md` §6.2 Stage 4). Read `nervis/docs/WIRING.md` before
starting; read `nervis/docs/PITFALLS.md` before writing the replacement.

Pages: Dashboard, Models, Downloads, Benchmarks, Queue, Results, Recommendations, Runtime,
System, Settings.

**Results views.** Single model: `Model / Build · Runtime · Role · TTFT · Tok/s · RAM ·
Score`. Runtime Set: `Combination · RAM · Chat · Agent · Joint · Swap`. **Never collapse
build and runtime detail away.** A headline median is fine; sample count and spread must stay
accessible.

Never display `Model X: 93` without context. Prefer:

```text
Model X · MLX 4-bit · Clarvis Agent · 5 runs · median … · spread … · runtime config …
```

Rollups are allowed; **drill-down must preserve build/runtime/role provenance**.

**Charts:** generation throughput, prompt throughput, TTFT, RAM, swap, quality, context
scaling, quality vs speed, quality vs memory, joint score vs RAM, concurrency degradation.

**Pareto analysis** for quality vs speed, quality vs memory, speed vs memory, joint quality vs
RAM.

**Historical regressions** after an LM Studio, llama.cpp, MLX, macOS or SIRVIS update —
`generation −8.4% · TTFT +3.1% · RAM +1.2 GB · quality unchanged`.

**Result comparison** shows what changed and what stayed the same, and warns when the OS,
runtime, benchmark version or machine differs.

**Playground** — a secondary manual interface (model, system prompt, user prompt, temperature,
max tokens, seed) showing TTFT, tok/s, tokens and elapsed, with **Save as benchmark case**.

---

# 17. Storage

```text
SystemSnapshot · RuntimeSnapshot
ModelFamily · ModelVariant · LocalModel · RuntimeModelInstance
RuntimeSet · RuntimeSetMember · RuntimeSession · ResourceLease
DownloadJob
BenchmarkSuite · BenchmarkTest
Experiment · ExperimentTarget · ExperimentJob · Generation · Metric · Evaluation
BenchmarkRun · BenchmarkResult
EvidenceRecord
RecommendationProfile · Recommendation · Tag
```

`BenchmarkRun` and `BenchmarkResult` are stored because they are already served
(`/api/v1/benchmark-runs/{run_id}`, `/benchmark-results/{result_id}`), already declared as
domain identifiers, and already the target of `source_run_id` — the provenance pointer RAVIS
must preserve and NERVIS must dereference to drill through to evidence. **A run is one execution
of one `Experiment`**: an Experiment has many runs, a run has one result per
`ExperimentTarget`, and a run never spans experiments. Without the entity and that cardinality
written down, the drill-through cannot be built without inventing the mapping.

High-frequency telemetry goes to Parquet where SQLite becomes unsuitable. Use database
migrations from the first commit.

---

# 18. CLI

```bash
sirvis doctor
sirvis serve

sirvis models list
sirvis models search qwen
sirvis models download ...

sirvis runtime list
sirvis runtime sessions

sirvis benchmark run basic.yaml
sirvis benchmark run --profile clarvis --chat MODEL_A --agent MODEL_B

sirvis results latest
sirvis compare RUN_A RUN_B
sirvis recommend --profile clarvis
```

`sirvis doctor` checks Apple Silicon, macOS, RAM, disk, LM Studio, the LM Studio API,
installed models, GGUF capability, MLX capability, the database, the results directory and
the benchmark directory.

**Keep API and CLI at parity for core functionality.**

Logging carries timestamp, experiment ID, job ID, runtime session ID, model ID, runtime,
operation, severity, message and `trace_id`. **Never log secrets.**

---

# 19. Repository structure

```text
sirvis/
├── pyproject.toml · README.md
├── src/sirvis/
│   ├── api/{routes,schemas,events}/
│   ├── cli/
│   ├── core/{models,runtime_sets,experiments,evidence,recommendations,config}.py
│   ├── services/{benchmark,models,runtime,recommendations,downloads}.py
│   ├── runtimes/{base,lmstudio}.py
│   ├── catalogs/{base,lmstudio,huggingface}.py
│   ├── resources/{manager,sessions,leases}.py
│   ├── benchmarks/{engine,runner,suites}.py
│   │   ├── adapters/clarvis.py
│   │   └── evaluators/
│   ├── telemetry/{system,memory,thermal}.py
│   ├── recommendations/{engine,scoring,prediction}.py
│   ├── jobs/{queue,workers}.py
│   ├── storage/{database,repositories,parquet}.py
│   └── web/{routes,templates,static}/
├── clients/{typescript,python}/
├── benchmarks/{performance,coding,reasoning,structured-output,long-context}/
│   └── integrations/clarvis/
├── tests/{unit,integration,api,runtimes,fixtures}/
└── docs/
```

---

# 20. Development principles for AI coding agents

**Engineering standards live in `ECOSYSTEM_RUNBOOK.md` §14** — complexity ceiling, naming,
comments, error handling, tests, and the CI gates that enforce them — and are not restated here.
The numbered principles below are SIRVIS's measurement and provenance rules, which no general coding standard implies, and they add to that standard rather than replacing
it. Where one of them tightens a §14 rule, the tighter rule wins.

1. Keep benchmark logic independent from presentation.
2. Keep runtime-specific behaviour inside adapters.
3. Never silently ignore unsupported load parameters.
4. Store requested **and** effective configurations.
5. Preserve all raw benchmark outputs.
6. Treat benchmark definitions as versioned artifacts.
7. Require repeated measured runs for performance claims.
8. Preserve provenance.
9. Distinguish measured and estimated evidence.
10. Treat Runtime Sets as first-class targets.
11. Never unload resources owned by another client.
12. Route all ownership through `ResourceManager`.
13. Every benchmark job must be cancellable.
14. Every external operation requires a timeout.
15. One failed model must not crash the queue.
16. Never execute generated code in the main service.
17. Bind localhost by default.
18. Use database migrations.
19. Maintain API compatibility within a version.
20. Mock runtime adapters in unit tests.
21. Keep real LM Studio integration tests separate.
22. Avoid framework complexity without evidence.
23. Keep API and CLI parity for core functionality.
24. Treat OpenAPI as a public contract.
25. Do not substitute prediction for measurement when measurement exists.
26. Never reduce evidence to one score per conceptual model.
27. Build + runtime config + role are part of evidence identity.
28. Reuse existing validated application benchmark tools before rewriting them.
29. SIRVIS must remain usable without RAVIS, NERVIS or Clarvis.
30. Do not invent another ecosystem service's API to satisfy a milestone.

---

# 21. Milestones

Milestone numbers identify work; the runbook's stages schedule it, and §21.2 maps between them. The
mapping is in §21.2.

| # | Milestone | Build | Exit |
|---|---|---|---|
| **M0** AUTOMATED VERIFIED | Foundation | Python package, config, SQLite, migrations, structured logging, FastAPI shell, CLI shell, the `/ecosystem/*` MEP surface | `sirvis doctor` and `sirvis serve` work; database migrates; no runtime dependency needed to start; MEP conformance fixtures pass at one pinned protocol version and a mismatched major fails cleanly — Stage 1 exits here, not at M4 |
| **M1** AUTOMATED VERIFIED | Hardware/system detection | Apple Silicon detection, RAM, CPU, GPU where possible, macOS, disk, swap, thermal basics, `SystemSnapshot` | Immutable snapshot persisted; a missing metric returns Unknown rather than a fabricated value; system endpoint works |
| **M2** LIVE VERIFIED | LM Studio adapter | health, runtime info, installed models, loaded models, load, unload, generation | Discover → load → generate → unload; effective configuration captured |
| **M3** AUTOMATED VERIFIED | Model domain and inventory | `ModelFamily`, `ModelVariant`, `LocalModel`, `RuntimeModelInstance`, family linking | GGUF and MLX variants represented separately; family relation without pretending exact equivalence; stable IDs survive restart; duplicate display names do not collide |
| **M4** AUTOMATED VERIFIED | Public API foundation | `/health`, `/system`, `/models`, `/runtime`, plus §4.5 token scopes and origin validation | An external script can inspect SIRVIS and control a model *through SIRVIS* rather than LM Studio directly; MEP conformance fixtures pass; unsupported-major and redaction tests pass; an unauthenticated or wrong-origin mutation is refused |
| **M5** | SDK foundation | TypeScript and Python clients | A Node client can reach health, system, models, runtime |
| **M6** LIVE VERIFIED | Single-model benchmark engine | Experiment, warmups, repetitions, raw response capture, TTFT, tok/s, memory, load lifecycle | `sirvis benchmark run examples/basic.yaml` persists a valid result |
| **M7** LIVE VERIFIED | Benchmark evidence schema | Canonical identity and repeated-measurement structure | No scalar-only canonical score; individual repetitions preserved; median and spread available; `ESTIMATED`/`UNKNOWN` never become `MEASURED` |
| **M8** IMPLEMENTED | Resource Manager | Loaded registry, ownership, reference counts, leases, conflict policies, runtime sessions | Two clients safely share a loaded model; concurrent, warm-reuse, cold-start, load-failure, hung-inference, crash, stale-lease, cancellation and exhaustion tests leave consistent state |
| **M9** AUTOMATED VERIFIED | Runtime Sets | Multi-role members, load order, combined memory, session creation, revisions | Two models stay loaded and independently addressable; two revisions distinguishable; old results retain their revision |
| **M10** AUTOMATED VERIFIED | Multi-model benchmarks | Sequential, alternating, concurrent, contention metrics, swap, combined memory | Interaction matrix produced; a simultaneous-load failure is recorded as a result, not converted into separate-model success |
| **M11** | Model browser and download | Installed, Discover, Downloads, GGUF/MLX filters, jobs, disk checks | Browser reload does not lose download state; disk warnings fire; nothing auto-deletes |
| **M12** IMPLEMENTED | Clarvis benchmark adapter spike | Inspect existing Clarvis benchmark assets — **do not rewrite** | Each asset classified `REUSE`, `WRAP`, `UNSUITABLE` or `MISSING`; a written mapping from Clarvis benchmark output to `EvidenceRecord` exists |
| **M13** LIVE VERIFIED | Clarvis role benchmarks | `clarvis-chat`, `clarvis-agent` using wrapped existing tests where possible | Role-specific verdicts exist; agent evidence includes tool-call reliability; no metadata-only capability claim |
| **M14** IMPLEMENTED | Web UI | Dashboard, Models, Downloads, Benchmarks, Queue, Results, System, Settings | Core workflow works entirely in the browser; provenance is drillable everywhere |
| **M15** LIVE VERIFIED | Recommendation engine | Role profiles, fit, single-model and Runtime Set recommendation, evidence levels, fast mode | SIRVIS recommends a Clarvis chat + agent pair; exclusions and uncertainty are reproducible |
| **M22b** AUTOMATED VERIFIED | Reasoning-token overhead | **Reasoning-token overhead as evidence.** How much of a completion a build spends on reasoning tokens before emitting content — measured per build, like every other figure here, because no runtime advertises it: LM Studio's `/api/v0/models` publishes `type`, `arch` and `quantization` and nothing about reasoning | A build that emits reasoning tokens is distinguishable from one that does not, from evidence rather than from its name; RAVIS M16's tiebreak can read it |
| **M15b** | The §14.3 outputs M15 left out | Runtime Set recommendation (M15 ranks models only), expected memory, performance and quality as named outputs rather than `Utility.axes` entries, evidence level as an input and an output, and §14.3's hard `constraints` — refused with `UNSUPPORTED_PARAMETER` until then | A request carrying `{"avoid_swap": true}` changes the ranking rather than being rejected; a Runtime Set can win a role |
| **M16** LIVE VERIFIED | RAVIS evidence API | Filtering and query APIs RAVIS requires | A RAVIS test client queries `clarvis-agent` on this machine for candidate builds and receives provenance-rich evidence |
| **M17** | Configuration sweeps | Model, context and runtime-flag matrix expansion with warnings | Expanded run count shown before execution |
| **M18** | Advanced quality suites | Coding, reasoning, structured output, long-context, tool use | — |
| **M19** | Format comparison | Family comparison, GGUF vs MLX, variant grouping, comparable-build download and benchmark | — |
| **M20** | Advanced analytics | Pareto, regression, historical comparison, validity warnings, combination prediction | Predictions labelled `ESTIMATED` |
| **M21** AUTOMATED VERIFIED | Ecosystem events and tracing | Shared event envelope, trace propagation, NERVIS subscription | A benchmark job trace is visible externally; tracing failure does not block benchmark execution |
| **M22** | Packaging | `SIRVIS.app` | Launches the service, opens the UI, shuts down cleanly, leaves no orphan process. **Un-ticked 3 Sep**: no `.app` bundle, packaging script or build config exists anywhere in the repo — `tools/run.py` is a dev-mode launcher for all three services together, never attributed to this milestone. Flagged as a suspect tick once already (1 Sep, alongside M12/M22b) but never resolved then; an audit confirmed it is genuinely unbuilt, not merely unverified |

## 21.1 First vertical slice

Before broad feature work, prove:

```text
Start SIRVIS → detect Mac → connect LM Studio → list installed models
  → load one GGUF → benchmark → unload
  → load one MLX → benchmark → compare → persist → expose via API
```

Then:

```text
Load GGUF + MLX → measure combined RAM → benchmark independently
  → benchmark concurrently → persist Runtime Set
```

## 21.2 Ecosystem gate mapping

| Runbook stage | Lands in |
|---|---|
| Stage 0 — baseline and invariant lock | M0 — label every object `EXISTING`, `PROPOSED` or `CONFLICT`; unresolved fields are STOP items |
| Stage 1 — shared protocol | M0 — the `/ecosystem/*` surface and MEP conformance. Stage 1 exits here; it cannot wait for M4, because M4 sits inside Stage 4 and Stage 4 may not start until Stage 1 has exited |
| Stage 4 — SIRVIS evidence plane *(parallel; may start after Stage 1)* | M1 + M2 (machine detection and runtime integration — nothing can be measured without them), M3 + M4 (inventory, state and the public API), M7 (evidence and provenance schema — **its acceptance is verbatim this stage's exit criterion**), M8 (Resource Manager, which owns every load and unload), M6 + M10 (benchmark lifecycle), M9 (Runtime Sets), M12 + M13 + M15 (the asset spike, the Clarvis role workloads it produced, and recommendations) + M15b (the §14.3 outputs M15 left out, unbuilt), M22b (reasoning-token overhead, the measurement RAVIS's reasoning tiebreak was waiting on), **M16 (the RAVIS evidence API)** |
| Stage 6–7 — NERVIS core, events and tracing | M21 — **no production test doubles** |
| Stage 10 — whole-ecosystem hardening | M22 |
| **Unscheduled — deferred by decision** | M5 (SDK), M11 (model browser and download), M17, M18, M19, M20. Listed so no milestone is silently unassigned |
| Stage 4 — visible increment | M14 (web UI). Not deferred: the prototype at `nervis/` already renders the Models, Benchmarks and Results screens against SIRVIS-shaped data, so this stage's increment is wiring those to real endpoints rather than building screens. §16's standalone requirement is what that satisfies |

> Stage 4 runs alongside Stages 2–3 and must be finished before Stage 5, when RAVIS begins
> ingesting evidence. SIRVIS has no inbound dependency before that point.
>
> **M16 is inside Stage 4, not after it.** It is the surface RAVIS reads, Stage 5 exits on a
> SIRVIS result changing a RAVIS preference, and the runbook requires a real producer — not a
> test double — for the SIRVIS→RAVIS pairwise gate. Scheduling it at Stage 6 would make Stage 5
> unexitable by construction.

---

# 22. Definitions of done

**MVP** — the user can start SIRVIS, inspect the machine, connect to LM Studio, browse
installed models, discover and download GGUF and MLX, load and unload, configure load
settings, run repeated benchmarks, compare results, create Runtime Sets, run concurrent
benchmarks, inspect telemetry and export results. External applications can query system,
model inventory and benchmark evidence; request benchmark jobs; monitor jobs; create and
release runtime sessions; and request recommendations.

**Ecosystem-ready** — evidence identity contains build, runtime and role; repeated
measurements are preserved; Clarvis role benchmarks exist; RAVIS can consume evidence through
the API; Runtime Sets provide pair evidence; NERVIS can consume health, events and results;
and SIRVIS still works standalone.

**Non-goals for the initial release** — cloud accounts, a public leaderboard, cloud sync,
distributed benchmarking, remote workers, multi-user servers, a plugin marketplace, automatic
model deletion, AI-generated benchmark suites.

---

# 23. Risks

| Risk | Mitigation |
|---|---|
| Runtime API drift | Adapter tests, version reporting, capability probing |
| Memory measurement ambiguity | Record multiple metrics, label estimates, preserve raw telemetry |
| Benchmark noise | Warmups, repetitions, visible spread, thermal tracking, controlled mode |
| Multi-model resource conflicts | `ResourceManager`, ownership, leases, conflict policy |
| Benchmark suite overfitting | Versioned suites, role separation, raw outputs, multiple evaluators |
| UI hiding provenance | Build/runtime/role always drillable; no one-number-only canonical view |

---

# 24. Conflicts resolved in this consolidation

| Conflict | Sources | Resolution |
|---|---|---|
| API path prefix | Plan used `/api/v1/…`; addendum used `/v1/…` | `/api/v1/` for the SIRVIS API, `/ecosystem/*` for MEP. SIRVIS has no OpenAI-compatible surface, so no `/v1` collision exists. Addendum resource names are kept, re-prefixed |
| Health/identity shape | Plan bundled identity and capabilities into `/api/v1/health`; MEP requires separate endpoints | MEP endpoints are canonical for negotiation; `/api/v1/health` remains a convenience alias with the same data |
| Health state enum | Plan: `HEALTHY/DEGRADED/OFFLINE/STARTING/UNKNOWN`; MEP: `healthy/degraded/unhealthy` | The service reports the MEP status. `OFFLINE`, `STARTING`, `stale` and similar are **observer-side** registry states owned by NERVIS, not values SIRVIS reports about itself |
| Evidence levels | Plan: 4 levels incl. `PARTIALLY_MEASURED`; addendum: 3 | Keep all four. Consumers that do not model `PARTIALLY_MEASURED` treat it as `ESTIMATED`. Aggregation never promotes |
| Benchmark job states | Plan: 12 internal phases; addendum: 7 contract states | Coarse enum `queued/preparing/running/succeeded/failed/cancelled/partial` is the published contract; the internal phase rides in a `detail` field |
| Milestone numbering | Plan M0–M22; addendum E-S0–E-S7 | M-numbers identify the work. The addendum's E-S gates are retired — that document is not in this set — and §21.2 now maps the milestones onto the runbook's stages, which schedule them |
| Dead citations | `fileciteturn…` markers throughout both sources | Removed. The 32% run-to-run variation claim is retained as a statement of Clarvis's own development finding (§11.7) |

---

# 25. Final success criterion

SIRVIS succeeds when another application can ask:

> Given this exact machine, these available local model builds, this role, this runtime
> configuration and this benchmark evidence — what should I run?

and receive a defensible answer based on **actual local measurements**, with estimated values
clearly marked as estimates.

For Clarvis specifically:

> Which chat build and agent build work best together on this Mac without unacceptable memory
> pressure, swap, latency or loss of tool-call reliability?

SIRVIS measures reality so the rest of the ecosystem does not have to guess.
