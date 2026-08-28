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
.venv/bin/pytest                      # part of 1433 tests, no network, no live service
.venv/bin/ravis conformance clarvis   # the §8.9 release gate — 17 checks
```

The other three packages are checked the same way, from their own directories:

```bash
cd protocol && ../ravis/.venv/bin/python -m pytest -q   # 22 tests
cd sirvis   && ../ravis/.venv/bin/python -m pytest -q   # 359 tests
cd nervis   && ../ravis/.venv/bin/python -m pytest -q   # 270 tests
```

**`ecosystem-protocol` must be installed first.** It is a local path dependency
and pip will not find it on PyPI, because it does not live there.

Expected: all clean, 1433 passing across the four, conformance `PASS`. CI runs the same four on
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
| 31 | **RAVIS M7** | Provider expansion. OpenRouter stays transparent, which is the measurement rather than the assumption. **Gemini moved to §6's translated path** after its OpenAI-compatible endpoint was measured reporting `finish_reason: stop` on a streamed tool call and omitting the tool-call index — both things Clarvis's agent role reads. A native Gemini adapter now makes the two providers indistinguishable on every surface §6 names, and `ravis conformance clarvis` stayed `PASS` throughout. Four judgement calls and two live-found bugs, settled below |
| 32 | **RAVIS M16** *(policy; the reasoning tiebreak waits on SIRVIS M22b)* | The policy engine. §14's four privacy levels, provider allow and deny, model exclusions, and §9.6.1's background-call class — every one of them a *hard* exclusion applied before ranking, which is how "privacy constraints can never be overridden by score" becomes structural rather than a rule to remember. `ClientApplication` regains `may_declare_background_calls` and `max_privacy_level`, this time read on the routing path. Pools carry a version and a derived revision, moving `ravis.virtual_profiles@1` off `degraded`. **Verified live**, and one gap found that way. Settled below |
| 33 | **RAVIS M11** | Sessions. §12.1's `RoutingSession` persisted, sticky routing as a preference that leads the ranking, expiry and retention as two separate windows, and isolation keyed to the application rather than to a name — which is the one thing §12.1 says outright must never merge. `ravis.sessions@1` moves off `unavailable`. **Verified live**, and the fourth request found a defect the first three hid. Settled below |
| 34 | **RAVIS M14** *(complete)* | The load-versus-don't tradeoff, and with it Stage 5's last open item. Expected session length is **measured from an application's own history** rather than predicted, because affinity settles a conversation on its model at the first request — when the current session's count is 1 and says nothing. Unknown routes exactly as before. **Verified live in both directions.** Settled below |
| 35 | **RAVIS M15** | The cost engine. Published prices split into input and output, usage captured from the stream without touching a byte of it, estimates that are never invoices, and §14's four budget bands enforced as policy. `ravis.usage_cost@1` moves to `available`. **Verified live against OpenRouter's own cost figures**, which agreed with RAVIS's arithmetic to the microcent. Settled below |


### Done — NERVIS

Its own table, because the one above is in completion order and NERVIS's
milestones are not in it at all. **That was the omission, not the ordering:**
every row above belongs to RAVIS or SIRVIS, while NERVIS had shipped eight
milestones and a voice stack, and the only thing tracking them was its own
capability surface. Interleaving them now would renumber thirty-four rows to
assert a sequence nobody recorded at the time, so they are listed on their own
and the dates they landed are in the sections below.

| Milestone | What it delivered | Evidence |
|---|---|---|
| **NERVIS M0** | Package, FastAPI, config, SQLite with migrations, logging, CLI, the web shell served rather than opened from disk, and NERVIS's own `/ecosystem/*` surface — which is what finally closed runbook Stage 1, since it requires the metadata endpoints in all three services | `nervis/tests/test_m0_foundation.py` |
| **NERVIS M1** | System telemetry: CPU, memory, swap, disk, load average, process list and thermal, sampled without loading the machine, degrading to Unknown rather than to zero | `/api/v1/system`, asserted across the M0 suite it shipped beside |
| **NERVIS M2** | The §5.1 registry: health model, probes on a timer, §5.2 capability negotiation, and the observer-side state vocabulary that keeps "unreachable" distinct from "unhealthy" | `nervis/tests/test_m2_registry.py`, `nervis.registry@1` **available** |
| **NERVIS M3** | RAVIS integration through published contracts only — health, providers, models, routes, usage and, since the session screen landed, sessions. NERVIS never reads RAVIS's database | `nervis/tests/test_m3_ravis.py`, `/api/v1/ravis/{surface}` |
| **NERVIS M4** | General chat as an ordinary RAVIS client: streaming, cancellation, local history, the route inspector, and conversation titles as §9.6.1 background calls — the last of which waited on RAVIS M16 and is the milestone's own exit criterion | `nervis/tests/test_m4_chat.py` |
| **NERVIS M5a** | SIRVIS's read surfaces with provenance intact and no benchmark logic of NERVIS's own. **M5b stays blocked** on SIRVIS M14's job queue, and §1 forbids inventing the endpoint | `nervis/tests/test_m5_sirvis.py`, `nervis.sirvis_views@1` **degraded** |
| **NERVIS M6** | The event hub: §4.4's envelope, ingestion, bounded persistence with retention, filters, and an SSE broadcast with replay. A malformed event is quarantined with safe diagnostics rather than crashing it | `nervis/tests/test_m6_events.py`, `nervis.event_hub@1` **available** |
| **NERVIS M7** *(its own half)* | Trace correlation, the waterfall, and gaps marked rather than interpolated. Cross-service spans need RAVIS M18b and SIRVIS M21 to publish, which is why the capability reads degraded — the missing half is other people's | `nervis/tests/test_m7_traces.py` |
| **NERVIS M8a** | §5.1's authenticated local dynamic registration: per-extension-host instances, leases, redaction, and no code path that could resolve a Clarvis gate. **M8b is blocked** on Clarvis building the Bridge at all | `nervis/tests/test_m8a_registration.py` |
| **Voice (§18.2)** | Not a numbered milestone and too large to leave unlisted: the credential in NERVIS's own storage, named voice profiles, engine settings, and the privacy gate that refuses a cloud voice for a locally-produced reply. Advertised only when configured | `nervis/tests/test_voice.py`, `nervis.voice@1` |

**Stages 0, 1, 2, 3 and 4 are complete.** Stage 1 was the last of them to
close. The runbook requires the metadata endpoints "in SIRVIS, RAVIS and
**NERVIS**", and exits when all three "pass live MEP conformance at one pinned
protocol version" — and for a long stretch this file claimed the stage was done
while NERVIS had no MEP surface at all, because `nervis/` was one HTML file.
Two services out of three was not a stage. NERVIS M0 built the third. Stage 2 completed as of 2026-08-24, when M10 landed. This file claimed Stage 2 was complete for some time before that, and
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
| 1 | **Stage 6 — NERVIS core** | The runbook's Stage 6 is mostly NERVIS, and most of NERVIS's own ladder is already behind it — M0 through M7 and M8a, now listed in their own table above. What remains is the stage's *exit*: every tile capability-driven, unavailable operations disabled with a stated reason, and the prototype ceasing to be one. RAVIS's half is items 1 and 3 |
| 2 | **SIRVIS M22b** | Reasoning-token overhead as evidence. It unblocks the one piece of M16 that could not be built, which needs a measurement rather than a guess from a model's name |

### After that

**Stage 5 is complete.** All five of the runbook's exit criteria for it are
met, and every milestone its table assigns to it — M3b, M4, M7, M8, M13, M16 —
has shipped. M16's reasoning tiebreak is the one piece recorded as unbuilt
rather than quietly dropped; it is blocked on SIRVIS M22b.

**NERVIS's milestones now have a table.** They had none: every row of the Done
table above belongs to RAVIS or SIRVIS, and eight NERVIS milestones plus the
voice stack were tracked only by NERVIS's own capability surface. Found by the
build-order audit and left open for a day because backfilling history is a
judgement call about how much to reconstruct; the answer was to list them
without inventing a completion order the file never recorded.

**M14's remaining half was filed under Stage 5 and cannot belong there**, which
the audit below explains: it depends on M11, and M11 is Stage 6. Stage 6 is
where the work now is — **M11** + **M15** on RAVIS's side, and on NERVIS's the
larger half, which is the prototype ceasing to be one.

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

> **This section records the sweep as it stood when it was run, and the table
> below has not been true for many milestones.** It is kept because the
> reasoning still holds and because the follow-up — *the badge audit*, further
> down — is only legible against it. For the current position, run the checks:
> `node nervis/tools/liveness_check.js` counts the cards that cannot report
> where their data came from, and `node nervis/tools/honesty_check.js` renders
> every screen against the running ecosystem and compares each badge with the
> data behind it.

Every screen was classified mechanically rather than by eye — each view function
matched against the data sources it touches, then every endpoint it asks for
probed against a running service.

**What it found, then.**

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

## The badge audit: one defect, found eight times by a person

The rule above was enforced from the day it was written, and it kept being
broken. Over one day, eight separate times, a card was found rendering real data
from a running service while wearing a `PROTOTYPE` badge. Providers had seven of
them. The Sessions screen shipped with six, hours after the first batch was
fixed. "Evidence in the ranking" showed thirty-two live rows off SIRVIS's own
benchmark runs, faded out. Every one was found by somebody looking at the
screen. None was found by a check.

Asked why cards that should work keep not working, the honest answer was that
this was not eight lapses.

**The answer existed and was being thrown away.** `live()` performs every read
on the page. It sets `SOURCE[service]` to `'live'` or `'mock'` — and then
returns the adapted payload with no trace of which, so a card holding the result
had nothing to consult. The global was the only witness, and the next call to
the same service overwrites it. Ten idioms grew up around that absence
(`fromSirvis`, `p.live`, `ev.source`, `j.source`, `tr.live`, `spend.live`, …),
and sixty-eight cards hardcoded their CSS class because there was nothing better
to read. Errors ran in **both** directions: live data faded, and mock data
badged live.

`live()` now attaches the answer to the object it answered. What that made
visible, and what it cost:

| | Before | After |
|---|---|---|
| Cards that cannot report provenance | 88 | 63 |
| Cards rendering live data as `PROTOTYPE` | 17 | 2, both understood |
| Cards claiming a live read with nothing running | 14 | 0 |

Three checks now hold it, all in `nervis/tools/`:

- **`liveness_check.js`** — a ratchet on the number of cards that hardcode their
  class. May fall, never rise. Demanding all of them change at once would mean
  marking cards live to satisfy a tool, which is this same failure pointed the
  other way.
- **`honesty_check.js`** — renders all 35 screens twice, once with nothing
  running and once against whatever is up, and compares. A card whose body
  changes between the passes was fed by a service; if it is not badged live it
  is understating. A card badged live in the dark pass is overstating. Not in
  CI: which services are up changes the answer.
- **`card()`** in `index.html` — a builder that *requires* `live` and throws
  when it is missing, so `render_check.js` turns an omission into a build
  failure instead of a faded card.

**Two things this cost, recorded because they are the useful part.**

The first version of `honesty_check.js` captured no markup at all — the page
writes through `querySelector` and the DOM shim handed back a fresh stub every
call — and it reported that every badge on every screen was correct. A checker
whose empty-input path was indistinguishable from a clean pass, hunting exactly
that bug. It now fails if it captures nothing.

And the suite did not catch any of this, which is worth being plain about: 1433
tests passed throughout. They also passed while NERVIS advertised
`clarvis_visibility` as unavailable for as long as M8a had been shipped, because
the test asserting it was written beside the declaration and locked in its
mistake. **Passing tests were never evidence for this class of defect.** The
things that found it were rendering the real page against real services and
comparing what it claimed with what it did.

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

**NERVIS Settings** was the shortest, on the reasoning that **NERVIS had no
service to configure**: M0 — package, FastAPI, SQLite, migrations, `nervis
serve` — was unbuilt and `nervis/` was one HTML file served statically. M0
shipped, NERVIS serves this page and answers `/api/v1/settings`, and the screen
now reads it. The paragraph is kept because the reasoning it records is still
how this dashboard decides what a settings screen may claim. The endpoints this
dashboard talks to were then its configuration, so
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

## NERVIS M1 — what this machine is doing, as distinct from what it is

SIRVIS already publishes `/api/v1/system`. NERVIS M1 adds one at the same path
and they answer different questions, which is the entire design rather than an
awkward overlap:

| | SIRVIS | NERVIS |
|---|---|---|
| question | *what is this machine* | *what is it doing right now* |
| lifetime | immutable, recorded once | sampled at the moment of the request |
| purpose | provenance on every benchmark result | a dashboard reading |
| machine | the one that ran the benchmark | the one NERVIS runs on |

Those diverge the moment NERVIS watches a remote peer, which is why neither
replaces the other and why the System screen now shows both.

### psutil, and the ladder that led there

§3 names psutil and M1 is where it earns its place. The alternative was hand
parsing `vm_stat`, `sysctl`, `/proc/meminfo` and Windows' WMI — SIRVIS took that
route for *static* detection and it is **398 lines that still needed a portable
fallback**, with no CPU utilisation and no process list. NERVIS has to run on
macOS, Linux and Windows, so the hand-rolled route is three more copies of that
parsing.

`types-psutil` is a dev dependency rather than a mypy override. SIRVIS waives
the check for PyYAML because its stubs return `Any` for the one call in use, so
nothing is lost; psutil's are genuinely typed, and `virtual_memory`,
`swap_memory` and `process_iter` are exactly the fields that get renamed between
major versions.

### Three properties that are decisions rather than mechanics

**No background sampler.** M1's exit says sampling must not noticeably load the
machine, and having no sampler at all is the only way to guarantee that. It also
makes the number honest: a value from a timer is whatever the timer last caught,
not the state at the moment somebody looked.

**Load average, not CPU percent.** A percentage needs two readings separated by
real time, and `psutil.cpu_percent(interval=…)` blocks for that interval — inside
a request handler, on every poll. The kernel already maintains a load average.

**Processes ordered by memory, and named without their command line.** Memory
because this ecosystem's failure mode is a multi-gigabyte model resident when
something else needs the room; CPU on a machine running an inference server is
either idle or pinned. And §15 forbids publishing a raw workspace path — the
argument list of an editor or a benchmark runner is exactly where one appears.

### The thermal probe is a second copy, deliberately

`psutil.sensors_temperatures()` does not exist on macOS, so the first version of
this field returned `None` on the only platform that can answer. It now reads
`NSProcessInfo.thermalState` through `osascript`, which is the same fifteen lines
`sirvis/src/sirvis/telemetry/thermal.py` already has.

Not extracted. `ecosystem_protocol` is the wire contract, and putting
`osascript` in it would make the one package every service depends on
platform-specific. Two copies is a cost; a protocol package that knows about
macOS is a worse one. **A third copy is where that trade changes.**

`None` is not collapsed into `nominal`, for the reason SIRVIS records: its first
probe did exactly that and reported a comfortable machine while it lost 48% of
its throughput to heat. A run *this session* was contaminated by thermal state
going unnoticed.

### Three things the wiring found

**A reduce seeded with `null` and not guarded on it.** SIRVIS's Dashboard —
the default screen — did `.reduce((a,b)=>b.measured.gen_tok_s>a.measured.gen_tok_s?b:a, null)`.
Unseeded it throws on an empty array, which is why the seed was added when
`/api/v1/models` carried no measurements. But `null` as the seed makes the first
callback read `a.measured` on null, so it threw for the opposite reason the
moment a build did carry one. `render()` has no catch, so either failure blanks
the whole app. `!a||` was the missing half.

**Two different totals for one machine.** The dashboard's SIRVIS adapter divided
by `1e9` and rendered a 24 GB Mac as "25.8 GB" — decimal-correct, and something
nobody including Apple calls that machine. It became visible the moment NERVIS's
own sample landed beside it on the same screen. Now GiB, which is what the
runtime-set adapter three hundred lines below already used.

**The Settings card said NERVIS had no service.** It read *"M0 — the package,
FastAPI, SQLite and migrations — has never been built"*, which was true for as
long as the dashboard was served statically. It now reports the count of
settings stored in the NERVIS database, `null` rather than `0` when NERVIS does
not answer — nought settings and no service are different facts.

## NERVIS M2 — the registry, and the end of the browser doing the negotiating

Until now the dashboard's service table was assembled **in the browser**: it
asked each service for its own `/ecosystem/identity` and `/ecosystem/capabilities`
and built the rows. That made the page the thing doing the negotiating, with
three consequences that only look small until the registry exists.

- Every open of the page re-probed the whole ecosystem.
- A stopped service cost a timeout **inside the render path**.
- `stale` was unobservable, because nothing remembered a previous answer.

NERVIS now probes on a timer and publishes `/api/v1/services`. The browser reads
one endpoint.

### The states are NERVIS's, not the services'

A service reports MEP `healthy | degraded | unhealthy` *about itself*. NERVIS
adds reachability on top, and the result is §5.1's eight-member vocabulary. The
sentence that decides every case:

> "Healthy" means the service's truthful response plus NERVIS reachability —
> never a successful TCP connect alone.

So a peer that accepts a connection and returns a proxy login page is
`degraded`, not `healthy`. A peer that answers and reports itself `unhealthy` is
`degraded`, not `unreachable` — NERVIS reached it perfectly well and it said no,
which is information rather than absence.

**`stopped` is never inferred.** §5.1 lists it, and it means a service NERVIS
supervises and knows it stopped — which needs M16's ownership. Guessing it from
a refused connection would report a service somebody else killed as though
NERVIS had done it deliberately.

**Staleness is computed on read, not written by a timer.** A value that only
goes stale when something runs stays fresh forever if that something dies, and
the timer dying is exactly what the state exists to make visible.

### The SSRF guard, and why it refuses hostnames

§5.1 requires allowing "only configured local transports and hosts by default".
A control plane is the worst place to get this wrong: it holds a list of URLs
and fetches every one on a timer, which is a request-forgery primitive with a
scheduler attached.

A hostname is **refused rather than resolved**. Resolving makes the check depend
on DNS at the moment of the check, and a name that resolves to loopback now can
resolve elsewhere on the next probe — the DNS-rebinding half of SSRF, and the
half an allowlist that resolves first will always miss.

```text
$ NERVIS_RAVIS_BASE_URL=http://metadata.internal nervis doctor

  REFUSED — these will not be probed at all
    ravis: host 'metadata.internal' is a name, not a literal address;
           resolving one would make this check depend on DNS at probe time
```

Refusals are returned, not raised: one bad endpoint must not stop NERVIS
starting. They are also **published** on `/api/v1/services`, because a service
missing because it was refused looks exactly like one nobody configured.

### §5.2's hardest sentence

> An unsupported required major marks that **feature** incompatible — not the
> whole dashboard, if other surfaces remain compatible.

The natural implementation checks the protocol once per service and disables
everything. `negotiate()` takes an operation and an entry, so an incompatible
RAVIS marks RAVIS's five operations `incompatible` and leaves SIRVIS's six
alone — asserted directly rather than assumed.

The other two rules: an unadvertised capability is `unknown`, never
assumed-fine — which is what enforces the gate's *"never calls a guessed
endpoint"*, since nothing unusable can be called on. And a capability this build
has never heard of is ignored rather than treated as a fault, or every upgrade
of a peer would look like a regression in NERVIS.

### `capable()` finally does something

It has existed in the dashboard for two milestones with **nothing calling it**,
under a comment claiming controls were capability-driven. The registry now
writes each peer's live capability list into the map it reads:

```text
capable('sirvis', 'sirvis.runtime.control')  -> true
capable('sirvis', 'sirvis.benchmarks.jobs')  -> false
```

Fourteen operations are declared in `nervis/src/nervis/operations.py`, each with
what §5.2 requires a control to declare: owning service, capability,
read-versus-mutate, timeout, idempotency and confirmation policy. Keeping them
there rather than beside the buttons means **a control cannot exist without
having answered those questions**. Ten are usable against a live RAVIS and
SIRVIS; none mutates, because §15.1 keeps RAVIS read-only until M18b.

### Two things the wiring found

**The first probe pass cannot be awaited in the lifespan.** It runs before
uvicorn binds the socket, so NERVIS's probe *of itself* fails by construction,
and any peer still starting reads `unreachable` — which on a one-command
launcher is most of them, on a dashboard opened seconds later. Scheduled
instead; entries read `discovering` until it lands, which is what §5.1 lists
that state for.

**The status banner disagreed with the table directly beneath it.** It is
painted at the top of every `render()`, before the screen below has fetched
anything, so it reported the state from one render ago. And the Overview's
"Services 3 / 4" was counting a hardcoded four against §5.1's six declared
entries. Both now read the registry NERVIS published.

## The status bar was one paint behind, and nothing polled at all

Reported from use: **LM Studio was opened, the ecosystem map showed it online,
and the top bar still said it was down** — in the same paint.

Two defects, and the second is the one that mattered.

**`globalStatus()` runs first in `render()`**, before the screen below it has
fetched anything. So the bar rendered whatever the *previous* render had
learned. M2 had already half-fixed this by giving the bar `REGISTRY_ROWS`
instead of the hard-coded `SERVICES` map, which turned permanently-stale into
one-render-stale — the same bug with a shorter fuse, and exactly what was
observed.

**Nothing polled.** The dashboard only re-read anything when somebody
navigated. Open a service and the page would not notice until you clicked
something. That also means **M1's "dashboard updates live" was never actually
met**, and this is the half that was missing — recorded rather than quietly
backfilled, because M1 was reported complete.

### The fix is that the bar does not wait for a screen

`pollRegistry()` reads `/api/v1/services` every 8 seconds — NERVIS itself probes
every 20, so anything faster only re-reads the same answer — and repaints the
bar when it lands. `absorbRegistry()` is the one place that writes what a
registry body means, because two callers now need it: the Overview's own read
and the poll.

`render()` also calls `globalStatus()` a second time, after the screen has
fetched. The first call keeps the bar from ever being blank; the second corrects
it with whatever the screen just learned.

Verified by watching the bar with **no navigation and no manual render** while
RAVIS finished starting:

```text
0s   RAVIS, Clarvis Bridge, Ollama unreachable · other surfaces operational
2s   Clarvis Bridge, Ollama unreachable · other surfaces operational
```

### Three things kept it from becoming a worse bug

**Repainting is an allowlist, not an exclusion.** Only `Overview`, `Ecosystem
map` and `System` repaint on the timer. Chat has a half-typed message, Settings
has toggles somebody is mid-way through, and the service dashboards have
popovers a rebuild would close under the cursor. A screen added later is not
live until somebody says so. Asserted directly: sitting on Chat, a poll leaves
`#content` byte-identical.

**Polling stops while the tab is hidden.** A background tab polling a local
service forever is how a laptop ends up running warm with no visible cause, and
this page is meant to be left open. `visibilitychange` also fires on the way
back — which is when a reading is most likely to be wrong — so returning
refreshes immediately rather than waiting out the interval.

**A reading older than two intervals says so.** `· as of 47s ago` appears only
once it is genuinely old, because a number that is always on screen stops being
read. The case it exists for is NERVIS itself going away, where the bar would
otherwise keep asserting the last thing it knew with nothing to indicate nobody
had checked since.

### And one the screenshot caught

The ecosystem map drew **two boxes labelled NERVIS** — one at the hub and one on
the ring — because §5.1 puts NERVIS in its own registry and the map drew every
entry. A control plane that skipped its own row would be the one entry nobody
could check, so the entry stays and the ring excludes it.

## NERVIS M3 — reading RAVIS, and three ways of getting the gate wrong

M3's exit: *"NERVIS inspects RAVIS without touching its DB; provider health and
recent routes visible; the RAVIS-unavailable state works."* Seven surfaces at
`/api/v1/ravis/{surface}` — health, providers, models, pools, routes, usage,
sessions.

**Not touching RAVIS's database is asserted as an absence of capability, not of
behaviour.** `nervis/src/nervis/peers/ravis.py` takes a base URL and an HTTP client and has no
filesystem access at all, and the test checks that `nervis.config.Settings` has
no field naming a RAVIS path. A test that watched for a file open would pass
right up until somebody added the setting that made one possible; this one fails
the moment such a setting exists.

**Sessions is listed and refuses.** RAVIS M11 has not shipped, so
`ravis.sessions@1` is advertised `unavailable` and NERVIS makes **no request at
all** — that is §5.2's *"never calls a guessed endpoint"*, and it is asserted by
counting requests rather than by inspecting a return value, because the claim is
about a call that must not happen.

### The gate was wrong three times, each differently

**Liveness as a veto.** The first version refused any read whose registry entry
was not `usable`. The registry's reading is up to one probe interval old, so
NERVIS reported `ConnectError` for a RAVIS that was answering, for twenty
seconds after it came up. That is guessing in the other direction from the one
§5.2 forbids. The gate is about *capability* — a surface never advertised, or
one the service says it does not offer. A capability last seen usable on a
service now thought unreachable is **attempted**: the connection refuses in
about a millisecond on loopback, and the transport's answer is fresher and more
specific than the registry's. `negotiate()` still returns `SERVICE_DOWN` and a
*control* should still grey out on it; a read should try.

**A retry loop with no exit.** The fix for the startup race was to probe every
three seconds while anything looked unwell. Four MEP endpoints × three MEP
services × every three seconds is **eighty requests a minute**. RAVIS's
anonymous rate limit is **sixty**. So NERVIS got itself rate limited, read the
resulting 429s as unwell, and kept the fast interval on — a self-sustaining
outage of its own making, observed rather than theorised:

```text
providers → RATE_LIMITED: Inbound rate limit exceeded
sessions  → version answered HTTP 429
```

The window is now bounded **by the clock**, not by a condition. A window that
closes after thirty seconds cannot keep itself open.

**429 read as a health problem.** It means reachable, answering, and asking too
often — which is the one thing probing harder cannot fix. Now `degraded` with
that stated, and `degraded` is usable, so a rate-limited peer does not take its
operations down with it.

### Probe cost, halved

A settled service is re-probed with **two calls, not four**. Identity and
version change on a restart or a rebuild and are not worth re-reading every
twenty seconds; health and capabilities are, and §5.2 has no capability-change
events until M6, so polling is the only way to know. A service that stops
answering gets the full probe again on recovery — a restart is exactly what
"stopped answering, then answered" looks like from here, and its identity is
what changed.

Startup recovery measured end to end: **RAVIS readable 3.1 s after NERVIS
answered**, against twenty before, with no rate limiting once the window closed.

### `execution_path` was being blanked after RAVIS started publishing it

`recentDecisions()` set `execution_path: null` under a comment explaining that
RAVIS "has only built Path A, and asserting TRANSPARENT_OPENAI here would be
this page claiming something the API never said". Path B shipped at M4 and every
decision record now carries the field:

```text
86e938f92064  path='TRANSPARENT_OPENAI'  provider='default'
```

§8 makes showing it mandatory — *transparent versus translated* is what a
compatibility regression turns on. **Blanking a field the API started publishing
is the same failure as inventing one, in the direction nobody checks.** `ttft_s`
stays empty and stays honest: it is per *target* on `/api/v1/health`, not per
decision, and averaging one into that column would be manufacturing a number.

### One shape for every outcome

RAVIS down, RAVIS refusing, a capability absent, and NERVIS itself not answering
all return `{available, reason, availability, data}`. `data` is `null` on every
failure and **never an empty list**, because an empty list means *RAVIS has none
of these* — confusing the two is how a screen renders a confident zero over an
outage. The Providers card names which of the four happened rather than
rendering them identically, and a refusal passes RAVIS's own §4.3 error code and
message through instead of replacing them with "HTTP 422".

## A real completion through M3's read path, and what it exposed

Ran with the user's approval, on a cold LM Studio. `lms server start`, then
`ravis/auto` with nothing loaded:

```text
elapsed 1.4s
model : deepseek-r1-distill-qwen-1.5b
reply : "\n\nOk, thanks! How can I assist you today?"
usage : 93 tokens
```

Read back through NERVIS, which is the point:

```text
GET /api/v1/ravis/routes?limit=2   available: True
  785e9cc61bd6  TRANSPARENT_OPENAI  ravis/auto -> deepseek-r1-distill-qwen-1.5b
  8490ce3a60e7  TRANSPARENT_OPENAI  ravis/auto -> deepseek-r1-distill-qwen-1.5b
```

And on the Routes screen, from live data rather than the mock:

```text
Pool       | Model                         | Path               | TTFT | Result
ravis/auto | deepseek-r1-distill-qwen-1.5b | TRANSPARENT_OPENAI |  —   | OK
```

The 1.89 GB JIT-loaded instance was unloaded afterwards; 8.3 GB free went back
to 9.2 GB.

**The first attempt at 16 max_tokens returned an empty string**, which is not a
bug in anything here: `deepseek-r1-distill-qwen-1.5b` is a reasoning model and
spent 14 of the 16 on reasoning tokens, leaving nothing for content. Worth
recording because a pool that picks the smallest build on a cost tiebreak will
keep picking this one, and a caller with a small `max_tokens` will keep getting
nothing back with a `finish_reason` that looks fine.

### RAVIS has no recovery path when an upstream returns

Found while setting this up, and it is a genuine defect rather than a quirk of
the test. RAVIS refreshes its catalogue every `models_cache_ttl_seconds` — **300
seconds**. It had started while LM Studio was down, so its candidate set was
empty, and starting LM Studio did not change that:

```text
POST /v1/chat/completions {"model": "ravis/auto"}
  → no models are available from the configured upstream
     considered: []
```

**The first explanation written here was wrong and is corrected rather than
edited away.** It said `GET /v1/models` reported 13 models at the same time,
"because the `ModelRegistry` refreshes lazily on read and the router's candidate
set does not", and concluded the gateway was contradicting itself. Neither half
held up. `is_due_for_refresh` is defined in `ravis/src/ravis/registry.py` and
**called from nowhere**, so there is no lazy refresh; and the 13 were the 13
*pools*, which `as_openai_list` lists ahead of the upstream's own models. The
count was `13 pools + 0 models`. The two surfaces agreed exactly.

The actual bug is simpler and no less real: **the catalogue refreshed on a
300-second timer and never sooner, whatever happened last time.** An upstream
that was down when RAVIS started left it serving an empty candidate set for up
to five minutes after that upstream came back, and the only recovery was a
restart or the wait.

Same class as a status bar held one paint behind — a true reading kept far past
the point where it is still true. **Fixed below.**

## Fixing the catalogue, and declining to fix the other thing

### The interval now shortens, and only for the case that recovers

`refresh_periodically` slept `models_cache_ttl_seconds` unconditionally. It now
sleeps fifteen seconds instead **while the upstream is unreachable** — and the
distinction it draws is the whole fix:

| last attempt | interval | why |
|---|---|---|
| did not answer | 15 s | may come back at any moment; a refused connection on loopback costs a millisecond |
| answered 429 or 403 | 300 s | present, and telling RAVIS something. Asking more often is the one response guaranteed to make it worse |
| answered with an empty catalogue | 300 s | LM Studio with its server up and nothing installed is legitimately empty. Retrying would not install anything |

The middle row is not hypothetical caution. NERVIS produced exactly that outage
on itself the same day by shortening its probe interval until it exceeded
RAVIS's sixty-per-minute anonymous limit, then reading the 429s as ill health
and keeping the short interval on.

Measured end to end — RAVIS started against a stopped LM Studio, then
`lms server start`:

```text
RAVIS started with LM Studio down:  0 upstream models, 13 pools
catalogue recovered 8s after LM Studio came back — 20 models

POST /v1/chat/completions {"model": "ravis/auto"}
  routed to : deepseek-r1-distill-qwen-1.5b
  finish    : stop
```

Eight seconds against up to three hundred, and the route that had been refusing
now answers.

**`is_due_for_refresh` was dead code** — defined in
`ravis/src/ravis/registry.py`, called from nowhere, and its existence is what
made the catalogue *look* self-healing when the only thing refreshing it was the
timer. It is what the loop reads now. Third one of these found in two days,
after `capable()` in the dashboard and `UnsupportedProtocolVersionError`.

### The empty reply was not a defect

The earlier completion returned `content: ""` at `max_tokens: 16`, because
`deepseek-r1-distill-qwen-1.5b` is a reasoning distill and spent 14 of the 16 on
reasoning tokens. Re-run at 120 tokens:

```text
finish  : stop
content : ' you\'re saying "OK." How can I assist you today? 😊'
```

`finish_reason` was correct both times and RAVIS proxied faithfully — §6's Path
A is transparent, and rewriting a response to make it look better is the one
thing it must not do. **Nothing here is broken.**

What is real is narrower: `ravis/auto` breaks a tie on *smallest build is
cheapest to run*, and on this machine that selects a reasoning distill, which is
a poor default for short completions. RAVIS cannot know that from advertised
metadata — LM Studio's `/api/v0/models` publishes `type`, `arch`, `quantization`
and `max_context_length`, and nothing about reasoning:

```json
{"id": "deepseek-r1-distill-qwen-1.5b", "type": "llm", "arch": "qwen2",
 "compatibility_type": "gguf", "quantization": "Q8_0", "max_context_length": 131072}
```

So the options were **measure it** or **guess from the name**, and this codebase
refuses the second. Scheduled as SIRVIS **M22b** (reasoning-token overhead as
evidence, measured per build like every other figure) with RAVIS **M16**'s
tiebreak as its consumer. Not built here, because building it would have meant
inventing the evidence.

## A sweep for dead code, and what it turned out to be hiding

Four times in two days a definition turned out to be written, exported,
documented and never called — `capable()` in the dashboard, then
`UnsupportedProtocolVersionError`, then `is_due_for_refresh`. None failed a
test, a lint or a type check, because there is nothing wrong with the *code*;
what is wrong is the belief that it does something. So the sweep became
`tools/check_dead_code.py`, and it runs in CI.

It found **eight**. Two were what the name suggests. **Five were not dead code
at all** — they were conditions nobody was detecting, with the class already
written for each.

### Deleted: four that were speculative

`requirements_of` in RAVIS's routing engine and `unknown_claim` in SIRVIS's
inventory both carried the same tell in their own docstrings — *"for callers
that need them directly"*, *"for callers assembling partial records"* — and had
none. `ServiceUnreachableError` was written at NERVIS M0 and superseded at M3 by
an envelope that reports rather than raises. `seedEvents` in the dashboard
targeted `#events`, an element that is not in the document.

### Wired: §4.3 published three codes and used one

`LoadFailedError` and `DeadlineExceededError` had never been raised, because
`_run_lms` raised `RuntimeUnavailableError` for **all three** ways the CLI can
fail: the binary being absent, a non-zero exit, and a timeout.

So a lease that failed because the runtime could not load a build reported
`RUNTIME_UNAVAILABLE` — sending whoever read it to check a process that was
working perfectly well. §4.3 publishes codes precisely so a caller can branch on
them, and this had three names for one answer.

The adapter now distinguishes them, and the shape matters: `RuntimeLoadFailedError`
and `RuntimeTimeoutError` are **subclasses** of `RuntimeUnavailableError`, not
siblings. Twelve `except RuntimeUnavailableError` sites already existed; as
siblings, each would have silently stopped handling a case it used to handle.

| the CLI | means | code |
|---|---|---|
| is not installed | no lifecycle exists on this machine | `RUNTIME_UNAVAILABLE` |
| exits non-zero on a load | the runtime answered and could not load this build | `LOAD_FAILED` |
| is still running past its deadline | a **busy** runtime, not an absent one | `TIMEOUT` |

The last distinction is the one worth having: the obvious response to
"unreachable" is to retry immediately, which is the worst possible response to a
load that is already underway.

Adding two `except` clauses pushed `open_session` past the complexity gate,
which was the right complaint — three branches differing only in which class
they construct is data pretending to be control flow. It is a table now.

### Wired: a 404 that was arriving as a 502

`MODEL_NOT_INSTALLED` is in SIRVIS.md §4.3's published list and nothing raised
it. A lease for a build this machine does not have went all the way to `lms
load`, failed, and came back as `LOAD_FAILED`. Now refused before the load, with
one deliberate exception: **an inventory that could not be read says nothing**,
because an empty inventory means the runtime did not answer, and refusing on
that basis would turn one unreachable runtime into every model being reported
missing.

**Two existing tests were leasing models the fake runtime had never heard of**
and being handed leases for them. That is exactly the bug the check closes, so
the fixtures now use ids the recorded runtime actually publishes — and a third
build was added to `tests/conftest_lmstudio.py`, because exercising a session
that exhausts a two-model ceiling needs three real ones.

### Kept and tested: §9's escape hatch

`ResourceManager.force_unload` had no caller, no endpoint and **no test**. §9
permits it "explicitly with authority", so deleting specified behaviour because
nothing calls it yet would be the wrong correction — but an untested escape
hatch is a claim rather than a feature. It has a test now. Nothing exposes it;
reaching it needs the authorization story M16 owns.

### The gate

`tools/check_dead_code.py` parses every module-level definition and method in
the four packages, plus every function in the dashboard's inline script, and
counts references across all Python, HTML, Markdown, TOML and YAML in the
repository. Anything at one — its own definition — fails.

Decorator-registered functions are exempt, because a FastAPI handler's name is
legitimately written once and forty route handlers would bury the seven real
findings. Reference counting is textual on purpose: an AST call graph would miss
`getattr`, pytest collection and decorators, and a sweep that is confidently
wrong is worse than one a person can read.

**Markdown counts as a reference site.** A name that appears only in a
specification is not called, but it *is* claimed — and the gap between the two
is the whole thing this exists to find.

## NERVIS M4 — chat, streamed through NERVIS and kept

M4's exit: *"`ravis/auto` chat works; streaming behaves; the route inspector
shows the decision."* All three, plus §7.2's storage.

Chat used to POST straight to RAVIS from the browser and wait for a whole
completion — a blank screen for as long as a local model took — with the
conversation living in `localStorage`, which is why it vanished with a browser
profile. It now goes through `/api/v1/chat`, streamed, and both turns are
stored in the NERVIS database.

**RAVIS's SSE frames reach the browser unchanged.** Reshaping them would make
NERVIS a second protocol nobody documented and would break the moment RAVIS
added a field. What NERVIS adds is keeping the text and cancelling RAVIS when
the connection goes away.

### Three things happen at once on a streamed turn

Bytes forward, text accumulates, and a disconnect cancels upstream. That last
one matters on a local GPU: a closed tab that kept RAVIS generating is a model
burning power for nobody. Measured, by hanging up mid-stream:

```text
read 22528 bytes, then hung up
stored 158 chars, interrupted=True
  '\n\n**Solution: Counting from One to Sixty**\n\nHere is the count starting'
```

The partial is **kept and marked**. Discarding it would delete text the reader
watched arrive; storing it unmarked would let the next turn present a
half-sentence as a finished thought.

### The route inspector, correlated exactly

§7.1 calls this "what makes ordinary chat a RAVIS debugging tool". RAVIS records
`request_id` on every decision and echoes the same id in `x-request-id`, so
NERVIS sends its own id and looks that one up — exact, rather than "the most
recent decision", which is wrong the moment two requests overlap:

```text
decision: 15524a8b1de2  TRANSPARENT_OPENAI  ravis/auto -> deepseek-r1-distill-qwen-1.5b
reason  : first eligible candidate in stable order. already loaded (HOT), so no load is required…
```

A miss is `null` and a 200, not a 404: RAVIS holds decisions in memory, so a
restart legitimately loses them and an older reply should read "no longer
recorded" rather than error.

### Titles are truncated, not generated — deliberately

> **Superseded.** RAVIS M16 honours the marker and NERVIS generates titles
> through it — see *Policy, wired: NERVIS titles as background calls*. Kept
> because the reasoning is why the feature waited rather than shipping wrong,
> and because the last paragraph names a gap in the dead-code gate that is
> still open. Read the rest of this section in the past tense.

§7 wants generated titles as a RAVIS **background call** carrying §9.6.1's
marker. RAVIS defines `may_declare_background_calls` on its application identity
and **honours it nowhere**, so a generated title would route as ordinary work
through `ravis/auto` and could select a paid model for a string nobody reads.
§7 states the trade outright: *an untitled conversation is a smaller failure
than a title billed to a frontier model.*

So `nervis.ravis_chat@1` is **degraded**, not available, and says which half is
missing.

Worth noting how that was found: `may_declare_background_calls` is a dataclass
*field*, and `tools/check_dead_code.py` only sweeps functions and classes. The
same class of claim, one level below where the gate looks.

### Three defects the wiring found

**A reply that produced no content was not stored at all.** Observed on the
second turn of the first real conversation: a reasoning model spent its entire
120-token budget on `reasoning_content` and emitted no `content`, so the append
was skipped and the question sat there with no answer beside it and nothing to
say why. An empty assistant turn that *finished* is now stored — "it answered
with nothing" is true, and is what a screen needs in order to explain it.

**The reply bubble did not exist for the first second.** `render()` refetches
this screen's profiles and decisions, and it was called without `await`, so
every delta arriving before it completed was written into nothing. The text
still appeared — `paint()` writes the whole accumulated string rather than
appending — but the first second was blank, which is exactly the blank screen
streaming exists to remove.

**Stopping a reply blamed the model.** An aborted stream with no text fell
through to the reasoning-exhaustion branch and rendered *"the model spent its
whole budget on reasoning — raise Max tokens"*, which is a confident diagnosis
of something that did not happen. A stop is not a failure and must not borrow a
failure's explanation.

### What §7 forbids, asserted rather than assumed

*Not a workspace, not a coding agent, no tools, no gates, no agent role.* The
absence is the feature, and an absence is the one thing a reader cannot see — so
a test inspects the body actually sent to RAVIS and asserts none of `tools`,
`tool_choice`, `functions`, `session_id` or `workspace` is in it.

## The dead-code gate learned to read fields, and the rule it was using was wrong

Extending it to dataclass fields took three attempts, and each failure was more
interesting than the extension.

**First: fields cannot be counted the way functions are.**
`may_declare_background_calls` appears three times — its declaration and two
constructor keywords — so a textual count called it referenced while nothing
ever asked what it held. **Setting a value is not using it.** Fields are now
judged by *reads*: `x.field` in a load context, plus `getattr(x, "field")`.
Keyword arguments deliberately do not count.

**Second: documentation was silencing the gate.** The original version counted a
Markdown mention as a reference, reasoning that "a name in a specification is
claimed, and the gap between claimed and called is what this finds". That is
exactly backwards — a name appearing only in prose is the *strongest* evidence
it is dead. The proof was immediate: `may_declare_background_calls` went
unreported because **STATUS.md's own entry about it being dead was the second
reference**. Writing about the problem made the gate stop seeing it. Markdown is
out; HTML stays, because the dashboard genuinely calls things.

**Third: a test is not a use.** With prose excluded it still went unreported,
because one line read it — `assert identity.may_declare_background_calls is
False`. That is the exact shape the gate exists to catch: *asserted, never acted
on*. Reads are now collected from `src/` only.

### Twelve findings, and none of them were what the name suggests

Two docstring lies, two unenforced trust boundaries, four values recorded and
never shown, three speculative fields and one specified escape hatch.

**`original_envelope` was described as the most important field in its file and
read nowhere.** Its module docstring opened *"one field here does more work than
the rest combined"*; its class docstring said the transparent path "forwards
`original_envelope` instead — which is why that field is not optional in
practice". Path A forwards `_Call.body` and `_Call.body_for`, which hold the same
bytes and always did. §7's guarantee is real and lives somewhere else entirely.
The test asserting the field is replaced by one asserting its absence, so it
cannot quietly return and be believed again.

**Two trust boundaries that protected nothing.** `may_declare_background_calls`
and `max_privacy_level` recorded §9.6.1's background marker and §9.6.0's privacy
ceiling on every resolved identity, and were read by nothing. A field describing
an unenforced boundary is worse than no field, because it reads as protection.
Neither can be enforced before M16 — the background-call class and the privacy
ladder do not exist to enforce them against — so both are gone until the engine
that checks them arrives.

**Four things recorded and never shown:**

| what | recorded since | now |
|---|---|---|
| `ModelSnapshot.last_error` | M1, under "a distinction a diagnostic needs" | `catalogue_error` on each provider row |
| `ReadStream.malformed_frames` | M2, counted on every stream | a 17th conformance check |
| `result_ids` on both outcomes | M6 | printed by the CLI run summary |
| `EvidenceAnswer.unresolved` | M7, "the field that keeps this honest" | deleted — the API's own local already did it |

`last_error` is the one worth dwelling on. A provider reachable *now* whose
catalogue is empty because the previous refresh failed looked identical to one
that genuinely has no models — which is precisely the confusion that cost an
afternoon yesterday.

`malformed_frames` is the sharpest. Every other conformance check compares what
survived the proxy against a direct read, so **a proxy corrupting a frame that
both sides skipped identically passed all sixteen of them.**

**One specified escape hatch, kept and tested.** `LMStudioAdapter.unload_all` is
on SIRVIS.md §7's runtime interface with no caller — the same position as
`ResourceManager.force_unload`. Deleting specified behaviour because nothing
calls it yet is the wrong correction; leaving it untested makes it a claim. It
has a test.

**And one of mine.** `ProcessSample.cpu_percent`, added at NERVIS M1 yesterday,
collected on every sample and read by nothing — not the ordering, not the
screen, not a test. psutil's first reading for a fresh process object is the
average since that process started, which is not a number to put beside a live
memory figure even for whoever might have read it.

## The dashboard's one invariant now has a check

*"It must render with nothing running"* is the rule the dashboard is built
around, and until now the only thing enforcing it was a person opening a
browser. That person caught four screen-blanking crashes in one week — a
`SERVICES[row.key]` lookup on a key that had no entry, a reduce seeded with
`null` and not guarded on it, `usable()` on an unknown key, a node list built
from a registry that had grown a row. Every one was a `TypeError` while building
a template string. None failed a test, because there were no tests.

`nervis/tools/render_check.js` renders every screen in a DOM shim with `fetch`
rejecting, and fails if one throws. It runs in CI.

**It found a fifth on its first run**, and one nobody would have found by
looking:

```text
1 of 34 screens failed to render:
  • sirvis/Recommendations: r.uncertainty.map is not a function
```

SIRVIS publishes `uncertainty` as a **list**, and the live adapter passes it
straight through. The mock carried one long **string** — and `.length` on a
string is truthy, so the guard `r.uncertainty && r.uncertainty.length` passed
and `.map` threw immediately after. **The Recommendations screen blanked
whenever SIRVIS was not answering**, which is precisely the condition the page
promises to survive. It was invisible because nobody runs this dashboard with
SIRVIS off.

Also **34 screens, not 27**. The browser check was iterating three apps; the
navigation has four.

### The shim, and two wrong turns getting there

A DOM shim rather than jsdom. The failures above happen while a render builds
its HTML *string*, before anything touches an element, so the DOM only has to
absorb writes — eighty lines buys that, against an npm dependency tree and a
lockfile in a repository that has neither.

The first version was a `Proxy` answering to any property, which is fewer lines
and looked elegant. It cost far more than the boring version would have:

- **A Proxy responds to `then`.** Awaiting anything that had touched the DOM
  turned it into a thenable that called `then(resolve, reject)`, got the proxy
  back, and waited forever.
- **Fixed, it hung again** somewhere inside the page's background canvas
  animation, where proxy arithmetic turned a bounded loop into an unbounded one.

Both hangs presented identically and gave nothing to work with: no output at
all, because `console` output to a pipe is buffered and discarded on kill.
Enumerating what the page actually uses is more lines and no cleverness, and
every gap announces itself as `x is not a function` naming the method — the
better failure mode for a thing whose whole job is to fail clearly.

Two smaller traps worth recording: a script's top-level `const` is
**script-scoped, not a property of the vm context**, so `context.APP_CONFIG` is
undefined however well the script ran — a second evaluation in the same context
reaches those bindings. And `main()` without a `.catch()` sent its own failures
into the unhandled-rejection collector, so the process exited **0 and printed
nothing**, which is worse than a false failure.

### What it does not prove

That a screen **assembles**. Not that the markup is valid, that anything is laid
out, or that a click works. It is the cheap half of the check, and the half that
keeps failing. Verified against a deliberately broken screen before being
trusted:

```text
exit: 1
1 of 34 screens failed to render:
  • nervis/Chat: Cannot read properties of null (reading 'boom')
```

**Complexity on that file is still ungated.** Roughly 48 of 262 functions would
fail the 8 the Python packages are held to, concentrated in the large
template-literal renders. That needs ESLint and the dependency tree that comes
with it, and it is the second problem there — a complexity number on untested
code says which function is frightening; a render check says when it broke.

## Chat parameters and history fold out from the right

Both used to sit stacked above the transcript as `<details>` blocks, each
pushing the messages further down while open — and both are reference material
consulted occasionally rather than read while typing. They are one drawer now,
opened by two small buttons in the chat card's top-right corner.

Four details that are decisions rather than styling:

**One `drawer` value, not two open flags.** The panel shows one thing at a time,
and two booleans would allow a state the layout has no way to draw.

**A dot on the Parameters button when anything is set.** A panel that folds away
must not fold away the fact that a system prompt is in force. `paramSummary()`
— which rendered that as text on the old closed summary — is deleted rather than
left behind, because nothing called it any more and the dead-code gate would
have said so on the next run.

**The buttons are pinned above the drawer, not inside the profile row.** That is
where they started, and the profile row wraps thirteen chips and then gets
covered by the drawer — so the buttons scrolled out of reach and the one that
closes the panel was underneath it. Found by looking at a screenshot.

**No close button in the drawer header.** It landed exactly under the openers,
which occupy the same corner. The opener toggles, Escape closes, and the header
says so.

Off-screen is `visibility:hidden` as well as translated, because a drawer whose
inputs are still focusable is a keyboard trap with no visible cursor. Under
640px the buttons fall back to sitting inline above the transcript, since a
340px drawer over a 380px card is not a drawer.

## LM Studio and Ollama draw as one node

They are interchangeable local runtimes serving the same purpose, and the
ecosystem map answers *can NERVIS reach a local model* rather than *which of two
ports is listening*. One node, **green when either answers**, with the label
naming which — so bundling never hides that one of them is down:

```text
both down          Local models · unreachable · LM Studio and Ollama unreachable
LM Studio only     Local models · healthy     · LM Studio answering
```

Both stay listed separately on the registry table, which is where the narrower
question belongs. That is also why the card's **Registered** counts six while
the diagram draws four, and the footnote says so rather than leaving the
arithmetic to the reader.

**The status bar still names Ollama individually**, and that is left alone: the
bar reports registry rows, which is the finer view. Worth noting as a
consequence rather than a defect — an installation with only LM Studio will
permanently read "Ollama unreachable" there, which is noise about software that
was never installed. Whether an unconfigured runtime belongs in the registry at
all is a §5.1 question, not a display one.

## An unconfigured runtime is absent, not broken

An installation with LM Studio and no Ollama read **"Ollama unreachable"** in
the status bar forever — an alarm about software that was never installed. §5.1
lists both among the initial registry entries, so both were probed, and a
refused connection is truthfully `unreachable`. The state was right; raising it
was not.

**"Configured" means the operator supplied the address**, not that it happens to
equal the default. `Settings.model_fields_set` records which fields were
actually given, and that is the only answer that holds — comparing a value
against the default would call somebody who deliberately typed
`http://127.0.0.1:11434` unconfigured, which is the opposite of what they did.

**Absence is still probed.** A refused connection on loopback costs about a
millisecond, and probing is what lets a runtime appear the moment somebody
starts it. What changes is what the absence *means*.

**The distinction that keeps it honest is "has it ever answered".** A peer that
answered once and then stopped is a real outage and says so, whatever it was
configured from — `awaiting_first_contact` is `optional and never seen and not
usable`, not `optional` alone.

| | banner | registry table | map node |
|---|---|---|---|
| LM Studio up, no Ollama configured | *All systems operational* | Ollama · **not configured** | Local models · healthy |
| `NERVIS_OLLAMA_BASE_URL` set, absent | Ollama unreachable | Ollama · unreachable | Local models · unreachable |
| Ollama answered, then stopped | Ollama unreachable | Ollama · unreachable | — |

RAVIS and SIRVIS are never optional: NERVIS exists to watch them, and one being
down is the thing it is for. The Clarvis Bridge is optional by §5.1's own
words — it starts and stops with an editor window.

### And a warning chip on a healthy service

Found while checking the result: **LM Studio rendered `healthy` wearing an amber
warning** on the ecosystem map. That table's chip read `usable(s.key)`, which
consults the fallback `SERVICES` map — and that map has no entry for `lmstudio`
or `ollama`, so `usable()` was false for a service the same row had just
labelled healthy.

It is the third variant of one mistake: a screen mixing a live read with the
hard-coded map beside it. The row has carried its own state since M2, and now
this chip reads it. The registry row shape was also dropping the two new flags
before the map ever saw them, which is why the fix took two passes.

## NERVIS M5a — reading SIRVIS, and the half that cannot be built

M5's exit has five clauses. Three are met: existing results appear, provenance
renders correctly, and **no benchmark business logic exists in NERVIS**. Two are
not, and could only be met by breaking a rule:

> a benchmark launches through the SIRVIS API; progress streams live

Both need `sirvis.benchmarks.jobs@1`, which SIRVIS advertises as **unavailable**
because submit/poll/cancel lands with its queue at M14 — and §1 forbids
inventing another component's endpoint to get there sooner. Split into **M5a**
(shipped) and **M5b** (blocked on SIRVIS M14), with the block written into the
build plan rather than left as a gap somebody rediscovers.

The `jobs` surface is **listed and refuses**, which is how planned-and-absent
shows itself:

```text
GET /api/v1/sirvis/jobs
  available: false
  reason:    SIRVIS reports sirvis.benchmarks.jobs as unavailable
```

`nervis.sirvis_views@1` is `degraded` and names which half is missing.

### §9's requirement is met by being unable to break it

> Never display `Model X: 93`. Display build, runtime, role, run count, median,
> spread, runtime config.

Nothing in the read path reshapes a body. Not a style choice — the surest way to
preserve `MEASURED`, `ESTIMATED`, `UNKNOWN`, timestamps, staleness, method,
sample count, units and evidence links is to be in no position to drop them. The
test asserts **equality with what SIRVIS sent**, rather than checking fields one
by one, because a field-by-field test only protects the fields somebody thought
of.

Live, through NERVIS:

```text
evidence_type   MEASURED
target          {"format":"mlx","model_family":"qwen3-4b-2507","quantization":"4bit",…}
metric          generation_tokens_per_second → median 49.1 · mean 49.2 · max 49.9
                samples 5 · units tokens/second · direction higher
21 fields preserved
```

And on the Recommendations screen: *"1 · qwen/qwen3-4b-2507 · MEASURED ·
clarvis-chat · fit UNKNOWN · score 1 **on 50% of the profile**"*, with
exclusions carrying their reasons. A bare score would be §9's forbidden line.

### One reader, two peers

`nervis/src/nervis/peers/ravis.py` was a full implementation; adding a
SIRVIS peer beside it
would have been a second copy of the gating, the envelope and the timeout —
which drift, and which would then make a screen need to know *which* peer it was
talking to in order to read a failure. The shared half moved to
`nervis/src/nervis/peers/reader.py`; each peer file is now a table of what
exists.

`recommendations` is the only POST, and it stays in the read-only module: §14.3
makes it a POST because its inputs are a body and its result is generated rather
than stored, but it reads nothing and changes nothing.

### Two things the wiring found

**A wildcard route swallowed the chat router.** `/api/v1/{service}` matches
everything under `/api/v1`, so `/api/v1/chat/conversations` resolved to "peer
`chat`, surface `conversations`" and 404'd — caught by an M4 test, not by
anything in M5. Two literal paths per peer cost two lines and cannot shadow a
sibling that has not been written yet. Pinned by a test.

**A shared handler taking `service` answered 422.** FastAPI reads any argument
not in the path as a *query* parameter, so registering one function at
`/ravis/{surface}` made it demand `?service=`. The service is closed over now.

**The recommendation POST needed thirty seconds, not the read timeout's 1.5.**
It reads every evidence record for the roles asked about, and the dashboard's
four-second budget was aborting calls that were working.

## NERVIS M6 — the event hub, and the one clause that gets the tests

M6's exit: *"Events appear live; RAVIS events ingest; retention is enforced;
**an invalid event cannot crash the hub**."* The last clause is the headline and
has the most tests, because it is the one a hub fails silently — a producer
sending rubbish is ordinary, and the wrong response punishes every *other*
producer for one's mistake.

`POST /api/v1/events` always answers **202 with the outcome**, never a 4xx or a
5xx. A rejected event is a fact about the producer, not a failure of the hub,
and an error status invites a retry of something that will never parse:

```text
POST {"looks": "nothing like an envelope"}
  → {"accepted": 0, "rejected": [{"reason": "missing required field(s)",
                                  "detail": "event_id, event_type, occurred_at"}]}
```

Refusals are **quarantined rather than dropped**, because *"the hub is quiet"*
and *"a producer is sending rubbish"* look identical from outside and need
different things done about them. The Events screen renders the quarantine
beside the feed.

The hub does **not** validate `data`. §4.4 says event type plus version
determines that schema, which makes it the consumer's business — a hub that
checked it would need to know every event type in the ecosystem, which is the
coupling the envelope exists to avoid.

### "RAVIS events ingest" — the half NERVIS owns

The ingestion path takes a RAVIS-shaped envelope over HTTP POST and it works.
The *producer* half does not exist: RAVIS advertises `ravis.events@1` as
unavailable until M18b, SIRVIS's until M21, and §1 forbids inventing either.

So **every event in the feed today is NERVIS's own** — registry transitions,
emitted through the same door as everyone else's, because §3.1 has NERVIS
implementing *and* consuming the MEP and a hub whose own events took a private
path would be the one producer nobody could validate. The screen says exactly
this rather than implying a busier ecosystem than there is.

Emitted **only on a change**: six peers probed every twenty seconds would
otherwise write eighteen events a minute saying nothing happened, and retention
would be measuring how long NERVIS had been running rather than how much had
occurred.

### Two bounds on retention, because they fail differently

Age alone lets a burst fill a disk inside the window; a count alone keeps a
quiet week forever. Retention by age uses **arrival, not the producer's clock** —
retaining on `occurred_at` would let a producer with a wrong year delete its
events on arrival, or never. Both timestamps are kept, because §11.2 requires
clock skew to be visible and one timestamp cannot show it.

### Three bugs the tests and the live stream found

**`json.dumps` allows `NaN` by default.** The serialisability check passed an
event no other JSON parser in the ecosystem would accept — an event only NERVIS
can read is not an event. `allow_nan=False`.

**The gap marker was never delivered.** On a full subscriber queue the code
discarded the subscriber and then put the marker *into the queue that had just
refused an event*, so it was silently dropped and the reader was cut loose with
no explanation — which is the one thing §4.1 asks a gap to prevent. It makes
room now by dropping the oldest unread event.

**A live event went out with an empty `id:`.** Found by watching an actual
stream rather than by a test. The sequence was attached only by the read path,
on the way *out* of storage, so a broadcast frame carried no cursor — and a
client that reconnected after receiving live events resumed from wherever its
last *stored* read had left off, silently duplicating everything between. The
sequence travels with the broadcast now, and a test pins it.

### What the hub is not

§11.1: *"the hub is operational telemetry, not the system of record"* for SIRVIS
evidence, RAVIS accounting or Clarvis workspace state. Envelopes are stored
whole and the columns beside them exist only to filter on — re-serialising an
event from those columns would be NERVIS publishing its own version of somebody
else's fact.

`nervis.event_hub@1` is **available**, and that is about the hub rather than the
ecosystem's traffic. Whether peers publish is theirs to declare, which is
exactly why their silence does not degrade this.

## All three services were storing a trace id that could not correlate

Found while starting M7, and it defeated §11.2 entirely:

```python
request.state.trace_id = request.headers.get("traceparent", "")
```

The **whole header**, in RAVIS, SIRVIS and NERVIS — the same line, three times,
wrong three times. `traceparent`'s third field is a **per-span parent id**, so
two spans in one trace carry two different headers and matching on the string
finds neither. §11.2's entire premise is that events from different services
join on this value.

Parsed in `ecosystem_protocol` rather than fixed three times. That is the
third-copy trigger I named when the refactor question came up: two copies is
coincidence, three is a pattern.

An all-zero trace or parent id is refused rather than propagated, because a zero
id would join every malformed trace into one. An unknown *version* keeps its
trace id — the specification says a receiver must not reject a higher version
outright, and refusing would make NERVIS the reason a newer client's traces
vanished.

**RAVIS's route decisions carried no trace id at all.** They do now, which is
what lets a route be placed beside the events either side of it.

## NERVIS M7 — the waterfall, and the bars it refuses to draw

§11.2 says it twice in one section: *mark missing spans and clock skew — never
synthesize a span as fact.* That is the only rule that changes the code's shape,
because a waterfall is a drawing of durations and a drawing is exactly where an
invented number stops looking invented. A bar is a bar whether it was measured
or guessed.

**One event is a point, not a zero-length bar.** A single event sets `started`
and `ended` to the same moment, so the arithmetic gives `0.0` — which draws a
zero-width bar reading *"this took no time"* rather than *"this was one recorded
moment"*. Keyed on the number of events, because that is the evidence: two bound
an interval, one does not.

**A service that recorded nothing gets no bar.** A hole is the finding, not
something to fill.

**Clock skew is reported, never corrected.** Straightening a waterfall deletes
the only evidence that two clocks disagree, and a timeline that visibly cannot
be straightened is the one that makes somebody go and look.

**An unparseable timestamp does not become "now"** — that would place a span at
the moment somebody opened the screen, which is the most confidently wrong a
timeline can be.

### A gap that explains itself

RAVIS does not publish events until M18b, so a chat turn traces as one lane. But
RAVIS *does* record `trace_id` on every route decision, which makes "RAVIS
handled this request" a **recorded fact** NERVIS can read:

```text
NERVIS   one moment
gap: RAVIS routed this request — decision 518acf34583e, selected
     deepseek-r1-distill-qwen-1.5b — and published no events, so it has no lane.
```

Still a warning and never a span. A decision record is evidence that something
happened, not evidence of *when it started and stopped*; drawing a bar from it
would invent the two timestamps the rule exists to protect.

`nervis.traces@1` is `degraded` and says the missing half is other people's.

### Two gates earned their keep

**The complexity gate fired on `send`**, at 9 against a limit of 8, when the
traced-span emit went in. That function was measured sitting *exactly* on 8 two
days ago, with the note that M11 or M16 would be what tipped it. M7 got there
first. Extracted, and back under.

**The render check caught a screen I broke while adding the waterfall.** A
`str.replace` matched the same early-return pattern in `ravisCredentials` as in
`tracesView`, so the credentials screen referenced a variable that does not
exist there:

```text
1 of 34 screens failed to render:
  • ravis/Credentials: traces is not defined
```

That is exactly the class the check exists for — a screen nobody was looking at,
broken by an edit to a different screen.

## NERVIS M8a — letting a Bridge announce itself, without gaining anything by it

M8 is the Clarvis integration and it splits cleanly in two. The half that reads
a running Bridge is blocked: `CLARVIS.md` §6 specifies one and the Clarvis
repository contains no implementation, and §1 forbids inventing another
component's API. The half that can be built is the receiving end — registration,
leases, redaction — and that half turns out to be where all the interesting
decisions are.

**§5.1 asks for two things that sound compatible and are not.** *"Support
static configuration and authenticated local dynamic registration"*, and, one
sentence later, *"do not accept an unauthenticated process's claimed service
type or endpoint."* So a Bridge must be able to say "I exist, on port 7073"
without any process on the machine being able to say the same thing and be
believed. Something has to distinguish them, and it cannot be the claim itself.

**The answer is a file, and its permissions are the authentication.** NERVIS
writes a random secret next to its database at `0600`, in a `0700` directory. A
registering process proves it is the user's by being able to read it. That is
the same check `ssh` makes of a private key, it needs no user interaction, and
the last point is not a convenience — the alternative considered here was
printing a token for somebody to paste into a terminal, and this project has
already rejected exactly that once, on the grounds that a security step which
is annoying is a security step that gets turned off.

What it does not prove is *which program* is registering. Any process running
as this user can read the file. That is the correct boundary for a localhost
control plane on a single-user desktop and it is the one the ecosystem already
assumes everywhere else — but it is a boundary, it is written down in
`enrollment.py`, and it would not be adequate on a shared machine.

### Three refusals that came out of taking the threat model seriously

**A registrant sends a port, not a URL.** The first draft took a `base_url`,
which is the obvious shape and hands an unvetted local process the exact SSRF
primitive `allowed_endpoint` exists to deny — from the one endpoint whose whole
purpose is to be called by things NERVIS has not checked. It sends a number
between 1 and 65535; NERVIS supplies `127.0.0.1` and runs the guard underneath
anyway, so the two can only disagree in NERVIS's favour.

**Only Clarvis may register dynamically.** Enrolment proves the caller is the
user, not that it is the Bridge. Without a list, a compromised local process
that read the secret could register itself as RAVIS — and then be handed the
chat traffic. `DYNAMIC_SERVICES` is one entry long.

**A live instance id is not taken over.** §5.1: *"resolve duplicate stable IDs
without overwriting a live instance."* The tempting resolution is last-writer-
wins, which is a race whose timing an attacker chooses. The instance already
answering keeps the row.

### Redaction that is structural rather than careful

The claim a registrant may make is a closed allowlist, and the thing worth
recording is what got *removed* from it. The first version had a `label`, on the
reasonable grounds that somebody with three editor windows open wants to tell
them apart. But a Bridge's natural label is its workspace folder name, and §6.7
forbids NERVIS holding the workspace root.

**A field that invites the value you have promised not to store is worse than
no field**, because the promise then rests on every future caller's restraint.
NERVIS derives the label from the instance id instead. There is nowhere for a
path to go, which is a stronger claim than filtering paths out.

The same reasoning applies to the tokens. Registration returns the instance's
token exactly once, in the response; it is not readable back, because if the
dashboard could read it then a browser tab would be sufficient to impersonate
an editor window. And the two credentials are deliberately different: the
enrollment secret registers and cannot renew, the instance token renews and
cannot register. One leaked heartbeat token is not a registration capability.

### §6.7 enforced by there being no code that could

`CLARVIS.md` §6.7 lists what NERVIS may not do: resolve gates, invoke tools,
expand the workspace root, change safety settings, read SecretStorage, keep
Clarvis alive past its extension host. None of those is behaviour that could
regress at runtime. Each would have to be **written** — which is why the test
for it reads the source instead of calling anything.

The first attempt asserted that NERVIS issues no non-GET request anywhere, and
failed immediately on two legitimate calls: chat POSTs to RAVIS, and SIRVIS's
recommendation endpoint takes a body. A test that is wrong in an obvious way is
a test somebody deletes. The invariant that is actually true and actually
load-bearing is narrower: **no surface declared for Clarvis is anything but a
read**, which is a property of the Bridge rather than a choice — §6.3 says
*"there is no write path on the Bridge, and §6.7 is why."* Every §6.7 verb would
need one, so adding one fails the suite before it can ship.

### And the recurring bug, for the fourth time

The dashboard's instances card gated on `usable('clarvis')` — the hard-coded
`SERVICES` fallback map — while rendering rows from a live read. A Bridge that
had registered stayed hidden behind a banner whenever the map disagreed. This
is the same defect found on three other screens and it now has a name: **a
screen that mixes a live read with the fallback map has a bug in it, and the
fix is always to gate on the rows.** If there are rows, there are Bridges.

The card also read `{bridge}/instances`, a path §6.3 does not define — and one
that could not have worked, since §6.6 says an instance describes *the host
serving it* and never aggregates, so no single Bridge can answer "which Bridges
exist". NERVIS holds that list. It reads `/api/v1/registry/instances` now, and
two registered windows show as two rows with their own leases.

**Verified live, 2026-08-26.** Unauthenticated registration refused; a claim
carrying `workspace_root`, `label` and `bridge_token` accepted with none of the
three surviving anywhere in the API; two windows listed separately; a duplicate
id while live refused; RAVIS refused; the enrollment secret unable to renew an
instance; one instance's token unable to delete another's.

## The dashboard's complexity, and the number that was wrong

The JavaScript gate was set at 30 when it was introduced, on the reasoning that
a ratchet at the worst survivor stops anything new being worse. The mechanism is
right and the number was not: 30 sits deep in the band conventionally read as
high risk, and thirty independent paths through one function is more than any
test suite realistically covers. Asked whether that was good, the answer is no.

The distribution was never the problem — 96% of functions were already under 10.
Four outliers were.

| | before | after | how |
|---|---|---|---|
| `runtimeSet` | 30 | 11 | accessors defined once instead of at twenty reads |
| `sendChat` | 30 | 7 | the frame loop extracted as a pure function |
| `BUILD.render` | 28 | 5 | split by section |
| `tracesView` | 25 | 4 | split by card |
| `ravisDiagnostics` | 18 | 6 | split by card |
| `draw`, `mikuMode` | 15 | <10 | the gate applies to decorative code too |
| the whole file | — | — | `dash()` and `warnUnless()` replaced 78 inline fallbacks |

The ratchet is 13 now and the target is 10. The Python packages hold 8 and this
still does not, for a reason that is measured rather than assumed: about a fifth
of this file's decision points are control flow, and most of the rest are `||`,
`??` and `?:` shaping optional fields inside template literals. Forcing 8 would
pressure a reader toward hiding optional-field handling rather than writing it
out — worse code that scores better. The two sweeps are the honest version of
that fix: name the pattern once, and the branch genuinely stops existing at the
call site.

### The check that had to exist first

Refactoring untested code is how untested code becomes broken code, and
`render_check.js` makes every `fetch` reject — so it exercises the **mock** path
of every live reader and none of the shaping, which is exactly where the
complexity was. `runtimeSet` reached 30 with not one of its branches ever
executed by a check.

`tools/shaping_check.js` runs the readers against recorded payloads and fails if
the shaped output moves. It is a **characterisation test**: it asserts the output
is unchanged, not that it is right. That is what a complexity refactor needs, and
it is worth being clear it is nothing more — a bug frozen into the golden file
stays frozen, and only a person reading a diff will catch it.

It caught three things before the refactor finished.

**A live bug, from the sparse fixture.** Every `||` in a reader is a claim about
a field the service might not send, so the fixture with every optional field
absent is the only thing that proves those defaults do what their author
believed. Co-residency read `complete ? 'resident together' : 'FAILED — ' +
reason`, so a matrix that had simply not recorded the field rendered **"FAILED —
undefined"** — an unknown printed as a confident failure, which is the one thing
every honesty rule in these specifications forbids. It is three states now.

**A flaw in the shim itself.** Pinning `BUILD.render` produced markup with every
escaped value empty: `textContent` and `innerHTML` were unrelated plain
properties on the element stub, and `escapeHtml` sets one and reads the other, so
it returned `""` for every value on the page. `render_check` never noticed,
because a screen that renders entirely blank still renders. A shim that silently
answers `""` to the most-used function in the file is worse than one that throws.

**My own sweep.** The first `dash()` regex rewrote the helper into
`return dash(value)` and dropped the receivers off four method chains. Nine
screens failed on the next run. Without the two checks that would have been a
quiet corruption of forty-three call sites.

**Verified live, 2026-08-26**, in a browser against NERVIS, RAVIS and SIRVIS all
running: all 34 screens render, `buildRow` draws eleven real models with their
measured chips intact (the two granite builds still read 8/8 and 1/8), and the
M8a instances card still shows a registered Bridge with no workspace path
anywhere in the page or the API.

## RAVIS becomes a switchboard, 2026-08-26/27

Four API providers were configured in one session — OpenAI, Google AI Studio,
Anthropic and OpenRouter — against twenty local models on LM Studio. Six hundred
and twenty-six models behind one gateway. Almost everything below was found by
using it rather than by reading it.

### What was broken and nobody knew

**`ravis/local` was not local.** "Never leaves this machine" was a `description`
field with nothing behind it. The routing engine receives a flat table of model
names with no record of which upstream produced them, so it could not have
enforced the promise even in principle. Observed live: a request to `ravis/local`
answered by an OpenRouter model. `ravis/private` did the same. With only a local
runtime configured the pools were accidentally correct, and adding API providers
is what made the gap reachable.

**The credential store was write-only.** M10 built the 0600 file, the Keychain
read, the precedence order and a `Secret` that refuses to print itself — and no
caller. A key typed into the Credentials screen was stored, reported as
configured, and sent to nothing.

**The same guard was wrong in four places.** `if upstream.api_key:` decides
whether to authenticate, and that field holds only what a *declaration* wrote —
empty for exactly the providers the store exists to serve. Three were fixed and
each remaining one surfaced identically: a live call where the provider listed
its models and then refused every request. The field is `declared_key` now, so
reading it where `key()` was meant is a type error rather than a 401 an hour
later.

**The fallback chain gave up on the wrong signal.** An open *provider* circuit
cleared the whole queue, abandoning candidates on other providers — so one
breaker made a gateway whose entire job is having somewhere else to go answer
502 with its alternatives unspent.

**A pool could not reach a translating provider.** `merged_candidates` walks
transparent upstreams, so Anthropic was invisible to every pool. What was missing
was not a lookup but a *map*: nothing knew which provider served a translated
model, so adding the models alone would have forwarded an Anthropic id down the
transparent path.

**Nothing published `OBSERVED_BY_RAVIS`.** §13.3 names the evidence kind. Every
request already recorded latency and every streamed one TTFT — into a registry
that lives in memory, so each restart discarded the lot. An audit found four
samples across six hundred models and concluded coverage was too thin to rank
on. It was thin because it kept starting over.

### What routing switches on now

Locality is a pool requirement, read from the upstream's *address* rather than a
list of provider names — a list is wrong the first time somebody runs a new local
runtime, and an unrecognised host must not inherit the promise. Loopback only: a
LAN box is somebody else's computer.

§9.2's soft column reads *"prefer local · prefer fast · prefer cheap · prefer
already loaded"*. Only the last existed. Price comes from OpenRouter's published
per-token rates and from the honest `0.0` a local runtime bills; unpriced sorts
**last**, because reading absence as free would hand every cheap route to
whichever provider says least about itself.

Reach replaced warmth. `Residency.UNKNOWN` ranked equal to `COLD`, and every
cloud model is UNKNOWN — so RAVIS believed calling an API and loading a 70B model
off disk cost about the same. A hosted model now sits between WARM and COLD,
which is fact rather than estimate: it needs no load, and a cold local model
does.

Speed ranks on measured TTFT, and only above a sample floor. **Unmeasured sorts
neutral, not last** — the one asymmetry against price, and it matters: every
model starts unmeasured and RAVIS only measures by routing, so sorting unmeasured
last is a trap that closes.

`ravis/balanced` uses both without inventing an exchange rate between
milliseconds and dollars. Models within a quarter-second count as equally quick —
a claim the data supports at that resolution — and the cheaper of them wins.

### "The model returned an empty message", which it had not

Pinning `gpt-5-chat-latest` produced that line in the chat bubble. The model had not returned an
empty message; it had not been called at all. OpenAI has **deprecated** the id — it is still in
their `/v1/models` listing, so it is still in RAVIS's catalogue and still pickable — and RAVIS was
saying so precisely: *circuit open after 3 consecutive failures (model_unavailable)*.

**Two services spelled a refusal differently and only one spelling was read.** NERVIS writes
`event: error` ahead of the data, because it is refusing something it never sent upstream. RAVIS
puts the whole envelope in a single `data:` frame. NERVIS recognised only its own, so RAVIS's
frame parsed as an ordinary one, found no `choices`, contributed no text — and the empty
accumulator fell through to the branch that explains an empty reply.

That branch is not wrong in general. It exists because a reasoning model really can spend its
whole budget on `reasoning_content`, which was observed. It was reporting a real failure as a
different real failure, which is worse than a bare error: it sent the reader to look at the model
when the answer was on the provider's deprecation page.

The check is truthiness rather than presence, because some proxies put `"error": null` on every
frame of a healthy stream — RAVIS learned that from an upstream and the note is in its own reader;
this is the same rule on the other side of the wire. Both shapes are now fixtures in the shaping
harness, which is exactly the divergence it exists to catch.

### Pricing the paid providers, and three faults it uncovered

A `prices.json` now states the current published rates for OpenAI, Anthropic
and Google — 29 models, read off the providers' own pricing pages rather than
recalled, because the catalogues carry builds newer than any training data and
a guessed rate silently mis-costs every budget it touches. Cache-read rates are
included where a provider publishes one.

Two figures are recorded as simplifications rather than left implicit. Gemini
Pro prices in tiers by context size — $2/MTok up to 200k and $4 above it — and
`Price` holds one rate, so the file states the standard tier; a long-context
Pro call is understated until `Price` learns about tiers. And Anthropic's
1-hour cache-write rate has no field at all; only the cache *read* rate does.

**Wiring it up found that Path B recorded nothing.** The usage tap was on the
transparent stream only, so Anthropic and Google — the two providers this file
most exists for — produced no usage record at all. Both translated paths now
record, streaming and non-streaming.

**Every translated call was attributed to the wrong provider.** The record
resolved its provider from the *selected* model, and a translated provider's
models never appear in a transparent upstream's catalogue, so
`claude-haiku-4-5` fell through to whichever transparent upstream was declared
first: three records naming `openai` for three different providers. It reads
the addressed provider now.

**And a half-reported call was being understated.** `reported = usage or …`
kept the *first* usage seen, and Gemini reports `usageMetadata` on every frame
with the counts growing — so the earliest, incomplete reading won. Fixing that
exposed the larger fault underneath: Gemini sometimes reports a prompt count
and no completion count at all, and the engine charged for the half that
arrived and labelled the result ESTIMATED. A cost computed from half a call is
not an estimate of that call, and a budget reads an understatement as room
left. Every priced component must now be known, or the cost is UNKNOWN — with
components priced at zero exempt, since a free model's missing output count
cannot change what it cost.

Verified across all three providers on one gateway: OpenAI and Anthropic priced
and attributed correctly, Gemini's half-reported call counted as unpriced —
*"2 of 3 call(s) priced"* — rather than quietly costing less than it did.

### Which providers can actually be costed

Asked whether cost is computed for anything beyond OpenRouter. It was not, and
checking turned up a wrong answer as well as a missing one.

**Only OpenRouter publishes prices**, on every catalogue entry. OpenAI,
Anthropic and Google ship catalogues with no pricing at all — which the adapters
have said in a comment since M3a — so a call to any of them was `UNKNOWN` no
matter how carefully the engine multiplied.

**Local runtimes were reported as unpriced, which is simply false.** LM Studio
and Ollama set the *ranking* price to zero and recorded no `Price`, so a local
call came back `UNKNOWN` while RAVIS knew perfectly well it was free. That is
the conflation the whole engine exists to prevent, arrived at from the other
direction: zero is a fact and unknown is an absence. Both now record a zero
`Price`, and a local call costs `0.0`.

**The three that publish nothing get a registry rather than a table.**
`prices.json` lets an operator state what they pay, per million tokens, and the
price carries `source: operator` and the file's own timestamp — §14's
price-source version and time. Deliberately *not* a table of published rates
shipped inside RAVIS: a hardcoded price goes stale silently and nobody can date
it, which is exactly what that clause of §14 guards against. An operator-stated
price is never overwritten by a catalogue, because a catalogue price is what a
provider charges anybody and an operator writing one down is stating what they
pay.

It fails closed, like the policy file and for a sharper reason: failing open
would return every paid model to `UNKNOWN`, and a budget reads unknown as
unspent — so a corrupt price file would quietly remove a spending limit rather
than merely losing a figure. `doctor` reports how many prices are stated and
which providers publish their own.

Verified live in both directions: one OpenAI call recorded `cost=None ·
UNKNOWN`, then the same call with a `prices.json` in place recorded
`6e-06 · ESTIMATED · source=operator` — 8 tokens in at $0.15/M and 8 out at
$0.60/M, which is what those rates come to.

### The cost data, on a screen

M15's figures had nowhere to be read. The Spend tile said *"not measured · the
cost engine lands at M15"* — true when written and false the moment M15 landed
— and the mock beside it carried `spend_today_eur` and `budget_used_pct`,
neither of which `/api/v1/usage` has ever published. A mock whose field names
cannot be confused with the real payload is a mock that never catches a rename,
so it now uses RAVIS's own.

The Spend tile reads the real estimate and carries its **coverage**: two of two
calls priced, not a bare total. A sum whose coverage is invisible reads as
complete, which is the misreading §14 spends a paragraph forbidding. A new card
on the Evidence screen — the same question in a different currency — lists each
call with the basis beside it: `REPORTED` where the provider stated its own
figure, `ESTIMATED` where RAVIS multiplied a published price, `UNKNOWN` counted
rather than treated as nought.

**Formatting had to be part of it.** Two decimal places render every real
figure as `$0.00`, which is the confident nought §14 forbids arrived at by
formatting rather than by arithmetic. Amounts below a cent keep enough
precision to be visibly non-zero.

**And it exposed a tile that had been broken for as long as it had been live.**
*Routing p50* read `undefined ms` against a running RAVIS, because
`routing_p50_ms` is a field the mock invented and the endpoint has never
published. §9.8 asks for normalization, capability, policy, scoring and total
routing times against a P50 target of 5 ms, and RAVIS measures none of them.
The tile says *not measured* and names the section, which is the fact; actually
measuring it is §9.8's own work and is not M15's to smuggle in.

### M15 — an estimate that says so, and a price the provider agreed with

§14's rule is one sentence: *never present an estimated cost as an invoice.*
Everything here is arranged around it. The state enum has no `BILLED`, because
a value nothing can reach is a value somebody will eventually set; an unpriced
call reports `None` rather than nought; and the endpoint's field is
`spend_estimated`, since a consumer reads the key and not the docstring beside
it.

**Three states, not two.** RAVIS multiplying a published price by reported
tokens is `ESTIMATED`. A provider stating what the call cost — OpenRouter
returns `cost` on every usage frame — is `REPORTED`, which is stronger, because
it is arrived at with knowledge RAVIS does not have: which upstream actually
served, at what negotiated rate. It is still not an invoice, and §14 asking for
"estimated-versus-billed status" is asking for exactly this middle. Preferring
our own arithmetic over the provider's figure would be choosing the weaker of
two available answers.

**The arithmetic was checked against the provider's own.** One call, 15 prompt
and 7 completion tokens: RAVIS estimated 6.45e-06 from published prices, and
OpenRouter's `upstream_inference_prompt_cost` and `completions_cost` came back
as 2.25e-06 and 4.2e-06 — the same figure, split the same way. That agreement
is what the split price is for; a blended rate would have been wrong in
proportion to how lopsided the call was, which is every call.

**Double counting is structural rather than careful.** A usage record is
written from one place, the point where a stream that produced something
finishes, so a retry that failed has no path to the ledger and a fallback
writes one row naming the model that actually answered. A stream that never
committed is a non-answer and writes nothing at all.

**Reading the stream does not touch it.** §6's byte-for-byte guarantee on the
transparent path is intact: the usage frame is read from the chunk already in
hand, on the line before it is yielded unchanged. The substring test rejects a
content delta before any JSON is parsed.

**Budgets are policy, not a second gate.** §14's four bands reach the routing
engine through `RoutingPolicy`, because every hard exclusion in this service is
applied in one place and a budget that blocked candidates somewhere else would
be a quieter second policy. Only a budget marked `hard` blocks — a figure this
service insists is an estimate should not become an outage because nobody said
it could. Verified live: with a hard budget of $0.000005 exhausted by a single
call, `ravis/cheap` excluded 415 paid candidates by name and still answered,
from a genuinely free model.

Two faults of my own were caught by tooling rather than by reading. The API
rounded to six decimal places, which renders a 6.45e-06 call as 6e-06 — the
rounding destroying the precision the engine exists to produce. And the
dead-code gate reported `price_captured_at` as written and never read: §14 asks
for price-source version *and time*, and the time was being recorded into a
field nothing published, which makes it unauditable and therefore pointless.

### The badge sweep: the flag was opt-in, so it defaulted to a lie

69 of the 100 tiles scoped to a service never passed the fifth element `kpis()`
reads as "this is real", so a figure taken straight off a live endpoint still
wore a PROTOTYPE badge. Patching 26 call sites by hand would have left the same
trap for the next tile somebody adds, so the default changed instead: a tile
now derives from `SOURCE[service]`, which `live()` already sets on every
successful fetch, and the fifth element became an override.

**The override still matters, and it is the whole reason this was not a
one-line change.** Six fetchers have no live path at all — SIRVIS's catalogue,
recommendations and downloads, RAVIS's settings and conformance, NERVIS's
traces — so the service answering says nothing about a figure that was never
going to come from it. Those pass `false` explicitly, as do the two fallback
branches on the RAVIS dashboard and Routes screen, which render mocks by
definition after their live guard has already returned.

A blanket regex over-suppressed two tiles on the way through: Diagnostics'
**Breakers** and **Pools down** come from providers and pools, both live, and
were marked false along with the conformance ones beside them. Caught by
reading the screen afterwards rather than by the sweep.

**And the sweep found a gap in the convention itself.** `live()` records
`SOURCE[service]` on a direct fetch; `peerRead()` — every read proxied through
NERVIS — recorded nothing. So a screen fed entirely through the proxy left the
flag at whatever a previous screen had set, which is why Logs showed one tile
of four as real while all four were. A successful negotiated read now records
the peer as answering; a refusal deliberately does not, because negotiation
declining a surface is not the peer being down.

Walked afterwards, every screen: RAVIS 4/4 on nine screens and 2/4 on Settings,
where three tiles genuinely come from a settings object RAVIS does not publish;
SIRVIS live on six and 0 on the three fed by mock-only fetchers; NERVIS live on
Overview and System, 0 on Traces. Which is the shape it should have had all
along.

### The explanation-card pass, screen by screen

Fifteen cards existed to describe the card above them. Each is now an
`explains()` disclosure attached to the data it is about — native `<details>`,
so no JavaScript, keyboard-operable, and it still renders with nothing running.

Folded: Providers' protocol mode; the event envelope and "Meanwhile"; the
§18.2 egress table; the credential location and the voice-key exception; "What
a session pins" and "Two windows"; "What is not logged"; SIRVIS's persistence,
failure detail, catalogue caveats, state-versus-detail, uncertainty, evidence
identity, memory, authorisation, run-detail retention and rate-versus-size;
RAVIS's provenance mapping and "Without SIRVIS"; and NERVIS's claim types.

**What was deliberately left as a card.** Several that look like explainers
render *derived data* — SIRVIS's "Validity" reads `rs.validity`,
"Observer-side state" reads the registry's own states, "Source priority" and
"Conditions" are tables of live values, and Discover's "Sources" is a computed
set. A disclosure hides those behind a click for no gain. The rule applied was
whether the card would say the same thing with every service stopped.

Voice stopped being a screen in the same pass: every card on it was a setting,
so it lives in Settings, which is now four foldable sections rather than nine
cards in a column.

### Version numbers that mean something

All three services reported `0.0.1`, which had been true on the first day and
was still being served after seventeen milestones. Worse, each one said it
twice — `pyproject.toml` carried a version and `BUILD_VERSION` carried another,
and the only reason nobody noticed they were two numbers is that both were
wrong in the same way.

`BUILD_VERSION` is now read from the installed package, so the pair cannot
disagree. The scheme is `0.<milestones completed>.<patch>`, which makes the
minor a fact anybody can check against the table above and `1.0.0` mean the
plan is finished:

| | | |
|---|---|---|
| RAVIS | 0.16.0 | M0–M14, M16, M18a |
| SIRVIS | 0.14.0 | M0–M4, M6–M10, M12, M13, M15, M16 |
| NERVIS | 0.8.0 | M0–M7, M8a in part |
| `ecosystem-protocol` | 0.2.0 | extracted, and three services stand on it |

**The rule going forward: the minor moves when a milestone lands**, in the same
commit that lands it. It stays informational — runbook §4.2 forbids a consumer
inferring behaviour from a build version, which is what capabilities are for —
so this is for the bug report, not for a compatibility check.

The one test that pinned the literal now asserts the endpoint reports the
*installed* version instead. Pinning the number meant every release edited a
test to restate what it had just changed, which only ever checked that somebody
typed the same string twice.

### Fluff cards, and a Settings screen with one real setting on it

Asked to look for cards that exist to hold prose, and for Settings screens that
are readouts rather than settings. Both were there.

**Providers' "Protocol mode" card was a second copy of a column.** It listed
which providers are transparent and which are translated — already the protocol
column of the Health table directly above it — plus two sentences nobody reads
until the moment they care. The sentences now attach to the value: clicking
`transparent` or `translated` explains that path where the question is asked,
and clicking it again closes it. One card fewer, nothing lost.

**"Not currently usable" went entirely.** Its three conditions are visible in
the table above as credential, breaker and the On/Off control, and on a healthy
deployment it rendered as a paragraph with nothing above it.

**The Settings screen was reading a hard-coded object.**
`API.nervis.settings()` performs no fetch at all, so Retention printed 14 days,
7 days and a 200 MB cap that nothing had been told, and Voice printed
*enabled: no · "cloud synthesis is disabled"* while the live service reported
voice configured, credentialed and `available`. One page disagreeing with the
service it is a dashboard for.

Voice is now the real thing: four toggles and the fallback mode, writing
`PUT /api/v1/voice/settings`, every one of them a key NERVIS actually reads —
verified by round-tripping a value through the API and back. Retention says the
window is set at start rather than inventing a number, because that is what it
is.

**What was deliberately left as a readout.** The Privacy card's five entries
are specified and unenforced, and its own prose already says so at length: *a
toggle wired to nothing would be a claim that the endpoint exists.* That
argument is right, and drawing switches there to make the screen look complete
is the failure it describes. Supervision is M16 and unbuilt. Neither gained a
control it could not honour.

### The prototype badge is opt-in, and mostly not opted into

Asked why the Providers screen still shows prototype cards. Because the badge
is **opt-in and easy to forget**, not because the data is missing.

`kpis()` reads a fifth element per tile as "this is real". **100 tiles are
scoped to a service and 31 pass it**, so 69 render with a PROTOTYPE badge
whatever their data actually is. Some of those 69 genuinely show invented
numbers and are correctly badged; an unknown number are like the ones below,
understating what they have. The badge only ever errs quietly in that
direction, which is why nobody notices.

**Every card on Providers was live data wearing a prototype badge.** The four
tiles, the protocol-mode table, the connection wiring and the unusable list are
all computed from the same `/api/v1/providers` response as the Health card
immediately below them — and the Health card was the only one that passed
`p.live`. The screen's own comment said "everything below this card is still
prototype content", which had stopped being true and was doing the same job as
a stale capability reason.

**And the same defect landed in a screen written the same day.** `ravis /
Sessions` shipped with four tiles and two cards unmarked, hours after this
audit criticised exactly that. Fixed with the rest.

**One card was worse than mislabelled.** "Breakers open" counted
`breaker !== 'CLOSED'`, which sweeps up every provider RAVIS has never called —
so it read *"4 — lmstudio, openai, anthropic, google"* while three of those
four were reachable and answering, and the Health table directly beneath
rendered the same providers as "not probed · never called, not a verdict". One
screen, two contradictory claims about one fact. An open breaker is a verdict
§10 reached; never probed is the absence of one. It reads `0 · none open · 4
never called` now.

**What was not changed, deliberately.** The remaining badged tiles were left
alone rather than mass-flipped: a tile that shows a mock and claims to be live
is the dangerous direction of this same error, and telling the two apart needs
the per-screen check that found these. `nervis / System`'s thermal and GPU
tiles, for instance, are correctly prototype — `/api/v1/system` publishes
neither.

### The UI audit: two milestones that stopped at the endpoint

Asked whether everything built so far reaches a screen. Mostly — Google's
native adapter shows on Providers, pool revisions on Pools, §12.2's tradeoff in
the route inspector's reason, and titles in chat. Two did not.

**M11 had no screen at all.** `/api/v1/sessions` and `/api/v1/sessions/{id}`
were serving and nothing read them, while NERVIS.md M3 lists *sessions* in the
RAVIS integration it is supposed to cover. There is a Sessions screen now, and
the columns are chosen to answer the question a session exists to answer: what
this conversation has been routed to, which pool revision it pinned, and how
many requests it has made — that last one because it is the number §12.2's
tradeoff reads, so a reader wondering why a strong model was passed over can
see what decided it.

**M16 was wired to the endpoint and would have crashed on it.** M16 changed the
payload to `{application_id, constraints[]}` and the page still expected the
`{policy_id, when, then, kind}` shape its own mock had invented. Every field
would have rendered `undefined`, and two lines dereferenced `items[1]` and
`find(kind === "soft")` — so a deployment with a policy configured would have
thrown rather than shown one. It was masked because nothing is configured on
this machine, which is the worst way for a screen to be right: the bug was
waiting for the first person to write a `policies.json`.

Its empty state also still read *"Policy engine M16 — not built"*. Same defect
as the two capabilities the earlier audit caught, in a third place: a screen
citing a limit that had been lifted. Empty now means no policy is *configured*,
which is a statement about this machine rather than about the engine.

**And the new screen was briefly invisible to its own gate.** Adding it to
`RAVIS_VIEWS` made it callable and left it out of the sidebar, and
`render_check.js` walks the nav rather than the view table — so the count
stayed at 35 and the screen was neither reachable nor tested. The gate was
right and the wiring was half-done; 36 now.

### M14 completed — and the estimate had to come from history

§12.2 states the tradeoff with an example: a stronger model that takes fourteen
seconds to load is *the worst choice for one simple question* and *worth it for
a session expected to make 100 requests*.

**The first attempt read the wrong number and contradicted a shipped rule.**
Penalising a cold model whenever the current session was short broke
`test_declared_preference_still_beats_residency_normally`, which pins M14's
observation half: a coding pool should reach for a coding model even at the cost
of a load. That test is right, and the failure exposed something better than a
threshold bug — **session affinity settles a conversation on its model at the
first request**, which is exactly when the current session's own count is 1 and
means nothing. A rule reading it would decline the load on request one, decline
it again on request two, and then switch models halfway through a long
conversation: the churn stickiness exists to prevent, arrived at by the feature
meant to avoid a load.

So expected session length is the **median request count of this application's
completed sessions** — a measurement available at the moment the decision is
made, and the reason this half needed M11 rather than merely wanting it.

**Three states, and the third decides most requests.** `None` means RAVIS has
not watched this application long enough, and not knowing routes exactly as it
did before the tradeoff existed. Only a measured expectation moves anything,
which is what keeps the shipped rule intact for every client nobody has
observed.

**What is measured and what is chosen, kept apart.** The real threshold is load
time divided by per-request advantage, and RAVIS can measure neither: no runtime
publishes a load duration, SIRVIS measures `load_seconds` only inside a Runtime
Set benchmark and does not expose it through the evidence API, and §13
deliberately refuses to reduce quality to one comparable number. So
`LOAD_AMORTISES_AFTER_REQUESTS = 8` is a declared policy with a stated reason
rather than a computed break-even, and it should disappear when SIRVIS publishes
load seconds.

Verified live, two applications against one gateway, same pool, same two models,
opposite routes from measured history alone:

    one-shot client   ravis/coding -> chatty-1b       (resident)
                      "sessions run about 1 request(s), so a one-time model load
                       would not amortise"
    long-session app  ravis/coding -> qwen-coder-30b  (cold, and worth loading)

Memory pressure still refuses the load whatever the session length. That is
where M14's two halves meet, and reversing it would let a busy conversation
force exactly the load the observation half was built to prevent.

### M11 — sessions, and the request that exposed the exemption

§12.1 decides the storage key in one sentence: *cross-workspace Clarvis sessions
must never merge because display names match.* So nothing keys on a label. The
stored identity is the application's own, plus the ID the client supplied, which
makes isolation structural — two applications that both call their session
`main` are two rows and cannot read or steer each other. Within one application
the client owns distinctness, and that limit is stated rather than hidden: §9.7
forbids RAVIS from handling workspace identifiers at all, so it cannot tell two
workspaces apart itself.

**A session ID influences routing, so what it can influence had to be bounded.**
The runbook §4.3 says every ID it defines is correlation data and never
authorization. Stickiness is applied among candidates that already passed every
hard filter, so presenting somebody else's session ID can at most express a
preference for a model the caller could already reach. §12.1's four break
conditions — capability, context, provider health, policy — are enforced *above*
the ranking rather than inside it, which is why affinity cannot resurrect a
model that stopped being allowed.

**Expiry and deletion are two windows, not one.** A stale session stops steering
routing after an hour and is still readable for a week. Collapsing them would
mean either routing on yesterday's choice or losing the correlation somebody is
reading, and §12.1 asks for both behaviours by name.

**Sessions persist, and that is a deviation the decision log does not make.**
The route-decision log is bounded and in memory, with a justification that
claimed §17 does not list route decisions — §17 lists them explicitly, so that
citation was false and is now recorded as a deviation instead. Sessions had to
go the other way: §12.1's gate names restart, and a session that forgot its
model on restart would swap the model under a conversation still in progress.

**The fourth request found what the first three hid.** Sending pool → name a
model → pool → background → pool against a live gateway, the exemption in
§9.6.1 looked correct for three steps: the background call did skip affinity and
route cheap. The fifth step showed the conversation had *moved* — the background
call had recorded its own cheap selection over the session's model, so
generating one title reset the conversation and the next real turn started
somewhere else. Exempt has to mean both directions: a background call neither
consumes affinity nor gets to redefine it.

### Build-order audit, 28 Aug 2026

Run before starting M14, and it changed what to build next. Four findings.

**Stage 5 is complete, and M14's remaining half was filed under it wrongly.**
The stage table assigns Stage 5 six milestones — M3b, M4, M7, M8, M13, M16 —
and all six have shipped; the runbook's five exit criteria for it are met. But
M14's split note said its remaining half *"stays at Stage 5"*, and that half
needs M11's expected session length. **M11 is a Stage 6 milestone**, so a Stage
5 item was declared to depend on one that lands after it. The stage table never
listed M14 under Stage 5 either, so the two had disagreed since the split. Both
are corrected in `RAVIS.md`, and the half now sits in Stage 6 behind M11.

**Two capabilities were citing limits that had been lifted.**
`nervis.dashboard@1` deferred to "M2" long after M2 shipped and
`nervis.registry@1` was advertised `available`; `nervis.ravis_chat@1` said
generated titles were waiting for RAVIS to honour §9.6.1's marker, which RAVIS
M16 does. This is the same defect as a capability stuck on `unavailable` and it
has now happened in both directions in this repository — a peer reads the
reason and plans around a limit that is gone. Chat's advertisement is now
conditional on the credential, mirroring `nervis.voice@1`, because what
separates degraded from available there is configuration rather than code.

**NERVIS's milestones are not tracked in the Done table.** Every row above is a
RAVIS or SIRVIS milestone, while NERVIS has shipped M0 through M7 and half of
M8a. Nothing is wrong with the code; the ledger simply does not cover a third of
the project, and the capability surface has been doing that job instead.

**The order that follows.** M11 first, because it is the blocker rather than
merely the next number; then M14's remaining half immediately after the thing it
waits for; then M15. Doing M16 before M11 turns out to have been right for a
reason nobody planned: a session is keyed to an application, and until M16's
credential lookup landed, every authenticated caller was a single identity
called `configured`.

### Policy, wired: NERVIS titles as background calls

M16 built the engine; this connects something to it. NERVIS.md §7 already said
what should happen — *NERVIS chat generates conversation titles... Each is a
RAVIS background call and must carry RAVIS's declared marker* — and
`set_title`'s own docstring said why it had not: *RAVIS does not honour that
marker yet.* It does now.

**Three things were missing, and the first was not obvious.** Policy is keyed to
an application identity, and every authenticated caller resolved to one identity
called `configured` — so a policy written for NERVIS would have applied to
Clarvis too, which is not policy but a global setting with a misleading name. A
credential stored as `client.<application>` now resolves the caller to that
application, which is the lookup M0 deferred in as many words: *"once M10 brings
a credential store, this grows a lookup and the rest of the service does not
change."* The legacy single credential still resolves to `configured`, because
removing it would turn an authenticated caller anonymous at the moment its rate
limit tightened.

Second, NERVIS called RAVIS with **no credential at all** — every request
arrived as `anonymous`, which is least-privileged by construction, so its
marker would have been ignored even if it had sent one.

Third, `/api/v1/policies` returned `[]` unconditionally. Empty still means no
policy is configured; it now means that because the file says so.

**The placeholder had to stop being permanent.** `store.append` writes the first
message's opening as a stand-in title, so "already has a title" could not mean
"leave it alone" — that reading would have made the generated path dead code
that never ran once. The test is exact rather than a heuristic: the stored title
either *is* `placeholder_title` of the first message, or a person typed it, and
a name somebody typed is never replaced. Overwriting that is worse than never
generating one — the first destroys their work, the second merely fails to help.

Verified end to end against a stub upstream, so the chain could be proved
without a provider key or a paid call:

    application: nervis     title call   -> declared a background call (§9.6.1)
    application: nervis     ordinary turn-> no marker, policy still applied
    policy      *-preview*  excluded     -> tiny-local-1b-preview refused
    title       "My sourdough starter smells like acet…" -> "Sourdough Starter
                                                            Troubleshooting"
    after a human rename and another turn -> "Bread notes", untouched

Every failure in the titler is silent by design. No credential, no RAVIS, a
refusal, an empty answer — each leaves the conversation with its truncation and
nothing else happens, because §7 states the trade outright: an untitled
conversation is a smaller failure than a title billed to a frontier model. A
retry or a fallback to a paid route would invert exactly that.

**Nothing is configured on this machine yet**, and that is deliberate: the
credential is the operator's to create. Until `client.nervis` exists in RAVIS's
store and the same value reaches NERVIS, NERVIS stays anonymous and simply does
not generate titles.

### M16 — policy, and the field that had to earn its way back

M16's own text says why two fields were deleted at M0:
`may_declare_background_calls` and `max_privacy_level` were set on every
identity and read by nothing, and *a field describing an unenforced trust
boundary reads as protection*. They come back here, and what makes them honest
is that flipping either one now changes which models a request may reach. The
test that used to assert their **absence** inverts rather than disappears: it no
longer asks whether the attribute exists — which is what it could check before,
and what proved nothing — but whether the permission changes the answer.

**Every policy constraint is a hard exclusion, applied before ranking.** §14's
rule 14 says privacy can never be overridden by score, and the only way to
guarantee that is for a forbidden candidate never to reach the scoring. So
policy removes candidates where the pool invariants do. `LOCAL_PREFERRED` is the
single exception and is deliberately *not* an exclusion — it is the one rung of
the ladder that ranks.

M16's first exit criterion is that each hard constraint **provably excludes a
top-ranked candidate**, which is stronger than excluding something: a constraint
that only ever removes candidates nobody wanted is indistinguishable from one
that does nothing. So the tests pin a control first — the hosted model wins
`ravis/auto` unaided — and then show each constraint overturning that.

**A policy file fails closed, and that is the opposite of `providers.json`.**
That file's own comment explains why it fails open: an operator whose provider
toggles got corrupted should find their providers working. It is exactly wrong
here. A corrupted policy file that reads as "nothing is restricted" turns
`LOCAL_ONLY` off without telling anyone. So a malformed file raises, `serve`
refuses to start, and `doctor` catches it and prints it — because the command
you run *because* the service will not start must not be the one that dies on
it.

**Live verification found the gap.** Sent through the running gateway, an
anonymous request marked `background` routed to a paid provider — which is
§9.6.1 working exactly as written, since the marker is honoured only from an
authenticated identity. But the route explanation read `requirements: none`. The
routing was right and the account of it was silent about the only thing the
caller had asked for, which is a caller being billed while believing otherwise.
An unhonoured marker now says so in the explanation.

With an authenticated identity the gate itself holds, on the live service and
against OpenRouter's real catalogue:

    authenticated, ordinary     ->  aion-labs/aion-2.0        (paid)
    authenticated, background   ->  cohere/north-mini-code:free
                                    415 candidates excluded, each naming §9.6.1

Unpriced counts as paid, and that asymmetry is load-bearing: OpenAI, Google and
Anthropic publish no pricing in their catalogues, so reading absence as free
would leave the gate holding only for providers that happened to publish a
number.

**Pools now carry a version and a revision**, and the revision is derived from
the pool's behaviour rather than stored. A hand-maintained revision is a number
somebody forgets to increment, and a consumer pinning a stale one is worse off
than one who could not pin at all, because they believe they are protected.
Wording is excluded from the hash on purpose — fixing a typo in a description
must not invalidate every pin. `clarvis-chat` and `clarvis-agent` get
independent revisions for free, which §5.4 requires explicitly.
`ravis.virtual_profiles@1` moves to `available`, and its `pools` constraint is
now counted rather than remembered: it said 13 while fourteen were being served.

**One part of M16 is not built.** The milestone also asks for a tiebreak that
knows about reasoning overhead, and says in the same sentence that it needs
SIRVIS M22b's measurement rather than a name-pattern guess. M22b is scheduled
and unbuilt, so the tiebreak has no evidence to read and guessing from a model's
name is the thing the milestone explicitly rules out. It is listed in Next
rather than quietly dropped.

### RAVIS M7 — and the measurement that chose the path

M7's exit is *"transparent/translated path chosen correctly; conformance stays green"*, which
makes the choice the deliverable rather than a build being one. §6 answers half of it outright:
OpenRouter speaks the protocol, so it stays on Path A and needed nothing. Google was the open
question, because it publishes an OpenAI-compatible endpoint at `/v1beta/openai` and RAVIS was
already using it — and §6 is emphatic that an already-compatible stream should not be normalized
for architectural purity.

**It is not compatible where it matters.** The same request, the same afternoon, through the same
code path:

| | `finish_reason` | tool-call index | usage chunk |
|---|---|---|---|
| Google via `/v1beta/openai` | `stop` | absent | absent |
| OpenRouter, transparent | `tool_calls` | `0` | present |

A client that notices a tool call by reading `finish_reason` sees a finished text answer, and one
that assembles fragments by index has nothing to key on. Both are what Clarvis's agent role does
and what its conformance suite checks. So the shortcut was worth trying, the measurement is why it
was abandoned, and Gemini went where §6 put it in the first place. After the native adapter, the
two providers are indistinguishable on every surface §6 names.

**Four mappings are judgements rather than transcription**, and each is argued where it lives.
Gemini's tool calls carry no id, so one is synthesised from position — stable, because a
conversation stored with one set of ids and replayed with another stops matching its own tool
results. Arguments arrive as an object and leave as a string. Thoughts arrive in the same parts
list as the answer and are filed as reasoning, because a client rendering them together shows the
model's working as if it were the reply. And `finishReason` is `STOP` even when the model called a
tool, so the reason is derived from the content — reading that field literally is the compat
endpoint's defect reproduced in our own code.

**Which is exactly what happened once.** The first native implementation asked the *finishing
frame* whether it carried a call, and Gemini sends the call and the finish separately — so the
answer was always no and the stream ended `stop` with a tool call in it. The bug the adapter
exists to avoid, rebuilt inside it, and caught only because the live probe was run again
afterwards rather than trusted to have worked. Whether a tool was called is a fact about the
stream, not about one frame.

A second one was cheaper and duller: Gemini names already begin `models/`, so paths built from a
name dropped the `/v1beta` segment and 404'd — which reads exactly like a deprecated model, and
would have been diagnosed as one without the log.

### What `doctor` did not know about Path B

Found by leaving `google` declared in `RAVIS_UPSTREAMS` after M7 and checking
what the diagnostics then said. Two faults, both in the same table.

The rows for translated providers named Anthropic **literally**, so M7 adding
Gemini to Path B did not add a row for it: `ravis/google/*` routes perfectly
well and the one diagnostic whose whole job is to say what routes where showed
nothing for it. It is a list now, so adding a translated provider means adding
it to the list rather than rewriting the function.

The gate was also the settings field alone — and nobody configures it that way.
M10 put credentials in a 0600 file behind the Credentials screen, so a key
typed in where the product asks for it produced **no row at all** for either
provider. On this machine `doctor` listed neither Anthropic nor Google while
both were configured and both were routing. The store is consulted here in the
same order `credential_for` uses on the request path, because a diagnostic that
disagrees with the request path is worse than not having one.

Reading a local file is not contacting an upstream, so M0's "without contacting
any upstream" still holds.

### Declaring Google as an upstream, measured rather than argued

M7 left the compat `kind` in place, and the open question was whether to keep
declaring it alongside the native adapter. Answered by diffing the two
catalogues rather than by preference:

    native 39    compat 54    shared 39    native-only 0

The native catalogue is a strict **subset**. All 15 extras are models the
adapter filters out precisely because they cannot serve `generateContent` —
`veo-3.1-*`, `gemini-embedding-*`, `lyria-realtime-exp`, the live-audio and
transcribe previews, `gemini-robotics-er-2-streaming-preview`, `aqa`. So
declaring the upstream adds no model anyone can chat with, and 15 ids a client
can pick out of `/v1/models` and be refused for — the thing `merged_catalogue`
already refuses to do for a *disabled* upstream, for the same stated reason.

It also buys nothing on latency, which was the reason for trying it: the native
adapter is itself the direct-to-Google path and is registered independently of
`RAVIS_UPSTREAMS`.

**And latency turns out not to separate the paths at all.** Measured once
billing replaced the free tier, 20 interleaved rounds, `gemini-3.6-flash`,
comparing per-round *differences* rather than per-run medians:

| | median paired Δ | faster in |
|---|---|---|
| native vs OpenRouter | −35ms | 11/20 |
| native vs compat | −94ms | 12/20 |
| compat vs OpenRouter | +178ms | 6/20 |

Native versus OpenRouter is a coin flip. The compat path is the slowest of the
three, which is one more reason not to declare it, though 14/20 is a weak
result and the catalogue argument above is the one that decides it.

**Three readings had to be thrown away to get here, and each was wrong in its
own way.** The first two counted refusals as data: the measurement loop
recorded a provider's 429 as a 0ms sample, and a rate-limited request is fast,
so the failing provider won. The third rejected refusals correctly and still
misled — comparing medians *across* runs, where drift between runs was as large
as the effect, produced a confident "OpenRouter is 630ms faster, the
distributions barely overlap" that pairing dissolved to nothing. Interleaving
the samples was already right; the analysis was not using it.

**The collision is now stated rather than resolved in silence.** One name
claimed by both tables is a working configuration — a direct address reaches
the translated provider while it has a credential, and the upstream still
contributes its catalogue — but resolving it quietly is how both defects above
survived. `doctor` prints a note naming which provider wins, and startup logs
the same thing at the moment somebody can still act on it.

### One name, two providers, one provider's numbers

The other half of what leaving `google` declared turned up. A provider's
catalogue was resolved by name, and a name identifies a provider only *within*
one of the two tables — so a transparent upstream called `google` sitting
beside the translated Gemini provider gave two rows one name, and the
translated row reported the transparent upstream's 54 models as its own. The
function's docstring already said a translated provider must report no count at
all, "because a count of nothing and no count at all are different claims"; the
lookup quietly supplied a third, worse option. Anthropic never hit it only
because nobody declares an upstream called `anthropic`.

The upstream now travels with the row rather than being looked up afterwards.
Both translated providers report no catalogue, which is what they have.

### Delete deleted half of it

Deleting a conversation removed the browser's copy and left NERVIS holding every message. The
history list is built from `localStorage`, so the surviving copy appeared nowhere at all — and the
pane said, in as many words, that conversations were *"stored in this browser only — not on the
machine and not in any service"*, which was never true. NERVIS has stored them since M4.

Reported by somebody who cleared their history and was told the machine still had forty-one of
them.

**The browser could not have deleted them even if it had tried.** `CHAT_STORE` keyed on the
session's local id and never recorded NERVIS's, so a stored entry had no way to name the
conversation it mirrored. The remote id is kept now, and Delete removes NERVIS's copy *first* —
if that fails the local row stays, so the conversation remains on screen to retry rather than
vanishing while the real copy survives. A 404 counts as success: something else already deleted
it, and reporting failure for a conversation that no longer exists leaves a row nobody can clear.

**With cross-session memory on, this was a privacy bug rather than an untidiness.** A conversation
somebody deleted was still in the pool the next one recalls from — deleted in the only place they
could see, and still being read.

The pane now shows what NERVIS holds whenever it exceeds what the browser lists, with a Forget-all
beside it, because a store nobody can see is a store nobody can empty.

**`confirm()` was silently disabled and every destructive control was dead.** Delete in the
conversation list did nothing — because Chrome offers *"prevent this page from creating additional
dialogs"* after a couple of prompts, and once that is ticked `confirm()` returns `false` with no
dialog, no error and no way for the page to know. The button was not broken; it was being
cancelled, forever, invisibly.

Reproduced by watching the call: it fired and returned `false` with nothing on screen. Four other
controls used the same pattern — releasing a multi-model lease, emptying a pool, emptying a
provider filter, removing a credential — so all five were one ticked checkbox from being inert.

They arm instead. The first click warns and turns the button amber, the second acts; the warning
still gets read, nothing is one keystroke from being lost, and no part of it depends on a modal
the browser may have muted. It is the same shape as `armProfile`, which was already here for the
same reason and is presumably why that one never broke.

**Models a provider lists and then refuses are hidden from the picker.** A deprecated id stays in
OpenAI's `GET /v1/models` after it stops working, so it stayed pickable and every attempt came
back 404 — which RAVIS already records as `MODEL_UNAVAILABLE`, per model, with the same machinery
that opens its circuit.

That record is the signal, and it is the only honest one available. There is no `deprecated` flag
on any provider's listing: OpenAI publishes deprecations as an HTML page, so the alternative was a
hardcoded table that covers one vendor, goes stale unnoticed, and dresses *a web page said so* as
knowledge. The observed refusal covers every provider, needs no network, is wrong only until the
next attempt, and reads identically for a model the account simply has no access to — which is
correct, because both answer the question a picker is asking.

**Tri-state, and `null` is not health.** Never called reports `null`, for the same reason a
provider nobody has probed reports a breaker of `null` rather than CLOSED; a model that has
answered reports `false`, so a provider recovering is visible rather than permanent. The picker
hides only `true`.

**Counted, not merely dropped.** The row reads *"125 model(s) pass its filter · 1 hidden, this
provider answered 404 for them"*. A picker showing fewer models than the provider publishes, with
no explanation, is one nobody can debug.

Found the guard wrong on the first run: it gated on `requests`, and a model that has only ever
failed has no completed request to its name — so it reported "never called" for exactly the models
it exists to find.

**A second one is left standing, deliberately.** Every `gpt-5` model rejects `max_tokens` and
wants `max_completion_tokens`. Translating it would mean the transparent path stops forwarding the
body verbatim, which is §6's whole distinction — so the parameter stays as sent and the refusal is
now legible, which is the outcome the fix above was for.

### Miku, back as a persona rather than an easter egg

She was a button labelled AGI at the end of the pool row — a thing that is "presentation only"
sitting among thirteen things that route — and she was deleted with the scene behind her. She is
back as one of the chat presets, which is what she should always have been: selectable like the
others, carrying a persona, an avatar and an accent colour.

`mode` is presentation and nothing else: it routes nothing, sends nothing and records nothing.

**Two questions, and they turned out to have different answers.** *Is she the one talking?* —
anywhere in NERVIS. *Is the dashboard wearing her colours?* — only on the chat screen. They were
one question for a while, which is how the theme ended up following you onto Diagnostics: a
persona has no business recolouring a table of error rates, and turquoise instrumentation with
her face above the error column is a costume on a measuring instrument.

The nosiness is behaviour, and behaviour does not stop at a screen boundary. She stays armed
wherever you are, notices you have wandered off, and says so — audibly, if the voice is on, which
is how you hear one from another screen. What does not follow you is the paint.

Her replies are labelled **Miku**, stamped onto the message when it arrives rather than read at
paint time, so a conversation held with her still says so when it is reopened in the ordinary
mode.

**She can see which screen you are on, and nothing on it.** A nudge carries the screen's *name*,
read off the view state and never off the numbers, so she can be nosy about you having stared at
Diagnostics for ten minutes without inventing what it says — the same line the persona itself
draws.
`data-miku` is set from the stored mode on every paint and removed otherwise, so nothing has to
remember to undo it — and a preset that names no mode is read as *the ordinary one* rather than
*leave whatever was there*, which is what puts NERVIS's face back when you switch away.

**Silence is now an option when the chosen voice cannot be used**, which is the other half of a
sentence §18.2 always had: *fall back to local system TTS, **or stay silent and say so***. Only
the first half had been built. The browser's own voice is free, offline and sends nothing, which
is exactly why it is the right refusal for a local model's reply — and it sounds nothing like the
voice somebody picked, which is why the second half is there. The text is on screen either way.

Reading it stays the default, because a voice feature whose out-of-the-box behaviour is
"sometimes nothing happens" is indistinguishable from a broken one. And silence needed no
mechanism of its own: an empty `fallback_text` already meant *say nothing*, which is the shape
mute has used since the start.

**A silence caused by a fault is still announced**, once per session. `muted`, `local_only` and
`nothing_to_say` are the design working and say nothing about themselves; `unreachable`,
`refused`, `no_credential` and `no_voice` are not choices anybody made, and a key that stopped
working would otherwise present as a voice that quietly stopped existing. That is the "say so"
in §18.2's sentence, and it is once rather than per line because a broken key speaks on every one.

**The daily voice cap is off unless switched on**, which reverses the decision it shipped with.
The guard was right about the risk — a dashboard that greets you on every tab open, and now talks
to you unprompted, is exactly the shape of thing that spends money while nobody is watching — and
wrong about the cost of *reaching* it. The fallback is the browser's own voice, so hitting the cap
drops you mid-conversation from the voice you chose to one that sounds nothing like it. A spend
guard whose failure mode is "everything suddenly sounds wrong" gets switched off in irritation
rather than tuned.

It is a separate setting from the number, so `0` keeps meaning what it always meant — *never call
Fish, the browser reads everything* — instead of being overloaded into a second way of saying "no
limit". The number is editable whether or not it is being enforced, because setting a limit and then
switching it on is the order anybody does it in. The counter runs either way, so the figure is
there to look at before deciding to enforce anything.

**Giving a model the time makes it say the time.** Four replies in a row opened with a clock
reading nobody asked for — *"it's 04:03 and you just deleted every conversation"*, *"judging your
life choices at 04:06"*. The greeting already had a rule against reciting it; the rule was needed
on every turn. It is there so she can *answer* about it, not decorate with it.

**And a coarse reading gets sharpened.** Asked a question fifty seconds after the previous one,
she said *"you asked this fifty seconds ago"* — correct by luck, from a reading that said only
"less than a minute". A rule against inventing a duration is worth nothing if the true one is
withheld, so the gap is given in seconds under the minute and the instruction says not to make it
more precise than it is written.

**Turns carry a timestamp, and the model carries a clock.** The transcript shows the time of each
turn in 24-hour form, in the reader's own zone — stored as an instant and formatted at paint time,
so the same conversation opened elsewhere reads in that zone rather than in a string frozen at
23:35. `hour12` is set explicitly rather than left to the locale, which would print one machine's
23:35 as another's 11:35 PM.

The model is handed the current local time and **how long the user has been quiet**, both
measured — the second computed from two stored timestamps rather than felt. That is the answer to
the duration problem below: the fix for *how long have I been away* is to answer it, not to forbid
the question. Asked exactly that, she now replies that she knows the last message was under a
minute ago and cannot say more without being told when they left, which is both halves of the rule
working at once.

The greeting is told not to read it out. She opened with *"it's 23:35 on Thursday 27 August 2026"*,
which is the clock being recited rather than used — and every turn is stamped with the time on
screen anyway. She is given it so she can answer about it later, which is a different thing.

It rides with a persona rather than arriving on its own. A request that configures nothing still
sends no system message: §7 makes NERVIS a plain client of RAVIS's published API, and a gateway
that prepends a line to every request is not one. The nudge is the exception, because an
unprompted remark about a silence is the one place the gap is load-bearing — and was the one place
that would otherwise have had no clock.

**The facts rule now covers durations, and that took a live failure to find.** It was written as
*no invented timings*, which a model read as being about latency numbers — and then opened a nudge
with *"you said that an hour ago"*. Nothing had told her how long it had been, and a conversation
carries no clock. An invented stretch of time reads exactly like a measured one, which is the
whole reason the rule exists, so both personas now draw the line at *inventing* rather than at knowing: the
time and the quiet gap are measurements they may state, and every other stretch of time is not
theirs to make up.

**And emoji are stripped before speech.** Both personas already say not to write anything you
would not say out loud; a model put a smiley on the end of a sentence anyway. An instruction is a
request, and `speakable` is the enforcement — what a voice does with one is provider-specific
(silence, a pause, or the character's name read out) and none of those is what the sentence meant.
The transcript keeps it.

**Three things in the supplied text were adapted, and the reason is the same each time.** She is
told she is shown no screen — NERVIS reads telemetry, not pixels, and a persona that claims to
see one invents what is on it, which is precisely what happened when the NERVIS persona listed
example readings and two models in a row repeated them back as fact. Memory is phrased as what is
in front of her rather than as a faculty she has, because cross-session recall is a setting. And
the facts-are-never-the-joke clause is carried across, because it survives every persona change
here.

**Her bubbles and yours are tinted apart.** Hers keeps the teal-into-pink gradient the character
is built from; yours is the same turquoise family with the pink removed and the whole thing cooled
and deepened. One palette, and still an answer to *who said this* from the colour alone.

**The whole panel now outlives the tab.** The persona, the name, the memory scope and the mode
were already settings; the sampling fields were not — a temperature somebody set was gone on the
next reload with nothing to say it had been. They are written on blur, captured from the session
rather than read off the inputs so a value that arrived from a preset is saved on the same path as
a typed one, and hydrated **once per page load rather than once per paint**: this runs after every
message, and re-applying the stored copy each time would overwrite whatever is being typed, since
the input updates the session on every keystroke and the stored copy only catches up on blur.

**And the voice is chosen there too**, on the panel where the rest of a mode lives — the same
selected voice the Voice screen writes, because there is one, not a per-screen one. Choosing a
mode and then leaving the conversation to change what it sounds like was the seam a preset exists
to close.

**The picker names what is in force.** Nothing tracked which preset was applied, so the dropdown
reset to the placeholder on every paint — which also meant the delete link, which read the
dropdown's value, never had anything to delete. `chat.preset` records it, the option is marked
selected, and Delete is a button beside Save rather than a link buried in the help text. NERVIS is
what an installation that has chosen nothing reads as: the seeded settings already hold that
preset's values, so naming it is a readout rather than a claim.

**She talks first when nobody else does.** Three remarks per silence at widening intervals — two
minutes, four, seven — and then nothing until the user says something. The browser owns the
timing, because it is the only side that knows the tab is visible and the composer has been
quiet; NERVIS owns the words, so a page cannot put a line in her mouth. Hidden tabs are skipped:
nudging a window nobody is looking at spends a completion to talk to a screensaver.

The flavours escalate, and the one that objects to being ignored cannot come first because it
refers to the two that went unanswered. The middle one recalls an earlier conversation, and is
offered **only when there is something it is allowed to recall** — a nudge is not a reason to
override the memory scope, which is an egress decision. Where recall is off she asks instead,
which is the honest version of "remembers things" on an installation nobody has asked to
remember any.

A nudge reads the conversation and is stored nowhere. The first version sent no history at all
and asked her to follow up on something earlier, which she could not see.

### The chat screen lost three things and gained two

The pool chips are gone from above the transcript. Thirteen of them duplicated a picker that
already existed in the Model drawer, could not represent a *pinned* model at all — pinning is two
slashes deep and no chip stands for one — and cost a second call to `/api/v1/profiles` on every
paint. One place to choose it. The Model, Parameters, History and Voice buttons moved into that
freed line, in flow rather than floating: the absolute strip had needed a hand-widened
`padding-right` every time a button was added.

**Miku mode is deleted** — the scene, the glitch overlay, the avatar and its embed. The typing
caret survives, renamed off the feature it outlived.

**A conversation can be kept out of the pool for good**, with a Private button beside New chat —
next to where a conversation is decided about, rather than three clicks into a settings drawer.
It replaced a global "skip the one I am in", which was de-duplication wearing a privacy label: the
current conversation's turns already travel as ordinary messages, so including it in the digest
only ever sent the same text twice. That de-duplication is now an unconditional rule with no
setting, and the switch means what a reader assumes it means — *keep this one out*, still true
tomorrow, from whichever other conversation is asking.

The decision is held on the session until the conversation has an id and written the instant one
arrives, because the moment you most want to mark a conversation private is **before** you have
typed the thing you did not want remembered. An unreadable exclusion list bars nothing rather than
everything: a corrupt setting that silently stopped all recall is a fault nobody reports, while a
conversation somebody meant to bar is visibly still listed on the screen that bars it.

**Memory has a scope**, and it is an egress decision the screen states rather than a convenience
it performs quietly. `This conversation only` is the default and is what NERVIS has always sent;
`All conversations on this machine` additionally hands the model a bounded digest — five
conversations, six turns each, four thousand characters, newest first. Bounded twice because
either bound alone fails: a per-conversation cap still lets fifty threads fill a context window,
and a total cap alone spends everything on one rambling one. The current conversation is left out
by default, since its turns already travel as ordinary messages and including it would send the
same text twice.

The honest part is the warning next to it: a conversation held with a local model has never left
this machine, and recalling it into a turn a cloud model answers sends it. That is the same shape
of leak §18.2 spends its length on for the voice — chosen deliberately here, per installation,
rather than happening by default. **It is not yet gated the way the voice is**, and that asymmetry
is recorded rather than hidden.

**Two bugs the testing found.** A preset only wrote the keys it carried, so switching from Code
to NERVIS left Code's temperature of 0.2 behind — invisible on the screen it came from, and
enough to make a mode unreproducible. A preset is a whole state now. And the shipped persona
listed illustrative readings — *"a model nobody has called in a week"* — which two models in a row
read as current fact and repeated back as data. The examples are gone and the persona now says
outright that it is shown no readings except the ones in the conversation.

### CI had been red for four commits, and none of it was what it looked like

**Two enrollment secrets were committed at M8a and had been in the history since.**
`nervis/:memory:.enrollment` and `nervis/nervis.enrollment` are `0600` on the machine that
generated them; git does not carry that bit, so a checkout gives them `644` and NERVIS refuses to
read a world-readable secret — correctly, and its own message says *treat the old one as
disclosed*. Every CI test that builds an app failed on it. The refusal was right twice over: the
file should not have been readable, and the first of the two should never have existed at all —
it was named by `with_suffix` on the literal string `":memory:"`, so every test run wrote a
secret into the source tree. An in-memory database keeps nothing between runs and now gets a
secret that keeps nothing either.

**The `undefined` check was failing on the code that fixed the bug it guards.** It banned the
substring anywhere in `index.html`, which also matched `!== undefined` — the ordinary way to ask
whether an optional field was supplied — and the comments describing the very failure it exists
to catch. Comments and comparisons come out before the search now.

Neither of those was visible from a green-looking local run, because `grep -Eo '[0-9]+ passed'`
matches `240 passed` inside `1 failed, 240 passed`. That is how a broken test was reported here
as all-green, and it is worth writing down as the thing that actually went wrong.

**A conformance case was failing locally and passing in CI**, which is the inversion of the usual
complaint and a worse sign. `ravis conformance clarvis` builds the real application, and the real
application reads `~/.config/ravis/pools.json` — so the Pools screen narrowing `ravis/clarvis-chat`
to nine real model ids meant the fixture's `chat-only-model` was no longer in the pool, and the
suite reported on a UI click made days earlier rather than on the build. CI has no such file and
saw nothing. The harness now points the membership store at a path that does not exist, which is
the state a fresh install is in and the one the suite means to certify. This is the same class of
bug as the missing `XDG_CONFIG_HOME` isolation found earlier this session, in a second tool.

### Voice, and the gate that was the actual work

NERVIS speaks. **NERVIS → Voice** takes a Fish Audio key, keeps named voices, and reads chat
replies aloud; §18.2 had specified all of this and deferred it until "the privacy gate above is
implemented and tested", which is the part worth writing down.

**Speech is an egress path.** §18.2: *failing closed on the route and open on the voice is still
a leak.* A reply a local model produced never left the machine, and sending it to Fish Audio to
be read would send it after all — undoing the routing decision with the speaker. So synthesis is
permitted only for text NERVIS can **positively confirm** already left: the browser states which
model answered, NERVIS asks RAVIS which models are remote, and everything it cannot confirm
falls back to the browser's own `speechSynthesis` — free, offline, and it sends nothing.

That inverted the obvious check. "Is this local?" fails open on every model nobody has heard of;
"is this confirmed remote?" fails closed. It also needed one new field on RAVIS's
`/api/v1/models` — `local`, and **tri-state**, because a `None` that reads as `false` opens the
gate on every model against any RAVIS built before the field existed.

**The credential is NERVIS's, not RAVIS's**, which looked wrong until three sources agreed:
§18.2 says voice credentials live in NERVIS's own storage, RAVIS.md §5.0.1 already reserves
`tts` and `audio` as substrings RAVIS ids must avoid, and the gate depends on a fact NERVIS holds
and RAVIS would have to be told. RAVIS → Credentials carries a card saying where it went, since
that is where somebody looks first.

**What the sibling project's voice stack was worth.** Clarvis has shipped Fish Audio for
milestones, and reading it first prevented four bugs rather than one: the engine id is an HTTP
*header* and Fish answers an unrecognised one with the account default instead of an error, so
getting it backwards is the wrong voice and no message; the listing endpoint is `/model` with no
`/v1` while synthesis is `/v1/tts`; the id in that listing arrives as `_id`, and reading `id`
yields blank rows rather than an error; and utterances must be serialised, because a second
`speechSynthesis.speak` chops the first off mid-sentence.

**Two things it did not have.** A reasoning model emits `<think>` inside `content` rather than in
`reasoning_content`, so the whole chain of thought would have been read aloud — caught on the
first greeting ever generated, on this machine, live. And the spend cap here is keyed on the
**local** date; Clarvis keys on UTC and pins the resulting 02:00 CEST rollover as a documented
wart, which is not worth inheriting on a personal dashboard.

**NERVIS greets you when the chat opens**, through the model, so a system prompt somebody
configured is audible from the first sentence instead of the second. It opens with how the machine is
doing, and **the figures never pass through the model** — NERVIS sends them as a header the page
prints verbatim.

That was the second design, and the first one is worth recording because it looked right. It put
the reading in the prompt and told the model to quote it exactly; a 1.5B build answered *"the
machine efficiently manages four key services"*, which is neither the number nor anything anybody
measured. §18.1 had already said so — *character lives in the sentence around the reading, never
in the reading* — and asking a model to copy a number is putting it in the reading. A model may
not restate a measurement, however plainly it is told to.

The directive moved too, from the user turn into the **system** slot, after the same small model
replied *"Greet me in a dry and world-weary tone. What do you need today?"* — the instruction read
aloud. A small model treats a user turn as something to respond to and a system message as
something to be, and a greeting is entirely a question of what the model is being. A configured
persona stays in front of it, untouched, which is what makes one audible from the first sentence.

The register sits between the two §18.1 describes — it gives sarcasm to Clarvis and dryness to
NERVIS — resolved by aiming it at the *machine* rather than at the reader, which is §18.1's
actual rule. How funny it lands scales with the model and the figures do not: the 1.5B manages
"Hello. What do you need today?" and Haiku manages "Well, here we are again. What can the machine
do for you today?", above the same exact reading.

**Chat remembers which pool you chose**, in its own `localStorage` key, surviving both a reload
and a new conversation.

And it starts on a new one. `ravis/chat` prefers a **hosted** model and keeps this machine's own
underneath it, because ordinary conversation is the one workload where §9.2's soft preferences
point the wrong way: *local, cheap, already loaded* selects whatever small thing is resident,
which is the right answer for a classification call and a poor one for talking to. Preferred, not
required — a hard `locality="remote"` would remove local models from the pool and leave a machine
with every provider down refusing to answer at all.

**That needed a new ranking term, and the term order is where it went wrong.** `prefer_remote`
first read as symmetric with `prefer_local`, so it was appended in the same place — after
`prefer_fast`. The pool then picked the resident 1.5B model, because that model genuinely *is*
the quickest thing on this machine: 181 ms to first token against gpt-4o-mini's 484 ms, measured
here this afternoon. Every term was working exactly as documented, and the pool selected the one
model it exists to avoid. A pool that prefers hosted models is saying *where* before *which*, so
placement now ranks ahead of speed — while `prefer_local` stays where it was, because the pools
declaring it rank on cost first by design. Pinned by a test that gives the local model the best
latency on record and requires it to lose.

**NERVIS has a character now, and it is a setting rather than a rule.** The persona ships seeded
into §14's key/value store and shows up in the Parameters drawer as ordinary editable text. That
placement is the whole point: a house character applied silently behind whatever the user typed
is indistinguishable from a model that developed opinions, and unfindable by anyone trying to
change it. Clearing it stays cleared — absent and empty are different states, and re-seeding over
a deliberate empty string would be the same bug that started this, wearing a helpful face.

It supersedes §18.1's split for chat, which is recorded there rather than left to contradict the
code. Two clauses survive intact and are restated inside the persona itself: the facts are never
the joke, and the teasing is never at the user's expense.

**Chat parameters save as named presets**, and five ship seeded — NERVIS, Just the facts, Deep
think, Off the record, Code — so the picker is not an empty list with a Save button beside it.
None of them names a voice: voice ids are per-installation and per-account, so a shipped one
would point at nothing, and empty means *leave the voice alone*, which is the only honest default
for a machine whose voices this code has never seen.

They are held in the settings store as one value rather than in a table with four endpoints — a preset is a handful of short strings and there are never many.
A preset carries the pool, the persona, the sampling *and* the selected voice, because that
combination is what a mode actually is: "the butler" is a model, a manner and a sound, and one
that restored two of the three is the one nobody trusts. The captured key list is built from
`ADVANCED_PARAMS` rather than written out again, so a knob added there cannot be silently
dropped by the thing that saves it.

**NERVIS knows your name**, kept in §14's key/value store rather than in a browser — a name is a
fact about the person, not about the tab. It is used in the greeting, which is where it is most
visible.

**Replies are short by default**, at about two or three sentences, behind a switch in the
Parameters drawer rather than a silent rule: quietly shortening every answer is how somebody
spends an hour debugging their prompt. "Unless the question needs more" is load-bearing — a hard
cap turns a request for twelve things into a list of three.

**And a long reply is announced rather than read.** Past about 110 words the voice says "Wall of
text incoming. It is on screen." instead of spending three quarters of a minute on it. That beats
truncating, which reads a fragment and stops — sounding like a fault, and leaving the listener
unsure whether they missed the answer that is in front of them. The line varies, chosen by length
rather than at random, because a voice that says something different on a re-read sounds like a
different answer. The instruction is NERVIS's
own — the browser sends a flag, not words — and the greeting is stored nowhere: §7.2's list is
what the user said and what the model answered, and an opening line stored there would come back
as history and teach the model to follow it again. Verified live: fifteen conversations before,
fifteen after.

**The avatar moves with the conversation** — thinking, answering, speaking — and while a cloud
voice is playing its waveform is driven by the audio's real amplitude through an `AnalyserNode`,
not by a timer. The browser fallback deliberately does *not* claim the speaking state: there is
no amplitude to read there, and a waveform of nothing is §9.4's prohibited guess drawn in pixels.

**And two more that only a real key could find.** The first Fish request answered `400`, and the
refusal said nothing but `400` — because the handler logged the status and threw the body away,
on the reasoning that a credential must never travel. The body describes the *request*: the key
goes in a header nobody echoes back. With it surfaced, Fish named the fault in one line —
`reference_id must be 1..=128 chars of [A-Za-z0-9_-]` — and the stored id turned out to end in a
slash, copied from a fish.audio address exactly as this screen's own help text advises. A pasted
URL is now the expected input rather than a mistake.

The second was worse and was visible in the same response: the selected profile's id was the
literal string `new`. Creating went through `PUT /profiles/new`, `new` is a perfectly good id, and
so every voice anybody added was stored under it and replaced the one before — a list that could
never grow past one entry, presenting as a save that failed rather than as a save that succeeded
and destroyed something. Creation is `POST /profiles` now, with the id generated where the ids
are.

**Two bugs found on the way.** `scrollChatToEnd(true)` had been sitting inside `topologyMap`
after a `return`, so the transcript had never scrolled to a finished reply — the mid-stream call
kept up while text arrived and the final repaint undid it. And `nervis()` was a chain of eight
`if`s that went over the complexity ratchet the moment a ninth screen was added, with the ratchet
pointing at a dispatch table written out longhand.

### Confirmed by using it, 2026-08-27

The design arguments above were made before there was data to test them. A
session with LM Studio started mid-run tested three of them at once, and it is
worth recording because two would have been indefensible if they had gone the
other way.

**The rolling window, against a running mean.** The first two requests to
`deepseek-r1-distill-qwen-1.5b` measured a median of **9,584 ms**. That was real
— 1.89 GB coming off disk — and it was not the model's speed. Six warm requests
later the same model measured **181 ms TTFT, 196 ms latency** at n=8. A running
mean would have carried the cold start forever and kept the model out of
`ravis/fast` permanently.

**The sample floor.** At n=2 that 9.6-second median was visible on the Evidence
screen and ranked on nothing. Had it ranked, RAVIS would have concluded the
local model was hopeless on the strength of one disk read, stopped routing to
it, and therefore never re-measured it — the closing trap the floor and the
neutral-when-unmeasured rule were written to prevent, arriving on the very first
model to be measured.

**And the payoff.** `ravis/fast` now selects the local model over `gpt-4o-mini`,
181 ms against 484 ms, on evidence RAVIS gathered itself from ordinary traffic.
Not a preference flag and not a vendor's product tier: a measurement, of two
models, taken here. `local_share` went from 0% of 3 executed to **75% of 12**.

**One limitation the run exposed.** TTFT is recorded only for *streamed*
responses, so a model answered only non-streaming accumulates latency samples
and stays unrankable for speed however many it has. That is correct — TTFT of a
non-streamed response is not a measurement anyone took — but it means
`prefer_fast` sees a model only once something has streamed from it, which is
not obvious from the sample count alone.

### What is not claimed

No latency figure is fabricated for a model nobody has called. No quality score
exists for any cloud model, because none is published and none is measurable
here. Google's free tier is real and cannot be derived: it is a quota on an
account, not a property of a model. Size tiers are read from vendors' own product
naming and are labelled a **default an operator overrules**, never a measurement.

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
