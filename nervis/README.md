# NERVIS

The ecosystem's control plane: the dashboard, and — since M0 — the service that
serves it.

```bash
cd nervis && ../ravis/.venv/bin/nervis doctor
```

`doctor` needs nothing else running. That is the point of it: the command you
reach for when something is broken must work when everything is broken. It
migrates the database for real rather than checking a path, prints every
capability with the milestone attached to it, and names where its peers would be
without contacting them.

## The service (M0 – M8a)

Package, FastAPI, settings, SQLite with forward-only migrations, structured
logging, the web shell, `nervis serve` / `nervis doctor`, and NERVIS's own
`/ecosystem/{health,identity,capabilities,version,events}` surface.

**M1** adds `/api/v1/system` — this machine's live load. Not SIRVIS's endpoint
of the same name, which is an immutable snapshot of *what this machine is*,
pinned to every benchmark result as provenance. This one is what it is *doing*,
sampled at the moment of the request, and once NERVIS watches a remote peer the
two describe different machines.

Sampled on demand rather than by a timer, because M1's exit says sampling must
not noticeably load the machine and no sampler is the only way to guarantee it.
Load average rather than CPU percent, because a percentage needs two readings
separated by real time and `cpu_percent(interval=…)` would block a request
handler for it. Processes ordered by memory rather than CPU, and named without
their command line — §15 forbids publishing a raw workspace path, and an
argument list is where one appears.

Four of the eleven capabilities §3.1 names are `unavailable`, each naming its
milestone; four are `degraded` and three are `available`. **Nothing advertises
an operation it cannot perform** — §4.1 forbids it, and both sibling services
spent milestones learning why.

The harder half of that rule is the other direction, and this file has been
wrong about it twice: a capability must not go on citing a limit that has since
been lifted. `nervis.dashboard@1` deferred to "M2" long after M2 shipped, and
`nervis.ravis_chat@1` waited on a RAVIS marker that RAVIS M16 now honours. A
stale reason is a working feature hidden behind an excuse, and a peer reading
one plans around a limit that is gone.

**M2** adds `/api/v1/services` — §5.1's registry, probed on a timer, and §5.2's
capability negotiation per operation. The states are NERVIS's own observer-side
eight rather than the services' three: *"healthy" means the service's truthful
response plus NERVIS reachability, never a successful TCP connect alone.* A peer
that answers with a proxy login page is `degraded`; a peer that answers and
reports itself unhealthy is `degraded` too, because NERVIS reached it and it said
no. `stopped` is never inferred from a refused connection — that needs M16's
ownership.

**An unconfigured local runtime is absent, not broken.** LM Studio and Ollama
are both declared, both probed, and neither raises an alarm until it has
answered once — an installation with one of them is an ordinary machine, not a
faulty one. "Configured" means the operator supplied the address
(`Settings.model_fields_set`), not that it matches the default. A peer that
answered once and then stopped *is* a real outage and says so. RAVIS and SIRVIS
are never optional: NERVIS exists to watch them.

Endpoints are checked against an allowlist that is loopback-only by default, and
a **hostname is refused rather than resolved**: resolving makes the check depend
on DNS at the moment of the check, which is the rebinding half of SSRF.
Refusals are published rather than silently dropped, because a service missing
because it was refused looks exactly like one nobody configured.

**NERVIS is still ready with every peer absent.** A control plane that reported
itself broken when the things it watches are broken could not be used to find
out why.

**M3** adds `/api/v1/ravis/{surface}` — negotiated reads of RAVIS's health,
providers, models, pools, route decisions, usage and sessions (§8). NERVIS
**never reads RAVIS's database**, and that is structural: the reader takes a
base URL and an HTTP client and has no filesystem access at all.

The gate is about *capability*, not liveness. A surface RAVIS has not advertised
produces no request — `sessions` refuses today, because RAVIS M11 has not
shipped. A surface last seen usable on a service the registry currently thinks
is down is **attempted anyway**: the registry's reading is up to one probe
interval old, and a connection that refuses in a millisecond gives a fresher,
more specific answer than a stale veto.

Every outcome has one shape, `{available, reason, availability, data}`, with
`data` null on failure and never an empty list — an empty list means *RAVIS has
none of these*, and confusing the two is how a screen renders a confident zero
over an outage.

