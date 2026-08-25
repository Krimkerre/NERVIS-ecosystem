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

## The service (M0, M1)

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

Nine of the ten capabilities §3.1 names are `unavailable`, each naming its
milestone. `nervis.dashboard@1` is `degraded`: the shell and this machine's
telemetry are served, and the peer data on it still comes from RAVIS and SIRVIS
directly. **Nothing advertises an operation it
cannot perform** — §4.1 forbids it, and both sibling services spent milestones
learning why.

**No peer is probed.** That is M2. NERVIS is ready with RAVIS, SIRVIS and Clarvis
all absent, deliberately: a control plane that reported itself broken when the
things it watches are broken could not be used to find out why.

`/api/v1` has `health`, `settings` and `system`. §14's other five paths arrive
with the milestones that own them, because a stub returning plausible data is §4.1's
prohibition one layer up.

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
```

**One rule outranks every screen on this page: it must render with nothing
running.** A live read that throws and stops the render destroys that, silently.
Verified by rendering all 27 screens across the three apps and asserting none
throws — which is how the `SERVICES[row.key]` crash was caught at M0.

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
