"""Settings, and the startup checks that refuse to serve on an unsafe one.

Configuration is read once at startup and treated as immutable afterwards.
Nothing here contacts a network: `ravis doctor` has to be runnable on a laptop
with no services up, which is also what makes it useful during an incident.
"""

from __future__ import annotations

import ipaddress
import json
import os
import pathlib
import shutil
from dataclasses import dataclass, field
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from ravis.credentials import config_directory
from ravis.upstreams import UpstreamConfigurationError, upstream_specs

# Loopback is the default listen address for every service in this ecosystem
# (ECOSYSTEM_RUNBOOK.md §9). 8731 is RAVIS's assigned port from the runbook's §5
# table — one port carries /v1, /api/v1 and /ecosystem together.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8731


class Settings(BaseSettings):
    """Everything RAVIS reads from the environment.

    Field names double as environment variable names with the RAVIS_ prefix, so
    `max_request_bytes` is `RAVIS_MAX_REQUEST_BYTES`. Defaults are the safe
    choice in every case: local-only, no credential required, limits on.
    """

    model_config = SettingsConfigDict(env_prefix="RAVIS_", env_file=".env", extra="ignore")

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    # ── §4.4 admission control ───────────────────────────────────────────────
    # 10 MB is generous for chat traffic and small enough that a hostile body is
    # refused before it costs anything. Inline base64 images are why it is not
    # smaller: a 5 MB image inflates to roughly 6.7 MB once encoded.
    max_request_bytes: int = 10 * 1024 * 1024
    max_images_per_request: int = 8
    # Requests per minute for an authenticated application. `anonymous` gets the
    # stricter figure, because an unidentified caller has not earned the ordinary
    # allowance (§9.6.0).
    rate_limit_per_minute: int = 600
    anonymous_rate_limit_per_minute: int = 60

    # Origins allowed to make browser requests. An empty allowlist fails closed;
    # an absent check fails open, which is why this is a list and not a flag.
    #
    # **The default was empty, and the reason given was that it was "correct
    # until a dashboard is actually served".** One is served now — NERVIS puts
    # its page on 8790 — and the consequence of not noticing was quiet: every
    # cross-origin read from that page was refused, and the dashboard's own
    # fallback turned each refusal into invented data rather than an error. Half
    # its live reads had never worked in a browser, and nothing said so.
    #
    # So the default names that one page, and nothing else. Two entries because
    # `localhost` and `127.0.0.1` are different origins to a browser and which
    # one appears depends on what was typed into the address bar. Any other
    # origin — a different port on this machine included — still gets nothing,
    # which is the property that matters: this API stores provider credentials,
    # and "any local page may write one" is the browser handing a stranger a
    # local write primitive. A named port is a boundary; "localhost" is not.
    # Host names this service may be addressed by. Loopback only, matching the
    # only bind that can start (runbook §9). It is a setting rather than a
    # constant because a deployment behind a reverse proxy is addressed by the
    # proxy's name, and because the test suite addresses it as `testserver` —
    # both are configuration, not exceptions to carve into the check.
    allowed_hosts: list[str] = ["127.0.0.1", "localhost", "::1"]

    allowed_origins: list[str] = [
        "http://127.0.0.1:8790",
        "http://localhost:8790",
    ]
    # Only these proxies may set a forwarded client address. Trusting that header
    # from anyone else is a rate-limit bypass, since the caller then picks their
    # own bucket (§4.4).
    trusted_proxies: list[str] = []

    # ── Remote exposure (§4.4 startup refusal) ───────────────────────────────
    # **The TLS settings were deleted with the rule that referenced them.** They
    # were read by nothing — `cli.py` never passed them to `uvicorn.run`, which
    # is the defect that closed this door — and `check_dead_code.py` puts it
    # exactly right: a field like that "makes something look implemented".
    # Keeping them for a remote mode nothing is building yet (runbook §9) would
    # keep the affordance that caused this. Two lines come back when something reads them.
    # `extra="ignore"` means a stale env var is harmless in the meantime.
    #
    # `client_credential` stays because RAVIS's `identity.py` genuinely reads it.
    # In NERVIS and SIRVIS nothing does, which is its own finding rather than
    # this one — see §16 item 4.
    client_credential: str = ""

    # ── The single upstream M1 forwards to (RAVIS.md §6, Path A) ────────────
    # One upstream, no routing: M1 proves the boring path works before anything
    # intelligent sits on it. Empty means no upstream is configured, which is a
    # legitimate state — /v1/models answers from an empty cache rather than
    # failing, because a client's availability probe must still get a 200.
    upstream_base_url: str = ""
    upstream_api_key: str = ""
    # Which vendor sits at that URL, and therefore which adapter discovers it
    # (M8). "generic" is the honest default: every OpenAI-compatible endpoint
    # answers to it, and it claims nothing it cannot see.
    #
    # Declared rather than detected, for the reason §3 gives about provider
    # clients — auto-detection means probing, and probing at startup makes
    # coming up depend on the upstream being awake. An unrecognised value falls
    # back to "generic" rather than refusing to start: the cost of a typo here
    # should be capabilities RAVIS does not know about, not an outage.
    upstream_kind: str = "generic"

    # ── Embeddings (RAVIS.md §4.2, pulled forward from "later") ─────────────
    # A single configured target rather than routing: embeddings have no pool,
    # no cost tradeoff and no fallback chain to choose between today, so this
    # is the boring path M1 proved for chat, applied to the one operation that
    # needs it. Defaults match the launcher's own Ollama exception — a local,
    # already-pulled model matched to a background lookup rather than a chat
    # model. Empty base URL means the capability answers DEGRADED with a
    # stated reason rather than guessing at an address nobody configured.
    embedding_base_url: str = "http://127.0.0.1:11434"
    embedding_model: str = "nomic-embed-text"
    # **How long a local model stays resident after its last request.**
    #
    # A local runtime keeps a model loaded until something evicts it, so a
    # machine that answered one chat turn an hour ago is still holding seven
    # gigabytes. That is not a leak — it is the right default for a runtime,
    # which cannot know whether another request is coming — but it is the wrong
    # default for a *router*, which just finished the request and has a routing
    # decision on this machine reading "memory is tight (19% free), so
    # already-loaded models were preferred over the pool's usual ordering".
    # Residency then decides routing: the model that happens to be warm wins,
    # and the pool's own order stops mattering.
    #
    # LM Studio takes `ttl` on the request and unloads the model that many
    # seconds after its last use, so this is the runtime's own mechanism rather
    # than an eviction loop here. Ten minutes: long enough that a conversation
    # never pays a reload between turns, short enough that a machine left alone
    # comes back to its memory.
    #
    # **0 leaves it to the runtime**, which is what an operator who has set a
    # TTL in LM Studio's own settings wants — RAVIS then sends nothing and does
    # not overrule them.
    local_model_idle_ttl_seconds: int = 600
    # **What Ollama serves a model with, when it is not already loaded.**
    #
    # Ollama reports the *architecture's* context length at `/api/show` —
    # 128,000 for `qwen2.5vl` — and serves the model at its own default,
    # which is far smaller and which nothing in its API reports. Routing that
    # believed the first number handed a 25,000-token request to a model
    # holding 4,096, and the runtime silently evaluated the first 2,050 and
    # answered as though it had read the whole thing.
    #
    # `/api/ps` gives the true figure for a model already resident, so this is
    # only consulted for a cold one. Raise it to match `OLLAMA_CONTEXT_LENGTH`
    # if that was raised; leaving it low costs a local model a long-context
    # pool, and raising it wrongly costs somebody their document.
    ollama_default_context: int = 4096
    # **What LM Studio serves a model with, when it is not already loaded.**
    #
    # The same gap as the setting above, in the other local runtime. LM Studio
    # publishes a build's ceiling (`max_context_length`) and, for a model in
    # memory, the length it was loaded with — but a model nobody has loaded yet
    # is opened at LM Studio's *default* load length, a figure in LM Studio's own
    # settings that its API does not report. RAVIS never asks for a bigger load,
    # so that default is the window a cold model will really have
    # (`providers/lmstudio.py`, `_served_window`).
    #
    # 8,192 is the default this machine's LM Studio holds. `tools/run.py` reads
    # the figure out of LM Studio's settings at every stack start and sets this
    # to match, so a default changed in LM Studio reaches RAVIS without anybody
    # copying a number; setting it yourself overrules the launcher. The costs
    # are the ones above: too low keeps a cold model out of a long-context pool,
    # too high routes a long document to a model that will be opened too small.
    lmstudio_default_context: int = 8192
    # More than one transparent upstream, as a JSON list (M8):
    #
    #   [{"name": "lmstudio", "base_url": "http://127.0.0.1:1234", "kind": "lmstudio"},
    #    {"name": "ollama",   "base_url": "http://127.0.0.1:11434", "kind": "ollama"}]
    #
    # When set it replaces the three singular settings above rather than adding
    # to them, so there is one place to read to know what is configured. When
    # empty the singular settings still mean exactly what they always meant —
    # every deployment written before this describes one upstream named
    # `default`, and continues to.
    #
    # A name is how a request addresses an upstream directly, in the same slot a
    # translating provider occupies: `ravis/<name>/<model>`.
    upstreams: str = ""
    # Whether a credential may still come from an environment variable (M10).
    # True by default because that is how every deployment written before M10
    # supplies its keys, and flipping it silently would make every configured
    # provider unavailable at once. An operator who has moved everything into
    # the credential file can turn it off and have the weaker source refused.
    credentials_allow_environment: bool = True
    # Generous, because a large local model's first token can be slow and a
    # timeout here reads to the client as the model failing.
    upstream_timeout_seconds: float = 300.0
    # §14's budget. Zero means *no budget*, not a budget of nothing: an
    # unconfigured deployment must route exactly as it did before the cost
    # engine existed, and a limit of zero would put every request in the
    # exhausted band on the first call.
    budget_limit: float = 0.0
    budget_currency: str = "USD"
    # `daily`, `weekly` or `monthly` (§14). A rolling window rather than a
    # calendar period — see `PERIOD_SECONDS`.
    budget_period: str = "monthly"
    # Whether reaching the limit blocks paid providers or only penalises them.
    # False by default: §14 blocks *if hard*, and a figure RAVIS calls an
    # estimate should not become an outage without somebody saying so.
    budget_hard: bool = False
    # How long a cached model list is served before a refresh is due. The list
    # is always served from cache regardless (§4.3); this only paces the
    # background refresh.
    models_cache_ttl_seconds: float = 300.0

    # ── Anthropic, the first translated provider (§6, Path B — M4) ──────────
    # The key is what decides whether the adapter is registered at all: a
    # provider with no credential is not a provider RAVIS can reach, and
    # registering one anyway would put an address in the routing table that
    # fails at the far end rather than being absent at the near one.
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"

    # Gemini, on §6's translated path since M7.
    #
    # **No `/v1beta/openai` here on purpose.** That endpoint exists and RAVIS
    # used it, transparently, which is what §6 prefers — but it reports
    # `finish_reason: stop` on a streamed tool call and omits the tool-call
    # index, both measured. A base URL pointing at it would quietly put Gemini
    # back on the path this milestone moved it off.
    google_api_key: str = ""
    google_base_url: str = "https://generativelanguage.googleapis.com"
    # Anthropic requires `max_tokens` on every request; OpenAI treats it as
    # optional. This is what a request that named no limit gets — a default for
    # the unstated case, not a cap on a stated one. Generous on purpose: a low
    # number truncates an answer mid-sentence and reads to the client as the
    # model failing.
    anthropic_max_output_tokens: int = 16000

    # ── Ecosystem events (runbook §4.4 — M18b) ──────────────────────────────
    # Where NERVIS's hub answers. Empty means RAVIS publishes nothing, which is
    # the ordinary state for RAVIS running on its own and is deliberately not a
    # degraded one — Stage 7 requires a collector outage to leave every product
    # healthy, and "no collector at all" is the strongest form of that.
    nervis_base_url: str = ""

    # ── SIRVIS evidence (§13 — M13) ─────────────────────────────────────────
    # Where SIRVIS answers. Empty means RAVIS runs without it, which §13.4
    # requires to keep working: provider metadata and RAVIS's own observations
    # carry routing, with the degradation labelled rather than hidden.
    sirvis_base_url: str = ""
    # The role a pool falls back to when it declares none of its own.
    #
    # **RAVIS used to ask SIRVIS about this role and no other**, on the
    # reasoning that a pool's invariant is role-specific. Half of that holds:
    # a pool wanting a model measured *for its own work* does need its own
    # role, and each pool now names one (§5.1.1's `evidence_role`). The other
    # half does not — a tool-call trial is a fact about the build, and reading
    # one role meant the seven trials on this machine were invisible to every
    # pool but one. The read is role-agnostic now and the role travels on each
    # record.
    sirvis_evidence_role: str = "clarvis-agent"
    # How long a measurement is believed (§13.3's staleness policy). Thirty days:
    # the evidence identity already pins machine, runtime and configuration, so
    # what expires is confidence that nothing else changed.
    sirvis_evidence_max_age_seconds: float = 30 * 24 * 3600

    # Operator-declared model capabilities, as {model: {capability: state}}.
    # The only way to make a tool-requiring pool usable before probing (§8.7) or
    # SIRVIS evidence (M13) exists — a generic OpenAI-compatible endpoint
    # publishes model IDs and nothing about what they can do, so without this
    # every capability stays UNKNOWN and every requiring pool fails closed.
    # `context_window` is accepted alongside capability states as a number, and
    # is the only way today to satisfy a pool that declares a minimum context.
    # Example: {"qwen/qwen3-4b-2507": {"tools": "SUPPORTED", "context_window": "32768"}}
    model_capabilities: dict[str, dict[str, str]] = {}
    # A file holding the same structure, merged underneath the env setting. It
    # exists because the honest version of this data is too big and too
    # *reviewable* to live in an environment variable: on this machine it
    # records that two packagings of one model disagree about whether tools
    # work, which is a claim that deserves a diff and a comment, not a shell
    # blob. Entries in `model_capabilities` win, so an operator can override one
    # model without editing the file.
    model_capabilities_path: str = ""
    # §13.3's OBSERVED_BY_RAVIS: RAVIS trying a hosted model's tool support itself when
    # no catalogue, measurement or declaration has said. See `trials.py`. Hosted models
    # only, a few per pass under a daily ceiling, each result kept for a month.
    capability_trials: bool = True
    capability_trials_per_pass: int = 3
    capability_trials_per_day: int = 40
    capability_trial_max_age_days: float = 30.0
    # Long enough for catalogues to warm and the first requests to be served before RAVIS
    # spends anything on its own account.
    capability_trial_start_delay_seconds: float = 60.0

    # ── §10 reliability: circuit breakers, retries and fallback ─────────────
    # Three consecutive failures, not one: a single failure is ordinary — a
    # model finishing a load, a connection dropped mid-restart — and opening a
    # circuit on it would take a healthy provider out of service for the length
    # of the cooldown every time one request was unlucky.
    breaker_failure_threshold: int = 3
    # Long enough that a restarting service is actually back, short enough that
    # nobody notices the outage outlived the cause. The half-open probe means
    # this is a *maximum* delay before recovery is retested, not a fixed wait.
    breaker_cooldown_seconds: float = 30.0
    # How long a model that refused tools is kept away from requests that carry
    # tools. Requests without tools keep using it the whole time.
    #
    # Half an hour, not the breaker's thirty seconds, because a refusal comes
    # from the model's template or the endpoint serving it and does not heal the
    # way an overload does. Long enough to cover a whole Clarvis task — up to 25
    # requests — at the cost of one refused call; short enough that a model an
    # operator reloads or fixes is back within the same sitting. Kept in memory
    # like the breakers: a restart forgets it, and the worst that costs is one
    # refusal, which now falls back to the next model anyway.
    tool_refusal_suppression_seconds: float = 1800.0
    # §10's retry budget. Three attempts is the primary plus §10's two
    # fallbacks, so the chain length and the attempt ceiling agree rather than
    # one silently truncating the other.
    retry_max_attempts: int = 3
    # Deliberately larger than `upstream_timeout_seconds`. The budget is checked
    # before each new attempt, so a ceiling below one timeout would mean a
    # timed-out request could never fall back — which is the case fallback
    # exists for. Keep this above the upstream timeout if you change either.
    retry_max_seconds: float = 600.0

    # ── Codex, the optional coding engine (runbook §2.2, §15.1.2 — M29) ───────
    # Whether RAVIS offers Codex. Unset means "if it is installed": the startup
    # check (`codex/runtime.py`) decides once, by whether the executable below
    # leads to a file. `false` switches it off — `ravis/clarvis-codex` is never listed and
    # nothing is run. `true` lists it even when the executable is missing, so a
    # client can learn why it cannot start.
    codex_enabled: bool | None = None
    # The Codex executable. Empty means "ask Homebrew": RAVIS runs `brew --prefix`
    # once at startup and uses `<prefix>/bin/codex`, the link Homebrew re-points on
    # every upgrade, so an upgrade reads as a new build to re-test rather than as
    # Codex gone (owner decision D4). Name that link, never the versioned copy
    # under `Caskroom/`, which the check refuses. Read from the environment only;
    # never from a request or the catalogue.
    codex_executable: str = ""
    # Where RAVIS's own Codex will keep its sign-in and history. Empty means
    # `ravis-codex` in the data directory (`XDG_DATA_HOME`, else `~/.local/share`).
    # Never the ChatGPT app's `~/.codex`, and never inside RAVIS's configuration
    # folder, which holds RAVIS's credentials: either leaves Codex not available.
    codex_home: str = ""
    # **The NERVIS skills folder** (owner decision, 14 September 2026): the Codex skills meant
    # for the ecosystem's tasks, `<NERVIS workspace>/clarvis/skills`, beside
    # `clarvis/nervis-tasks/`. RAVIS makes it when Codex starts and keeps its skills on unless the
    # owner switches one off; the owner's personal skills (`~/.agents/skills`) start off
    # (`agent/skills.py`). The launcher names it from NERVIS's workspace path. A value that isn't
    # absolute, isn't strictly inside `agent_allowed_roots`, or is or holds `~/.codex`, RAVIS's
    # Codex home or a protected repository refuses every task, and `GET /api/v1/codex` says why.
    # No Codex task may work in it or in a folder that holds it (`agent/roots.py`).
    codex_skills_folder: str = "~/Documents/coding/NERVIS workspace/clarvis/skills"
    # **The owner's personal skills** (owner decisions, 14 and 15 September 2026): the folder RAVIS
    # reads them from itself, for the models that aren't Codex — Clarvis's own engine and NERVIS
    # chat (`agent/skill_catalog.py`). RAVIS only ever reads it. Each of its skills, one added
    # later included, starts off for Codex and for the other models until the owner switches it
    # on. Codex finds the same folder on its own, through the owner's `HOME`.
    skills_personal_folder: str = "~/.agents/skills"
    # The Apple team that must have signed the executable: OpenAI's.
    codex_expected_team_id: str = "2DC432GLL2"
    # How often RAVIS reads the ChatGPT plan's remaining allowance while no Codex turn is
    # running: every 15 minutes (design §3.3). While turns run it doesn't poll at all, because
    # Codex's own notifications keep the figure current; reading never starts a model call.
    codex_usage_refresh_seconds: int = 900
    # The applications whose admin credential may start the file-rules re-test (design §3.4).
    # Only the owner's command-line credential by default — never NERVIS's `launcher` — because
    # the re-test runs commands and spends a little of the plan's allowance, and NERVIS never
    # starts Codex work (runbook §2.2).
    codex_reproof_applications: list[str] = ["owner_cli"]
    # **Calibration** (design §10.4): dev-only, run with the owner present. While true, the route
    # `POST /api/v1/codex/calibration/runs` exists for the owner's command-line credential, and
    # Codex's process starts with the file-rules profile under test. Never set by the launcher.
    codex_calibration: bool = False
    # A JSON file `{"name", "flags"}` to try as that profile instead of the pinned one or RAVIS's
    # candidate — for when Codex's own validation errors call for another syntax.
    codex_calibration_profile: str = ""
    # Where a run's redacted transcripts and results summary go. Empty: this checkout's
    # `ravis/tests/fixtures/codex/calibration/`, else `ravis-codex-calibration` in the data folder.
    codex_calibration_output: str = ""
    # The ecosystem's own checkouts, which no Codex work — calibration included — may use as a
    # project (owner decision (b)). The launcher passes this checkout and its sibling `clarvis`;
    # RAVIS also refuses the checkout it runs from, so the rule holds without the launcher.
    agent_protected_repositories: list[str] = []

    # ── Codex agent sessions (M29's third increment, R3; RAVIS.md §15.1.2) ─────
    # The client applications whose credential may start, read, steer, stop and answer Codex
    # tasks on `/api/v1/agent-sessions` (design §3.5.1). Only Clarvis by default. NERVIS and the
    # launcher stay refused even if someone lists them, and so does every admin credential.
    agent_client_applications: list[str] = ["clarvis"]
    # The admin applications that may use the stop-only owner route (§3.5.5): the menu bar's
    # `owner_cli` and NERVIS's `launcher`. Kept apart so each has its own rate limit and audit name.
    agent_owner_stop_applications: list[str] = ["owner_cli", "launcher"]
    # Where a Codex task's project may be (§3.5.1): a folder strictly inside one of these. An
    # entry may instead be `{"path": …, "allow_protected": true}`, which would allow exactly that
    # folder even though it is a protected repository — documented, not used (owner decision
    # (b)). JSON lists, as the launcher passes them (N1a's notes).
    agent_allowed_roots: list[str | dict[str, Any]] = ["~/Documents/coding"]
    # Folders no task may be in or contain, and whose appearance in a command hides its output:
    # the launcher passes its `.run`, which holds every key it mints.
    agent_denied_paths: list[str] = []
    # How many Codex tasks may be live at once (409 CODEX_SESSION_LIMIT, design §3.5.3).
    agent_session_limit: int = 3
    # The unanswered-request policy (design §4.8): how long one of Codex's requests may wait with
    # a Clarvis panel attached, and with none, before RAVIS declines it and pauses the task.
    agent_unanswered_attached_seconds: float = 7200.0
    agent_unanswered_detached_seconds: float = 1800.0
    # How long Codex's own history of a task (its thread) is kept unused before RAVIS deletes it
    # with `thread/delete` — unless a Clarvis checkpoint in the project still names the thread
    # (design §4.10, owner decision D3).
    agent_history_retention_days: int = 90

    database_path: str = "ravis.db"
    log_level: str = "INFO"

    def is_loopback_bind(self) -> bool:
        """True when the configured host cannot receive traffic from the network.

        A hostname that is not a literal IP address is treated as non-loopback.
        That is deliberately pessimistic: "localhost" almost always resolves to
        loopback, but resolution is not ours to guarantee, and the cost of
        guessing wrong is publishing the service to a network.
        """
        try:
            return ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            return self.host == "localhost"


