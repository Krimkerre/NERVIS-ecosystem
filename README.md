# NERVIS ecosystem template

An interactive, single-file prototype of the **NERVIS** dashboard and the three
applications it observes — **SIRVIS**, **RAVIS** and **CLARVIS**.

> SIRVIS knows. RAVIS chooses. CLARVIS acts. NERVIS connects.

Open `index.html` in a browser. No build step, no server, no dependencies.

## What this is, and what it is not

It **is** a working shell for four applications that do not exist yet, built so the
screens can be argued about before any of them is written — and built so that wiring
it to real services is mechanical rather than a rewrite.

It is **not** a mockup in the usual sense. Every figure on screen comes from a data
layer shaped like the real API responses, every screen degrades when its owning
service is absent, and nothing is drawn that a live service would not supply.

Only **Clarvis** exists as real code today (`../clarvis`). SIRVIS, RAVIS and NERVIS
are specifications, in `docs/spec/`.

## Layout

```
index.html              the whole prototype — markup, styles, data layer, views
avatars/*.html          the four animated avatars, embedded into index.html
tools/embed-avatars.py  re-embeds avatars/ after you edit one
tools/check.py          the smallest check that fails when the template is broken
docs/CURRENT_STATE.md   what is built, what is stubbed, where to start
docs/WIRING.md          how to replace the mock data with real services
docs/spec/              snapshot of the four build plans and the runbook
AGENTS.md               working rules for an agent picking this up
```

## The two rules that matter

**Every figure has an author.** Each tile declares the service that owns it. When
that service cannot answer, the tile says which service and why — never a zero, never
a dash that reads like a measurement. Try it: the **SIMULATE** strip at the
bottom-right cycles RAVIS, SIRVIS or the Clarvis Bridge through
`healthy → degraded → unreachable`, and only the surfaces that depend on the missing
service change.

**No invented contracts.** Every endpoint in the data layer is cited from the spec
that owns it. Anything not cited does not exist yet and is a STOP item — not an
invitation to make one up. This is the ecosystem's governing rule and it applies to
the UI as much as to the services.

## Getting started

```bash
open index.html            # or drag it into a browser
python3 tools/check.py     # before and after any edit
```

Then read `docs/CURRENT_STATE.md`. It takes five minutes and it is kept honest about
what is finished and what is still a stub.
