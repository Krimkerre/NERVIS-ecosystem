"""Settings, and the startup checks that refuse to serve on an unsafe one.

Configuration is read once at startup and treated as immutable afterwards.
Nothing here contacts a network: `ravis doctor` has to be runnable on a laptop
with no services up, which is also what makes it useful during an incident.
"""

from __future__ import annotations

import ipaddress
import json
import pathlib
from dataclasses import dataclass, field

from pydantic_settings import BaseSettings, SettingsConfigDict

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
    allowed_origins: list[str] = [
        "http://127.0.0.1:8790",
        "http://localhost:8790",
    ]
    # Only these proxies may set a forwarded client address. Trusting that header
    # from anyone else is a rate-limit bypass, since the caller then picks their
    # own bucket (§4.4).
    trusted_proxies: list[str] = []

    # ── Remote exposure (§4.4 startup refusal) ───────────────────────────────
    # Binding beyond loopback requires BOTH of these. Either alone fails to
    # start, which is the trap the rule exists to close.
    client_credential: str = ""
    tls_certificate_path: str = ""
    tls_key_path: str = ""

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

    # ── SIRVIS evidence (§13 — M13) ─────────────────────────────────────────
    # Where SIRVIS answers. Empty means RAVIS runs without it, which §13.4
    # requires to keep working: provider metadata and RAVIS's own observations
    # carry routing, with the degradation labelled rather than hidden.
    sirvis_base_url: str = ""
    # The role RAVIS asks about. One role rather than all of them because a
    # pool's invariant is role-specific — `ravis/clarvis-agent` is admitted on
    # `clarvis-agent` evidence and nothing else, and evidence for a different
    # role is about a different question.
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
    # §10's retry budget. Three attempts is the primary plus §10's two
    # fallbacks, so the chain length and the attempt ceiling agree rather than
    # one silently truncating the other.
    retry_max_attempts: int = 3
    # Deliberately larger than `upstream_timeout_seconds`. The budget is checked
    # before each new attempt, so a ceiling below one timeout would mean a
    # timed-out request could never fall back — which is the case fallback
    # exists for. Keep this above the upstream timeout if you change either.
    retry_max_seconds: float = 600.0

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
    return report


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
    """Refuse a non-loopback bind that lacks TLS or a credential.

    The runbook requires remote exposure to carry TLS *and* authentication —
    both, not either (§4.4). The trap this closes is the credential-only bind:
    it passes every other check and publishes the model registry to the network
    in cleartext. Each missing piece is reported separately, so the operator is
    told what to add rather than merely that something is wrong.
    """
    report.findings.append(
        ConfigurationFinding(
            False,
            "host",
            f"binding beyond loopback ({settings.host}) — TLS and a credential are required",
        )
    )
    if not settings.client_credential:
        report.findings.append(
            ConfigurationFinding(
                True, "client_credential", "required for a non-loopback bind, and is not set"
            )
        )
    if not (settings.tls_certificate_path and settings.tls_key_path):
        report.findings.append(
            ConfigurationFinding(
                True,
                "tls_certificate_path",
                "TLS is required for a non-loopback bind, and is not configured",
            )
        )
