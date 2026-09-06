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

## Clarvis — 0.12.7

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **A task handed over from NERVIS is now an offer, not an announcement.** It was posted into
  the transcript and nothing was armed, so no buttons appeared and the only way to act on a
  task somebody had just sent was to retype it. E-C8's exit says it *"is offered as a build"*,
  and a sentence with no answer attached is not an offer.
- **The handoff file survives until you answer it.** It was deleted at the moment of asking —
  for a good reason, so a declined task would not be re-offered at every window — but the same
  exit says the prompt is *"editable before it runs"*, and the document you were invited to
  edit was already gone. Saying yes now re-reads it from disk, so an edit counts; a task
  withdrawn between question and answer says so instead of running a document its author took
  back. **Operator note:** the offer names `clarvis-task.md` and waits.
- **A handed-over task asks before every step**, whatever the mode would otherwise do. A brief
  that arrived from another program has had no human hand on it, and §9's rule is that the file
  is evidence of what somebody asked for, never an instruction followed unreviewed.
- **The startup ordering moved into the pure decision that exists for it.** The handoff was a
  hand-placed `if` whose *position* was the rule, encoding an exemption — declining to plan a
  project is not an answer about a task somebody just sent — that lived only in a comment. That
  file exists because the same mistake has now happened three times in the same method.
- **The offer stopped going through the voice**, which was a defect on its own: it pinned
  `clarvis-task.md` as a fact the sentence never contained, so every rewrite was rejected,
  silently, after the model call had been paid for.

### 0.12.6

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

## NERVIS — 0.23.12

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **F9: `tools/run.py stop` on Windows now checks the same process marker POSIX always
  has.** `_alive()`'s Windows branch confirmed only that a recorded PID still existed via
  `tasklist`, never that it was still *this launcher's* process — so a PID reused by an
  unrelated process after a crash, reboot, or wraparound would be force-killed by
  `taskkill` believing it was one of NERVIS/RAVIS/SIRVIS/code-server (CWE-367, a Claude
  Security scan). Applied from the same patch pipeline as F3/F4/F8, two rounds: the first
  added a real marker check via PowerShell's `Get-CimInstance Win32_Process` but crashed
  `stop()` outright if `powershell.exe` was missing or blocked (AppLocker-style locked-down
  installs) — caught before shipping and fixed with a `try/except OSError` that fails
  closed instead, matching the function's existing behaviour for a PID that no longer
  exists. **Verification note:** this session has no Windows host; the fix is verified by
  reading, by independently confirmed `ruff`/`mypy`-clean status, and by a logic harness
  reproducing the exact PID-reuse scenario and both PowerShell-unavailable failure modes
  against the real applied file — not by an execution on real Windows.

### 0.23.11

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The clean-clone gate is green again.** A full end-to-end `tools/check_clean_clone.sh`
  run after F5/F6/F7 above found two failures, one real: 0.23.9's `residencyAuthHeader`
  ternary, inlined at its call site, pushed `sirvisRuntime`'s complexity from 13 to 14 —
  now its own named function, under the ratchet again with the identical behaviour
  `dashboard shaping`/`render`/`liveness` confirm unchanged. The other predates this
  session entirely and is unrelated to any of it: an import in `app.py` out of sorted
  order, and one test signature over the line-length limit in `test_origin_guard.py` —
  both trivial, both fixed while the gate was open.

### 0.23.10

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **F7: a committed enrollment secret is untracked, purged from history, and a gate now
  catches this class of mistake repository-wide.** `nervis/nervis.enrollment` — the bearer
  secret gating `POST /api/v1/registry/instances` — was committed and sitting in history
  despite `*.enrollment` already being in `.gitignore` since M8a: a gitignore pattern only
  stops a *future* `git add`, never untracks a path already committed. `git rm --cached`
  removed it from tracking (the file stays on disk at its required `0600`; nothing about a
  running installation changed), and a new gate, `tools/check_no_tracked_secrets.py`, tests
  every tracked file against `.gitignore` as if it were untracked, closing the actual gap
  rather than re-stating the rule. **History and rotation were the repository owner's calls,
  asked for explicitly rather than assumed:** on confirmation, a full backup bundle was taken,
  `git filter-repo` rewrote all 551 commits, two local-only refs from an unrelated tool
  session still holding the secret were found and removed, and the rewritten history was
  force-pushed to `origin/main` — verified after by confirming the secret's blob is
  unreachable from `main`'s object graph and gone from the repository entirely, not by
  trusting exit codes. The live secret itself was explicitly left unrotated, the repository
  owner's choice; GitHub's own internal caching of pre-rewrite commits (a direct SHA link, an
  open PR or fork) is outside what a rewrite here can guarantee, though none were found to
  check.

