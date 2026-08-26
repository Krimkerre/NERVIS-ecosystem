"""Settings, and the startup checks that refuse to serve on an unsafe one.

Configuration is read once at startup and treated as immutable afterwards.
Nothing here contacts a network: `sirvis doctor` has to be runnable on a laptop
with no runtime installed, which is also what makes it useful during an
incident — and §21's M0 exit requires the service to start with no runtime
dependency present at all.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from pydantic_settings import BaseSettings, SettingsConfigDict

# Loopback is the default listen address for every service in this ecosystem
# (ECOSYSTEM_RUNBOOK.md §9). 8721 is SIRVIS's assigned port from the runbook's
# §5 table, one below RAVIS's 8731.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8721


class Settings(BaseSettings):
    """Everything SIRVIS reads from the environment.

    Field names double as environment variable names with the SIRVIS_ prefix, so
    `database_path` is `SIRVIS_DATABASE_PATH`. Defaults are the safe choice in
    every case: local-only, no credential required, no runtime assumed.
    """

    model_config = SettingsConfigDict(env_prefix="SIRVIS_", env_file=".env", extra="ignore")

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    # ── Remote exposure (runbook §9, mirroring RAVIS §4.4) ───────────────────
    # Binding beyond loopback requires BOTH. Either alone fails to start, which
    # is the trap the rule exists to close: a credential-only bind passes every
    # other check and publishes the machine's inventory in cleartext.
    client_credential: str = ""
    tls_certificate_path: str = ""
    tls_key_path: str = ""

    # Origins allowed to make browser requests. An empty allowlist fails closed;
    # an absent check fails open, which is why this is a list and not a flag.
    #
    # **The default was empty, and the reason given was that it was "correct
    # until a dashboard is actually served".** One is served now — NERVIS puts
    # its page on 8790 — and the consequence of not revisiting the line was
    # quiet rather than loud: a cross-origin read that is refused reaches the
    # page as a failed fetch, and the dashboard turns every failed fetch into
    # its mock. So its SIRVIS screens showed invented numbers and said nothing.
    #
    # Two entries because `localhost` and `127.0.0.1` are different origins to a
    # browser, and which one appears depends on what was typed into the address
    # bar. Nothing else is named, including other ports on this machine: SIRVIS
    # exposes runtime mutations that load and unload models, and "any local page
    # may call them" is a browser handing a stranger the ability to evict a
    # model another client is holding.
    allowed_origins: list[str] = [
        "http://127.0.0.1:8790",
        "http://localhost:8790",
    ]

    # ── The local runtime SIRVIS measures through (§7) ───────────────────────
    # Empty is a legitimate state and M0's exit requires it: SIRVIS must start,
    # migrate and answer `/ecosystem/*` with no runtime present. A runtime that
    # is absent is reported as absent, never as broken.
    lmstudio_base_url: str = "http://127.0.0.1:1234"
    # Where the `lms` binary lives. Configuration rather than a constant because
    # it is the *second* channel to a runtime and nothing pointed it anywhere
    # safe: LM Studio exposes no HTTP load or unload, so lifecycle shells out —
    # which means aiming `lmstudio_base_url` at a dead port protects reads and
    # nothing else. A test suite that believed it was isolated loaded models on
    # the developer's machine through exactly this gap.
    lmstudio_cli_path: str = "~/.lmstudio/bin/lms"

    # ── §9 resource management ───────────────────────────────────────────────
    # Two co-resident models cost almost nothing on 24 GB and the third is where
    # `clarvis/docs/benchmarks.md` measured it getting tight, so two is a
    # defensible default rather than a round number. Raise it on a bigger
    # machine; the point is that a ceiling exists and is stated.
    max_loaded_models: int = 2
    # Long enough to outlast a benchmark suite, short enough that a client which
    # died without releasing does not strand a multi-gigabyte model until
    # somebody notices (§9's "conservative defaults").
    default_lease_seconds: float = 3600.0

    # Where §11.9's raw results go: one directory per experiment, holding the
    # responses that let a rescoring happen without rerunning inference. Kept
    # out of the database because a generated response is a blob, and SQLite is
    # the wrong place for many of them.
    results_path: str = "results"

    database_path: str = "sirvis.db"
    log_level: str = "INFO"

    def is_loopback_bind(self) -> bool:
        """True when the configured host cannot receive traffic from the network.

        A hostname that is not a literal IP address is treated as non-loopback.
        Deliberately pessimistic: "localhost" almost always resolves to loopback,
        but resolution is not ours to guarantee, and the cost of guessing wrong
        is publishing the service to a network.
        """
        try:
            return ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            return self.host == "localhost"


@dataclass
class ConfigurationFinding:
    """One thing `doctor` noticed, and whether it stops the service starting."""

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
        return not self.fatal_findings


def inspect_configuration(settings: Settings) -> ConfigurationReport:
    """Check settings for contradictions, without contacting anything.

    A query: it reports and changes nothing (runbook §14.2). `serve` calls it and
    refuses on a fatal finding, `doctor` calls it and prints. Sharing one
    function is what stops the two disagreeing about what safe means.
    """
    report = ConfigurationReport()
    if not settings.is_loopback_bind():
        _check_remote_exposure(settings, report)
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

    Both, not either. The trap this closes is the credential-only bind: it looks
    configured and publishes every model on the machine in cleartext. Each
    missing piece is reported separately, so the operator is told what to add
    rather than merely that something is wrong.
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
