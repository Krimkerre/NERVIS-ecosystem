# Pitfalls

*Every defect that has actually happened in this repository, and the rule that
prevents each one. Recorded because **almost all of them are silent** — the page
still renders, the JS still parses, `tools/check.py` still passes, and the screen
looks finished while being wrong.*

The unifying lesson, if you read nothing else: **this codebase fails quietly.**
A falsy read draws nothing, a duplicate object key wins silently, a detached node
throws inside an interval where nobody is listening, and a fabricated number looks
exactly like a measured one. Reading the diff will not catch these. Measuring the
rendered page will.

---

## 1 · Editing the file with a script

`index.html` is one 200 KB file, so edits get made with Python string surgery. That
is fine, and it is also where the worst bugs have come from.

**A region replace built from two `index()` calls, where the end marker also occurs
earlier in the file.** `h[:start] + new + h[end:]` silently re-emits everything
between `end` and `start` when `end < start`. This duplicated `downloads`,
`machine`, `recommendations` and `runtimeSet` three times over in the `API` object.
It parsed, `check.py` passed, and **the last duplicate won** — which was the stale
copy, so the screens kept rendering old data while the new fixture sat above it
doing nothing.

> Assert `start < end` before slicing. `tools/check.py` now detects duplicate
> `API` methods per namespace — that check exists because of this bug, and it is
> the only one here that catches it.

**`replace()` without a count.** Python's `str.replace` replaces *every*
occurrence. One such edit injected a stylesheet into a JS string literal and broke
the page.

> Always pass a count, and assert `h.count(old) == 1` first.

**Slicing on `function render()`** matched inside `async function render()` and
silently removed the `async` keyword.

> Anchor on text that cannot appear as a substring of something else.

**A bulk region replacement deleted `chatView()`**, because it happened to sit
between the two functions being replaced. Clicking Chat threw for two turns before
anyone noticed.

> Re-test the screens either side of a region edit, not just the one you meant to
> change.

**Assertions must run before the write, not after.** A script that asserts at the
end leaves a half-applied edit on disk when it fails. Every failed run in this
repo has been recoverable only because the write was the last statement.

---

### A backtick in a comment ends the string the comment is inside

Three separate syntax errors in one session, all the same shape. An HTML comment
written *inside* a template literal is not a comment to JavaScript — it is text
in a string, and a backtick in it closes the string:

```js
$('#content').innerHTML = `
  <!-- keyed on `ev.source`, like the card above -->   <-- ends the template here
  <div class="card">...</div>`;
```

The error surfaces far away and names an identifier from the following line
(`Unexpected identifier 'ev'`), so it reads as a problem with the code rather
than with the prose. Write those comments without backticks.

The mirror-image trap: an HTML comment placed between the two branches of a
ternary is in *code* position, not string position — and `<!--` is a legal
single-line comment in JavaScript, so only the first line disappears and the
rest becomes code:

```js
const card = rows ? `...`
  <!-- the failure branch -->    <-- line 1 is a comment; line 2 is a syntax error
  : `...`;
```

Use `/* */` there. Rule of thumb: inside a template literal, HTML comment
without backticks; anywhere else, a JavaScript comment.

**`python3 tools/check.py` now catches the first of these.** It bit four times
in one session before it became a check -- which is the honest measure of how
easy it is to write. The second (an HTML comment in code position) is still on
you.

## 2 · Renames, and the silence of `undefined`

**`undefined` is falsy, so a stale read draws nothing rather than failing.**

Renaming `tools` → `tools_advertised` on the build record broke
`API.sirvis.catalog()` two hundred lines away. The Discover table stopped drawing
a tools chip on all eleven installed builds — silently claiming none of them can
call a tool, while the Models page showed eight that do. Nothing errored. The
table looked plausible.

> After renaming any field on a shared record, grep the old name across the whole
> file before assuming you are done. Then open every screen that reads it.

**A missing lookup key produces `setState(undefined)`.** Adding a fifth avatar
without an entry in `desiredAvatarState()` meant the sync call threw inside the
frame — where it is wrapped in `try/catch`, so it vanished entirely and the avatar
simply kept whatever state it had initialised itself with.

> A `catch(e){}` that exists for a real reason will also swallow your new bug.
> Add the key when you add the case.

