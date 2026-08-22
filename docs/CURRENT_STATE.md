# Current state

*Snapshot as of 22 Aug 2026. Written so an agent with no memory of how this got here
can be useful in five minutes. If this file and the code disagree, the code is right
and this file is stale — fix it.*

## What exists

One file, `index.html`, containing four applications' worth of UI: a top-level tab
bar (NERVIS / SIRVIS / RAVIS / CLARVIS), a per-app sidebar, and a data layer shaped
like the API responses each service will eventually return.

**Only Clarvis exists as real code** (`../clarvis`, a VS Code extension). SIRVIS,
RAVIS and NERVIS are specifications; their screens here are the first concrete thing
about them. The specs are snapshotted in `docs/spec/` — canonical copies live in
`../clarvis-ecosystem/`, so re-copy if those change.

## Architecture, in one pass

**`API`** — one method per real endpoint, each returning the exact shape its owning
spec defines, each citing the endpoint in a comment. Async already, and the views
already `await`, so swapping in `fetch` changes nothing else. See `docs/WIRING.md`.

**`SERVICES` + `usable()` + `cell()`** — the absence vocabulary. Observer-side
registry states (`healthy`, `degraded`, `unreachable`, `stale`, `incompatible`,
`unauthorized`, `stopped`, `discovering`), deliberately distinct from the
`healthy|degraded|unhealthy` a peer reports about itself. `cell(body, service)`
renders the value only if that service can answer, and otherwise names the service
and the reason.

**`prov()` / `stamp()`** — `MEASURED` / `ESTIMATED` / `UNKNOWN` and staleness, kept
visible at the point of the number rather than in a footnote.

**Views** — `nervis()`, `sirvis()`, `ravis()`, `clarvis()`, dispatched by `render()`.
Sub-screens dispatch off `state.view`: `SIRVIS_VIEWS` and `RAVIS_VIEWS` map a rail
entry to its function, and NERVIS uses the `if` chain at the top of `nervis()`. Both
tables name functions declared further down the file, which works because function
declarations hoist. A rail entry with no entry in the table falls through to that
app's dashboard rather than rendering an empty page.

**Avatars** — four animated SVG pages in `avatars/`, embedded into `index.html` as
base64 and rendered through `srcdoc` iframes so their stylesheets, id spaces and
scripts cannot collide. Driven by one call: `frame.contentWindow.setState(name)`.

## What is built vs stubbed

Every screen in the rail is built. Nothing falls back to a dashboard any more.

| App | Screens |
|---|---|
| NERVIS | Overview · Chat · Ecosystem map · Events · Traces · **System** · Diagnostics · Settings |
| SIRVIS | Dashboard · Models · **Discover** · Benchmarks · Runtime sets · Results · Recommendations · Downloads |
| RAVIS | Dashboard · Routes · Pools · Providers · Policies · Evidence · Logs · Diagnostics |
| CLARVIS | all seven — by design the rail drives the editor's side panel, it does not swap pages |

Two of those moved or are new since the first pass:

- **System lives on NERVIS**, not SIRVIS. It renders SIRVIS's machine record, so
  every cell on it is `cell(…,'sirvis')`: NERVIS hosts the page and does not own
  the figure. Cycle SIRVIS off and the page says so rather than quietly describing
  the host it happens to be running on.
- **Discover** is SIRVIS's model browser — `GET /api/v1/catalog`, per SIRVIS.md §8's
  Installed / Discover / Downloads split. Search and the two filter groups repaint
  only the result rows, because re-running `render()` per keystroke would rebuild
  `#content` and take the caret with it. In the real build those three values are
  query parameters on the endpoint.

The wordmark top-left is a home link back to the NERVIS overview, keyboard
reachable, and it leaves focus mode on the way.

## The data is this machine

`LMSTUDIO_SNAPSHOT` is the verbatim body of `GET http://127.0.0.1:1234/api/v0/models`
from the LM Studio server on this machine — 11 local builds and an embedding model,
with their real architectures, quantisations, context ceilings and `tool_use`
capability. `lmstudioBuilds()` maps that payload onto SIRVIS's build identity, and
`LMSTUDIO_SIZES` / `LMSTUDIO_PARAMS` carry what `lms ls` reports and the models
endpoint does not.

It is embedded rather than fetched because this file opens from disk with no
server, and a runtime fetch would make the template depend on LM Studio being up
to render at all. Keeping the payload in its own shape is what makes the swap
real: the mapper an implementation needs already exists and has been exercised
against a genuine response.

Consequences worth knowing:

