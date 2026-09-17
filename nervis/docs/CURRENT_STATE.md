# Current state

**This is a snapshot, not the current state — noted 12 September 2026.** It was last
rewritten on 28 August 2026. A few facts below were corrected on 12 September and marked
where they were; the rest was not re-checked. What is built now is in the milestone tables
of `../NERVIS.md` §21, `../RAVIS.md`, `../SIRVIS.md` and `../CLARVIS.md`, and in
`../STATUS.md`.

*Snapshot as of 28 Aug 2026. Written so an agent with no memory of how this got here
can be useful in five minutes. If this file and the code disagree, the code is right
and this file is stale — fix it.*

*Since the 26 Aug snapshot: the voice stack (§18.2's credential gate, personas, the
chat surface around them), RAVIS M7 and M16, and NERVIS's conversation titles as
RAVIS background calls. The audit that dated this file also found two capabilities
citing limits that had been lifted — see `nervis/README.md`.*

*It was stale, and by a lot: it said "NERVIS is still a specification" three days
after NERVIS became a running service with six milestones behind it. Nothing catches
that — `tools/check_status.py` checks STATUS.md's counts and paths, and the dead-code
gate reads definitions. A prose claim about the state of the world has no gate.*

## What exists

One file, `index.html`, containing four applications' worth of UI: a top-level tab
bar (NERVIS / SIRVIS / RAVIS / CLARVIS), a per-app sidebar, and a data layer shaped
like the API responses each service will eventually return.

**All four exist as real code.** Clarvis is a VS Code extension in its own
repository (`../../clarvis`); SIRVIS, RAVIS and — since M0 — NERVIS are Python
services, and this file is **served by `nervis serve`** rather than opened from
disk. Most screens read a running service.

That changes what this file is. It is no longer "the prototype"; it is NERVIS's
frontend, and the data layer below is a client of NERVIS rather than of every
service at once. `../STATUS.md` says what is actually finished.

**The one rule that has not changed**: it must still render with nothing running.
`tools/render_check.js` enforces that across every screen in `APP_CONFIG`, when somebody
runs it (`node tools/render_check.js`). **It does not run in CI** — corrected 12 September
2026: GitHub Actions is switched off for this repository, so the workflow file in
`.github/workflows/` no longer runs anything.

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

**`SERVICES` is now the *fallback*, not the source.** NERVIS's registry
(`/api/v1/services`) is polled every eight seconds and overwrites it. Three separate
defects have come from a screen mixing a live read with this map — a `usable(key)`
on a key the map has never held, a `SERVICES[row.key].state` on a row whose key was
new, a healthy service wearing a warning chip. **If a row carries its own state,
read that.**

**`peerRead(service, surface, params, method)`** — every read of RAVIS or SIRVIS goes
through NERVIS, which negotiates the capability first. See `docs/WIRING.md`; the
direct-`fetch` pattern that used to live there is the pre-M3 one.

**`EVENT_FILTERS` + the hub** — the Events screen is a live feed of
`/api/v1/events` since M6, with §11.2's filters held across renders.

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
| NERVIS | Overview · Chat · Files · Notifications · Ecosystem map · Events · Traces · Diagnostics · **System** · Settings |
| SIRVIS | Dashboard · Models · Runtime · **Discover** · Benchmarks · Runtime sets · Results · Recommendations · Downloads |
| RAVIS | Dashboard · Routes · Sessions · Spending · Pools · Providers · Credentials · Policies · Evidence · Logs · Diagnostics · **Settings** |
| CLARVIS | all seven — by design the rail drives the editor's side panel, it does not swap pages |

Files, Notifications, Runtime, Sessions, Spending and Credentials were missing from this table
until 12 September 2026; the list above is copied from `APP_CONFIG` in `index.html`, which is
the authority if the two ever disagree again.

**Most of them now read a live service**, and the count is deliberately not
repeated here. `STATUS.md` carries the live/partly-live/invented table and is
checked against the repository; a second copy in this file went stale at
"three of them" and stayed there while the real number passed twenty. **One
table, in STATUS.md.**

What this page can say without going stale: a screen that reads a service says
so on screen, an invented card is faded and labelled `PROTOTYPE`, and a screen
that mixes the two fades only the invented half. Where a value cannot be read at
all — RAVIS's routing timings (§9.8) — the screen reports the effect it *can*
observe and names what would publish the value. **Corrected 12 September 2026:**
this list also named SIRVIS's benchmark job queue (M14) and the peers' event
streams (RAVIS M18b, SIRVIS M21). All three have shipped since — the Benchmarks
screen reads SIRVIS's queue, and SIRVIS and RAVIS both publish their events.

