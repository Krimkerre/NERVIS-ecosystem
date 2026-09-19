# Agent instructions for `nervis/`

*Read this before touching anything in this directory. Tool-agnostic: it applies to
Claude Code, OpenCode, Codex or anything else.*

**This is the NERVIS service and its dashboard** — `index.html` is the single-file UI
the service in `src/nervis/` serves, and the one the build changes. It began as the
`template/` directory in this repository and now owns the working apparatus outright:
these rules, `docs/`, `tools/` and `avatars/`.

**A frozen reference copy lives outside the repository**, at
`~/Documents/coding/nervis-template/`. It stays on its mocks so it always opens from
disk with nothing running. It is not a second version of this file to be reconciled —
it is a snapshot, and this one is expected to be ahead of it. The repository's "one copy
of everything" rule is why it lives out there rather than beside this.

## Orient yourself

1. `../STATUS.md` — what is built and what is next, today. (`docs/CURRENT_STATE.md`
   is a snapshot from 28 August 2026, kept as history; it is not current.)
2. `docs/PITFALLS.md` — every defect this repo has produced and the rule that
   prevents it. Read it before your first script-driven edit to `index.html`;
   almost nothing here fails loudly.
3. `docs/WIRING.md` — how the data layer becomes real services.
4. `../` — the four build plans, the overview and the runbook, in the root of this
   repository. These are the authority for what each service owes the others, and
   they are the same files the services are built from — not a copy of them.

## The rule that governs everything

> **No agent may invent another ecosystem component's API, schema, capability or
> behaviour merely to complete its own milestone. If the required contract does not
> exist, implement against the canonical contract, use a labelled test double, or
> stop and report the missing dependency.**

Here that means: **do not add a UI control without an endpoint that backs it.** A screen
that implies an API nobody has agreed to is worse than a missing screen, because it looks
finished. If you need something the specs do not define, say so and stop.

`../STATUS.md` says which endpoints are real today. Most screens now read a live service
first and fall back to a mock when it does not answer. A mock is fine — a mock presented as
live is not.

## Working rules

- **A live read must degrade to its mock, never take the page with it.** This file has
  one property worth more than any screen on it: it renders with nothing running. A
  `fetch` that throws and stops the render destroys that, and destroys it silently for
  everyone who is not currently running the service you were testing against.
- **Run `python3 tools/check.py` before and after every edit.** It parses the inline
  script and balances the CSS. Both failure modes are silent in a browser.
- **Pass a count to every `replace()`.** An unbounded replace once injected a
  stylesheet into a JS string and broke the page.
- **After a region edit, re-test the screens either side of it.** A bulk replacement
  silently deleted `chatView()` once; it was two turns before anyone clicked Chat.
- **Verify in the browser, not by reading.** Measure with the devtools console —
  widths, computed styles, element counts — rather than assuming a rule applied.
- **Keep contract field names in `API`.** Rename in the view.
- **Facts stay verbatim.** Clarvis's panel and NERVIS's chat both have character, and
  in both the personality goes in the sentence around a number, never in the number.

## What "done" looks like for a new screen

- Reads from an `API` method that cites its endpoint.
- Every figure names its owning service, so it can go absent.
- Provenance survives where the underlying value has any (`MEASURED` / `ESTIMATED` /
  `UNKNOWN`), and staleness is shown where it matters.
- Stopping that service changes this screen and nothing else.
