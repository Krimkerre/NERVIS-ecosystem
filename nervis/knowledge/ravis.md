# RAVIS — what it does and how it decides

RAVIS is the model gateway. Everything in the ecosystem that wants a model asks
RAVIS, and RAVIS decides which one actually serves the request. It speaks the
OpenAI chat-completions API, so anything already pointed at OpenAI or OpenRouter
can point at RAVIS instead without changing its code.

## How a request is routed

A caller names either a **pool** (`ravis/chat`) or a **model** (`gpt-4o-mini`).
Naming a model is honoured as-is — RAVIS does not second-guess an explicit
choice. Naming a pool is asking RAVIS to choose, and that runs in four stages.

**1. Hard constraints, before anything is scored.** Three independent sets: the
pool's own invariants, the request's requirements (context length, tools,
images), and the calling application's policy. A candidate failing any of them
is gone before ranking. This ordering is why a privacy constraint cannot be
outvoted by a good score — the candidate is never in the running.

**2. Health.** A model or provider whose circuit breaker is open is excluded
with its reason. Model-scoped and provider-scoped are separate, so one dead
model does not take its provider's other models with it. Since 12 September
2026 there is a third, narrower kind of rest: a model that refused a real tool
call is skipped for requests that carry tools for 30 minutes, and keeps
answering requests without tools — so Clarvis's chat still reaches it while its
agent moves on. The request it refused goes to the pool's next tool-capable
model. If every tool-capable model in a pool is resting, RAVIS answers "try
again later" (503) rather than "this pool cannot use tools", because Clarvis
would remember the second for the whole session. RAVIS → Diagnostics lists
resting models, and an admin can lift one early.


**3. Ranking, in this order.** When the caller's privacy level is "prefer this
machine" (`LOCAL_PREFERRED`), whether a model is local comes before everything
else. Then, for Clarvis's one-token tool check only, whether a model answers
without loading. Then session affinity — a conversation stays on one model for
consistency and prompt caching. Then whether the request would pay
a model load, when RAVIS has measured that this caller's sessions are short.
Then reseller preference. Then cost, placement, the pool's declared preference,
and reach. Size is consulted last and only for pools that declared a preference,
because "smaller is cheaper" is a tiebreak, not a ranking.

**4. The attempt chain.** The ranked list is walked in order. If the first
choice fails, the next is tried, and every attempt is recorded with its outcome
and elapsed time.

### What the health screen counts, and what it blames

Counting a failure and blaming one are separate, and `/api/v1/health` shows
both. An attempt that dies at the provider — the runtime is not running, the
connection never opens — is counted against the model it was aimed at *and*
against the provider, so a model that has never worked reads as never having
worked rather than as error-free. Only the provider's circuit breaker moves,
because a model must not be dropped from routing over its runtime being down.

A wrong API key moves neither breaker: `authentication` is counted on both rows
and blamed on neither, since a credential is configuration somebody can fix in a
minute rather than an outage to route around.

Timing is recorded only for the target actually responsible, so a connection
that never opened cannot make a model look slow.

## What it optimises for

Cost first for a pool that asked to be cheap; latency first for a pool that
asked to be fast. Between two free models the one that costs no network wins.
Hosted models are ranked on published price per million tokens; local models on
size, because size is the only cost a local model has.

**A context window is what the runtime serves, not what the model could hold.**
Ollama publishes the *architecture's* maximum — 128,000 for `qwen2.5vl` — while
loading the model at its own default, and RAVIS believed the larger number until
7 September 2026. A 25,000-token request was handed to a model holding 4,096,
and the runtime evaluated the first 2,050 of them and answered as though it had
read everything. RAVIS now reads `/api/ps`, which reports the window a resident
model actually has, and reports a cold model at the default it will be loaded
with rather than at its maximum. This machine starts Ollama with 32,768, and
RAVIS is told the same number by the launcher so the two cannot disagree. A
document larger than that is now refused with *"context window 32768 < estimated
N tokens needed"* rather than quietly truncated.

The same holds for LM Studio since 12 September 2026. LM Studio lists the most
each model build allows — 32,768 for `qwen2.5-coder-7b` — while a model it loads
on demand opens at its own default, which is 8,192 on this machine. RAVIS now
takes a loaded model's window from what it was loaded with, and counts a model
that is not loaded at 8,192, capped by the build's maximum. **So a local LM Studio
model that is not loaded no longer qualifies for a pool that needs more than
8,192** — `ravis/agent` asks for 32,768 and `ravis/long-context` for 131,072, which
leaves the long-context pool with no local model at all — until one is loaded that
large, and a
model loaded from the menu bar app opens at the same default. RAVIS does not guess
that 8,192: since 13 September 2026 the stack reads LM Studio's own default
context setting each time it starts and tells RAVIS the same number. So if you
change the default context in LM Studio's settings, restart the stack and RAVIS
follows. Someone who wants RAVIS to assume a different number can set
`RAVIS_LMSTUDIO_DEFAULT_CONTEXT`, which wins over LM Studio's setting.

**Clarvis's "can this model use tools?" check goes to a model that answers
without loading** (since 12 September 2026). Clarvis asks once per session with a
tiny request — one tool, a one-token answer — and gives it ten seconds; a model
that has to load first can take longer than that and make the whole agent look
unable to use tools. RAVIS now recognises that check by its shape and sends it to
a model already loaded, or a hosted one, ahead of the pool's favourite. With
nothing loaded and nothing hosted it is still answered, by the usual pick. Real
agent work still goes to the best model.

**"Prefer this machine" now comes before everything else in the ranking**
(since 12 September 2026). `LOCAL_PREFERRED` is the one privacy level that
ranks instead of excluding, so where it sits in the order is its whole
protection — and five things sat ahead of it: keeping a conversation on its last
model, avoiding a load in a short session, a pool that favours hosted or fast
models, and the tool check above; trying out an alternative model could also
land on a hosted one. Any of them could send a request off the machine when a
local model could have taken it. The preference now leads, the alternative tried
out stays local when the winner is local, and the route explanation says when
this preference decided the pick. One visible consequence: a conversation that
had drifted to a hosted model comes back to a local one on its next turn.

**Never a reseller when the maker is reachable.** An aggregator like OpenRouter
sells other companies' models with a margin on top. When the vendor's own
provider is configured and usable, its models rank ahead of the aggregator's
copies of the same models. Measured on this machine: Claude Haiku direct took
0.7-1.0s, the same model through OpenRouter 8.5-11.1s. It is a *preference* and
not a ban — the aggregator's copy stays in the chain as a fallback, because a
vendor catalogue can advertise a model whose endpoint answers 404, and a ban
there would leave no route at all.