**M4** adds `/api/v1/chat` — a normal client of RAVIS's *published*
OpenAI-compatible API, streamed, with both turns stored (§7, §7.2). RAVIS's SSE
frames reach the browser unchanged; what NERVIS adds is keeping the text and
cancelling RAVIS when the connection goes away. A cancelled reply is stored as
the partial the reader actually saw, marked `interrupted`.

**Titles are generated as RAVIS background calls, when a credential allows it.**
§7 wants them produced that way, carrying §9.6.1's marker, and RAVIS M16 honours
the marker — from an *authenticated* identity, so NERVIS presents a client
credential and RAVIS resolves it to the `nervis` application. Without one it
stays anonymous, the marker is ignored, and titles remain the truncation of the
first message.

That fallback is the design rather than a gap, and every failure takes it: no
credential, no RAVIS, a refusal, an empty answer. §7's own words are why — an
untitled conversation is a smaller failure than a title billed to a frontier
model — so retrying or falling back to a paid route would invert the trade the
feature exists to respect. A generated title replaces the truncation and never a
name a person typed.

**It is not Clarvis chat.** No workspace, no tools, no gates, no agent role, and
a test asserts none of them appear in the body sent to RAVIS — because the
absence is the feature, and an absence is the one thing a reader cannot see.

**M5a** adds `/api/v1/sirvis/{surface}` — §9's reads: machine, models, runtimes,
residency, Runtime Sets, benchmark runs, evidence and recommendations. **Nothing
is reshaped on the way through**, which is how §9's rule is kept: preserving
`MEASURED`, `ESTIMATED`, `UNKNOWN`, timestamps, staleness, method, sample count,
units and evidence links is easiest when the code is in no position to drop
them. No benchmark logic lives here.

**M5b is blocked**, and the `jobs` surface is listed so the block is visible
rather than inferred: launching a benchmark and streaming its progress need
`sirvis.benchmarks.jobs@1`, which SIRVIS advertises as *unavailable* until its
M14 queue exists — and §1 forbids inventing the endpoint.

**M6** adds `/api/v1/events` — §11.1's hub: §4.4's envelope, HTTP ingestion,
bounded persistence with retention, §11.2's filters, and an SSE stream with
`Last-Event-ID` replay.

**An invalid event cannot crash the hub**, which is M6's headline clause.
Ingestion always answers `202` with the outcome — a rejected event is a fact
about the producer, not a failure of the hub, and an error status invites a
retry of something that will never parse. Refusals are quarantined rather than
dropped, because "the hub is quiet" and "a producer is sending rubbish" look
identical from outside.

**NERVIS is currently its own only producer.** RAVIS and SIRVIS advertise
`events@1` as unavailable until their Stage 7 milestones, so the feed carries
registry transitions and says so. Those go through the same ingestion door as
anyone else's: §3.1 has NERVIS implementing *and* consuming the MEP, and a hub
whose own events took a private path would be the one producer nobody could
validate.

**M7** adds `/api/v1/traces` — §11.2's correlation and waterfall, assembled from
the hub and nothing else. The rule that shapes it is stated twice in that
section: **never synthesize a span as fact.** One event is drawn as a *moment*,
not a zero-length bar; a service that recorded nothing gets **no bar at all**;
clock skew is reported and never corrected, because straightening a waterfall
deletes the only evidence two clocks disagree.

A missing lane explains itself where NERVIS can do so honestly: RAVIS records
`trace_id` on every route decision, so *"RAVIS routed this and published no
events"* is a recorded fact rather than an inference — and it stays a **warning,
never a span**, because a decision proves something happened, not when it
started and stopped.

**M8a** adds `/api/v1/registry/instances` — §5.1's *authenticated local dynamic
registration*, and the receiving half of the Clarvis integration. A Bridge is
one process per open editor window, so instances are keyed by
`(service, instance_id)` and **never merged**: §6.6 says an instance describes
the host serving it, and folding two windows into one row would show one
window's activity under the other's name.

Registration is authenticated by a secret NERVIS writes beside its database at
`0600`. **The file's permissions are the authentication** — a registering
process proves it is the user's by being able to read it, which needs no user
interaction and is the same check `ssh` makes of a private key. It does not
prove *which program* is registering; that boundary is written down in
`enrollment.py`.

Three refusals follow from taking that seriously. A registrant sends a **port,
not a URL**, so it never gains the SSRF primitive `allowed_endpoint` exists to
deny. Only Clarvis may register dynamically, so a local process that read the
secret cannot register as RAVIS and be handed the chat traffic. And a live
instance id is **not taken over**, because last-writer-wins is a race whose
timing an attacker chooses.