**`check.py` rejects the literal string `undefined` in the source.** That guard
exists because the string used to reach the page. It also means a legitimate
`x !== undefined` comparison fails the check — use `x == null` instead, which
covers both cases anyway.

---

## 3 · CSS that fights other CSS

**Grid items default to `min-width:auto`.** One long identifier widened a `1fr`
track and pushed a sibling panel outside its container. Worse and subtler: every
`.table-row` is its own grid, so a long value in one row widened *that row's*
column and knocked it out of alignment with the row above — the columns stopped
being columns and nothing looked broken enough to investigate.

> `.table-row>span{min-width:0;overflow-wrap:anywhere}` is now global. Wrap long
> identifiers; never ellipsize a contract name.

**A CSS brace imbalance discards the rule that follows it.** Two stray `}` once ate
a whole rule and the avatar vanished with no error anywhere.

> `tools/check.py` catches this. Run it.

**Inline styles do not override everything.** The Ecosystem map sets each node's
`left`/`top` inline, but the Overview map's `.node:nth-of-type(n)` rules still
matched and contributed `right`/`bottom`. With both edges set, the boxes stretched
to 754 px. Inline wins the *property* it sets — it does not disable the rule.

> Neutralise the other edge explicitly (`right:auto;bottom:auto`) when you position
> something inline that a class rule also positions.

**A custom property in a `calc()` you forgot to use.** The scrollable frame was
written as `calc(var(--rows,4) * 32px + 26px)` with a hardcoded `32px`, so
`--row-h:74px` did nothing and four rows rendered as two.

> Measure the result (`getBoundingClientRect()`), do not trust the arithmetic.

**`applyTheme()` writes custom properties onto `documentElement`.** A stylesheet
rule setting `--accent` on `.app` therefore only recolours inside `.app`, and
anything reading it from `:root` keeps the old value.

> Set theme variables where `applyTheme` sets them, or accept a partial recolour.

---

## 4 · Async and lifecycle

**An interval that throws never surfaces and never resolves.** The typing
animation did `el.parentElement.scrollTop = …`. When a re-render detached the
node, `parentElement` became `null`, the callback threw on every tick into
nothing, the promise never settled, and the whole sequence hung with its button
permanently disabled until a reload.

> Any `setInterval` driving a promise needs an escape: check `el.isConnected`,
> clear the interval, resolve. Wrap long sequences in `try/finally` so global
> state (a theme attribute, an overlay class, a `running` flag) is restored on
> every exit path, including the one where the user clicked away.

**Timers throttle when the tab is hidden.** Background tabs clamp `setInterval` to
~1 s, so a timed sequence measured through a hidden pane looks broken when it is
not.

> Do not retune pacing from a measurement taken while the page was not visible.

---

## 5 · Data honesty — the failures that matter most

These are the ones that make a screen *look finished and be wrong*, which this
project treats as worse than a missing screen.

**Changing one figure invalidates others.** Setting the machine to 24 GB left
three fixtures arithmetically impossible: 27.8 GB of resident models, a 27.8 GB
combined peak with zero swap, and 28.9 GB of headroom. No tool catches this; only
reading the numbers against each other does.

> When you change a constraint, list every figure derived from it before editing
> anything. If the cascade changes the story, ask rather than guess.

**Metadata is not measurement, and the two disagree.** Exclusions were written
from LM Studio's `tool_use` capability flag. The benchmark contradicts it on four
of ten builds *in both directions* — three advertise nothing and make well-formed
calls in every attempt; one advertises support and loses seven calls in eight.

> Keep both fields (`tools_advertised`, `measured`) and let the measurement win.
> Never reconcile them at the source: the disagreement is the data.

**A caption that contradicts the data underneath it.** A `NO_ROUTE` reason claimed
no build satisfied vision plus tools, while the derived pool count showed one
member. Derived numbers move; hand-written prose about them does not.

> Derive the sentence from the same values it describes, or check it every time
> the data changes.

**A nonsense constraint nobody read.** An exclusion said
`minimum_context 32768 > 40960` — false on its face, and the build actually
qualified.

> Read exclusion reasons as arithmetic, not as flavour text.

