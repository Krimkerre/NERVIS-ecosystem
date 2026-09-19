# RAVIS

A local-first AI routing gateway with an OpenAI-compatible API. Everything in the ecosystem that
wants a model — NERVIS chat, Clarvis's chat and agent — asks RAVIS, and RAVIS decides which model
serves the request, local or hosted, and records why.

Runs on macOS and Linux. On Windows the ecosystem installs inside WSL 2, where RAVIS runs as it
does on Linux.

**Specification:** [`../RAVIS.md`](../RAVIS.md) · **Cross-product contracts:**
[`../ECOSYSTEM_RUNBOOK.md`](../ECOSYSTEM_RUNBOOK.md), which wins wherever the two disagree about
anything that crosses a product boundary · **Status:** [`../STATUS.md`](../STATUS.md) ·
**Release notes:** [`../RELEASES.md`](../RELEASES.md)

## What it does

- **An OpenAI-compatible surface.** `/v1/models` and `/v1/chat/completions`, streaming and
  cancellation included, so a client already pointed at OpenAI or OpenRouter can point here
  unchanged. `/v1/embeddings` forwards to one configured local runtime (Ollama's
  `nomic-embed-text` by default). `/v1/responses` is translated onto the chat path, and refuses by
  name what it cannot carry: streaming, server-stored responses and provider-run tools.
  `ravis conformance clarvis` runs 24 wire-level checks, reading recorded streams with a port of
  Clarvis's own reader; a release that claims Clarvis compatibility must pass it (`../RAVIS.md`
  §8.9).
- **Routing.** A caller names a model, which is honoured as given, or one of 18 pools:
  `ravis/auto`, `chat`, `balanced`, `fast`, `performance`, `cheap`, `free-api`, `local`, `api`,
  `private`, `coding`, `reasoning`, `draw`, `vision`, `long-context`, `agent`, `clarvis-chat` and
  `clarvis-agent`. For a pool, hard constraints come first — the pool's own rules, what the request
  needs (context length, tools, images) and the calling application's policy — then health
  (circuit breakers per model and per provider), then ranking, then a fallback chain that records
  every attempt. `ravis/local` and `ravis/private` admit only models on this machine (and, by the
  owner's decision, those LM Studio reaches on the owner's other machine through LM Link);
  `ravis/free-api` holds only hosted models that cost nothing, and refuses rather than fall back to
  a paid one.
- **Route explanations.** Every decision is recorded with what was considered, what was excluded
  and why, and what the request needed — never the text of a message. Decisions are kept for
  30 days and can be replayed against today's catalogue, prices and health to see whether the
  answer would change.
- **Providers.** LM Studio and Ollama locally. Anthropic and Google Gemini through adapters that
  translate the wire format. OpenAI, OpenRouter, DeepSeek, xAI and any other OpenAI-compatible
  endpoint forwarded unchanged. Several upstreams at once, each addressable directly as
  `ravis/<name>/<model>`. A hosted provider with no key yet is known but not contacted until one
  is saved, and saving one brings its models in without a restart.
- **Credentials.** Provider keys are kept in the platform keyring (the macOS Keychain, or the
  Secret Service on Linux), or, where there is none, in a `0600` file in `~/.config/ravis`, which is
  plaintext and says so. A key in an environment variable still works. Only names are ever listed.
  Management writes need an `admin.` credential; being on the same machine is not permission.
- **What each model can do.** A claim about a model (tool use, vision, context window) carries its
  provenance, and a stronger one replaces a weaker one: a catalogue's flag, then RAVIS's own trial
  of a hosted model's tool use, then SIRVIS's measurement, then the operator's declaration. A
  context window is the one the runtime actually serves, not the one the architecture allows.