**Redaction is structural.** The claim is a closed allowlist with no free-form
string in it — not even a label, which was tried and removed: a Bridge's natural
label is its workspace folder name, and §6.7 forbids NERVIS holding the
workspace root. A field that invites the value you promised not to store is
worse than no field, because the promise then rests on every future caller's
restraint. NERVIS derives the label from the instance id, so there is nowhere
for a path to go.

The instance's token is returned once and never listed — if the dashboard could
read it, a browser tab would be enough to impersonate an editor window — and the
two credentials do different jobs: the enrollment secret registers and cannot
renew, the instance token renews and cannot register.

**M8b is blocked.** Reading a running Bridge needs one to exist; `CLARVIS.md` §6
specifies it and the Clarvis repository has no implementation, and §1 forbids
inventing another component's API. `peers/clarvis.py` declares the surfaces §6.3
names, all reads — §6.7's limits are enforced by there being no write surface to
use, and a test fails the suite if one appears.

The **Routes** screen reads RAVIS's own decision records
(`/api/v1/route-decisions`) rather than a transcription. What it shows is
shaped by what RAVIS actually publishes, which is not what the mock invented:
there is no per-candidate score, no per-candidate time-to-first-token and no
phase breakdown of the routing time. Ranking on quality is RAVIS M13 and
nothing instruments the decision in phases, so a live screen shows the funnel
and the *reasons* instead of a bar chart of numbers nobody measured.

Exclusions are grouped by reason, never listed per model. A pool with six
hundred candidates excludes most of them, and the reason is the finding — the
card that rendered one row each was forty thousand characters nobody read.

`/api/v1` has `health`, `settings`, `system`, `services`, `registry/instances`,
`ravis`, `sirvis`, `chat`, `events` and `traces`.
§14's other three paths arrive with the milestones that own them, because a stub
returning plausible data is §4.1's prohibition one layer up.

Two deviations from §3, stated rather than left to be discovered. **Not
SQLAlchemy and not Alembic**: both sibling services migrate with the standard
library and the schema here is a handful of tables, so an ORM plus a migration
framework would buy nothing the other two found they needed. **No CORS**: NERVIS
serves the dashboard and the dashboard's own API from one origin, and the
cross-origin reads go to RAVIS and SIRVIS, which each carry the allowlist that
governs them.

```bash
cd nervis
../ravis/.venv/bin/ruff check src tests
../ravis/.venv/bin/mypy
../ravis/.venv/bin/python -m pytest -q
```

## The dashboard

[`index.html`](index.html), a single file, served by the service above rather
than replaced by it. It was built screen by screen against two live services; a
server-rendered skeleton would be a worse version of something that works. §3's
HTMX direction applies to the screens NERVIS itself supplies data for, from M1
onward.

Its apparatus: [`AGENTS.md`](AGENTS.md) for the rules, [`docs/`](docs/),
[`tools/`](tools/) and [`avatars/`](avatars/).

**A frozen reference copy lives outside the repository**, at
`~/Documents/coding/nervis-template/`. It stays on its mocks, so it always opens
from disk with nothing running — that is what makes it a template. This one
tracks the services as they come up and is expected to be ahead of it. Neither
supersedes the other and neither should be copied over the other; the repository
keeps exactly one prototype, which is this file.

