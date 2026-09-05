# Release notes

**Status:** Per-component release notes for the five things this ecosystem ships
**Audience:** Operators upgrading, and anyone asking what changed between two versions
**Enforced by:** `tools/check_releases.py` — every shipped version has an entry here, or the gate fails

§15 asks that "compatibility matrix, operator runbook, release notes and known limitations
are published". There were no release notes, for any of the five, at any version. Five
components carried version numbers that nothing explained: `ravis 0.21.2` told an operator
that something changed twice since `0.21.0` and nothing about what.

**This file starts where it starts, and does not invent what came before.** Versions before
the ones below shipped without notes, and reconstructing them from commit messages months
later would produce a document that reads like a record and is a guess — §14.6 has one
incident of a number copied rather than run, and this would be the same mistake in prose.
What happened before is in `STATUS.md`, which is contemporaneous, and in the git history.
The gate below only requires an entry for the version each component currently declares, so
the record grows forward from here and no gap is papered over.

**Versions are per component, deliberately.** §3 keeps the four products independently
buildable and shipped on their own cadence; one ecosystem-wide version number would be a
fifth thing to keep in step with four others, and the first release where they disagreed
would make it a lie. What ties them together is the protocol version, which is recorded on
every entry.

---

## Clarvis — 0.12.6

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **The Bridge states its version at registration**, closing the one gap in §12's peer
  matrix. NERVIS can now hold a Clarvis Bridge to a supported window like any other peer —
  reported and never refused, because §12 says a peer one supported minor behind must be
  tolerated during a rolling upgrade.

### 0.12.5

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **The agent gate's *effect* is tested, not only its wording.** `Gate.ts` classifies
  dangerous commands and `explainGate` phrases the question; both were tested thoroughly,
  and whether saying *no* actually stops the command was not tested at all — that decision
  lived inside a private method of a class importing `vscode`, unreachable from the test
  suite. It is `gateDecision.ts` now, with nine tests, and `AgentRunner` calls it rather
  than repeating it.
- **`escapes` is distinct from "not confined".** A machine with no sandbox confines nothing
  either, and the two want opposite handling: one was permitted at a modal, the other has
  permitted nothing and still owes the operator a separate question.
- `AgentRunner.test.ts` is now `streamNarration.test.ts`, which is what it always tested. A
  reader looking for the agent loop's coverage found a file with its name on it and stopped
  looking.

### 0.12.4

- **One window's agent run no longer destroys another's undo.** The checkpoint record and its
  file copies were installation-wide, and a run clears the store as it begins — so starting a
  task in a second window silently destroyed the first window's ability to undo, leaving a
  record whose entries pointed at files that were gone. Both are per workspace now.
- Upgrading keeps an in-flight undo: `stored()` still reads the old key when the new one is
  empty, and entries carry absolute copy paths, so a record written by 0.12.3 restores.

### 0.12.3

- `AgentRunner.loop` brought back under its complexity ceiling by extracting `begin()`.

---

## NERVIS — 0.23.0

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **Changing RAVIS's configuration through NERVIS now needs the dashboard's control token.**
  NERVIS holds RAVIS's admin credential and proxies six configuration mutations, and asked
  callers for nothing — so anything able to reach `127.0.0.1:8790` could change RAVIS's
  configuration while holding nothing at all. A token is minted per process, embedded in the
  page NERVIS serves, and required back on those six routes. **Operator note:** a dashboard
  left open across a restart must be reloaded before its configuration controls work again.
- **Three surfaces stopped publishing absolute paths** — `/api/v1/logs`, `/api/v1/learned`,
  and SIRVIS's `results_path` — which carried the home directory to a page served with no
  authentication. They publish the file inside the directory the operator configured.
- **RAVIS's own observation no longer wears SIRVIS's badge.** The Evidence screen showed
  `OBSERVED_BY_RAVIS` — a rolling window over real traffic — with the same `MEASURED` badge as
  a controlled benchmark. `OBSERVED` is its own badge.