### 0.23.9

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **F5/F6 (paired with a SIRVIS fix): the Runtime screen's Owner column and self-lease
  detection now send the dashboard's own token on the one read that needs it.** SIRVIS's
  `close_session`/`renew_session` gained a real ownership check (see the SIRVIS entry
  below), which meant `GET /api/v1/runtime/residency`'s `owner` field became a real
  identity that had to stop leaking to an anonymous caller — and this dashboard reads that
  same endpoint with no token at all today. `liveSirvis`'s residency call now attaches the
  tab's own runtime token when it holds one, which is what un-redacts the value SIRVIS
  otherwise reports as `null`; every other `liveSirvis` caller is unaffected.

### 0.23.8

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **F2: the reply-text markup parser could be driven into a quadratic-time hang, and is
  fixed.** The same Claude Security scan that found F1 also flagged `_marked` in
  `nervis/src/nervis/layout.py` for uncontrolled recursion (CWE-674). Two rounds through
  the automated patch pipeline each failed adversarial review on a real defect — an O(n²)
  fix, then a fix that silently dropped italic styling — so this one was fixed directly.
  The root cause: an exhaustion cache meant to remember "no match past this point" was
  re-scanning the remaining text from scratch every step regardless, quadratic on any line
  with only two of the three mark kinds present. It now caches that exhaustion for real,
  and the italic pattern's lookbehind — unsafe once the scan moves forward through one
  shared string instead of recursing on ever-smaller slices — is re-applied by hand against
  the true original text instead. Verified with a 200,000-trial differential fuzz against
  the pre-patch implementation (zero mismatches) and linear timing up to 32,000 repeats
  across four adversarial shapes.

