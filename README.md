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
avatars/*.html          the five animated avatars, embedded into index.html
tools/embed-avatars.py  re-embeds avatars/ after you edit one
tools/check.py          the smallest check that fails when the template is broken
docs/CURRENT_STATE.md   what is built, what the data is, where to start
docs/PITFALLS.md        every mistake made here, and the rule that prevents it
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

## One control here is not load-bearing

Open **NERVIS → Chat**. At the end of the routing-profile chips, past a divider,
there is a teal **AGI** button. Press it.

What follows is an easter egg: the dashboard goes Hatsune Miku teal, the heading
becomes MIKU MODE, the avatar is replaced, and a short scene plays out in the
transcript — including one line she should not have said, delivered while the
whole page corrupts. It runs about seventeen seconds and puts everything back
exactly as it was.

It is worth a paragraph in a README about a project whose governing rule is *no
invented contracts*, because at first glance an "AGI" button is exactly the thing
that rule forbids. It is not one, and the code is written to keep it honest:

- **It sits outside the profile group, behind a divider**, and says so on hover.
  RAVIS publishes its routing profiles and NERVIS may not invent one — a chip
  labelled AGI *inside* that row would be a claim about an API nobody has agreed
  to, which is the failure mode this repo cares most about.
- **It calls nothing.** No endpoint, no mutation, no event, no route, no record.
  Every surface it touches is local presentation, in the same category as the
  Focus toggle.
- **It reverts by re-rendering, not by snapshotting.** `chatView()` rebuilds the
  heading, avatar, profile row and transcript from the data layer, so there is no
  saved copy of the page to drift stale.
- **It is abort-safe.** Click away mid-scene and it stops and restores cleanly.
  That took a fix: the typing interval used to throw forever on a detached node,
  which never surfaced and left the sequence hung with its button dead.

The avatar itself needed no special handling. It is the fifth entry in
`AVATAR_SOURCE_B64` and honours the same contract as the other four — a
self-contained page exposing `setState(name)`, embedded as base64 and rendered in
a `srcdoc` iframe. Driving it is one call, exactly as it is for a real Clarvis
extension host.

## Getting started

```bash
open index.html            # or drag it into a browser
python3 tools/check.py     # before and after any edit
```

Then read `docs/CURRENT_STATE.md`. It takes five minutes, and it is kept honest
about what the figures on screen actually are — every screen in the rail is built,
the model list and machine record are read from this machine, and the benchmark
evidence is a real suite run. It also says plainly which numbers are still staged.
