"""What NERVIS reads about itself before a model sees the question.

Every function here answers one question about the running ecosystem — the
benchmark queue, the routing pools, the editor windows, spend, residency — and
each is **read only when the question is about it**. That is the whole design:
a turn that asks about the weather costs no peer reads at all, and a turn that
asks about a benchmark reads the queue and nothing else.

**The question travels into these and never out of them.** It is matched against
the registry's own keys and labels to decide what to fetch, and then dropped.
Nothing a person types reaches a peer as a parameter, which is why a crafted
question cannot select anything NERVIS does not already publish about itself.

**Absence rather than a guess, everywhere.** These assemble the picture a model
is handed, so a surface that did not answer must be missing from that picture
rather than represented by an empty list that reads as "there are none".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from fastapi import Request

from nervis import bridges, commands, situation
from nervis.api.chat_calls import _named, _ravis_read, _sirvis_read
from nervis.peers import ravis as ravis_peer
from nervis.registry import RegistryEntry

FACTS_TIMEOUT_SECONDS = 4.0

# How many recent events the reading counts over. A window, not a transcript:
# `situation` reports types and tallies and never a message body, so this bounds
# the read rather than what is said about it.
EVENT_SAMPLE = 200

# How many benchmark jobs are read when the question is about them. The queue
# runs one at a time (§4.2), so anything past the recent handful is history the
# Benchmarks screen holds.
JOB_SAMPLE = 25

# How many finished runs are read with them. Small: a run carries every metric
# the suite measured for every target, and the reading quotes the headline
# figures of the most recent one rather than the history.
RUN_SAMPLE = 3

# How many runs to fetch when the question names something in particular.
#
# **Three was the whole history chat could see.** A person asking "how did the
# GGUF gemma do" was answered from the two most recent results on the machine,
# and the run they meant was the fifteenth — so the reading was correct, current
# and silent about the only thing being asked. Fifty is the window SIRVIS's own
# Results screen reads, and the selection below narrows it to what was asked
# about rather than sending fifty runs into a prompt.
NAMED_RUN_SAMPLE = 50

# Bounded because both are unbounded at the source: a hub holds every trace it
# has seen, and a quarantine grows for as long as something keeps sending
# malformed events.
TRACE_SAMPLE = 5
QUARANTINE_SAMPLE = 5

# When measurements are worth reading, which is not the same question as when
# the *queue* is worth reading.
#
# The two were one condition — runs were fetched only if the queue read had
# returned something — and the queue read only triggers on "bench", "job" or
# "queue". So "how did the GGUF gemma do on tool calls" fetched no results at
# all: not because the machine had none, but because the sentence did not
# mention a queue. A finished measurement outlives the job that produced it and
# is asked about in the words below.
RESULT_WORDS = (
    "bench", "job", "queue", "result", "measure", "measured", "score",
    "tok/s", "tokens", "tool call", "compare", "faster", "slower",
    "gguf", "mlx", "quant",
)


def _wants_results(question: str) -> bool:
    """Whether this question is about what was measured."""
    return any(word in question.lower() for word in RESULT_WORDS)


def _run_window(question: str) -> int:
    """How far back to read. Wider when the question names something specific,
    because the run being asked about is rarely the most recent one — the pair
    this was reported over sat fifteen runs deep."""
    return NAMED_RUN_SAMPLE if _wants_results(question) else RUN_SAMPLE

# When the local runtime is worth asking directly. "lm studio" and "lmstudio"
# both appear because the registry key and the product name differ, and a person
# types whichever they are looking at.
# `model`, `gguf`, `mlx` and `variant` are here for a reason that is not about
# the runtime at all: RAVIS names two builds of one model `google/gemma-4-e4b`
# and `google/gemma-4-e4b@4bit`, and nothing in its catalogue says which of
# those is the GGUF. The runtime knows — so a question about models reads it
# too, and the names below carry a format instead of a suffix nobody can
# interpret.
# The pool a conversation gets when the caller names none.
DEFAULT_CHAT_POOL = "ravis/chat"

RUNTIME_WORDS = (
    "lm studio", "lmstudio", "loaded", "runtime", "context window",
    "model", "gguf", "mlx", "variant", "quant",
)

# When the routing record is worth reading. "log" and "recently" are here
# because that is how the question is actually asked — "what happened recently"
# rather than "show me route decisions".
ROUTING_WORDS = (
    "log", "route", "routing", "request", "decision", "recently", "lately",
    "happened", "pool", "why did", "chose", "picked",
)

# How many decisions are read. The reading quotes six; a few more are fetched so
# the newest six are the newest six.
DECISION_SAMPLE = 10

# When a sentence might be asking to change where this conversation routes.
# **The same verbs the proposal itself matches on, not a second list.**
# This was `("pool", "profile", "switch", "route this", "use ravis/")` and it
# decided whether the pool list was fetched at all — so "use cheap" never
# reached the matcher that would have resolved it, and improving the matcher
# changed nothing. One list deciding whether to look and another deciding what
# was found is how a fix lands in the wrong layer.
POOL_WORDS_PATTERN = commands.SWITCH

# When a question is about the editor rather than about the ecosystem around it.
EDITOR_WORDS = (
    "clarvis", "editor", "setting", "settings", "configured", "configuration",
    "theme", "code-server", "code server", "vscode", "vs code",
)

# How many windows are asked. Each is an HTTP round trip into an extension host
# that may be busy running an agent; §6.3's per-window rule means there is no
# aggregate read to make instead.
EDITOR_SAMPLE = 2

# How long RAVIS's catalogue is reused before it is read again. The registry,
# the leases and the hub are already in memory and cost nothing per turn; the
# catalogue is one HTTP call, and doing it on every message would put a remote
# read in front of every reply. A failure is remembered for less time than a
# success, so a service that has just come back is not treated as absent for a
# minute.
CATALOGUE_TTL_SECONDS = 60.0
CATALOGUE_RETRY_SECONDS = 5.0

# How long to wait before asking RAVIS a second time after a 429. Short enough
# that a reply is not visibly delayed, long enough to leave the burst that
# tripped the limit behind.
RATE_LIMIT_PAUSE_SECONDS = 0.4

async def _jobs(request: Request, question: str) -> list[dict[str, Any]]:
    """SIRVIS's benchmark queue, read only when the question is about it.

    Not cached and not read on every turn: it changes minute to minute, so a
    stale answer to "is a benchmark running" is worse than no answer, and most
    turns have nothing to do with the queue. Reads are open on SIRVIS — the
    scope is on the *mutation* — so this needs no credential.
    """
    if not any(word in question.lower() for word in ("bench", "job", "queue")):
        return []
    entry: RegistryEntry | None = request.app.state.registry.get("sirvis")
    if entry is None:
        return []
    client: httpx.AsyncClient = request.app.state.probe_client
    try:
        answered = await client.get(
            entry.declaration.base_url + "/api/v1/benchmark-jobs",
            params={"limit": JOB_SAMPLE}, timeout=FACTS_TIMEOUT_SECONDS,
        )
        if answered.status_code >= 400:
            return []
        items = answered.json().get("items") or []
    except (httpx.HTTPError, ValueError, AttributeError):
        return []
    return [item for item in items if isinstance(item, dict)]


async def _pools(request: Request, question: str) -> list[dict[str, Any]]:
    """The pools RAVIS publishes, when the question might be about switching.

    §7: NERVIS addresses the pools RAVIS publishes and never invents one, so a
    switch offer is only ever made against this list. Read on the same terms as
    the rest — when the words suggest it, and absent rather than guessed.
    """
    if not POOL_WORDS_PATTERN.search(question):
        return []
    entry: RegistryEntry | None = request.app.state.registry.get("ravis")
    if entry is None or not entry.is_usable:
        return []
    client: httpx.AsyncClient = request.app.state.probe_client
    try:
        answered = await client.get(
            entry.declaration.base_url + "/api/v1/pools",
            timeout=FACTS_TIMEOUT_SECONDS, headers=_named(request),
        )
        if answered.status_code >= 400:
            return []
        found = answered.json()
    except (httpx.HTTPError, ValueError, AttributeError):
        return []
    items = found.get("items") if isinstance(found, dict) else found
    return [item for item in (items or []) if isinstance(item, dict)]


async def _editors(request: Request, question: str) -> list[dict[str, Any]]:
    """What each open Clarvis window is configured to do, when asked.

    §6.7 forbids NERVIS changing a Clarvis setting, and this is the read that
    makes the restriction bearable: asked "which model is Clarvis using" or "how
    do I change the theme", the answer is the value in force and the setting id
    to search for, rather than a trip into the editor to look.

    One request per live window, and only when the question is about the editor
    — a window running an agent should not be asked for its settings because
    somebody asked how RAVIS was doing.
    """
    if not any(word in question.lower() for word in EDITOR_WORDS):
        return []
    client: httpx.AsyncClient = request.app.state.probe_client
    now = request.app.state.instances_clock()
    found: list[dict[str, Any]] = []
    for instance in request.app.state.instances.live("clarvis")[:EDITOR_SAMPLE]:
        read = await bridges.read_config(client, instance, now)
        if read.get("settings"):
            found.append({"label": instance.label, **read})
    return found


async def _decisions(request: Request, question: str) -> list[dict[str, Any]]:
    """RAVIS's recent routing decisions, when the question is about them.

    The same record the Logs screen tabulates. Asked in words — *"what happened
    recently"*, *"why did it pick that"* — the table is the wrong shape and the
    screen is the wrong place, so the decisions travel as text and the model
    puts them in a sentence. NERVIS is a named caller now, so this read is not
    the one that trips RAVIS's rate limit.
    """
    if not any(word in question.lower() for word in ROUTING_WORDS):
        return []
    entry: RegistryEntry | None = request.app.state.registry.get("ravis")
    if entry is None or not entry.is_usable:
        return []
    client: httpx.AsyncClient = request.app.state.probe_client
    try:
        answered = await client.get(
            # `/api/v1/route-decisions`, which is what RAVIS actually serves —
            # `routes` is NERVIS's own surface *key* for it, and reading the key
            # as the path gave a 404 that this function then reported as "no
            # decisions" rather than as a mistake. The peer table is the one
            # place that mapping is written down.
            entry.declaration.base_url + ravis_peer.BY_KEY["routes"].path,
            params={"limit": DECISION_SAMPLE}, timeout=FACTS_TIMEOUT_SECONDS,
            headers=_named(request),
        )
        if answered.status_code >= 400:
            return []
        items = answered.json().get("items") or []
    except (httpx.HTTPError, ValueError, AttributeError):
        return []
    return [item for item in items if isinstance(item, dict)]


async def _runtime(request: Request, question: str) -> list[dict[str, Any]]:
    """What LM Studio itself is holding, when the question is about it.

    **The runtime, not the router.** RAVIS reports what it can route; LM Studio
    knows the quantisation it loaded, the context window it opened and whether
    the build takes tools — and none of that is in RAVIS's catalogue. Asked
    "what is loaded", the honest source is the process holding the weights.

    Read on the same terms as the queue: only when the question is about it,
    never cached, and absent rather than guessed on any failure. LM Studio
    publishes no MEP surface, so there is no capability to negotiate — the
    registry's own state is the whole of what NERVIS knows before asking.
    """
    if not any(word in question.lower() for word in RUNTIME_WORDS):
        return []
    entry: RegistryEntry | None = request.app.state.registry.get("lmstudio")
    if entry is None or not entry.is_usable:
        return []
    client: httpx.AsyncClient = request.app.state.probe_client
    try:
        answered = await client.get(
            entry.declaration.base_url + "/api/v0/models", timeout=FACTS_TIMEOUT_SECONDS
        )
        if answered.status_code >= 400:
            return []
        items = answered.json().get("data") or []
    except (httpx.HTTPError, ValueError, AttributeError):
        return []
    return [item for item in items if isinstance(item, dict)]


async def _runs(request: Request, question: str = "") -> list[dict[str, Any]]:
    """The most recent benchmark runs, with the numbers they measured.

    Read only when the queue was read, and bounded to a handful: a person who
    pressed Run wants to know how it went, and "go and look at the Results
    screen" is a dashboard answering a question with a map. Open like the queue
    — the scope is on mutations, not reads.
    """
    entry: RegistryEntry | None = request.app.state.registry.get("sirvis")
    if entry is None:
        return []
    client: httpx.AsyncClient = request.app.state.probe_client
    try:
        answered = await client.get(
            entry.declaration.base_url + "/api/v1/benchmark-runs",
            params={"limit": _run_window(question)}, timeout=FACTS_TIMEOUT_SECONDS,
        )
        if answered.status_code >= 400:
            return []
        items = answered.json().get("items") or []
    except (httpx.HTTPError, ValueError, AttributeError):
        return []
    return [item for item in items if isinstance(item, dict)]


# When the machine itself is the subject. Route decisions on this machine
# already say things like "memory is tight (19% free), so already-loaded models
# were preferred" and benchmark results are marked SUSPECT for thermal pressure
# — both are facts about the hardware that chat could not read, so it could
# repeat the consequence and never explain the cause.
# What has been happening. **The one reading that reads as news**, and the one
# chat kept volunteering: asked nothing more than "hello", it would report that
# RAVIS had turned down a few routes by policy. A policy refusal is routine, and
# announcing it unprompted is what the operator asked to stop. Saying so in the
# prompt helped and was not reliable — two greetings in five still leaked — so
# the list is now gathered only when the question is about activity at all.
#
# Deliberately broad, because the cost of missing is higher than the cost of
# including: a question this misses gets no event list, and the services,
# catalogue and everything else in the reading still travel.
# Whether the question is about this machine *at all*. **The gate on the whole
# reading**, added on 9 September 2026 after three attempts to fix the same
# complaint with instructions.
#
# The complaint: chat brought up the system status every turn, unasked. Telling
# it not to went from nought clean in five, to two in five, to nought in six —
# an instruction partly obeyed is not a fix, and with a live service list in
# front of every question this model will talk about it. The reading is now
# gathered only when the question is about the machine, which is the rule every
# sub-reading in this file already follows.
#
# **Three things make dropping it safe.** NERVIS prints its own status line
# under every reply regardless, so the operator still sees it. The knowledge
# base still answers what exists and how it works. And a question that misses
# this gate gets "I don't know" rather than a wrong answer, which is recovered
# by asking again in more words.
STATE_WORDS = (
    "status", "running", "up", "down", "healthy", "health", "alive", "online",
    "offline", "working", "broken", "ok", "okay", "fine", "reachable",
    "service", "services", "system", "systems", "ecosystem", "stack",
    "machine", "model", "models", "pool", "pools", "everything",
)

EVENT_WORDS = (
    "event", "happen", "happened", "happening", "log", "logs", "recent",
    "lately", "just now", "wrong", "error", "errors", "fail", "failed",
    "failing", "refus", "warning", "broke", "broken", "crash", "issue",
    "problem", "status", "going on", "up to", "activity", "trouble",
    "quiet", "busy", "news",
)

MACHINE_WORDS = (
    "memory", "ram", "thermal", "hot", "throttl", "swap", "disk", "machine",
    "hardware", "cpu", "gpu", "slow", "pressure", "space",
)

# What a call costs. The one subject where being unable to answer is expensive
# in the literal sense.
SPEND_WORDS = (
    "cost", "spend", "spent", "price", "pricing", "expensive", "cheap",
    "bill", "budget", "money", "usage", "$",
)

# Whether an upstream is answering. "Is OpenRouter down" is a question RAVIS
# knows the answer to and chat could not reach.
PROVIDER_WORDS = (
    "provider", "upstream", "openrouter", "anthropic", "openai", "google",
    "breaker", "credential", "api key", "reachable", "down", "outage",
)

# Latency measured from real traffic, which is a different claim from SIRVIS's
# benchmark: one is what happened in production, the other is a controlled run.
OBSERVATION_WORDS = (
    "latency", "ttft", "first token", "responsive", "fast", "faster",
    "slow", "slower", "speed", "quick",
)


async def _machine(request: Request, question: str) -> dict[str, Any]:
    """The machine SIRVIS is measuring on, when the question is about it.

    **`sensitive_fields` is honoured rather than noticed.** SIRVIS publishes the
    list of fields it considers sensitive — `hostname` today — and this reading
    ends up inside a prompt that may be answered by a hosted model. A field the
    producer flagged is a field that must not leave the machine, and reading the
    flag is cheaper than remembering which field it was.
    """
    if not any(word in question.lower() for word in MACHINE_WORDS):
        return {}
    found = await _sirvis_read(request, "/api/v1/system")
    if not found:
        return {}
    sensitive = {str(name) for name in (found.get("sensitive_fields") or [])}
    return {key: value for key, value in found.items() if key not in sensitive}


async def _spend(request: Request, question: str) -> dict[str, Any]:
    """What routing has cost, per RAVIS's own accounting."""
    if not any(word in question.lower() for word in SPEND_WORDS):
        return {}
    return await _ravis_read(request, "/api/v1/usage")