- **Machine is a MacBook Air M5** — M5, 10 CPU / 10 GPU, 24 GB, macOS 27.0, 501 GB
  free, LM Studio 0.4.21 / llama.cpp 2.29.1 / MLX 1.11.0, all read from this
  machine. Memory figures elsewhere are sized against 24 GB, not the 64 GB the
  first draft assumed.
- **`tool_use` is load-bearing.** Eight of eleven builds advertise it. The three
  that do not — both qwen2.5-coder builds and phi-4-mini — are excluded from
  `ravis/clarvis-agent` by the pool invariant, and two of them are the largest
  coding builds installed. That exclusion is a real capability record, not a
  staged one, and it is the clearest argument in the template for probing
  capabilities instead of trusting model names.
- **Pool membership is derived**, not written down: `API.ravis.pools()` filters the
  real build list by each pool's own invariant. Install a build with tool_use
  tomorrow and the agent pool grows without anyone editing a number.
- **Nothing is empty.** Every pool on this machine has a member, so `NO_ROUTE`
  comes from a request-level constraint (vision at 300K under LOCAL_ONLY) rather
  than from an unavailable pool. The screens handle both — the "unavailable pools"
  cards render their own absence.
- **Six providers**, four of them cloud: LM Studio (answering here), Ollama
  (installed, daemon not listening), Anthropic, OpenAI, Google Gemini, OpenRouter.
  Each record carries origin, chat path, protocol mode and auth scheme, plus a
  `secret_ref` naming where the key lives — never a value. That table is the
  wiring, not a decoration of it.

## The evidence is measured, too

`BENCHMARK` and `BENCHMARK_PAIRS` carry a real suite run — `../clarvis/docs/benchmarks.md`,
20 Aug 2026, this machine. Ten of the eleven installed builds went through it: each
loaded alone with `--parallel 1`, warmed twice, five scenes three times, medians
reported. Ten measured records, one honest `UNKNOWN` for the build nobody has run.

**The most useful thing in it is a disagreement.** LM Studio's `tool_use`
capability flag and measured tool-call reliability disagree about four builds, in
both directions:

| build | advertised | measured |
|---|---|---|
| `qwen2.5-coder-7b-instruct` | none | **8/8** |
| `qwen2.5-coder-14b-instruct-mlx` | none | 3/3 |
| `phi-4-mini-instruct` | none | 3/3 |
| `granite-4.0-h-tiny` · MLX | `tool_use` | **1/8** |

So `toolsWork(build)` takes the measurement over the claim in both directions, and
`API.ravis.pools()` filters on it. Trusting the flag would have excluded the
recommended model outright and admitted one whose calls the MLX runtime discards —
the same model in its GGUF packaging gets 8 of 8, which is also the sharpest
possible argument for identity being family + variant + **runtime**, not a name.

Other things the real data changed:

- **No score anywhere in SIRVIS.** Code correctness came back 6/6 for every build
  including the 1.8 GB one, and tool calls were clean for everything that reached
  the table. Averaging measures with no variance manufactures a difference the run
  did not find, so evidence records carry rate, first token, tool reliability and
  follow-up behaviour instead. RAVIS computes its own ranking from those and owns
  it — which is what makes a bad route diagnosable as either a bad measurement or
  a bad weighting.
- **The memory budget is ~14 GB, not 24.** The system sat at 7.6 GB with an
  ordinary working set open before any model loaded, and every fit decision is
  against that. It is also fanless, which is why a build that finishes fast beats
  one that scores better and grinds.
- **Co-residency discriminated where nothing else did.** Memory behaved exactly as
  arithmetic predicted across five measured pairings, up to 12.9 GB. Behaviour did
  not: `ministral-8b` recovers from a failed tool call alone and stopped calling
  the tool in both pairings it appeared in — `no-call` at 8.8 GB, `gave-up` at
  12.9 GB. Nothing in its solo record predicts it.
- **The staged scenario is the benchmark's own recommendation**: one build,
  `qwen2.5-coder-7b-instruct` at 4.3 GB, serving both roles.

Still not real: cloud provider health and credential presence, spend, and the
event/trace fixtures. Those are marked as staged in the code comments.

## Two things that are not load-bearing

**The automated tour is gone.** Every screen is reachable by clicking now, so a
scene rotation that moved the page underneath a reader was only ever fighting
them. `scenes`, `nextScene`, `runTour`, the bottom-left controls and the
`manual` / `demo` / `scene` flags on `state` all went with it — `state` is down to
`{app, view}`. The **SIMULATE** strip stays: that one is the absence gate, and it
is the point.