**That badge is only as good as the flag behind it, and for a long time it was
not good at all.** `live()` performs every read on this page; it knew whether
the service had answered and returned the payload without saying so, leaving a
global that the next fetch overwrote as the only witness. Sixty-eight cards
hardcoded their CSS class in consequence, unable to report provenance whatever
their data did — so live data rendered faded, and mock data rendered live. The
helper now attaches the answer to the object it answered, and three checks hold
the line: `liveness_check.js` ratchets the number of cards that *cannot* report,
and `honesty_check.js` renders every screen twice and compares. See PITFALLS §7.

Two screens moved or are new since the first pass:

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

## Everyday and advanced, per application (17 September 2026)

Each of NERVIS, RAVIS and SIRVIS shows a shorter menu until its own **Advanced
controls** switch is pressed — at the foot of that application's menu, and
repeated as a row in its settings screen where it has one. Clarvis is untouched:
the editor is somewhere to work, and every tab stays on show in both modes.

- The preference is one settings key per application — `ui.nervis_advanced`,
  `ui.ravis_advanced`, `ui.sirvis_advanced` — through the store the page already
  uses, missing meaning off, and a refused write puts the switch back.
- `APP_CONFIG.nav` stays the **complete** list: it is what `resolveRoute`
  validates, so every address still names a screen. `EVERYDAY_NAV` is the shorter
  list, and `visibleNav(app)` picks between them.
- A technical screen opened by its address still opens, under a **Technical
  details** banner, and does not switch the application into advanced mode
  (`technicalScreen`). Remembered navigation alone will not reopen one
  (`everydayLanding`).
- Everyday menus: NERVIS keeps Overview, Chat, Files, Notifications, Skills and
  Settings; RAVIS keeps Dashboard, Pools, Providers, Credentials, Spending and
  Settings; SIRVIS keeps everything but Runtime sets — testing models is what
  SIRVIS is for, so benchmarks and results are not advanced.

**Stage two has begun with NERVIS's Overview** (0.34.26). Everyday shows the
services tile, Codex, *What you can do now* — Chat, Files, Notifications and the
editor, each answered from the service it depends on (`EVERYDAY_NEEDS`,
`everydayReady`) — recent events, and a line saying where the rest is. Advanced
adds the throughput tiles, the local share, the service registry, the map, the
Clarvis instances, the trace and the diagnostics card. The event card is shared by
both modes (`overviewEventsCard`): the flood guard's notes and a refused sender are
blocked work and a refused permission, which no preference hides.

**RAVIS's dashboard followed** (0.34.27): a *What you can use* card built from the
page's own provider read (`providerReady`, `providerStopper` — a provider switched
off is a choice, a refused key is a fault, and they are worded differently), with the
decision, funnel, attempts and recent-decision cards behind `ui.ravis_advanced`. The
four headline tiles stayed: `tools/codex_check.js` records them as the owner's layout.

Still to do: RAVIS's Pools, Spending and Settings contents, SIRVIS's screens, then
stage three's plain-language explanations with technical detail folded underneath.

## The only mutating controls on the page

RAVIS.md §15.1 defines five mutations. **None of them is built**, and all five
render as disabled buttons naming the endpoint they are waiting for — this
section used to call them "the only controls in this repository that change
anything", which had it exactly backwards:

```text
POST /api/v1/profiles/{profile_id}/activate
POST /api/v1/providers/{provider_id}/enable
POST /api/v1/providers/{provider_id}/disable
POST /api/v1/evidence/refresh
POST /api/v1/route-tests
```

The controls that *do* mutate are elsewhere, and arrived with milestones §15.1
does not cover:

```text
POST   {sirvis}/api/v1/runtime/sessions              take a lease on a model
DELETE {sirvis}/api/v1/runtime/sessions/{id}         release it
POST   {sirvis}/api/v1/runtime/sessions/{id}/renew   extend it
PUT    {ravis}/api/v1/providers/credentials/{name}   store a provider credential
DELETE {ravis}/api/v1/providers/credentials/{name}   remove one
PUT    {ravis}/api/v1/providers/{name}/enabled       enable or disable a provider
PUT    {ravis}/api/v1/providers/{name}/models        narrow which models it offers
POST   {ravis}/v1/chat/completions                   the chat screen, as a client
```