**Silent truncation.** The route inspector listed 3 of 9 eligible candidates; the
results table filtered out any build with no rate, which quietly removed the one
build that produced no output at all — the worst result in the run vanished from
the report.

> If a view bounds what it shows, say so on screen. A build that produced nothing
> is a result and belongs in the table.

**Inventing a reason the endpoint does not give.** `providers()` reports
`enabled: false` and not *why*. The narrow card guessed "not running" versus
"policy" from an unrelated field.

> Report the field you have. If the reason is not in the payload, say the endpoint
> does not carry it.

**Provenance detail hardcoded to the wrong branch.** `prov()` was passed
`'provider metadata'` for records whose kind was `UNKNOWN`.

> Derive the detail from the kind, in one place.

**Generated prose is still prose.** `'has ' + caveat` produced *"has the MLX
runtime discards its tool calls"*.

> Read the rendered sentence, not the template.

**Chip polarity.** Privacy settings rendered redaction `on` in the warning colour,
so the safe state looked like the alarming one.

> A colour is a claim. Decide which value is the safe one per setting.

---

## 6 · Wiring a screen to a real service

**A shared `API` method changed shape and a screen two files away threw.**
`API.sirvis.runs()` has two consumers — the Results run-detail card and the
SIRVIS Dashboard. Wiring Results to live SIRVIS changed what the method returns,
the Dashboard read `rn.generation_tok_s.median` on a shape that no longer had
it, and it threw on render for a whole commit. §1's rule says "re-test the
screens either side of a region edit", which was followed and was not enough:
the broken screen was not either side of anything, it was a caller.

> When a shared `API` method changes shape, **grep its callers** before
> declaring it done. Two screens away is still a caller — and §2's rule applies
> with it, because a missing field draws nothing rather than failing.

**A live endpoint a browser cannot read is not a live endpoint.** The SIRVIS
read surface was built, served, and completely unreachable from this page: no
`Access-Control-Allow-Origin`, so the browser discarded every response. The
endpoints answered perfectly to `curl`, which is what makes it a trap — the
service looks finished from the terminal and is useless to the thing it was
built for.

> Verify a wiring from the *page*, not from `curl`. When adding a read surface a
> dashboard is meant to consume, CORS is part of the surface rather than an
> afterthought.

**A screen wired to real data shows you things the engine never noticed.** The
Benchmarks screen counted two runs as `running` on its first render — runs
killed mid-flight hours earlier, which nothing reconciled and no log had
mentioned. A queue view counts states; a log does not.

> That is the argument for wiring a screen early rather than last. It is equally
> the argument against wiring one whose data does not exist: the same
> attentiveness that surfaces a real defect renders a fabricated number just as
> convincingly.

---

## 6a · Mixing a live read with the map beside it

**Three separate defects, one mistake.** `SERVICES` is a hard-coded fallback map;
the registry is live. A screen that reads one for part of a row and the other for
the rest breaks the moment they disagree — which is whenever a row exists that the
map has never held.

| what it did | what happened |
|---|---|
| `SERVICES[row.key].state` | `undefined.state` on a row whose key was new. **Blanked the whole Overview and Ecosystem map.** |
| `usable(s.key)` on a chip | a *healthy* LM Studio rendered wearing an amber warning, because the map has no `lmstudio` entry |
| `usable(k)` in a filter | threw for a registry row carrying a key with no entry |

**The rule: if a row carries its own state, read that.** Rows have since M2. And
`usable()` now treats an unknown key as *not usable* rather than throwing — one
guard in the shared function beats a guard at every caller.

The middle one is the nastiest: it did not throw, did not fail the render check,
and just quietly put a warning on something that was fine.

---

## 6b · Guards that pass for the wrong reason

```js
r.uncertainty && r.uncertainty.length ? r.uncertainty.map(…) : ''
```

The live API returns a **list**. The mock carried a **string**. `.length` on a
string is truthy, so the guard passed and `.map` threw immediately after —
blanking the Recommendations screen **whenever SIRVIS was not answering**, which
is exactly the condition this page promises to survive.

Nobody found it for weeks because nobody runs the dashboard with SIRVIS switched
off. `tools/render_check.js` found it on its first run.