### 0.23.7

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **F1's network mitigation now covers Linux, not only macOS.** `unshare --net
  --map-root-user` — standard on every mainstream distro, no install needed — drops
  each gate into a fresh network namespace, probed for availability first since
  unprivileged user namespaces are disabled on some hardened or older distributions.
  **Windows gets no third mechanism; it gets a recommendation.** Native Windows has no
  equivalent this repository's own tooling can wire in without an administrator-level
  firewall change or a custom compiled helper — out of proportion for what this is. An
  operator wanting this guarantee on Windows runs these gates under **WSL2** (a real
  Linux kernel, so the Linux branch applies unchanged) rather than natively; WSL1 does
  not count, since it has no real network namespaces. `tools/sandbox_check.js` now
  proves whichever layer the running platform actually has, and states plainly when
  none applies rather than reporting a false pass. **Verification note:** the Linux
  branch is implemented and reasoned through carefully but has not been executed on a
  real Linux or WSL2 host this session — only macOS, where the full suite passes.

### 0.23.6

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The F1 mitigation now also closes network.** 0.23.5 stopped an escaped script from
  writing files or spawning processes, but Node's own permission model has no socket
  dimension at all — an escape could still call `fetch()` and quietly read a file out
  over the network, which mattered next to a repository that (as of the scan) has a
  committed secret in it. On macOS, every gate now also runs under a `sandbox-exec`
  Seatbelt profile (`tools/no-network.sb`) denying network access outright.
  `tools/sandbox_check.js` proves both halves independently — a file-write escape and a
  network escape, each run guarded and unguarded — because a script that reaches
  `process` has both available unless both layers are actually in place. **Stated
  plainly rather than silently:** `sandbox-exec` is macOS-only; on other platforms the
  gates keep the filesystem/`child_process` lockdown and network stays open, which
  `check_clean_clone.sh` now says out loud rather than implying full parity.

### 0.23.5

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **A Claude Security scan of this repository found a HIGH-severity `node:vm` sandbox
  escape, and it is mitigated at the process level.** `nervis/tools/page_context.js`'s
  `loadPage()` executes `index.html`'s inline `<script>` inside `node:vm` for every one
  of the dashboard's check tools — and `index.html` is exactly what an ordinary pull
  request edits. Node's own documentation says `vm` "is not a security mechanism", and
  the scan proved it: a script planted there gets a real `process` reference and, from
  it, `child_process`, achieving arbitrary code execution on whatever machine reviews the
  branch — no merge required. There is no complete fix inside `page_context.js` itself:
  the same escape is reachable through the file's own DOM-shim objects, not only the
  named built-ins, so a narrower patch would look closed and not be. Every gate now runs
  under Node's own permission model (`--permission --allow-fs-read=*`, no filesystem
  write, no `child_process`) — the escape still reaches `process`, but can no longer act
  on it. `tools/sandbox_check.js` is the new gate proving this holds: it reproduces the
  exact reported escape in a real child process, twice — once under the restricted flags,
  once without — and fails if the guarded run can still write a file. **Not yet done:**
  the fuller fix the scan also named, replacing `vm` execution with static parsing of
  `index.html`, remains an architectural decision for later.

### 0.23.4

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **`nervis serve` binding a real port with every peer absent is now proved, not
  assumed.** Every existing test drove the app through `TestClient`, an ASGI transport
  with no socket — none of them could have caught a failure to bind one. Two new tests
  run a real `uvicorn.Server` on port 0 and hit it with a real HTTP client: the service
  answers its health and serves the dashboard page, both with RAVIS and SIRVIS absent.

### 0.23.3

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **A page on another origin could mutate this service, and now cannot.** The control
  token protects six RAVIS-proxy routes; every other state-changing route — supervision,
  chat, background jobs, settings import, proposals, and more — checked nothing about
  where a request came from. This module's own reasoning for skipping CORS argued *"a
  header nobody's browser ever sends a request past"*, which does not hold: CORS response
  headers govern whether a page's script may read a response, never whether the browser
  sends the request or the server acts on it. Verified live before the fix: a forged
  `Origin` and a `Content-Type: text/plain` body — a browser "simple request", no
  preflight — flipped the supervision switch and wrote an attacker-chosen executable and
  argument list into the adapter table. **Not remote code execution**: every declared
  service defaults to `ownership=EXTERNAL`, and nothing in this codebase grants
  `nervis_managed`, so the launch step that would run that executable refuses
  unconditionally today — but the write went through unchecked regardless, and would
  become one the day that default changes without this being re-examined.
  `nervis.api.origin_guard` checks `Sec-Fetch-Site` (falling back to `Origin`) on every
  non-`GET`/`HEAD`/`OPTIONS` request, refusing anything that reads as cross-origin while
  leaving every read, and every non-browser caller, untouched.

### 0.23.2

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The provenance badge map is now a gate, not a fix that happened once.** `PROV_BADGE`
  and `prov()` exist because RAVIS's own rolling observation over real traffic wore the
  same green `MEASURED` badge as a SIRVIS benchmark run — found by a person reading the
  Evidence screen, on 5 September, with eighteen dashboard gates already running against
  the page and not one of them calling `prov()` with a value of its own choosing. The
  twentieth gate does: every kind either surface can actually send is checked against its
  expected badge, checked against the page's own CSS so nothing renders unstyled, and the
  regression itself — a benchmark and RAVIS's observation rendering identically — is its
  own named assertion rather than left to be implied by two rows of a table.

### 0.23.1

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The control-token gate is now checked for completeness, not just for the six routes
  that remember to declare it.** Reverifying §15's safety-gate line found the guard was a
  hand-written list of six paths agreeing with itself by construction — a seventh mutating
  route added without the guard would have shipped silently. The gate now walks the real
  route table and asserts that anything reaching `settings.ravis_admin_credential` requires
  the token, whatever verb it arrives on; proved by adding an ungated route and watching it
  fail. **Operator note, unchanged in substance but now written down where the checklist
  reads it:** the token is CSRF-grade, stopping a cross-origin page with no credential —
  it does not stop a local process that can already reach this port, which is the
  operating system's boundary rather than this one's.

### 0.23.0

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

## RAVIS — 0.21.6

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **F4: the credential listing no longer names identity credentials.** `GET
  /api/v1/providers/credentials` has no authorization guard by design (it needs to answer
  before anyone has exchanged a token), and it merged every stored credential name —
  including the `client.*`/`admin.*` bearer credentials `identity.py` uses to grant
  `may_write_credentials`/`may_write_configuration` — into its response with no filtering.
  Any unauthenticated caller could enumerate which application identities exist, and which
  one held admin rights: reconnaissance a Claude Security scan flagged as CWE-200. Applied
  from the same twice-verified patch pipeline as F3 and re-verified in this working tree:
  `list_credentials` now excludes `CLIENT_PREFIX`/`ADMIN_PREFIX` names (the real constants
  `identity.py`'s own authentication logic uses, not redeclared) before merging with
  `KNOWN_PROVIDERS`. Every legitimate provider row is unaffected; 18 targeted tests plus the
  full 969-test suite pass, `ruff`/`mypy` clean.

### 0.21.5

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **F3: a client-controlled model string could redirect the outbound Gemini request path,
  and now cannot.** `GoogleAdapter._path()` spliced `request.requested_model` — a string
  the router never validates for a translated provider like this one — straight into the
  outbound URL for both `complete()` and `_stream()`, so a shaped value such as
  `../../../etc/passwd` or one carrying `?`/CRLF could alter which path actually gets
  requested on Google's own host, using RAVIS's configured credential. A Claude Security
  scan found it; the suggested-patches job produced the fix, and it earned its patch file
  by passing an independent verifier and adversarial pass before this session applied it:
  `_path()` now rejects anything outside an allow-list of the shape a real Gemini model
  name takes (`[A-Za-z0-9][A-Za-z0-9._-]*`, no `/`, `%`, `?`, `#`, `:`, whitespace, or
  control characters — checked, not assumed, against Unicode-bypass and ReDoS shapes),
  raising the existing `TranslationError` (already mapped to a 400) instead. Every
  legitimate call shape resolves exactly as before; 28 targeted tests plus the full
  969-test suite pass, `ruff`/`mypy` clean.
