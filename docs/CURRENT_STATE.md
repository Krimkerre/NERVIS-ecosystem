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
Sub-screens dispatch off `state.view`; `SIRVIS_VIEWS` and `RAVIS_VIEWS` are empty
lookup tables ready for per-screen functions.

**Avatars** — four animated SVG pages in `avatars/`, embedded into `index.html` as
base64 and rendered through `srcdoc` iframes so their stylesheets, id spaces and
scripts cannot collide. Driven by one call: `frame.contentWindow.setState(name)`.

## What is built vs stubbed

| App | Screen | State |
|---|---|---|
| NERVIS | Overview | **built** |
| NERVIS | Chat | **built** |
| NERVIS | Events | **built** |
| NERVIS | Diagnostics | **built** |
| NERVIS | Ecosystem map · Traces · Settings | **stub** — falls back to Overview |
| SIRVIS | Dashboard | **built** |
| SIRVIS | Models · Benchmarks · Runtime sets · Results · Recommendations · Downloads · System | **stub** — falls back to Dashboard |
| RAVIS | Dashboard | **built** |
| RAVIS | Routes · Pools · Providers · Policies · Evidence · Logs · Diagnostics | **stub** — falls back to Dashboard |
| CLARVIS | all seven | by design: the rail drives the editor's side panel, it does not swap pages |

**Endpoints already exist for most stubs.** `API.sirvis.jobs/downloads/machine/
recommendations` and `API.ravis.providers/pools/policies/conformance` and
`API.nervis.traces/settings` are written and unused — the screens are the missing
half, not the data.

## Where to start

1. `python3 tools/check.py` — parses the JS, balances the CSS, lists cited endpoints.
2. Open `index.html`, click the **SIMULATE** strip bottom-right. Cycling a service
   through `healthy → degraded → unreachable` should change only the surfaces that
   depend on it. That is the E-N2 gate, and it is walkable rather than asserted.
3. Pick a stub from the table. Add a function, register it in `SIRVIS_VIEWS` /
   `RAVIS_VIEWS` (or the `if` chain in `nervis()`), read from the endpoint that
   already exists.

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

## Conventions worth keeping

- Contract field names in `API`, display names only in views. Rename at the view or
  the swap to real services stops being mechanical.
- Never add a UI control without an endpoint that backs it. A UI need does not create
  an API.
- Facts stay verbatim. Both characterful surfaces — Clarvis's panel and NERVIS's chat
  — put the personality in the sentence *around* the reading, never in the reading.
