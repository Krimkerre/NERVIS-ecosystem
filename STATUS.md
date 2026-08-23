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
cd ravis && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check src tests        # lint, imports, naming, complexity ≤ 8
.venv/bin/mypy                        # strict types
.venv/bin/pytest                      # 250 tests, no network, no live service
.venv/bin/ravis conformance clarvis   # the §8.9 release gate — 16 checks
```

Expected: all clean, 250 passing, conformance `PASS`. CI runs the same four on
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
| 10 | **M9 groundwork** | `ravis preflight clarvis`, and CORS on the read surface so a browser dashboard can reach it. Not M9 itself — that needs a real editor pointed at a running gateway |

Stages 0, 1 and 2 are complete. Stage 3 is in progress.

### Next — in this order

| # | Milestone | Why here |
|---|---|---|
| 11 | **M9** | **Live Clarvis ↔ RAVIS.** Point unmodified Clarvis at RAVIS, configuration only. No Clarvis source change is permitted to make this pass. Everything RAVIS owes it is built; what remains needs a running LM Studio, a running gateway and VS Code — `ravis preflight clarvis` is the ten seconds that tells you whether to bother |

Reaching M9 completes Stage 3 and is the first genuinely useful release
(`ECOSYSTEM_RUNBOOK.md` §12). Everything Stage 3's exit criteria name is now
built except the live integration itself: chat and agent resolve independently,
the agent pool refuses a non-tool model, and fallback does not corrupt the
stream — each asserted by `ravis conformance clarvis` rather than by prose.

### After that

Stage 5 is RAVIS intelligence: **M3b** (translated execution path), **M4**
(Anthropic native), **M7** (Google, OpenRouter), **M8** (LM Studio, Ollama,
generic), **M13** (SIRVIS evidence), the rest of **M14**, **M16** (policy).
Stage 4 — the whole of SIRVIS — may run in parallel from Stage 1 onward and must
finish before Stage 5.

**Do not start M3b or M4 before M9.** Both build a second execution path, and
§20.2 says translation comes only after the transparent Clarvis slice works. The
reason is not ceremony: the transparent path is the control, and its correctness
is not yet proven by anything except fixtures.

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
ravis/                 the only service with code (M0–M18a, and M12)
```

Clarvis lives in its own repository (`../clarvis`) — different language, runtime
and release cadence, and `clarvis/plan.md` is normative for it.