- **`ravis serve` binding a real port is now tested — M0's last untested clause.**
  §15's "products build and run independently" line found it: `RAVIS.md`'s M0 was
  IMPLEMENTED rather than AUTOMATED VERIFIED specifically because "`ravis serve`
  binding a port alone is tested nowhere". A new test runs a real `uvicorn.Server`
  on port 0 with no upstream configured and drives it with a real HTTP client. M0
  moves to AUTOMATED VERIFIED.

### 0.21.3

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **The translated-path (Path B) disconnect test could not fail, and now can.** It asserted
  the adapter's generator closed, which its own `finally` guarantees whether the stream was
  abandoned early or drained to the end — so a proxy that ignored a client disconnect and
  read every remaining event anyway would have passed unchanged. Rewritten to drive the relay
  directly and count how many of the adapter's events were actually produced, mirroring the
  transparent path's `frames_pulled` check. Proved against an injected eager-buffering bug
  that pulled all fifty events before yielding the first frame: the new assertion catches it,
  the old one would not have.

### 0.21.2

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

## SIRVIS — 0.15.8

**Protocol:** MEP 1.0.0 · **Measures:** local models through LM Studio

- **F8: a caller-supplied model name or configuration value could no longer smuggle a flag
  into the `lms` CLI.** `LMStudioAdapter.load()`/`unload()` built `lms`'s argv straight from
  caller-controlled `model_key`, `context_length` and `gpu_offload` — JSON body fields from
  `POST /api/v1/runtime/sessions`/`POST /api/v1/runtime-sets` — with no check that any of
  them couldn't be read as one of `lms`'s own options rather than the value it named
  (CWE-88, a Claude Security scan). Applied from the same twice-verified patch pipeline as
  F5/F6 and re-verified in this working tree: a new `_as_lms_argument()` helper refuses any
  value beginning with `-` with `InvalidConfigurationError` (mapped to a 422) before it ever
  reaches `subprocess.run`, applied at every dynamic argv slot in both methods. Ordinary
  values reach `lms` exactly as before; 23 targeted tests plus the full 469-test suite pass,
  `ruff`/`mypy` clean.

### 0.15.7

**Protocol:** MEP 1.0.0 · **Measures:** local models through LM Studio

