"""Settings, and the startup checks that refuse to serve on an unsafe one.

Configuration is read once at startup and treated as immutable afterwards.
Nothing here contacts a network: `ravis doctor` has to be runnable on a laptop
with no services up, which is also what makes it useful during an incident.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from pydantic_settings import BaseSettings, SettingsConfigDict

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

    # Origins allowed to make browser requests. Empty means no browser origin is
    # allowed, which is correct until a dashboard is actually served: an empty
    # allowlist fails closed, an absent check fails open.
    allowed_origins: list[str] = []
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
    # Generous, because a large local model's first token can be slow and a
    # timeout here reads to the client as the model failing.
    upstream_timeout_seconds: float = 300.0
    # How long a cached model list is served before a refresh is due. The list
    # is always served from cache regardless (§4.3); this only paces the
    # background refresh.
    models_cache_ttl_seconds: float = 300.0

    # Operator-declared model capabilities, as {model: {capability: state}}.
    # The only way to make a tool-requiring pool usable before probing (§8.7) or
    # SIRVIS evidence (M13) exists — a generic OpenAI-compatible endpoint
    # publishes model IDs and nothing about what they can do, so without this
    # every capability stays UNKNOWN and every requiring pool fails closed.
    # Example: {"qwen/qwen3-4b-2507": {"tools": "SUPPORTED"}}
    model_capabilities: dict[str, dict[str, str]] = {}

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


def inspect_configuration(settings: Settings) -> ConfigurationReport:
    """Check settings for contradictions, without contacting anything.

    This is a query: it reports and changes nothing (runbook §14.2). `serve`
    calls it and refuses on a fatal finding, `doctor` calls it and prints.
    Sharing one function is what stops the two disagreeing about what safe means.
    """
    report = ConfigurationReport()
    if not settings.is_loopback_bind():
        _check_remote_exposure(settings, report)
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
    return report


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