- **Cost and budgets.** Every call's usage, with an estimated cost from published prices; a call
  with no known price is counted as unpriced, never as free. Budgets are daily, weekly or monthly,
  over all spending, one application or one provider, set from NERVIS or from `RAVIS_BUDGET_*`.
  Nearing a budget makes routing lean cheaper; a hard budget at 100 % blocks paid providers (or,
  for a provider budget, that provider's models).
- **Sessions.** A conversation stays on one model for consistency and prompt caching, and sessions
  are kept apart per application.
- **Codex.** `ravis/clarvis-codex` runs OpenAI's Codex on the owner's ChatGPT plan as an engine for
  Clarvis's agent role. RAVIS supervises one Codex process in a Codex home of its own, never the
  ChatGPT app's. It runs only a build whose origin it can check — Homebrew's, signed by OpenAI's
  Apple team, on macOS; Arch's `openai-codex` package, or OpenAI's npm build installed by
  `install.sh` with its registry signature and provenance, on Linux — and only a build it has
  tested (`src/ravis/codex/tested_runtimes.json`) or the owner has accepted in NERVIS. Clarvis
  drives tasks through `/api/v1/agent-sessions`; the sign-in, the plan's remaining allowance, the
  sites Codex may reach and each task's Stop are on NERVIS's RAVIS screens.
- **Skills.** Folders with a `SKILL.md` — from NERVIS's skills folder, the owner's personal skills
  and Codex's built-in ones — switched on or off separately for Codex and for the other models
  (Clarvis's own engine and NERVIS chat). New ones can be installed from a GitHub folder, a zip or a
  website's skills index, always after a review, and arrive switched off. Remove moves a skill's
  folder to `~/.Trash` rather than deleting it.
- **Events.** Route selected, refused and completed, and a model resting after refusing tools, are
  published to NERVIS's event hub under the request's trace id.

The management API is under `/api/v1/`: health, providers, models, pools, route decisions,
sessions, usage, budgets, policies, evidence, observations, Codex and skills. The ecosystem
protocol's `/ecosystem/*` surface is served beside it, and `/ecosystem/capabilities` says what is
`available` or `degraded` and why. As of 19 September 2026 three are degraded: the management API
(some planned writes, and `Idempotency-Key`, are not built), embeddings (one local runtime, no
fallback) and `/v1/responses` (no conformance suite yet).

## Running it

Normally RAVIS starts with the rest of the stack, from the menu bar app, the tray or
`python3 tools/run.py start` at the repository root, on <http://127.0.0.1:8731>. The launcher
also tells it where LM Studio, Ollama, SIRVIS and NERVIS are, and declares every provider.

To run it on its own, from this directory:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ../protocol -e ".[dev]"
.venv/bin/ravis doctor                 # configuration and the resolved routing table; contacts nothing
.venv/bin/ravis serve                  # http://127.0.0.1:8731
.venv/bin/ravis preflight clarvis      # would Clarvis pointed here work right now?
.venv/bin/ravis credential NAME < file # store one credential, read from stdin
.venv/bin/ravis restore-database --version N
```

`ecosystem-protocol` is a dependency in `pyproject.toml`, but it lives in `../protocol` and not on
PyPI, so pip can only find it when that path is installed too, as above. `python3 tools/run.py
setup` at the repository root builds the same environment with all four packages.
`ravis codex calibrate` is a development tool, run with the owner present.

Configuration is environment variables with a `RAVIS_` prefix. The ones most often changed:

| Variable | What it is |
|---|---|
| `RAVIS_UPSTREAMS` | Every upstream, as a JSON list of `{name, base_url, kind}`. Replaces `RAVIS_UPSTREAM_BASE_URL`, which declares a single one |
| `RAVIS_SIRVIS_BASE_URL` | Where SIRVIS's evidence is read from |
| `RAVIS_BUDGET_LIMIT`, `_PERIOD`, `_CURRENCY`, `_HARD` | The one budget set in configuration; the rest are set from NERVIS |
| `RAVIS_MODEL_CAPABILITIES_PATH`, `RAVIS_MODEL_CAPABILITIES` | The operator's declarations about models, as a file or inline |
| `RAVIS_OLLAMA_DEFAULT_CONTEXT`, `RAVIS_LMSTUDIO_DEFAULT_CONTEXT` | The window a model that is not loaded yet will be opened with |
| `RAVIS_ALLOWED_ORIGINS` | Browser origins allowed to call RAVIS; `http://127.0.0.1:8790` and `http://localhost:8790` by default |
| `RAVIS_CODEX_ENABLED` | Unset means "if Codex is installed" |

[`src/ravis/config.py`](src/ravis/config.py) is the full list, with the reason for each default.

## Telling RAVIS what a model can do

**The order, weakest first:** a default, a catalogue's advertisement, RAVIS's own observation, a
SIRVIS measurement, and an operator's declaration. The operator is last on purpose: they can know
something about their own deployment that RAVIS cannot observe.

- **A catalogue.** LM Studio publishes tool support for most local builds, and those claims enter
  as advertised. A generic endpoint that publishes nothing reads `UNKNOWN`, and a pool that
  requires the capability fails closed.
- **RAVIS's own trial**, for hosted models whose providers publish no tool support. RAVIS sends one
  small request that must call a tool, a few per pass and at most 40 a day, and keeps the result a
  month. It never tries a local model, since that would load one. `RAVIS_CAPABILITY_TRIALS=false`
  switches it off.
- **SIRVIS.** Measured tool-call trials outrank the catalogue. That matters: LM Studio advertises
  tool use for both packagings of `granite-4.0-h-tiny`, and SIRVIS measured the GGUF build passing
  every trial and the MLX build almost none. Build identity is load-bearing, so builds are compared
  by full `publisher/model`, never by the bare name.
- **The operator.** [`operator-capabilities.json`](operator-capabilities.json) is what the
  launcher loads: Anthropic's catalogue publishes no tool support, so the owner declared the two
  Claude models Clarvis uses. `RAVIS_MODEL_CAPABILITIES` overrides the file per model. A named file
  that cannot be read stops RAVIS from starting.
  [`measured-capabilities.json`](measured-capabilities.json) is an older example, not loaded by the
  launcher: tool-call trials that Clarvis's own harness ran on the owner's Mac, which can be passed
  to `ravis preflight clarvis` through `RAVIS_MODEL_CAPABILITIES_PATH`. Either file enters at the
  operator's rank, so it outranks SIRVIS for the models it names.

**A context window is what the runtime will actually serve.** Ollama's `/api/show` reports what the
architecture supports, and it serves a model at whatever it loaded it with. So for a loaded model
RAVIS reads the true window (`/api/ps` for Ollama, the loaded length for LM Studio), and for one
that is not loaded it uses `RAVIS_OLLAMA_DEFAULT_CONTEXT` or `RAVIS_LMSTUDIO_DEFAULT_CONTEXT`. The
launcher keeps both in step: it starts Ollama with `OLLAMA_CONTEXT_LENGTH` (32,768 unless set) and
passes the same number on, and it reads LM Studio's own default from LM Studio's settings at every
start. A capability file that declares a window replaces the runtime's figure outright, so a test
fails if a shipped file declares one without saying who measured or chose it.

## Security

- **Loopback only.** A non-loopback bind is refused at startup, and a request whose `Host` names
  anything but loopback is refused, which defends against DNS rebinding.
- **Browsers are checked by origin.** A request with an `Origin` that is not in
  `RAVIS_ALLOWED_ORIGINS` is refused, and such an origin gets no CORS headers. An allowed origin may
  use `GET`, `HEAD`, `OPTIONS`, `POST` (for chat), `PUT` and `DELETE`. A state-changing request sent
  with a form's content type is refused, so it cannot slip past the preflight. `Origin: null`,
  which every page opened from disk sends, can be allowed, and `ravis doctor` warns when it is.
- **Identity.** A caller presenting a credential stored as `client.<application>` is that
  application (`clarvis`, `nervis`), and per-application policy keys on it. A caller with no
  credential, or an unrecognised one, is `anonymous` rather than refused, so `/v1/models` answers
  everyone. `ravis credential` is how the first `admin.` credential gets in, since the HTTP route
  for storing one already needs one; the launcher does this for you.
- **Admission control.** Request-size, image-count and per-minute rate limits (lower for anonymous
  callers) apply before routing. The body-size limit is a plain ASGI wrapper rather than framework
  middleware because middleware runs in reverse order of registration;
  `tests/test_body_limit_ordering.py` asserts where it sits.
- **Absence is a value.** `anonymous` is an identity, `UNKNOWN` is a provenance, and an empty
  provider table means none is configured. None of these may stand in for "empty" or for
  "failure" (runbook §14.4).

## Checking it

```bash
.venv/bin/ruff check src tests        # lint, imports, naming, complexity <= 8
.venv/bin/mypy                        # strict types
.venv/bin/pytest                      # no test touches a network or a live service
.venv/bin/ravis conformance clarvis   # the wire-contract gate
```

The conformance suite reads each recorded stream twice, once straight and once after it has passed
through the real application, using a faithful port of Clarvis's own reader, and requires the two
readings to match. The fixture is the oracle, so RAVIS is never certified against its own idea of
the protocol. It runs in-process in well under a second.

There is no CI: GitHub Actions is switched off on this repository.
`../tools/check_clean_clone.sh` is the full gate. It clones from GitHub, builds an environment from
the packages' own metadata, and runs these checks for every package, plus the repository's own.

## History

RAVIS was the first of this repository's services to be built (23 August 2026), because proxying
Clarvis's stream without breaking its tool calls was the riskiest unknown in the plan
(`../ECOSYSTEM_RUNBOOK.md` §6.1). After that: the Clarvis wire contract and conformance suite, the
Anthropic and Gemini adapters, several upstreams at once, credentials and admission control,
SIRVIS evidence, sessions, cost, the management API, events, the free pool, Codex (from 13 September
2026, on Linux too from 18 September) and skills. The milestone table, with each row's state, is in
`../RAVIS.md`; the release candidates `ecosystem-rc1` (18 September 2026) and `ecosystem-rc2`
(19 September 2026) are in `../RELEASES.md`.
