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
.venv/bin/pytest                      # 173 tests, no network, no live service
.venv/bin/ravis conformance clarvis   # the §8.9 release gate — 12 checks
```

Expected: all clean, 173 passing, conformance `PASS`. CI runs the same four on
every push (`.github/workflows/checks.yml`), plus `template/tools/check.py`.

See it actually work, against a real model:

```bash
RAVIS_UPSTREAM_BASE_URL=http://127.0.0.1:1234 .venv/bin/ravis doctor
RAVIS_UPSTREAM_BASE_URL=http://127.0.0.1:1234 .venv/bin/ravis serve
curl -s localhost:8731/api/v1/pools | python3 -m json.tool | head -30
```

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
| 3 | **M2** | Clarvis wire-contract suite — `ravis conformance clarvis`, 12 checks |
| 4 | **M3a** | `ProviderAdapter` protocols, normalized request/response/stream shapes, capability discovery |
| 5 | **M5** | The 13 virtual pools, route resolution, route explanations |
| 6 | **M14** *(observation half)* | Residency preference, memory-pressure route change |
| 7 | **M6** | Request-derived hard constraints — tools, vision, context, schema, streaming |
| 8 | **M18a** | Read-only management API — pools, models, providers, profiles, policies, route decisions, usage |

Stages 0, 1 and 2 are complete. Stage 3 is in progress.

### Next — in this order

| # | Milestone | Why here |
|---|---|---|
| 9 | **M12** | Health, retries, fallback, circuit breaker. Stage 3's exit requires that fallback never corrupts a stream and that cancellation never triggers one |
| 10 | **M9** | **Live Clarvis ↔ RAVIS.** Point unmodified Clarvis at RAVIS, configuration only. No Clarvis source change is permitted to make this pass |

Reaching M9 completes Stage 3 and is the first genuinely useful release
(`ECOSYSTEM_RUNBOOK.md` §12).

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

---

## Map of the repository

```text
ECOSYSTEM_RUNBOOK.md   cross-product authority — protocol, build order, gates, §14 engineering standards
RAVIS.md SIRVIS.md     per-product build plans; the runbook wins on anything crossing a boundary
NERVIS.md CLARVIS.md
ECOSYSTEM_OVERVIEW.md  conceptual, no contracts
template/              the prototype — every screen, wired to mocks shaped like the real responses
ravis/                 the only service with code (M0–M18a)
```

Clarvis lives in its own repository (`../clarvis`) — different language, runtime
and release cadence, and `clarvis/plan.md` is normative for it.