**But only when the maker's own copy can actually serve the request.** Until
11 September 2026 the aggregator's copy was pushed to the back even when the
vendor's own copy had just been refused. Anthropic's model catalogue says nothing
about tool support, so Clarvis's agent pool, which requires tools, refused every
Claude model bought directly from Anthropic — and still ranked OpenRouter's
Claude, which can call tools, behind everything nobody resells. Every Clarvis
build from 5 to 11 September ran on Qwen3 Coder 30B, although that pool prefers
Claude Sonnet first. Now a resold copy only drops behind its maker when one of
the maker's own copies passed the same checks. On the same day the operator
declared Claude Sonnet 5 tool-capable, in the operator-capabilities file the
launcher gives RAVIS, so Clarvis's builds go to Sonnet 5 bought directly from
Anthropic. The next morning Claude Haiku 4.5 was declared the same way, so
Clarvis's chat, which also sends tools, goes to Haiku bought directly rather
than to Gemini 2.5 Flash Lite. Both declarations are the operator's word,
recorded at configured provenance, not measurements.

**A slow chatbot is a bad chatbot, so chat now weighs speed — last.** Until
9 September 2026 the conversational pool ignored measured response times
entirely: `ravis/fast` ranked on latency, `ravis/chat` did not, so two models
the pool liked equally were separated by nothing more meaningful than
alphabetical order, and the slower one won about half the time. Chat now adds a
speed term to its ranking, placed *after* everything that says what the pool is
for. Ordering matters more than the term itself: a model the pool was written
around still beats a faster stranger, so chat cannot degenerate into "whichever
tiny model replies quickest". It only decides between candidates the pool
already considers equals.

Speed is read in 750-millisecond buckets rather than as a raw number, because a
difference nobody can feel should not reorder anything — 620ms and 700ms are one
bucket and rank identically, while 600ms and 2,400ms do not. A model nothing has
measured yet is not punished for it; it sorts as though average, so an unmeasured
model still gets its turn and can earn a real timing. This is why the pool
carries `speed_tiebreak_ms` rather than the `prefer_fast` flag `ravis/fast` uses:
the same measurement, read as a tiebreak instead of as the purpose.

## Pools

Every pool is a standing description of what matters, so a caller states intent
rather than picking a model.

- `ravis/auto` — no constraint beyond what the request needs
- `ravis/chat` — conversation; a hosted model first, this machine's underneath
- `ravis/balanced`, `ravis/fast`, `ravis/performance` — the speed/cost/quality axis
- `ravis/cheap` — least money, preferring local
- `ravis/free-api` — costs nothing and runs on somebody else's hardware; logged,
  never private (see the free pool, below)
- `ravis/local` — never leaves this machine
- `ravis/api` — cloud only
- `ravis/private` — strictest privacy; cloud excluded
- `ravis/coding`, `ravis/reasoning`, `ravis/long-context` — capability-shaped
- `ravis/agent` — tools required
- `ravis/vision` — image input required; chat's save-time visual check uses it
- `ravis/draw` — models that *emit* an image, which is a different capability
  from reading one and shares no members with the pool above
- `ravis/clarvis-chat`, `ravis/clarvis-agent` — Clarvis's two roles

**The owner can rule a model out of a pool, and that outranks everything else a pool weighs**,
including a passing measurement, an operator's picks and the everything-fallback (RAVIS 0.24.1).
Today one model is ruled out: `openai/gpt-4.1-mini` never serves the coding pools
(`ravis/clarvis-agent`, `ravis/coding`, `ravis/agent`), because it skipped ticking plan steps in a
Clarvis build on 13 September 2026. It still serves the chat pools, and an explicit address like
`ravis/openrouter/openai/gpt-4.1-mini` still reaches it. The route explanation says "ruled out of
this pool by the owner".

A pool's constraint holds when things fail, not only when they are chosen. If
the local model behind `ravis/local` is unreachable — or accepts the connection
and never answers — RAVIS refuses the request rather than reaching for a cloud
model that is configured, eligible and answering. Sending the prompt off the
machine is the thing that pool exists to prevent, so it is not available as a
degraded answer.

## Asking for a picture rather than for words

A model that answers with an image is a model answering an ordinary chat
completion — the picture rides beside the text, and OpenAI's wire format has no
field for it. OpenRouter invented one: an `images` array of `image_url` parts on
the message or the delta. RAVIS emits that same shape on both of its paths,
because a picture that arrived one way through the transparent path and another
way through a translated provider would be the gateway creating the difference
it exists to remove.

That mattered immediately. Gemini's native API returns the picture as an
`inlineData` part beside the text, and RAVIS's translation kept only the parts
it recognised: `models/gemini-2.5-flash-image` answered *"Here you go: "* with
200 OK and no image, while the same model through OpenRouter returned a real
PNG. Which route was chosen decided whether the caller got a picture. Fixed
7 September 2026; the same day, `ravis/draw` was measured end to end and
returned a 1024×1024 PNG from `google/gemini-2.5-flash-image`.

Two things about that pool are deliberate. **A router that advertises drawing is
not a drawing model** — `openrouter/auto` publishes `image` among its output
modalities because something behind it can draw, then picks the model itself;
asked for a red circle it chose `z-ai/glm-5.2` and answered in words, so it is
excluded by name. And **the native Google models stay out**, because Google's
catalogue publishes no output modalities at all: `image_out` is UNKNOWN there
and an unknown fails closed. Naming one of those models directly still works,
which is the rule everywhere else too.

Nothing local draws. This machine's runtimes serve no image-generating model,
so `ravis/draw` is hosted-only in practice — and asking a vision model to draw
does not work, because reading an image and emitting one are separate
capabilities that happen to share a word.

The reverse does hold, and it is why one profile serves both directions: every
model in this pool reads images as well as emitting them, so a picture sent
*to* `ravis/draw` is understood and a picture asked *of* it comes back. Measured
in one conversation on that profile — an attached triangle described, a circle
drawn, and then the attached triangle redrawn in a different colour from the
picture itself.

**Session affinity is a preference, not a pin.** A conversation stays on one
model for consistency and prompt caching, and a turn that names a different
profile is routed afresh: the same conversation went to `amazon/nova-2-lite-v1`
for a reading and to `google/gemini-2.5-flash-image` for a drawing.

## What the local models on this machine are actually good for

Measured on 7 September 2026 rather than assumed, because "use local" and "use
the cloud" are usually argued rather than tested. Both findings below were
measured on the small models Ollama serves here — a vision model, a small chat
model and two embedding models. LM Studio serves many more beside them, several
of them larger, so read the live list at `/api/v1/models` rather than trusting a
list written down here, which goes stale.

**Bounded jobs: local, and it works.** The save-time layout glance on a PDF runs
on `qwen2.5vl:3b` and correctly named a real defect in a deliberately broken
page. Embeddings for chat's own background retrieval run on `nomic-embed-text`.
Both are single-purpose, small-input tasks, and they are free, private and fast
enough.