- A Runtime Set with no concurrent measurement says so, rather than rendering
  "lost null% of its throughput".
- The expired-cursor message can state the retention floor again; it read a shape the server
  stopped sending when refusals moved onto the §4.5 envelope.
- A benchmark queued through NERVIS's command surface joins the caller's trace.

---

## RAVIS — 0.21.2

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Usage and upstream errors are in the conformance suite.** §15 lists five things it must
  pass and the suite had fixtures for three. A usage frame carries an empty `choices` and an
  upstream error carries none, so a proxy dropping either looked correct to every check.
- **A hard constraint is now proven to refuse at the route**, not only in the engine —
  including that a policy-refused request never reaches the upstream, which is what
  `LOCAL_ONLY` actually promises.
- The correlation context reaches log lines this codebase did not write: an outbound `httpx`
  call now names the inbound request that caused it.
- `restore-database --version` answers with its own sentence instead of a traceback.

---

## SIRVIS — 0.15.5

**Protocol:** MEP 1.0.0 · **Measures:** local models through LM Studio

- `restore-database --version` answers with its own sentence instead of a traceback.
- `results_path` publishes the results directory rather than the machine's layout.
- Evidence records carry `requested_configuration` beside the effective one, so a run that
  could not get the context it asked for says so.

---

## ecosystem-protocol — 0.2.0

**Protocol:** MEP 1.0.0 · **Consumed by:** SIRVIS, RAVIS, NERVIS

- **The MEP schemas exist.** `/ecosystem/version` had published `schema_versions` since Stage
  1 with no schemas behind it. Six are released under `protocol/schemas/`, generated from the
  models so they cannot drift from the code, with twenty-three conformance fixtures.
- **The event envelope carries the six §4.4 fields it was missing** — `event_version`,
  `subject`, `request_id`, `session_id` and `privacy`. `session_id` had a column, a reader and
  no writer anywhere.
- **Every log line written while serving a request names that request.** The formatter had
  always promoted the correlation IDs; nothing ever set them.
- **The event publisher backs off.** A failed batch was re-posted every two seconds forever,
  at a fixed interval, so two producers that lost the same collector retried in lockstep.
  Bounded now, capped at thirty seconds, and jittered.

---

## Known limitations

Published as gates rather than as a list, so they are counted rather than remembered:

- `tools/check_degradation.py` — §10's nineteen failure conditions, each scored, each with the
  gap that remains. Every one currently reads `PARTIAL`.
- `tools/pairwise_check.py` — §13.4's evidence states. Two are deterministic and *not*
  distinguishable: RAVIS cannot tell a withdrawn measurement from one that never existed, nor
  a runtime that could not be asked from a build with nothing measured.
- `tools/check_plans.py` — sixty-four milestone rows still carry the pre-§14.8 tick.
- `clarvis/docs/code-server-matrix.md` — graded against Clarvis 0.0.1 and not re-run since.

## Compatibility

`tools/check_compatibility.py` prints the matrix and holds it to what the peers actually
ship. It is not reproduced here: a table in a document is a second copy of the windows
`nervis/src/nervis/compatibility.py` declares, and the copy is the one that goes stale.

NERVIS answers the window on every `/api/v1/services` row, beside the version it judges —
**reported, never refused.** §12 says NERVIS must *tolerate* a peer one supported minor
behind during a rolling upgrade, and an upgrade happens one service at a time: a check that
refused would turn the ordering of one into an outage.

**All three peers are judged, since Clarvis 0.12.6.** The Bridge was the one peer NERVIS
could not hold to a window: the others state a version on `/ecosystem/identity`, and an
extension host has no such surface, so its claim is the only place a version can arrive — and
it carried none. It sends `build_version` now, taken from the value the Bridge already
publishes about itself rather than read a second time, and NERVIS answers the window beside
it on every instance row.