**A mock whose shape differs from the live response is worse than no mock**: it
makes the failure conditional on which one you happened to exercise.

---

## 6c · A wildcard route eats its siblings

Registering `/api/v1/{service}` on the NERVIS side matched **everything** under
`/api/v1` — so `/api/v1/chat/conversations` resolved to "peer `chat`, surface
`conversations`" and 404'd. Caught by a test for a *different* milestone.

Two literal paths cost two lines and cannot shadow a route that has not been
written yet.

---

## 6d · Clever stubs hang; boring stubs name the missing method

Writing `tools/render_check.js`, the DOM stub started as a `Proxy` answering to
any property. Fewer lines, looked elegant, and cost hours:

- **A `Proxy` responds to `then`.** Awaiting anything that had touched the DOM
  turned it into a thenable that called `then(resolve, reject)`, got the proxy
  back, and waited forever.
- Fixed, it hung again inside the page's canvas animation, where proxy arithmetic
  turned a bounded loop into an unbounded one.

Both hangs presented identically with **no output at all**, because `console`
output to a pipe is buffered and discarded when the process is killed. The
enumerated stub is more lines and every gap announces itself as `x is not a
function` naming the method.

Two smaller traps from the same file: a script's top-level `const` is
**script-scoped, not a property of the vm context**, so `context.APP_CONFIG` is
`undefined` however well the script ran; and `main()` without a `.catch()` sent
its own failures into the unhandled-rejection collector, exiting **0 and printing
nothing**.

---

## 7 · What actually catches these

Since this list was written, eight of them became automated:

- **`node tools/render_check.js`** renders all 35 screens with nothing running
  and fails if one throws. Catches §6a, §6b and everything in §1 that reaches a
  render. **Runs in CI.**
- **`python3 ../tools/check_dead_code.py`** finds definitions and dataclass
  fields nothing references — the shape behind a comment that describes
  behaviour which does not exist. **Runs in CI.**
- **`python3 ../tools/check_status.py`** checks STATUS.md's counts and cited
  paths against the repository.
- **`node tools/complexity_check.js`** holds every function in the file under a
  ratchet of 13. **Runs in CI.**
- **`node tools/liveness_check.js`** counts the cards that hardcode their CSS
  class and so *cannot* report where their data came from. A ratchet, pinned
  from both sides so a movement either way is reported. **Runs in CI** — which
  was written here before it was true, and is a small instance of the last
  paragraph of this section.
- **`node tools/injection_check.js`** wraps every `API` method so its answer
  carries a hostile payload in every string, renders all 35 screens, and fails
  if a tag the page did not write reaches the markup. It fails the other way
  too, on markup the page escaped into text — which is the half that makes
  fixing the first half safe to attempt, and the half no security check looks
  for. A one-sided ratchet: 28 sites when it was written, and it may only fall.
  **Runs in CI.**
- **`node tools/empty_world_check.js`** renders all 35 screens against services
  that are **up and hold nothing** — the fresh-install world, which is neither
  of the two the other checks cover. Six screens threw in it. **Runs in CI.**
- **`node tools/recovery_check.js`** renders every screen with the services down,
  then again after they answer, and reports any screen that kept its fallback.
  **Not in CI** — it needs services, and RAVIS rate-limits an anonymous caller
  at 60 requests a minute, so a screen it flags may simply have been refused.
  Confirm a finding in a browser before believing it; all three it flagged on
  its first useful run recovered correctly when driven by hand.
- **`node tools/honesty_check.js`** renders all 35 screens twice — once with
  nothing running, once against whatever is up — and compares the badges. A card
  whose body changes between the passes was fed by a service; if it is not
  badged live, it is understating. A card badged live in the *dark* pass claims
  a live read with nothing to read from. **Not in CI**, because which services
  happen to be up changes the answer.
- **`node tools/routing_check.js`** builds each of the 35 screens' addresses,
  parses them back, and requires the same screen out — then checks that an
  address naming no screen resolves to one that exists *and says so*. The view
  it exists for is `Run & debug`: an unencoded `&` ends the fragment and the
  address quietly names a different screen, which is a router that looks
  entirely correct from outside. **Runs in CI.**