**Long documents needing synthesis: not local, on this hardware.** Asked for its
thoughts on a 42-page blueprint — about 25,000 tokens plus six rendered pages —
`qwen2.5vl:3b` took four and a half minutes and returned twenty-six characters
of nothing. The same question routed to a hosted model returned four thousand
characters citing the document's actual concurrency model and offline profile. A
3B model given that much dense material spreads its attention too thin; the
context window was not the limit, capability was.

So the honest split is by *size of job*, not by principle: bounded work stays on
this machine, long synthesis goes wherever the request's requirements lead. That
is also the argument for naming a **pool** rather than pinning a model — the
right choice depends on what the request carries, which changes every turn.

## What it will not do

**It does not route around a policy constraint.** A request that policy refuses
gets a structured no-route naming the reason, never a quiet substitution.

**It does not invent capability claims.** A model whose tool support is unknown
is UNKNOWN and fails closed, until an operator configures it or SIRVIS measures
it.

**It does not bill a background call to a frontier model by default.** A caller
can declare a request as background — a conversation title, say — and RAVIS
excludes paid providers unless told otherwise.

**It does not treat Codex as a chat model.** ravis/clarvis-codex names the Codex coding engine that
Clarvis will run through RAVIS, and it is not a pool. A chat or embeddings request naming it, or
any id under ravis/clarvis-codex/, is refused with a 400 that says so before anything runs, whether or
not Codex is set up. It appears in `/v1/models` only for a Clarvis that asks for it (0.17.0 and
later) while Codex is installed. RAVIS checks the Codex on this Mac once when it starts — the
Homebrew build, OpenAI's signature and the pinned version — and writes what it found to its log.
The id was ravis/codex until 13 September 2026, when the owner renamed it to sit with the
ravis/clarvis-agent and ravis/clarvis-chat pools (RAVIS 0.23.11); the old name is not kept as an
alias.

**It reports Codex's state and the ChatGPT plan's allowance, and signs Codex in.** `GET
/api/v1/codex` answers any caller from memory with one state — checking, not installed, not
available, paused for re-testing, process restarting, signed out, sign-in expired, a different
account, allowance used up, or signed in — and the allowance left in each window with when it
resets: an allowance, never a cost, and unknown rather than zero. With an admin credential,
`/api/v1/codex/sign-in` starts, shows and cancels the ChatGPT browser sign-in,
`/api/v1/codex/sign-out` signs out, and `/api/v1/codex/account/confirm` confirms a changed
account; `/api/v1/codex/version-check` and `/api/v1/codex/accept-version` check and accept a
Codex build RAVIS hasn't tested. The file-rules re-test, `/api/v1/codex/reprove`, starts only from
the menu bar with the owner's own credential. RAVIS runs one Codex process for this, and only for a
tested or accepted build, and the pinned Homebrew Codex 0.154.0 stays paused for tasks until its
file rules are proven.

**It runs Codex tasks for Clarvis** (RAVIS 0.23.15). A Clarvis window starts a task on
`/api/v1/agent-sessions` in a project inside the coding folder — never the ecosystem's own
repositories or the coding folder itself — follows it on the task's event stream, answers Codex's
approvals and questions, steers it, stops it and saves its work. Codex saves its work as commits
on a branch of its own, so the project has to be a git repository: since Clarvis 0.17.2, a task
refused because the folder has no git offers **Set up git here** in the chat (git init and a first
commit, then the task carries on), even if Clarvis's own git offer was declined before, and typing
"git init" does the same. Folders NERVIS hands a task over in already start as git repositories
(NERVIS 0.29.3). Since Clarvis 0.17.3, when earlier Codex work is still on a branch that isn't
merged, Clarvis asks before a new Codex task whether to **Build on** that branch (Codex picks its
earlier task back up there and keeps the conversation) or **Start fresh** from the project's trunk;
Unattended picks on its own and says which. The task keeps running with no
editor open, and another window can reattach to it. Only a Clarvis credential holding the task's
token may do any of that; NERVIS and admin credentials are refused. The menu bar and the dashboard
can only stop a task, after confirming its folder and turn. A question nobody answers pauses the
task after 30 minutes with no editor open, or 2 hours with one; RAVIS never approves anything
itself. Codex's commands reach only sites the owner approved: when one is blocked, Clarvis asks whether to allow it (allowing adds it while Codex runs; package registries and GitHub are allowed from the start). Tasks stay refused while the file rules are
unproven. Calibration exists only while RAVIS runs with `RAVIS_CODEX_CALIBRATION=1`; the owner
starts it from a terminal with `tools/run.py codex calibrate` on two throwaway git projects, and it
asks sixteen questions of Codex using the plan's allowance. Only a full run in which every
must-pass question passes marks the file rules proven; a failure of the decoy-file questions keeps
them unproven and sends the decision back to the owner. The first real run (13 September) passed
the file-rule questions but failed the network and stop questions, against rules the owner has
since replaced. Since RAVIS 0.23.16 those questions check what was decided — commands reach only
approved sites, a site allowed while a task runs reaches that task with no restart and nothing lost, a stopped turn
leaves nothing unanswered, and a Codex that never asks for permissions is recorded rather than held
against the run — so the next full run can prove the rules, with no override profile. The fifth run
still failed two questions, and RAVIS 0.24.2 fixed both. Allowed sites were being ignored: RAVIS
used to hand Codex its site list as a start-up option, and Codex lets a start-up option outrank any
site added later. Now RAVIS writes the default sites into Codex's own settings each time Codex
starts, and no task starts until Codex has taken them; if it doesn't, Codex's state says why and
RAVIS tries again at the next start. The stop question could never see all its commands, so each
project now runs one command that keeps its long-running processes alive together.
The sixth run (14 September, those two questions only) failed both again. The stop question had
counted three short-lived start-up shells as project B's commands; RAVIS 0.24.3 made it judge only
the long-running commands it started, and the seventh run passed it. The network question still
failed: Codex takes the allowed site, but a Codex conversation that is already open keeps the site
list it started with, in its next step too. RAVIS 0.24.4 makes calibration try two more ways to carry
a task on without losing its progress — reopening the conversation from disk, or copying it with its
history — and the owner approved a run to find out which one works. The eighth run found it:
reopening the conversation once Codex had let go of it (about a minute) reached the site. The full
run that followed passed every must-pass question, so the file rules are proven for Codex 0.154.0
and Codex is no longer paused (RAVIS 0.24.5).

**It lets the owner allow sites before a task, and carries a task on after one is allowed** (RAVIS
0.25.0). Clarvis can add the sites a task will likely need before Codex starts, through
`/api/v1/codex/sites`; RAVIS adds only exact public website names, and nothing at all if one is
refused, and its own default sites can't be removed. When a step is blocked from a site anyway, every
site that step was blocked from is asked about together, on one card. Because an open Codex
conversation never sees a site allowed later, RAVIS lets go of the conversation straight away and
reopens it once Codex has let go (about a minute), so the owner's "carry on" reaches the newly allowed
site; if Codex hasn't let go within two minutes, RAVIS carries on and warns that the site may still be
blocked. Each task can also run at a chosen effort, from the levels Codex offers its model.

