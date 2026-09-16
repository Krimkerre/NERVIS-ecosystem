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

    # ── Ecosystem events (runbook §4.4 — M21) ───────────────────────────────
    # Where NERVIS's hub answers. Empty means SIRVIS publishes nothing, which
    # SIRVIS.md requires to keep working: telemetry export is optional, and a
    # benchmark must run on a laptop with no collector installed.
    #
    # Read here rather than probed: `inspect_configuration` contacts nothing by
    # design, and `doctor` has to be usable before anything is running.
    nervis_base_url: str = ""
    # The secret SIRVIS presents with its events. NERVIS 0.34.18 refuses a batch that proves no
    # sender (`design/security/review-2026-09-16.md`, S7); the launcher mints it and gives the same
    # value to NERVIS.
    nervis_events_secret: str = ""

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
    # Host names this service may be addressed by (§16 item 5). Loopback only,
    # matching the only bind that can start. A setting rather than a constant so
    # a reverse proxy — and the test suite's `testserver` — are configuration
    # rather than exceptions carved into the check.
    allowed_hosts: list[str] = ["127.0.0.1", "localhost", "::1"]

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

    # ── §8 model browser and downloads (M11) ─────────────────────────────────
    # Where discovery searches: Hugging Face's public API, anonymously. LM Studio
    # publishes no search, and a public model needs no token. Configurable so a
    # test can point it at a port nothing listens on, the same way the runtime is.
    huggingface_base_url: str = "https://huggingface.co"
    # Where LM Studio keeps models, for the disk check: the free space that
    # matters is on the volume a download lands on.
    lmstudio_models_path: str = "~/.lmstudio/models"
    # §8's warnings. A download that would leave less than this free is warned
    # about, and so is one that takes more than this share of what is free.
    # Neither refuses; a download that does not fit at all is refused outright.
    download_low_disk_bytes: int = 20 * 1024**3
    download_large_share: float = 0.5
    # How often a running download's progress is asked of LM Studio.
    download_poll_seconds: float = 2.0

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