The SIRVIS trio needs a runtime-scoped token (§4.5) and the RAVIS writes need an
allow-listed origin. Everything else on the Settings page — the serving surface, the budget
ladder, the privacy level, credentials — is real configuration with no agreed
endpoint, so it renders as state and says so. A settings screen is exactly where
"a UI need does not create an API" is most tempting to break, because every row
on it looks like it wants to be editable.

**Activating a profile is not the same control as the chips on NERVIS's chat
screen**, and they are deliberately styled differently. The chat chips set a
profile for *one conversation*, as a parameter on that request; nothing is stored
and no other client is affected. Activating changes the router's default for
**every** client, Clarvis's agent runs included — so it arms first, states its
blast radius, sends `If-Match` on the profile revision so it fails rather than
racing another operator, and names the `routing-control` permission it needs.

## The data is this machine

`LMSTUDIO_SNAPSHOT` is the verbatim body of `GET http://127.0.0.1:1234/api/v0/models`
from the LM Studio server on this machine — 11 local builds and an embedding model,
with their real architectures, quantisations, context ceilings and `tool_use`
capability. `lmstudioBuilds()` maps that payload onto SIRVIS's build identity, and
`LMSTUDIO_SIZES` / `LMSTUDIO_PARAMS` carry what `lms ls` reports and the models
endpoint does not.

It is embedded rather than fetched because this file opens from disk with no
server, and a runtime fetch would make it depend on LM Studio being up
to render at all. Keeping the payload in its own shape is what makes the swap
real: the mapper an implementation needs already exists and has been exercised
against a genuine response.

Consequences worth knowing:

- **Machine is a MacBook Air M5** — M5, 10 CPU / 10 GPU, 24 GB, macOS 27.0, 501 GB
  free, LM Studio 0.4.21 / llama.cpp 2.29.1 / MLX 1.11.0, all read from this
  machine. Memory figures elsewhere are sized against 24 GB, not the 64 GB the
  first draft assumed.
- **`tool_use` is load-bearing, and measurement outranks it.** Eight of eleven
  builds advertise it — but `ravis/clarvis-agent` has ten members, because
  `toolsWork()` prefers measured tool-call reliability over the advertised flag.
  Three builds advertise nothing and work anyway; one advertises support and loses
  seven calls in eight to the MLX runtime's parser. Those disagreements run both
  ways, which is the clearest argument on this page for probing capabilities
  instead of trusting model names — and the reason `toolsWork` keeps the measured
  and advertised fields separate rather than reconciling them at the source.
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
   depend on it. That is the runbook's Stage 6 degradation requirement (`NERVIS.md` §21.1,
   M1 + M2), and it is walkable rather than asserted.
3. To add a screen: write the function, register it in `SIRVIS_VIEWS` /
   `RAVIS_VIEWS` (or the `if` chain in `nervis()`), and read from an endpoint that
   already exists. If the endpoint does not exist, check the specs one directory up for the
   canonical one before writing a line of UI — and if the spec does not define it,
   stop and say so.

## Things that have actually gone wrong here

Moved to **`docs/PITFALLS.md`**, which is now the single register of every defect
this repository has produced and the rule that prevents each one. It is kept
there rather than here because it is the file you want open while editing, not
the one you read once to orient yourself — and because a second copy of a list
like that drifts within a day.

Read it before your first script-driven edit to `index.html`. The short version:
almost nothing here fails loudly.

The newest entries are the ones the badge audit produced:

- **When the same mistake keeps appearing in new code, look for the answer that
  exists and is being thrown away.** Eight instances of one defect in a day were
  not eight lapses; they were one missing return value.
- **An empty answer is an answer.** Four adapters treated "the service replied
  with nothing" as "the service is not there" and fell back to invented data.
  The worst of them rendered fabricated co-residency measurements on the screen
  whose entire argument is that such numbers cannot be predicted.
- **A test written beside the code it checks locks in that code's mistake.**
  NERVIS advertised `clarvis_visibility` as unavailable for as long as M8a had
  been shipped, and a test asserted the wrong value the whole time.

And the one live wiring produced before them: **when a shared `API` method
changes shape, grep its callers.** Two screens read `API.sirvis.runs()`, the
Results wiring changed what it returns, and the SIRVIS Dashboard threw for a
commit because only the neighbouring screens were re-tested.

## Conventions worth keeping

- Contract field names in `API`, display names only in views. Rename at the view or
  the swap to real services stops being mechanical.
- Never add a UI control without an endpoint that backs it. A UI need does not create
  an API.
- Facts stay verbatim. Both characterful surfaces — Clarvis's panel and NERVIS's chat
  — put the personality in the sentence *around* the reading, never in the reading.