Read [`docs/PITFALLS.md`](docs/PITFALLS.md) before your first script-driven edit
— every defect this page has produced and the rule that prevents each one. Almost
nothing here fails loudly. [`docs/WIRING.md`](docs/WIRING.md) is the method for
swapping a mock for a real endpoint;
[`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md) is what the data actually is.

```bash
python3 nervis/tools/check.py
node nervis/tools/render_check.js
node nervis/tools/complexity_check.js
node nervis/tools/shaping_check.js
```

**One rule outranks every screen on this page: it must render with nothing
running.** A live read that throws and stops the render destroys that, silently.

`render_check.js` is what enforces it: every screen rendered in a DOM shim with
`fetch` rejecting, failing if one throws. It runs in CI. Until it existed the
only enforcement was a person opening a browser — which caught four
screen-blanking crashes in a week and missed a fifth, because nobody runs this
dashboard with SIRVIS switched off.

It proves a screen **assembles**, and nothing more: not that the markup is
valid, not that anything is laid out, not that a click works. That is the cheap
half of the check and the half that keeps failing.

**And it makes every `fetch` reject**, which is the state it exists to test and
also its blind spot: it exercises the *mock* path of every live reader and none
of the shaping. That is where the complexity actually was — `API.sirvis.
runtimeSet` reached cyclomatic complexity 30 with not one of its branches ever
executed by a check.

`shaping_check.js` covers that half. It runs the readers against recorded
payloads and fails if the shaped output moves — a **characterisation test**, so
it asserts the output is unchanged rather than right. The fixture that matters
most is the sparse one: every `||` in a reader is a claim about a field the
service might not send, and the sparse payload is the only thing that proves
those defaults do what their author believed. It found a live bug on its first
run — an unrecorded co-residency field rendering as "FAILED — undefined".

`complexity_check.js` holds the file at a **ratchet of 13**, lowered whenever
the worst survivor comes down; the target is 10. The Python packages hold 8 and
this does not, because about a fifth of this file's decision points are control
flow and most of the rest are optional-field fallbacks inside template literals
— forcing 8 would pressure a reader toward hiding that handling rather than
writing it out. Naming the pattern once (`dash()`, `warnUnless()`) is the honest
version of that fix, and it removed 78 branches.

## Running it against a live RAVIS

The browser has to be *allowed* to read the answer, and this is the step that is
invisible until it bites. Loopback is not a boundary against a browser — a page
the user is visiting can reach `127.0.0.1` carrying whatever credentials the
browser already holds. So RAVIS refuses a request from an origin nobody listed,
and sends no CORS headers to one either. Both halves read one setting, so they
cannot drift apart.

**Serve this over http and allow that origin.** One command each:

```bash
cd nervis && python3 -m http.server 8080
```

```bash
RAVIS_UPSTREAM_BASE_URL=http://127.0.0.1:1234 RAVIS_ALLOWED_ORIGINS='["http://127.0.0.1:8080"]' ravis serve
```

A page opened straight from disk sends `Origin: null` instead — and so does
every *other* page opened from disk, and every sandboxed iframe.
`RAVIS_ALLOWED_ORIGINS='["null"]'` therefore allow-lists a category rather than
a page. It works, `ravis doctor` warns about it, and it is the wrong habit.

Only `GET`, `HEAD` and `OPTIONS` are permitted cross-origin, because every
`/api/v1` endpoint that exists today is a read.

## What is wired, and what is deliberately not

`API.ravis.requests()` reads `GET /api/v1/route-decisions` for real. Everything
else is still a mock, and two of them are mocks *on purpose* rather than for
lack of time:

- **`models()` must not be wired until SIRVIS exists.** The screens are built on
  three separate tool fields — `advertised`, `measured`, `effective` — because
  on this machine the runtime's flag and the benchmark disagree about four
  builds in both directions. RAVIS's real endpoint publishes one state and one
  provenance, because that is all RAVIS holds. Wiring it would collapse exactly
  the distinction `docs/PITFALLS.md` §5 says never to reconcile at the source:
  the disagreement *is* the data.
- **`pools()` therefore cannot be wired either**, because the Pools screen counts
  membership against `models()`. Half-wiring it would put a live count beside a
  mock one on the same card, which is the failure mode that section is about.
- `providers()` and `usage()` have real endpoints whose shapes differ enough to
  need adapters; neither is blocked, just unwritten.

The route-decision rows render `—` for path and TTFT. That is not a gap in the
wiring: RAVIS does not publish an execution path (only Path A is built, and
asserting it here would be this page claiming something the API never said), and
time-to-first-token is per *target* on `/api/v1/health`, not per decision.

`SOURCE.ravis` records which of the two a screen is showing.

## What the live reads actually return

`ravis/` is built through M18a and M12, so several `API.ravis` methods have a
real endpoint behind them now. Two shape notes before swapping a body:

- **List responses are `{items, next_cursor, snapshot_revision}`** — the mocks
  already match, so `.items` keeps working.
- **`/api/v1/route-decisions` is thinner than the mock, honestly so.** It
  carries `selected`, `fallbacks`, `considered`, `excluded[{model, reasons}]`,
  `requirements`, `unverified`, and `execution` — the §10 attempt history, which
  models were tried and how each ended. It carries **no per-candidate score and
  no TTFT**, because RAVIS has no benchmark evidence until M13. The mock's
  `eligible[].score` is staged data with nothing behind it yet. Map the real
  fields and let the missing ones render `UNKNOWN`; do not average something to
  fill the column.

`ravis preflight clarvis` prints what the Routes screen will have to render,
before you wire it.