**Miku mode** is the easter egg, on the Chat screen's profile row behind a
divider. It is presentation only and the code is built to keep it that way: no
API call, no `DEMO_API` mutation, no event, no route, nothing recorded. That
matters here specifically — RAVIS publishes its routing profiles and NERVIS may
not invent one, so an "AGI" chip sitting *inside* the profile group would be the
exact unbacked control the ecosystem rule forbids. It sits outside the group, it
is labelled as not a profile, and every surface it touches is restored by a plain
`render()`.

Two details worth keeping if you edit it:

- **It reverts by re-rendering, not by snapshotting.** `chatView()` rebuilds the
  heading, avatar frame, profile row and transcript from the API, so there is no
  saved copy of the page to drift stale.
- **It is abort-safe, and that was a real bug first.** Navigating away mid-scene
  detaches the node being typed into; the interval then threw on every tick,
  which never surfaced and never resolved, leaving the sequence hung with its
  button disabled until a reload. `typeInto` now stops on `!el.isConnected`, the
  loop checks the same before each line, and the whole sequence sits in a
  `try/finally` that restores the theme, the overlay and the running flag on
  every exit path.

The avatar is the fifth entry in `AVATAR_SOURCE_B64` and needs no special
handling: it is the same contract as the other four — a self-contained page
exposing `setState(name)`, embedded as base64 and rendered through a `srcdoc`
iframe.

## Where to start

1. `python3 tools/check.py` — parses the JS, balances the CSS, lists cited endpoints.
2. Open `index.html`, click the **SIMULATE** strip bottom-right. Cycling a service
   through `healthy → degraded → unreachable` should change only the surfaces that
   depend on it. That is the E-N2 gate, and it is walkable rather than asserted.
3. To add a screen: write the function, register it in `SIRVIS_VIEWS` /
   `RAVIS_VIEWS` (or the `if` chain in `nervis()`), and read from an endpoint that
   already exists. If the endpoint does not exist, check `docs/spec/` for the
   canonical one before writing a line of UI — and if the spec does not define it,
   stop and say so.

## Things that have actually gone wrong here

Recorded because each one is silent in a browser and cost real time:

- **A CSS brace imbalance discards the rule that follows it.** Two stray `}` once ate
  a whole rule and the avatar vanished with no error anywhere. `tools/check.py`
  catches this.
- **`replace()` without a count.** Python's `str.replace` replaces *every*
  occurrence; one such edit injected a stylesheet into a JS string literal and broke
  the page. Always pass a count.
- **Slicing on `function render()`** matched inside `async function render()` and
  silently removed the `async` keyword.
- **A bulk region replacement deleted `chatView()`** because it happened to sit
  between the two functions being replaced. Clicking Chat threw for two turns before
  anyone noticed. Re-test the screens either side of a region edit.
- **Grid items default to `min-width:auto`.** One long line of code widened a `1fr`
  track and pushed a sibling panel outside its own container.
- **A region replace built from two `h.index()` calls, where the end marker also
  occurred earlier in the file.** `h[:start] + new + h[end:]` with `end < start`
  re-emitted everything between them, so `downloads`, `machine`, `recommendations`
  and `runtimeSet` appeared three times in the `API` object. It parsed, `check.py`
  passed, and the *last* duplicate won — which was the stale copy, so the screens
  kept rendering the old fixture while the new one sat above it doing nothing.
  Assert `start < end` before slicing, and grep for duplicate method names after.
- **Inline positions fought `.node:nth-of-type` rules.** The Ecosystem map sets
  each node's `left`/`top` inline, but the Overview map's rules still matched and
  contributed `right`/`bottom`; with both edges set, the boxes stretched to 754px
  wide. Inline `right:auto;bottom:auto` fixes it. Nothing errored — the map just
  looked wrong.
- **Every `.table-row` is its own grid**, so `min-width:auto` let one long
  identifier widen a track and knock that row out of line with the row above it.
  Now fixed globally with `min-width:0` plus `overflow-wrap:anywhere`, so a long
  capability string wraps instead of eliding or shoving its neighbours.

## Conventions worth keeping

- Contract field names in `API`, display names only in views. Rename at the view or
  the swap to real services stops being mechanical.
- Never add a UI control without an endpoint that backs it. A UI need does not create
  an API.
- Facts stay verbatim. Both characterful surfaces — Clarvis's panel and NERVIS's chat
  — put the personality in the sentence *around* the reading, never in the reading.
