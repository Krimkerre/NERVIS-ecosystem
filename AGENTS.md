# Agent instructions for this repo

*Read this before touching anything. Tool-agnostic: it applies to Claude Code,
OpenCode, Codex or anything else.*

## Orient yourself

1. `docs/CURRENT_STATE.md` — what is built, what the data actually is, and where
   to start. Five minutes.
2. `docs/PITFALLS.md` — every defect this repo has produced and the rule that
   prevents it. Read it before your first script-driven edit to `index.html`;
   almost nothing here fails loudly.
3. `docs/WIRING.md` — how the data layer becomes real services.
4. `docs/spec/` — the four build plans and the runbook. These are the authority for
   what each service owes the others.

## The rule that governs everything

> **No agent may invent another ecosystem component's API, schema, capability or
> behaviour merely to complete its own milestone. If the required contract does not
> exist, implement against the canonical contract, use a labelled test double, or
> stop and report the missing dependency.**

In this repo that means: **do not add a UI control without an endpoint that backs
it.** A screen that implies an API nobody has agreed to is worse than a missing
screen, because it looks finished. If you need something the specs do not define,
say so and stop.

## Working rules

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
- Cycling that service on the **SIMULATE** strip changes this screen and nothing else.