- **F5/F6: any RUNTIME-scoped caller could release or renew a session it did not open,
  and now cannot.** A Claude Security scan found `close_session`/`renew_session` checked
  only that the caller held *some* `Scope.RUNTIME` token, never whether it was the one
  that opened the session — and session ids are not secret, since the unauthenticated
  `GET /api/v1/runtime/residency` lists every live one by design. Sent through the
  automated patch pipeline, it took two rounds and a second objection (the pipeline
  declines rather than trying a third time blind), so this was fixed directly. `Lease.owner`
  is now the caller's real, server-verified identity rather than a self-declared request-body
  string, and close/renew require it to match (an `Scope.ADMIN` caller, or a session that no
  longer exists, is let through). **That fix's own first attempt introduced a real
  regression**, caught by its own adversarial verifier: binding `owner` to a real identity
  meant the intentionally-open residency read started disclosing every RUNTIME token's true
  label to an anonymous caller — the kind of fact `GET /tokens` already admin-gates for the
  same reason. `residency()` now takes a `reveal_owner` flag; `read_residency` sets it from
  whether the request carries *any* valid token (no particular scope required — this is not
  an authorization check), so an anonymous caller gets `owner: null` on every lease and a
  credentialed one — including NERVIS's own dashboard, now sending its token on this one
  read (see the NERVIS entry) — sees the real value exactly as before.

### 0.15.6

**Protocol:** MEP 1.0.0 · **Measures:** local models through LM Studio

- **`sirvis serve` binding a real port with no runtime present is now proved.** The
  existing standalone test drives the app through `TestClient`, which has no socket
  to fail to bind; a new one runs a real `uvicorn.Server` on port 0 and reaches it
  with a real HTTP client, LM Studio genuinely absent.

### 0.15.5

**Protocol:** MEP 1.0.0 · **Measures:** local models through LM Studio

- `restore-database --version` answers with its own sentence instead of a traceback.
- `results_path` publishes the results directory rather than the machine's layout.
- Evidence records carry `requested_configuration` beside the effective one, so a run that
  could not get the context it asked for says so.

---

## ecosystem-protocol — 0.2.1

**Protocol:** MEP 1.0.0 · **Consumed by:** SIRVIS, RAVIS, NERVIS

- **`session_id` reached the log record and stopped there.** `carrying()` has accepted it
  since the event envelope needed it, and `CorrelationFilter` copied it onto every record
  the same way it copies `request_id` — but `JsonLineFormatter`'s promotion tuple never
  named it, so it never reached the written line. The one service that already sends it
  (RAVIS, from `x-session-id`) now has it in its own logs. **Not closed by this:** SIRVIS
  and NERVIS still never read the header into their own correlation context at all, so
  §4.3's "logs carry ... session_id" is still only true for requests RAVIS serves.

### 0.2.0

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
- `tools/check_plans.py` — every milestone now carries one of §14.8's four states, and the
  states are themselves the limitation. Fifteen are **IMPLEMENTED**: code on the shipping path
  with no test exercising the row's own acceptance criterion. Twenty-nine are **AUTOMATED
  VERIFIED**: tested, never demonstrated against running services. Twenty-one are **LIVE
  VERIFIED**. The checkmark they replaced could not have told an operator which of the three
  they were reading, and for sixty-five milestones it did not.
- `clarvis/docs/code-server-matrix.md` — graded against Clarvis 0.0.1 and not re-run since.
- **`data residency`, listed in `RAVIS.md` §9.2 as a hard routing constraint, is unimplemented.**
  No provider declares a jurisdiction, `RoutingPolicy` carries no region field, and nothing
  refuses a route on it. The other ten hard constraints in that table are real; this one was
  advertised without a line saying otherwise, which the soft column's unimplemented entries
  already had and the hard column now has too.
- **NERVIS's control token is CSRF-grade, not authentication.** It stops a page on
  another origin from driving RAVIS's six proxied configuration mutations with no
  credential at all — the gap RAVIS's own stabilization work found. It does not stop a
  local process that can already reach NERVIS's port, because the token sits in the page
  NERVIS serves with no login of its own; a reader of that page could already do anything
  the page can do. `tools/check_clean_clone.sh` (`nervis pytest`) now also asserts the
  gated set is complete — every route reading `settings.ravis_admin_credential` requires
  the token — derived from the route table rather than a hand-kept list, so a new
  mutation added without the guard fails the build rather than shipping quietly.

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