**It keeps Codex to the skills the owner chose** (RAVIS 0.26.0, 14 September 2026). A skill is a set
of instructions Codex can decide to follow. In the first live test Codex found one of the owner's own
skills on this Mac, "graphify", and ran it unasked: it made a folder in the project and used some of
the plan's allowance. Now NERVIS has a skills folder of its own, `clarvis/skills` in the NERVIS
workspace, which RAVIS makes (with a short README) whenever Codex starts. Skills in that folder are on,
and so are the ones built into Codex; the owner's personal skills (`~/.agents/skills`) are off, a new
one included, until the owner switches one on. RAVIS remembers each switch and puts Codex back to it at
every start and whenever a skill file changes; if it can't, no Codex task starts and the dashboard
says why. A change counts from a task's next start or reopen. So that no task can write a skill into
that folder, **Codex can no longer be started on "NERVIS workspace" or on its "clarvis" folder as a
whole**; the task folders under `nervis-tasks` and every other project are unaffected. Clarvis's own
engine can still write there when the owner has the workspace open, and a skill it writes is on for
Codex. Not yet checked with a real Codex task: that a switched-off skill is really left out of what
Codex reads.

**It lets the other models use skills too** (RAVIS 0.27.0, 15 September 2026). Clarvis's own engine
and NERVIS chat can use skills as well now. Every skill has two switches: one for Codex, and one for
"the other models" (Clarvis's own engine and NERVIS chat together), so a skill can be on for one and
off for the other. RAVIS reads the skill folders itself for this, so it works even while Codex is
off: NERVIS's skills folder, and the owner's personal skills in `~/.agents/skills`. Skills in
NERVIS's folder start on for both; personal skills start off for both, a new one included; Codex's
built-in skills are for Codex only. The other models get a short list of the skills switched on for
them — each one's name and one-line description — and read a skill's full instructions only when
it fits what was asked. RAVIS only ever hands out a skill that is switched on. It never hands out a
skill with a broken header, a file bigger than 64 KB, a hidden file, or anything reached through a
link leading out of the skill's folder, and it logs every read without what the file says. A switch
for the other models counts from their next request. Every switch the owner had already made for
Codex was kept. The Skills page reads and switches through `GET /api/v1/skills` and
`POST /api/v1/skills`; the programs calling the other models read `GET /api/v1/skills/models` and
`GET /api/v1/skills/models/read`; the older `GET /api/v1/codex/skills` still answers, for Codex alone.

**It installs skills, and has a marketplace to find them** (RAVIS 0.28.0, 15 September 2026). From
NERVIS's Skills page the owner can install a skill from a GitHub link to a skill's folder, from a zip
file, or from a website that publishes an Agent Skills index. Nothing is installed before a review:
RAVIS first downloads or unpacks the skill into a private staging folder of its own, checks it
against the Agent Skills specification (agentskills.io) and its own safety rules — no paths leading
out of the skill, no links, size limits, one skill at a time — and shows its name, description,
license, its files with scripts marked, and its SKILL.md. **An installed skill arrives switched off**
for Codex and for the other models, so the owner decides when it gets used; a skill copied into the
folder by hand still starts on. **Update** fetches the skill again from where it came from and shows
what changed; if its SKILL.md or a script changed, both switches go off again, and it never updates
by itself. **Remove** moves the skill's folder to the Trash, and works only for skills RAVIS
installed. The marketplace lists skills from anthropics/skills, openai/skills (marked deprecated by
its owner), ComposioHQ/awesome-claude-skills, the VoltAgent/awesome-agent-skills link list, and
skills.sh's search, plus GitHub repositories, link lists and websites the owner adds. Listings are
kept for a day, and a link list's links are looked up only when shown, to stay inside GitHub's
hourly limit for requests without a token; when GitHub is rate-limiting RAVIS, the page says when to
try again. RAVIS talks only to GitHub, skills.sh and websites the owner added, over https, and never
sends a password or token. Not yet tried against the real GitHub, websites or skills.sh.

**It keeps one writer per project for both engines, and cleans up after Codex** (RAVIS 0.24.0).
Clarvis's own coding runs take the project lock through `/api/v1/project-locks`. A window can take a
project over from another window that is gone, unresponsive or waiting — never from a Codex task,
which is joined instead — and RAVIS stops whatever that window had running first. Switching a task
between Codex and Clarvis's own engine hands the lock over. RAVIS knows which of Codex's processes
belong to which task by the folder each command runs in, and a Stop ends only that task's; a process
that could belong to two tasks is reported, never killed. After a restart RAVIS ends only what it
recorded, and leaves a project an editor still holds alone until that editor lets go. A new Codex
build no longer waits behind a question nobody answers: ten minutes after it appears, such a task
is paused.

**A dead NERVIS never makes RAVIS report itself unready.** This regressed
once for real: a readiness check that read the event publisher's own
dropped-event count made a dead collector look like a RAVIS problem, which
broke the rule that a collector outage leaves every product healthy. The fix
was to stop checking it — a dropped event is logged, not turned into an
opinion about RAVIS's own health — and it is now a route-level regression
test rather than only a code comment: overflow the publisher's buffer against
a collector that refuses every request, and readiness still reads true.


### What happens to events while the hub is unreachable

Events outlive a hub outage rather than being retried into one. Each service
buffers what it could not publish, up to a fixed number, and drops the *oldest*
when that fills — the newest events are the ones somebody is looking at — and
counts what it dropped, so a gap in the timeline is visible rather than silent.

When the hub answers again the buffer is sent in order, and each event carries
an id derived from what it describes rather than minted per attempt, so a
re-send arrives as the same event rather than as a second one.

## What an operator can ask it for

Live: which providers are reachable, which models are routable, what a specific
routing decision chose and what it excluded and why, per-call usage and
estimated cost, sessions and their model affinity, and the pools themselves.

Every routing decision is recorded rather than recomputed, because re-running a
router later uses a different catalogue and can reach a different answer — an
explanation you recompute is a guess about the past.

**Kept for thirty days, since RAVIS 0.30.0 (18 September 2026).** They used to
be held in memory only — 200 of them — so a decision fell off the end after 200
requests and a restart emptied the list. Now the whole explanation is written
down: every candidate considered, every one excluded and why, and what the
request itself needed. Old records are deleted after thirty days, or sooner if
there are more than fifty thousand, so it cannot grow forever. The most recent
200 are still kept in memory as well, so looking at the dashboard doesn't touch
the database.

**You can ask what RAVIS would choose today.** Any stored decision can be routed
again against the models, prices and health of right now, and RAVIS says whether
the answer would be different — useful after a price change, a model being
withdrawn, or a pool being edited. It contacts no provider and costs nothing: it
only decides. Two things it can't reproduce, and says so: which model the
conversation was already on, and the occasional deliberate choice to try
something unmeasured, both of which are facts about a live request.

**No message is ever stored.** What is written down is identifiers and reasons —
which capabilities the request needed, how much context it was estimated to take,
what it capped its answer at — and never the text of anything anybody typed.

## Where provider credentials live

(Not a provider key, but a credential RAVIS holds: since RAVIS 0.29.2 it sends NERVIS a secret
with its events, given to it by the launcher as `RAVIS_NERVIS_EVENTS_SECRET`. NERVIS 0.34.18
refuses events without one, so a RAVIS started without the launcher has its events refused, and
NERVIS's Events screen says so.)

Keys go to the **platform keyring** — macOS Keychain, or the Secret Service on
Linux — written through the tool the platform already ships, with the secret on
standard input so it never appears in the process list. Every write is read back
and compared before it is trusted, because macOS's interactive parser unquotes
what it reads and will drop a backslash; a mismatch falls through to the file
rather than storing a mangled key that would fail later as an authentication
error.

Only the *name* is kept beside the credential file, so RAVIS can still list what
it holds without asking the keyring to be searched — client and admin identities
are matched by walking stored names, and a name that vanished with its value is
an identity nobody can authenticate with.

Where there is no keyring — Windows, a headless server, or
`RAVIS_CREDENTIAL_KEYRING=0` — a `0600` file in RAVIS's config directory holds
them, which is plaintext and is said so rather than promised away. That switch
means *leave this machine's keyring alone entirely*: not read, not written, not
deleted from. Reads are file, then keyring, then environment.

Providers with a credential row: Anthropic, DeepSeek, Google AI Studio, OpenAI,
OpenRouter and xAI. The last two of those were added on 9 September 2026 and
need nothing but an address, because both speak the OpenAI protocol.

Saving a key also refreshes that provider's model list, so a new key's models
appear straight away. Since 12 September 2026 saving the *same* key again does
not: when the stored key did not change and the list was last refreshed
successfully less than a minute ago, RAVIS reports the count it already has
instead of fetching again. A double click, or a save retried after a timeout, no
longer repeats a network call.

## Signing Codex in to the ChatGPT plan

Since 13 September 2026 RAVIS runs its own copy of Codex, OpenAI's coding agent, and
that copy has to be signed in to the owner's ChatGPT plan before it can do anything.
It is done on the dashboard, on **RAVIS → Credentials**
(`http://127.0.0.1:8790/#/ravis/Credentials`), in the card headed *ChatGPT
subscription (Codex)*, below the provider keys. `tools/run.py codex sign-in` does the
same from Terminal. It is a separate sign-in from the ChatGPT app's own.

- **Signed out:** press **Sign in with ChatGPT**. A new tab opens on OpenAI's sign-in
  page; sign in there. The card shows *signing in*, a link to open the page again if
  the tab was closed or the browser blocked it, the time left (the page lasts ten
  minutes) and **Cancel**. It notices by itself when the sign-in is done.
- **Signed in:** the card shows the account as a hint, like `o…@example.com`, and the
  plan. **Sign out…** takes two clicks.
- **A different account:** when Codex is signed in to another account than the one
  confirmed, the card asks **This is my account** (with the hint) or Sign out. Until
  one of them is pressed, Codex takes no new work.
- **A sign-in that didn't finish** — the page expired, the sign-in failed, or RAVIS
  restarted while the page waited — shows why, with **Try again**. A restart loses
  the sign-in because the page's listener lived in the Codex process RAVIS was running.
- **"Another program is holding the sign-in ports 1455 and 1457":** Codex's sign-in
  listens on one of those two, and the ChatGPT app's own Codex uses the same two when
  it signs in. Finish or close that sign-in, then Try again.
- **Not installed, not available, or the process restarting:** there is nothing to
  sign in to yet, and the card shows RAVIS's reason and no button. While Codex is
  *paused for re-testing* on a build RAVIS has tested, signing in still works; only a
  Codex build RAVIS has never tested gets no process to sign in with.
- **Older RAVIS:** before RAVIS 0.23.9 the card says that RAVIS doesn't offer the
  sign-in yet.

The screen never shows a password or a token, and RAVIS never gives out the account's
full email address. The sign-in page's address is a live way into the sign-in until it
ends, so RAVIS hands it only to an admin credential: the dashboard gets it through
NERVIS's control route `/api/v1/ravis/codex/sign-in`, which checks the page's control
token, presents NERVIS's RAVIS admin credential and tells the browser not to keep a
copy. RAVIS's public state, `GET /api/v1/codex`, says whether a sign-in waits and until
when, but never where.

### How much of the plan's allowance is left

RAVIS → Dashboard's headline row has a **Codex** tile beside Spend (since 13 September
2026, where the Active profile tile used to be; the active profile is on RAVIS →
Settings). It is kept compact: what is left in the window that runs out first — "62%
left" — and one line with that window and when it resets ("weekly · resets in 6 days"),
with a small chip when Codex isn't ready (such as "paused") or the reading is stale.
Hovering the tile, or tabbing to its question mark, opens a tooltip with the rest:
RAVIS's reason, the plan and account hint, every window (the 5-hour and the weekly one)
with what is left and its exact reset day and time, how old a stale reading is, and
that this is the plan's allowance, not money. The Overview carries a one-line summary.

It is the ChatGPT plan's allowance, not money, and never a cost. **Unknown** means RAVIS
hasn't read the allowance yet — while Codex is idle it reads it every 15 minutes — and is
never shown as 0%. **Stale** means the last reading is more than 30 minutes old, and the
tooltip says how old. Signed out, the tile links to RAVIS → Credentials.

### Codex's tasks, allowed sites and new versions: the Codex card

Since 14 September 2026 (NERVIS 0.29.0) RAVIS → Dashboard has a **Codex** card under the headline
tiles (`http://127.0.0.1:8790/#/ravis/Dashboard`); the Overview's Codex line counts the tasks and
opens it.

- **Tasks.** Each Codex task RAVIS is running or holding: its project folder (never a path); its
  state — running, waiting for your answer, paused after waiting 30 minutes for an answer, paused
  because Codex updated, finished and needing review, uncertain after being cut off mid-step, or
  stopped with processes left over; how long it has waited or run; the model and effort it runs at;
  how many editors have it open; and *reconnecting* while RAVIS reopens its Codex conversation so a
  site the owner just allowed can be reached, which takes up to two minutes. A project Clarvis's own
  engine is writing in is listed too, marked as not Codex.
- **Stop… is the only thing the dashboard can do to a task.** It sits beside a running or waiting
  task and takes two clicks: the first says what will happen, the second stops Codex's current step
  and the commands it started. Codex's work so far stays in the project, to review and save in
  Clarvis. The stop names the folder and turn the card showed: if the task moved on meanwhile, RAVIS
  stops nothing and the card says the task changed and shows the fresh list. A click retried after a
  lost answer is recognised by RAVIS and never stops anything twice. Answering, approving and steering
  happen only in Clarvis.
- **Allowed sites.** The websites Codex's commands may reach: RAVIS's defaults (package registries,
  GitHub and the like), folded away and never removable, and the sites the owner allowed through
  Clarvis, each with **Remove** (two clicks). A removed site stops reaching Codex conversations that
  start or reopen afterwards; a task already running keeps reaching it until its conversation
  reopens. While Codex isn't running the list can't be read, and the card says so.
- **Skills** moved to a page of their own, NERVIS → Skills, in NERVIS 0.32.0: the card keeps a line
  that opens it. NERVIS 0.30.0 and 0.31 listed Codex's skills on the card, each with a one-click
  switch.
- **A new Codex version.** When Homebrew installs a Codex build RAVIS hasn't tested, new tasks pause.
  **Check this version** shows RAVIS's seven checks on the build and what changed since the tested
  one; **Use this version…** (two clicks) accepts it. Accepting doesn't start the file-rules re-test
  and spends none of the plan's allowance; tasks stay paused until the re-test proves the rules, and
  the re-test starts only from the menu bar (NERVIS → Codex → Re-test the file rules…), using one
  short Codex turn.

Behind the card NERVIS forwards four control routes to RAVIS with its RAVIS admin credential, each
after checking the page's control token: a task's Stop, to RAVIS's owner Stop; removing a site;
the version report; and accepting a version. A task id, the stop's key and a site's name are
checked before anything reaches RAVIS. The Skills page's switch has a control route of its own,
which carries only the skill's path, which engine, and on or off.

Each Codex task's commands have a temp folder inside the project, `.clarvis/tmp/<task id>`. Since
RAVIS 0.26.3 (15 September 2026) RAVIS removes it whenever the task has nothing running — once its
work is saved and it waits for a follow-up — and when the task ends, and makes it again just before
Codex does anything more in that task: the next turn, or reconnecting the task to Codex. If RAVIS
can't make it, that turn is refused with a message saying so. `.clarvis/tmp` and `.clarvis` go too
when RAVIS made them and nothing else is in them. The folder stays while a turn runs or is being
stopped, while commands aren't confirmed stopped, and while work waits to be saved, for any task of
the project using it. RAVIS never follows a shortcut out of the project and never touches Clarvis's
own lock or checkpoint files. RAVIS 0.26.2 removed the folder only when a task ended, but Clarvis
never ends a task — it keeps finished ones open for follow-ups — so the folders stayed; RAVIS removes
those the next time it starts.

### Codex in the menu bar

The menu bar app has a Codex line under the model runtimes, read from `tools/run.py status --json`:
the task count or Codex's state, each task with how long it has waited, and the allowance per
window. Clicking the line opens the Codex card. Each task's submenu shows the model and effort and
has **Stop this task…**, which asks first, naming the folder, and runs `tools/run.py codex stop`
with the owner's own command-line key. **Re-test the file rules…** appears for an accepted Codex
version whose rules aren't proven yet, and asks first, saying it uses one short Codex turn of the
plan's allowance; for a version nobody has accepted, the menu points to the Codex card instead.
**Sign in to Codex…** appears while Codex is signed out, and **Cancel the Codex sign-in** while a
sign-in waits. The line is never red. The copy of the app in /Applications shows it once it has been
rebuilt from NERVIS 0.29.0.

## Trying a model on purpose, so it can be measured at all

Two switches under **Model** in chat's settings, both off unless switched on.
The first, *"occasionally try a different model"*, spends about one turn in
twelve on a model that was **not** the best pick. The second, nested under it,
aims those turns at models nothing has timed at all.

**Why they exist.** A model that is never chosen is never measured, and a model
that is never measured is never chosen — a loop that closes on itself. It got
teeth once chat started ranking on speed: an untimed model sorts as merely
average, which is enough to keep it out of first place indefinitely. And it
cannot be solved by benchmarking, because SIRVIS drives local runtimes only —
load time, memory pressure and thermal readings do not exist for an API — so a
hosted model is measured by being used, or not at all.

**What it costs, stated plainly.** A worse answer some of the time. That is the
entire trade, and the reason nothing switches this on by inference. When it does
fire, the route explanation says so in as many words — *"trying X on purpose"* —
rather than describing the choice as though the model had won on merit.

**Where the randomness lives, which is not where it looks.** The routing engine
is a pure function of its arguments and a gate enforces that: identical inputs
must produce an identical decision. So the dice are thrown at the edge, in the
API layer, and only the result is handed to the router. Same throw, same route;
still random across requests.

The model that would ordinarily have won stays first in the fallback list, which
matters more here than usual — an untried model is exactly the one most likely
to fail.

**An exploratory pick that refuses a setting no longer costs the reply.** On 11 September 2026 exploration picked `gpt-5.6-sol`, OpenAI refused the request because that model only accepts `max_completion_tokens`, and chat showed "the model returned an empty message". RAVIS now sends OpenAI the name it wants, treats "this model does not support that parameter" as a reason to try the next model rather than a bad request, and reports a failed stream as the error it was — so the usual pick answers instead, and a real refusal reads as one.

**And no exploratory pick costs the reply at all (RAVIS 0.28.2, 16 September 2026).** Exploration picked `gpt-4.1-2025-04-14`, and OpenAI refused the turn with "Unrecognized request arguments supplied: explore, explore_prefer_unmeasured, reasoning_effort". Two of those were RAVIS's own instructions, which it had been passing on to every provider since 9 September; OpenAI is the one that refuses what it doesn't know, which is why no NERVIS chat message to an OpenAI model had ever succeeded. The third, `reasoning_effort` (chat asks every model not to think before an ordinary reply), is a real setting gpt-4.1 doesn't take. Now:

- RAVIS keeps `explore`, `explore_rate` and `explore_prefer_unmeasured` to itself.
- A model that names settings it refuses, all of them ones that only tune an answer (thinking, temperature, top-p and the like), is asked once more without them, and they are left out for that model for 30 minutes. A spending limit, tools, the answer's format and stop words are never dropped; a refusal naming one of those goes to the next model.
- Whatever an exploratory pick fails with, the model that would ordinarily have answered does, and the failed model is not explored again for 30 minutes. A safety refusal still stops the chain.

A route decision's attempts list `dropped_parameters` and `held_from_exploration`.

**What a streamed reply used is counted for every provider (RAVIS 0.28.3, 16 September 2026).** OpenAI and LM Studio only say how many tokens a streamed reply used when the request asks, and neither chat nor Clarvis asked, so the first OpenAI calls RAVIS recorded had no counts and no cost, and the budget read them as free. RAVIS now asks (`stream_options.include_usage`) on any streamed request that didn't say either way. The stream then ends with one extra frame that carries the counts and no text, which chat already received from OpenRouter and skips. A provider that refuses the question is asked again without it. A dated build such as `gpt-4o-2024-08-06` now finds the rate written for `gpt-4o`. A model with no rate in `prices.json` still shows no cost, only its tokens; gpt-4.1 has none written down.

## How busy RAVIS is (RAVIS 0.29.0, 16 September 2026)

RAVIS → Diagnostics has a **RAVIS load now** card, read from `load` on RAVIS's `/api/v1/health`:

- how many requests RAVIS has running now, and how many of those run on this machine;
- each provider and model with its count, the number of attempts since RAVIS started, and the most
  it has had at once;
- memory free and whether it counts as under pressure;
- each provider that answered "too many requests" or "overloaded", when it last did and how often;
- the limits providers state on their answers (requests or tokens left, when they reset, a
  "wait N seconds"), each with its age.

RAVIS keeps no queue of its own, and LM Studio and Ollama don't report theirs, so those are said as
such rather than shown as zero. RAVIS doesn't read provider balances. Since RAVIS 0.29.1 the card also shows
**models measured together**: for each pair SIRVIS benchmarked as a Runtime Set (say a chat model
and a coding model loaded at once), how much slower each got to its first word and its output in
each condition (one after the other, taking turns, at the same time), the least memory left, when
it was measured and SIRVIS's notes, with a warning when the measurement is older than RAVIS's
30-day window or the two didn't fit together. Only pairs of models RAVIS can reach right now are
shown, so with LM Studio off it says there are none.

**Since RAVIS 0.30.0 (18 September 2026) some of it changes which model RAVIS picks.** The owner
decided: a provider that has just answered "too many requests" or "overloaded", or asked RAVIS to
wait and the wait hasn't run out, or whose own answer says it has almost no allowance left, is
ranked *below* an equally suitable model somewhere else. Nothing is ever refused for being busy —
a busy provider is working, and dropping its models would empty pools only it can fill — and RAVIS
still keeps no queue. The route explanation says when this moved anything, so a model you didn't
expect on top comes with a sentence explaining why. Plain counts of what RAVIS has running change
nothing, on purpose. For a hosted provider RAVIS isn't the only thing calling that account and
doesn't know the ceiling. For a local runtime the wait is real — LM Studio does queue a second
generation — but this preference sits above the pool's own, so treating a busy LM Studio as a reason
to look elsewhere would send the next request to a paid provider. Spending money because your Mac is
busy is your decision to make, not something routing should do quietly. Say the word if you want it.

The **RAVIS health** card above it has two rows about RAVIS's **first provider only** (NERVIS 0.34.7):
whether it answers and how many models it lists. With LM Studio first and switched off they read "not
answering" and 0 while every other provider works; the Provider health table covers all of them. There,
**"not probed"** means RAVIS hasn't sent that provider a request since it last started, so it has no
breaker or error rate yet; it is not a fault. The **Conformance** tiles say "not run here" because the
Clarvis conformance suite runs only from the command line (`ravis conformance clarvis`); it ran 24
checks and passed on 16 September 2026.

## Where the prices come from, and why some are approximate

RAVIS ships **no built-in price list**. Every hosted rate is written down by the
operator in `~/.config/ravis/prices.json`, keyed by the vendor's own model id,
in dollars per million tokens. This is deliberate: a rate baked into the source
goes stale silently and nobody can say when it was true, whereas a file someone
wrote has both an author and a date. Of the providers configured here only
OpenRouter publishes per-token figures in its catalogue; OpenAI, Anthropic,
Google, DeepSeek and xAI publish catalogues with no pricing in them at all, so
without that file their calls cost `UNKNOWN` — which a budget reads as *nothing
has been spent*.

**Those rates are also what routing ranks on, since 12 September 2026.** A
hosted model whose provider publishes no price used to reach ranking unpriced,
so two direct builds of one family tied and the tie went to alphabetical order —
the oldest build first. It now ranks on the price in that file, and a dated
build name such as claude-haiku-4-5-20251001 finds the price written as
claude-haiku-4-5, which also stopped those calls costing UNKNOWN. A price the
provider does publish is never replaced.

DeepSeek and xAI rates were added on 9 September 2026 from each vendor's own
documentation. Both vendors bill **two tiers**, and the file holds one rate per
model, so two deliberate choices were made:

- **DeepSeek charges double during peak hours** (01:00–04:00 and 06:00–10:00
  UTC, Monday to Friday; everything else, weekends included, is half price). The
  file states the *peak* rate, so an off-peak conversation is reported as
  costing about twice what it did. Overstating is the safe direction — a budget
  that under-reports is worse than one that flatters itself.
- **xAI charges double once a single prompt reaches 200,000 tokens** — the whole
  request, not just the excess. The file states the under-200K rate, because
  ordinary conversation is nowhere near that and doubling every message would
  make the running total meaningless. A genuinely enormous prompt is therefore
  reported at about half what it cost.

Neither distortion touches routing decisions between vendors, since both models'
rates move together. It only affects the figure on the spend screen.

**A call whose token counts went missing was priced as nothing.** RAVIS reads
the token counts off the stream as it passes. It used to look at each HTTP chunk
on its own — but HTTP chunking has nothing to do with the frames a provider
sends, so a chunk boundary landing inside the counts made both halves
unrecognisable. The call was then recorded with no usage at all, which is priced
`UNKNOWN`, which a budget reads as *nothing spent*. Nothing announced it; the
only symptom was RAVIS's spend sitting below the provider's own figures. Since
10 September 2026 the reader keeps the tail of an unfinished frame and joins it
to the next chunk. The bytes sent to the client are untouched either way.

**The spend screen used to forget everything when RAVIS restarted.** Usage
records were kept only in memory, so every restart emptied the list, the
day's total and the monthly budget along with them — a restart was enough to
make a month's spending read as none. Since 12 September 2026 RAVIS writes each
record to its own database and reads the recent ones back when it starts. A
record holds no prompt and no reply, only which model, which app, the token
counts and the estimated cost. Records older than ninety days are dropped; the
budget only looks back thirty.

**Spending can be looked up by day, and the spend tile can be reset.** Since 12
September 2026 the Spending page lists each calendar day RAVIS served a call,
newest first: the estimated total, how many calls were priced, the tokens, and
a breakdown by model and by app. Only days with calls appear, so a missing day
before 12 September is not proof that nothing was spent. The Reset to 0 button
on the dashboard's spend tile makes that tile count from the moment it was
pressed; no record is deleted, and the monthly budget keeps counting as before.

**How often any of this updates, which was worse than it looked.** Until
9 September 2026 the answer was *never*: prices were read from the file once,
at startup, and OpenRouter's published rates — the only machine-readable
pricing any configured provider ships — were parsed on every catalogue refresh
and then thrown away, because the method that accepts a catalogue price had no
callers at all. The source comment claimed otherwise, which is how it went
unnoticed. Every figure on the spend screen was as old as the process.

Now, on the same refresh that already fetches catalogues:

- `prices.json` is **re-read from disk**, so editing a rate takes effect on the
  next refresh instead of at the next restart, and a rate deleted from the file
  stops being charged rather than lingering until a reboot.
- **OpenRouter's own published rates are taken automatically**, so anything
  routed through it stays current with no help. An operator-written rate always
  wins over a catalogue one — a catalogue price is what a vendor charges
  anybody, and what the operator wrote down is what *they* pay.

The hand-written rates still have to be maintained by hand. OpenAI, Anthropic,
Google, DeepSeek and xAI publish no machine-readable prices anywhere, so there
is nothing to fetch; RAVIS will not scrape a marketing page and call the result
a fact. Those numbers are only as current as the last time somebody checked.

## Which provider a model belongs to

One answer, used by policy, execution, provider health and session attribution.
It has to be one answer: they described different requests when it was two.
Until 9 September 2026 a translated provider's model resolved to `anthropic`
when the request named it in full (`ravis/anthropic/<model>`) and to `default`
when it named the model plainly — so an operator's deny-list naming `anthropic`
was compared against `default`, matched nothing, and the request reached
Anthropic anyway. A deny-list a client evades by dropping four characters is not
a deny-list.

Provider allow-lists, deny-lists and trusted-provider rules come from the policy
configured for the calling application, never from the request. A request may
tighten privacy and nothing else, so no client can grant itself a provider by
asking.

## How a model gets measured, and which ones never do

RAVIS times every request it routes and keeps a median per model, but only ranks
on the ones with enough samples to mean it — a model with two samples is treated
as unmeasured rather than as slow. An unmeasured model sorts in the *middle*
rather than last, deliberately: sorting it last would close the loop of never
chosen, never measured, never chosen.

**That guard is weaker for hosted models than for local ones.** A local model
nobody has used can be measured on purpose — SIRVIS loads it and benchmarks it.
A hosted one cannot: SIRVIS drives local runtimes only, and load time, memory
pressure and thermal readings do not exist for an API. So a hosted model is
measured only by being used, and in a pool that ranks on speed — `ravis/fast`,
`ravis/balanced` — anything already measured under a second beats the neutral
placeholder, so an untried hosted model can stay untried.

Known and written down rather than fixed automatically: routing to unproven
models to collect timings spends real money, which is a decision rather than a
default.

**Whether a hosted model can call tools is tried on purpose, since 12 September
2026.** Anthropic, OpenAI and Google publish no tool support in their
catalogues, so their models bought directly sat at unknown and every pool that
requires tools refused them — Clarvis's builds went to a smaller model until
the operator declared Claude Sonnet 5 and Haiku by hand. OpenRouter leaves a
model at unknown too when its list of accepted settings does not mention tools.
RAVIS now sends such a model one small request carrying one tool it must call,
or merely offers the tool when the provider will not let it insist.
A call counts as tool support, a provider refusing tools counts as none, a
passing hiccup — a timeout, a rate limit — counts as nothing and is tried again
hours later, and a refusal for some other reason, such as a model that does not
chat at all, counts as nothing for a month. The makers' own copies are tried
before an aggregator's listings. It is bounded: hosted models only, never a
local one, only models a tool-requiring pool would otherwise admit, three per
pass and forty a day, each result kept a month, and every trial a provider
answered on the spend screen. The result ranks above a
catalogue's word and below SIRVIS's measurements and the operator's own
declarations. The capability_trials setting switches it off.

## Known limits, as of this writing

The management API is **degraded**: reads work, and pool-membership,
provider-configuration and credential writes exist. Authorization on them is
finished: since 4 September 2026 every write needs an admin credential, and
being on the same machine no longer counts as permission. What is still missing
is three writes the plan asks for — activating a profile, refreshing SIRVIS
evidence and running a route test — and repeat-safe request keys, left out on
purpose because each write replaces whole state, so sending it twice changes
nothing. Cost figures are estimates from published prices unless the provider
reported them; a record says which.

**RAVIS is at its delay target since 0.23.2, narrowly (12 September 2026).** A load test
first found RAVIS adding about 16 ms to each request with one caller, against a
target of 5 ms, and falling behind at roughly 100 to 180 requests a second. Three
causes were measured and all three are fixed. A caller that presents a key no
longer waits about 46 ms while RAVIS checks each stored key in the keychain: it
remembers them and renews them in the background (0.23.1). Routing no longer runs
a system command to read free memory on every request, and no longer asks a hosted
provider for its model list again on every request after that provider's answer
failed. It remembers a failure for thirty seconds, so a provider with a bad key or
an outage drops out of pools for up to half a minute (0.23.2). Measured afterwards:
about 4 to 5 ms added with one caller, against a target of under 5 — one test run
came in at exactly 5 — and about 450 requests a second kept up with. It
stays correct under load: every request at up to 200 at once got its own answer.

**Embeddings are also degraded, and narrowly so on purpose.** `POST
/v1/embeddings` forwards to one configured local runtime — Ollama's
`nomic-embed-text` by default — with no routing between candidates and no
fallback chain, unlike chat. Built for NERVIS chat's own knowledge lookup
rather than a general-purpose embeddings API; a machine with no local
embedding model configured gets a stated refusal, not a guess.

**The newer OpenAI shape is translated, not implemented (RAVIS 0.30.0).** A
client that speaks `POST /v1/responses` works: the request is reshaped into the
one RAVIS already routes and answered by the same path, so it obeys exactly the
same policies, budget and privacy rules. What it carries across: the messages,
standing instructions, tools and the calls a model makes, a required JSON shape,
how hard to think, a cap on the answer's length, and the token counts back.
Three things it **refuses with a message** instead of quietly ignoring —
streaming (use `/v1/chat/completions`, which streams), continuing from an
earlier response by id (RAVIS keeps no conversation on the server), and asking
for the answer to be stored (RAVIS stores no answers). Tools the provider runs
itself, such as web search, are refused too: RAVIS forwards requests, it doesn't
run provider machinery. It's advertised as degraded because there's no
conformance suite for that surface yet.

## The free pool

`ravis/free-api` holds models that cost nothing **and** run on somebody else's
hardware. Both halves matter. `ravis/cheap` prefers local, and on a machine with
a runtime "cheapest" means a local model — which is right for cheap and wrong
for background work, because loading a local model is how work nobody is
watching starts competing for memory with the conversation you are having.

It is deliberately not private. A free tier is free because your prompt is worth
something to the provider, so anything sent here is logged and likely trained
on. A request that asked to stay on this machine can never reach it: free
requires remote and local requires local, so the two have no model in common.

Rate limits are normal here rather than a fault — a refusal from one free model
means try the next, which is what the fallback chain already did.

If nothing free is available the pool refuses rather than quietly using a paid
model. That is the difference between a ceiling that is a promise and one that
is a preference: cheap falls back to the cheapest paid model, free does not fall
back at all.