- **`node tools/outcome_check.js`** drives `live()` through a refusal, a 503, an
  absent service, a body that is not JSON, a hung port and an adapter that
  threw, and requires six distinct answers plus `answered`. It caught two of its
  own subjects on the first run: a 200 with a bad body classified as "the
  service answered HTTP 200", and a cross-realm `instanceof SyntaxError` that
  read a malformed body as an unreachable service. **Runs in CI.**

The last one exists because the defect it hunts was found **eight times in one
day, every time by a person looking at the screen**. Providers had seven of
them. Sessions shipped with six, hours after the first batch was fixed.
"Evidence in the ranking" showed thirty-two live rows from SIRVIS's own
benchmark runs, faded out. Not one was caught by a check, because
`render_check` proves a screen *assembles* and nothing proved it was *honest*.

The root cause was structural, and worth stating because it generalises: the
fetch helper `live()` knew whether the service had answered — it sets
`SOURCE[service]` — and returned the payload without it. The only witness was a
global the next fetch overwrote. Ten different idioms grew up around that
absence (`fromSirvis`, `p.live`, `ev.source`, `j.source`, `tr.live`, …), and
sixty-eight cards hardcoded their class because there was nothing better to
read. **When the same mistake keeps appearing in new code, look for the answer
that exists but is being thrown away.**

None of them catches a **prose claim about the state of the world**.
`docs/CURRENT_STATE.md` said "NERVIS is still a specification" three days after
NERVIS became a running service, and nothing noticed. That one is still on you.


In order of how much they have found here:

1. **Measuring the rendered DOM.** Element counts, `getBoundingClientRect()`,
   `getComputedStyle()`, and re-reading the emitted text. Nearly every defect
   above was found this way or not at all.
2. **Cycling the SIMULATE strip** and diffing which screens changed. This is the
   runbook Stage 6 degradation requirement (`NERVIS.md` §21.1, M1 + M2) and it is
   walkable, not asserted.
3. **Reading numbers against each other** — resident versus budget, members versus
   invariants, exclusions versus counts.
4. **`tools/check.py`** — catches JS that does not parse, unbalanced CSS braces,
   the literal `undefined`, and an `API` method defined twice in one namespace.
   Every one of those checks was added after the corresponding bug shipped. It
   catches nothing else, so passing it means very little.

A note on writing checks: the first version of this checklist told you to grep
for duplicate method names. That grep reports `models` twice — `API.sirvis` and
`API.ravis` both legitimately have one — so it cries wolf, and a check that cries
wolf trains you to ignore it. It was replaced with a real per-namespace check in
`check.py`. Prove a new check fires by reintroducing the bug it is for.

### Before you commit

```bash
python3 tools/check.py                              # parse · braces · duplicate API methods
node tools/render_check.js                          # all 35 screens render with nothing up
node tools/complexity_check.js                      # every function under 13
node tools/liveness_check.js                        # the hardcoded-card ratchet
node tools/shaping_check.js                         # adapters shape payloads as before
node tools/injection_check.js                       # provider data cannot write markup
node tools/routing_check.js                         # every screen is addressable
node tools/outcome_check.js                         # a refusal is not an outage
node tools/stream_check.js                          # the stream resumes and refuses correctly
node tools/preserve_check.js                        # a repaint keeps what you were doing
node tools/empty_world_check.js                     # screens survive live-but-empty services
python3 tools/embed-avatars.py && git diff --stat    # avatars round-trip byte-identical
```

With the ecosystem running, also:

```bash
node tools/honesty_check.js                         # do the badges match the data's source?
```

Then, in the browser: render every screen in all four apps, check for console
errors, placeholder text and horizontal overflow, and cycle each service on the
SIMULATE strip to confirm only dependent surfaces change.

---

## 8 · Tooling that can delete your work

**`tools/embed-avatars.py` rebuilds the entire `AVATAR_SOURCE_B64` block** from a
hardcoded list. Adding a fifth avatar to `index.html` without adding it to `APPS`
meant the next person to edit any avatar and re-run the tool would have silently
deleted it. The source also lived outside the repository, so nothing would have
regenerated it.

> A generator that rebuilds a whole block is a deletion tool for anything not in
> its input list. Keep the input list and the output in sync, vendor the sources,
> and verify the round trip leaves `index.html` byte-identical.