async def _providers(request: Request, question: str) -> list[dict[str, Any]]:
    """Upstream health: reachable, breaker state, error rate, credential."""
    if not any(word in question.lower() for word in PROVIDER_WORDS):
        return []
    found = await _ravis_read(request, "/api/v1/providers")
    items = found.get("items") or []
    return [item for item in items if isinstance(item, dict)]


async def _observations(request: Request, question: str) -> list[dict[str, Any]]:
    """Latency RAVIS has measured from real traffic (§13.5).

    Complements SIRVIS rather than repeating it: one is what production did, the
    other is a controlled benchmark, and §13.5 is explicit that the two answer
    different questions.
    """
    if not any(word in question.lower() for word in OBSERVATION_WORDS):
        return []
    found = await _ravis_read(request, "/api/v1/observations")
    items = found.get("items") or []
    return [item for item in items if isinstance(item, dict)]


# What a request is *allowed* to do. "Why can't this route to OpenAI" has an
# answer RAVIS holds and chat could not reach — and an empty policy set is an
# answer too, not a silence.
POLICY_WORDS = (
    "policy", "policies", "privacy", "allowed", "blocked", "denied", "deny",
    "exclude", "excluded", "restrict", "permitted", "why can't", "why cant",
)

# What is resident and who is holding it. Distinct from the runtime read: LM
# Studio says what is loaded, SIRVIS says under whose lease — including models
# it did not load itself, which is how a machine runs out of memory for reasons
# nothing in SIRVIS asked for.
RESIDENCY_WORDS = (
    "loaded", "resident", "lease", "holding", "evict", "unload", "residency",
    "who is using", "occupied",
)