def resolved_capabilities(settings: Settings) -> dict[str, dict[str, str]]:
    """Operator-declared capabilities, from the file and the environment.

    The file is the base and the environment overrides it, per model. That
    direction is deliberate: the file is a considered, checked-in record, and
    the environment is what someone reaches for when they need to change one
    thing right now — so the immediate one has to win, or it does nothing.

    A path that cannot be read yields nothing rather than raising, because this
    is also called by `doctor`, whose job is to *report* a broken configuration
    rather than die on it. `inspect_configuration` is what turns the same
    problem into a refusal to serve.
    """
    merged = _capabilities_file(settings.model_capabilities_path)
    for model, declared in settings.model_capabilities.items():
        merged[model] = {**merged.get(model, {}), **declared}
    return merged


def _capabilities_file(path: str) -> dict[str, dict[str, str]]:
    """Read the capability file, or return empty when there is nothing to read."""
    if not path:
        return {}
    try:
        loaded = json.loads(pathlib.Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    # Keys beginning with an underscore are notes for whoever reads the file —
    # JSON has no comments, and a capability record that cannot explain where
    # its claims came from is the thing this whole file exists to avoid.
    return {
        model: {str(k): str(v) for k, v in declared.items()}
        for model, declared in loaded.items()
        if not model.startswith("_") and isinstance(declared, dict)
    }


@dataclass
class ConfigurationFinding:
    """One thing `doctor` noticed, and whether it stops the service starting.

    Fatal is separated from advisory so `doctor` reports everything in one pass
    rather than dying on the first problem — an operator fixing a
    misconfiguration wants the whole list, not a game of whack-a-mole.
    """

    fatal: bool
    setting: str
    message: str


@dataclass
class ConfigurationReport:
    """Everything `doctor` found, in the order it was found."""

    findings: list[ConfigurationFinding] = field(default_factory=list)

    @property
    def fatal_findings(self) -> list[ConfigurationFinding]:
        return [finding for finding in self.findings if finding.fatal]

    def is_startable(self) -> bool:
        """True when nothing found would make serving unsafe."""
        return not self.fatal_findings


def _check_upstreams(settings: Settings, report: ConfigurationReport) -> None:
    """Whether the declared upstreams can be read at all.

    Fatal on a malformed list, unlike an unrecognised `upstream_kind` which
    degrades to the generic adapter on purpose. The difference is what the
    mistake costs: a bad kind loses the vendor metadata that kind would have
    read, while a list RAVIS cannot parse leaves it not knowing where to send
    anything.
    """
    try:
        specs = upstream_specs(settings)
    except UpstreamConfigurationError as failure:
        report.findings.append(ConfigurationFinding(True, "upstreams", str(failure)))
        return
    if len(specs) > 1:
        report.findings.append(
            ConfigurationFinding(
                False,
                "upstreams",
                f"{len(specs)} transparent upstreams: "
                + ", ".join(f"{spec.name} ({spec.kind})" for spec in specs),
            )
        )


#: The windows RAVIS assumes for a local model its runtime has not loaded yet.
DEFAULT_CONTEXT_SETTINGS = ("ollama_default_context", "lmstudio_default_context")


def _check_default_contexts(settings: Settings, report: ConfigurationReport) -> None:
    """Refuse a default context that is not a positive number of tokens.

    Each is the window reported for a model Ollama or LM Studio has not loaded yet
    (`providers/ollama.py`, `providers/lmstudio.py`). **A negative one was accepted**
    until 13 September 2026 — only by hand, since the launcher never passes one — and
    reported every such model at that window, so none of them qualified for any
    request and a local runtime went quietly unused. **Zero was no better:**
    `adapter_for` turned it back into the built-in default, silently ignoring what the
    operator wrote, which is the very disagreement between RAVIS and the runtime these
    settings exist to remove. Fatal, like `max_request_bytes`, so it is fixed before
    serving rather than discovered in a route explanation.
    """
    for setting in DEFAULT_CONTEXT_SETTINGS:
        value = getattr(settings, setting)
        if value <= 0:
            report.findings.append(ConfigurationFinding(
                True, setting,
                f"must be a positive number of tokens, not {value} (RAVIS_{setting.upper()})",
            ))


def inspect_configuration(settings: Settings) -> ConfigurationReport:
    """Check settings for contradictions, without contacting anything.

    This is a query: it reports and changes nothing (runbook §14.2). `serve`
    calls it and refuses on a fatal finding, `doctor` calls it and prints.
    Sharing one function is what stops the two disagreeing about what safe means.
    """
    report = ConfigurationReport()
    if not settings.is_loopback_bind():
        _check_remote_exposure(settings, report)
    _check_upstreams(settings, report)
    if settings.max_request_bytes <= 0:
        report.findings.append(
            ConfigurationFinding(True, "max_request_bytes", "must be greater than zero")
        )
    _check_default_contexts(settings, report)
    if settings.allowed_origins:
        report.findings.append(
            ConfigurationFinding(
                False,
                "allowed_origins",
                f"browser origins allowed: {', '.join(settings.allowed_origins)}",
            )
        )
    if "null" in settings.allowed_origins:
        # A page opened from disk sends `Origin: null` — and so does every other
        # page opened from disk, and every sandboxed iframe. Allow-listing the
        # literal string therefore allow-lists a *category*, not a page. Nothing
        # RAVIS reads out is a credential (§9.7 keeps them out by construction),
        # so this is advisory rather than fatal, but it is worth saying out loud
        # because it does not look like a wildcard and behaves like one.
        report.findings.append(
            ConfigurationFinding(
                False,
                "allowed_origins",
                "'null' matches any page opened from a file:// URL, not one page — "
                "serve the dashboard over http://127.0.0.1 and allow that origin instead",
            )
        )
    _check_capabilities_file(settings, report)
    if settings.retry_max_seconds < settings.upstream_timeout_seconds:
        # Advisory rather than fatal: the service works, it just cannot do the
        # one thing the setting exists for. §10's budget is checked before each
        # new attempt, so a ceiling below a single upstream timeout means the
        # first timeout also ends the chain — fallback configured, never reached.
        report.findings.append(
            ConfigurationFinding(
                False,
                "retry_max_seconds",
                f"{settings.retry_max_seconds:.0f}s is below the upstream timeout of "
                f"{settings.upstream_timeout_seconds:.0f}s, so a timed-out request can "
                "never fall back",
            )
        )
    _check_codex(settings, report)
    return report


# ── Codex, the optional coding engine (runbook §2.2 — M29) ──────────────────


def data_directory() -> pathlib.Path:
    """Where RAVIS keeps data that is not configuration: `XDG_DATA_HOME`, else `~/.local/share`.

    The sibling of `config_directory` (`credentials.py`), read from the environment for the
    same reason: so a test never touches a real home directory. Codex's home and the throwaway
    folders its check runs in belong here (design §4.2), outside the configuration folder that
    holds RAVIS's own credentials.
    """
    base = os.environ.get("XDG_DATA_HOME") or pathlib.Path.home() / ".local" / "share"
    return pathlib.Path(base)


def codex_home(settings: Settings) -> pathlib.Path:
    """The folder RAVIS's own Codex uses as its home, as an absolute path."""
    if settings.codex_home:
        return pathlib.Path(os.path.abspath(os.path.expanduser(settings.codex_home)))
    return data_directory() / "ravis-codex"


def codex_skills_folder(settings: Settings) -> pathlib.Path:
    """The NERVIS skills folder as a real path: `~` expanded and symlinks followed where they exist.

    Resolved whether or not the folder exists yet, since RAVIS makes it once Codex starts. A
    relative value resolves against RAVIS's working folder here, and `agent/skills.py` refuses it.
    """
    return pathlib.Path(os.path.realpath(os.path.expanduser(settings.codex_skills_folder)))


def skills_personal_folder(settings: Settings) -> pathlib.Path:
    """The owner's personal skills folder as a real path, `~` expanded and symlinks followed.

    A relative value resolves against RAVIS's working folder here, and `agent/skill_catalog.py`
    refuses it rather than read whatever that is.
    """
    return pathlib.Path(os.path.realpath(os.path.expanduser(settings.skills_personal_folder)))


def codex_home_refusal(settings: Settings) -> str | None:
    """Why the Codex home can't be used, or `None` when it can.

    Two folders are refused, by comparing paths and reading neither (design §4.2):

    - **The ChatGPT app's `~/.codex`,** and anything inside it. It holds the owner's own Codex
      sign-in, and RAVIS's Codex signs in on its own (runbook §2.2).
    - **RAVIS's configuration folder,** and anything inside it. It holds RAVIS's credentials,
      and runbook §2.2 puts Codex's home outside it.

    A home elsewhere doesn't stop Codex's commands reading those folders — only the permission
    profile's deny list does, and that arrives with the Codex process (R2) — but a home inside
    one would hand it over outright.
    """
    home = codex_home(settings)
    refused = (
        (pathlib.Path.home() / ".codex", "the ChatGPT app's own Codex home, ~/.codex"),
        (config_directory(), "RAVIS's configuration folder, which holds its credentials"),
    )
    for folder, what in refused:
        absolute = pathlib.Path(os.path.abspath(folder))
        if home == absolute or absolute in home.parents:
            return f"the Codex home {home} is inside {what}"
    return None


def names_caskroom_copy(executable: str) -> bool:
    """Whether a configured executable names Homebrew's versioned copy rather than its link.

    `/opt/homebrew/Caskroom/codex/0.154.0/bin/codex` names one version, and after `brew upgrade`
    it is stale or gone, so Codex would read as not installed instead of as a new build to
    re-test. The link, `$(brew --prefix)/bin/codex`, follows every upgrade (design review N6).
    """
    return "Caskroom" in pathlib.Path(executable).parts


def _check_codex(settings: Settings, report: ConfigurationReport) -> None:
    """Say why Codex would read as not installed or not available, and never refuse to serve.

    Codex is optional and every product works without it (runbook §2.2), so each finding here
    is advisory, and a Codex switched off has nothing to report. What the startup check does
    beyond this — asking Homebrew, the signature, the pin — runs programs, and `doctor` runs
    none.
    """
    if settings.codex_enabled is False:
        return
    executable = _codex_executable_problem(settings)
    if executable is not None:
        report.findings.append(ConfigurationFinding(False, "codex_executable", executable))
    home = codex_home_refusal(settings)
    if home is not None:
        report.findings.append(
            ConfigurationFinding(False, "codex_home", f"{home}, so Codex is not available")
        )


def _codex_executable_problem(settings: Settings) -> str | None:
    """What stops the Codex executable being found or trusted, judged from paths alone."""
    configured = settings.codex_executable
    if not configured:
        if shutil.which("brew") is None:
            return (
                "not set, and Homebrew is not on PATH to ask, so Codex reads as not installed; "
                "RAVIS runs without it"
            )
        return None
    if names_caskroom_copy(configured):
        return (
            f"{configured} is Homebrew's versioned copy; name $(brew --prefix)/bin/codex so an "
            "upgrade reads as a new build to re-test. Until then Codex is not available"
        )
    if not pathlib.Path(configured).expanduser().is_file():
        return (
            f"{configured} does not lead to a file, so Codex reads as not installed; "
            "RAVIS runs without it"
        )
    return None


def _check_capabilities_file(settings: Settings, report: ConfigurationReport) -> None:
    """Refuse to serve on a capability file that was named and cannot be read.

    Fatal rather than advisory, and the reason is the failure it replaces:
    without the file every capability stays UNKNOWN, every requiring pool fails
    closed (§5.2), and `ravis/clarvis-agent` becomes unroutable — which reaches
    the operator as "the agent is broken" rather than as "you typed the path
    wrong". A named file that is missing is a mistake, not a preference.
    """
    path = settings.model_capabilities_path
    if not path:
        return
    location = pathlib.Path(path)
    if not location.is_file():
        report.findings.append(
            ConfigurationFinding(True, "model_capabilities_path", f"{path} does not exist")
        )
        return
    if not _capabilities_file(path):
        report.findings.append(
            ConfigurationFinding(
                True,
                "model_capabilities_path",
                f"{path} is not a readable JSON object of {{model: {{capability: state}}}}",
            )
        )
        return
    declared = _capabilities_file(path)
    report.findings.append(
        ConfigurationFinding(
            False,
            "model_capabilities_path",
            f"{len(declared)} model(s) declared from {path}",
        )
    )


def _check_remote_exposure(settings: Settings, report: ConfigurationReport) -> None:
    """Refuse a bind that is not loopback, whatever else is configured.

    **This used to require TLS and a credential, and that rule was satisfiable
    without being true.** `cli.py` calls `uvicorn.run` without `ssl_certfile` or
    `ssl_keyfile`, so a bind naming a certificate and a key served plain HTTP
    regardless: configuration validated at startup and then never applied. An
    external audit found the same shape in all three services.

    Refusing is the honest failure. A remote mode that looks encrypted and is not
    is worse than no remote mode, because the operator stops looking. The two
    findings that used to name what was missing are gone with it — naming them
    invites completing the set, and the completed set was the defect.

    Remote operation returns when it is built and proven end to end: TLS wired to
    the listener, per-request authentication, Host and Origin validation, SSE held
    to the same rules, and a test against a real TLS listener.
    `ECOSYSTEM_RUNBOOK.md` §9 records that nothing is building it yet; the §16
    that once listed it is retired.
    """
    report.findings.append(
        ConfigurationFinding(
            True,
            "host",
            f"binding beyond loopback ({settings.host}) is not supported yet"
            " — see ECOSYSTEM_RUNBOOK.md §9",
        )
    )
