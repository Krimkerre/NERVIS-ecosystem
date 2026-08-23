# NERVIS — the dashboard being built

The single-file NERVIS dashboard, and the one the build changes. It began as this
repository's `template/` directory and now owns the working apparatus:
[`AGENTS.md`](AGENTS.md) for the rules, [`docs/`](docs/), [`tools/`](tools/) and
[`avatars/`](avatars/).

**A frozen reference copy lives outside the repository**, at
`~/Documents/coding/nervis-template/`. It stays on its mocks, so it always opens from
disk with nothing running — that is what makes it a template. This one tracks the
services as they come up, and is expected to be ahead of it. Neither supersedes the
other and neither should be copied over the other; the repository keeps exactly one
prototype, which is this file.

Diverged from the snapshot in two places so far: `fallback` → `fallbacks` in the
route-decision shape, because that is what `GET /api/v1/route-decisions` publishes, and
a distinct `<title>` so the two are not confused in a tab strip.

Read [`docs/PITFALLS.md`](docs/PITFALLS.md) before your first script-driven edit —
every defect this page has produced and the rule that prevents each one. Almost nothing
here fails loudly. [`docs/WIRING.md`](docs/WIRING.md) is the method for swapping a mock
for a real endpoint; [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md) is what the data
actually is.

```bash
python3 nervis/tools/check.py
```

**One rule outranks every screen on this page: it must render with nothing running.** A
live read that throws and stops the render destroys that, silently, for everyone not
currently running the service it was tested against. Every live read falls back to its
mock.

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