# Combination evidence: a pair measured together rather than two models
# measured apart (§10.1).
SET_WORDS = ("runtime set", "runtime-set", "combination", "pair", "together", "set")

# One call's cost rather than the day's total. "Which model cost me that" is a
# different question from "what did today cost".
RECORD_WORDS = ("which model cost", "per call", "per-call", "each call",
                "breakdown", "itemis", "itemiz", "last call", "recent call")

# The evidence index, which carries §15.1's tombstones. A withdrawn measurement
# and one nobody ever took lead to different decisions, and only this surface
# can tell them apart.
EVIDENCE_WORDS = ("evidence", "tombstone", "deleted", "withdrawn", "removed",
                  "capability", "capabilities")

# NERVIS's own two: what a request did, and what arrived malformed.
TRACE_WORDS = ("trace", "request id", "what happened to", "timeline", "span")
QUARANTINE_WORDS = ("quarantine", "malformed", "rejected event", "bad event",
                    "dropped event")


async def _policies(request: Request, question: str) -> list[dict[str, Any]] | None:
    """Routing policy: privacy levels, provider denials, model exclusions.

    **`None` when nobody asked, `[]` when they asked and there are none.** The
    reading prints "nothing is restricted by policy" for the second, because
    that is the answer to "why can't this route to OpenAI" — and printing it for
    the first would put a policy statement on every unrelated turn.
    """
    if not any(word in question.lower() for word in POLICY_WORDS):
        return None
    found = await _ravis_read(request, "/api/v1/policies")
    return [item for item in (found.get("items") or []) if isinstance(item, dict)]


async def _residency(request: Request, question: str) -> dict[str, Any]:
    """What SIRVIS says is resident, and under whose lease."""
    if not any(word in question.lower() for word in RESIDENCY_WORDS):
        return {}
    return await _sirvis_read(request, "/api/v1/runtime/residency")


async def _runtime_sets(request: Request, question: str) -> list[dict[str, Any]]:
    """Defined combinations of models, per §10.1."""
    if not any(word in question.lower() for word in SET_WORDS):
        return []
    found = await _sirvis_read(request, "/api/v1/runtime-sets")
    return [item for item in (found.get("items") or []) if isinstance(item, dict)]


def about_the_machine(question: str, services: Sequence[Mapping[str, Any]]) -> bool:
    """Whether this question is asking about the ecosystem at all.

    The union of every gate in this module, so a question that would have
    triggered any single sub-reading still brings the whole reading with it —
    the parts are useless without the services and catalogue they sit beside.
    """
    asked = question.lower()
    vocabularies = (
        STATE_WORDS, EVENT_WORDS, MACHINE_WORDS, SPEND_WORDS, PROVIDER_WORDS,
        OBSERVATION_WORDS, POLICY_WORDS, RESIDENCY_WORDS, SET_WORDS,
        RECORD_WORDS, EVIDENCE_WORDS, TRACE_WORDS, QUARANTINE_WORDS,
        RESULT_WORDS, RUNTIME_WORDS, ROUTING_WORDS, EDITOR_WORDS,
    )
    if any(word in asked for vocabulary in vocabularies for word in vocabulary):
        return True
    return bool(situation.named_in(question, services))


async def _spend_records(request: Request, question: str) -> list[dict[str, Any]]:
    """Individual priced calls, newest first."""
    if not any(word in question.lower() for word in RECORD_WORDS):
        return []
    found = await _ravis_read(request, "/api/v1/usage/records?limit=10")
    return [item for item in (found.get("items") or []) if isinstance(item, dict)]


async def _evidence(request: Request, question: str) -> dict[str, Any]:
    """SIRVIS's evidence index — capability states and §15.1's tombstones."""
    if not any(word in question.lower() for word in EVIDENCE_WORDS):
        return {}
    return await _sirvis_read(request, "/api/v1/evidence?limit=25")


def _traces(request: Request, question: str) -> list[dict[str, Any]]:
    """Recent traces, from NERVIS's own hub.

    Read in process rather than over HTTP: the hub is right here, and a service
    calling its own API through the network stack is a round trip that can fail
    for reasons that have nothing to do with the data.
    """
    if not any(word in question.lower() for word in TRACE_WORDS):
        return []
    from nervis.traces import summarise

    events = request.app.state.hub.events_of_recent_traces(TRACE_SAMPLE)
    return list(summarise(events))[:TRACE_SAMPLE]


def _quarantine(request: Request, question: str) -> list[dict[str, Any]]:
    """Events the hub refused, from the hub itself."""
    if not any(word in question.lower() for word in QUARANTINE_WORDS):
        return []
    return list(request.app.state.hub.quarantined(QUARANTINE_SAMPLE))
