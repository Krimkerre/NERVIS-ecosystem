"""`ravis doctor` and `ravis serve`.

Two commands, one shared validator. `doctor` reports and exits; `serve` refuses
to start on anything `doctor` calls fatal. Sharing `inspect_configuration` is
what stops them disagreeing about what a safe configuration is — a check that
only runs in the diagnostic tool is a check the running service does not have.

argparse rather than a CLI framework: two subcommands do not justify a
dependency (runbook §14.2, and the ladder — stdlib before anything else).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from ecosystem_protocol import configure_logging

from ravis.config import ConfigurationReport, Settings, inspect_configuration
from ravis.cost import PriceConfigurationError, load_prices
from ravis.credentials import CredentialStore
from ravis.policy import PolicyConfigurationError, load_policies
from ravis.providers_map import resolve_provider_map, shared_provider_names
from ravis.storage.database import available_backups, resolved_path, restore_backup
from ravis.upstreams import UpstreamConfigurationError, upstream_specs

EXIT_OK = 0
EXIT_FATAL_CONFIGURATION = 1
# Distinct from a configuration failure so CI can tell "RAVIS will not start"
# apart from "RAVIS starts but would break Clarvis" — different people fix those.
EXIT_CONFORMANCE_FAILED = 2


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code rather than calling sys.exit.

    Returning the code keeps this testable: a test can assert on the value
    without trapping SystemExit, which is the difference between a test that
    reads clearly and one that fights the framework.
    """
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    settings = Settings()
    configure_logging(settings.log_level)
    if arguments.command == "restore-database":
        return _restore(settings, arguments.version)
    if arguments.command == "doctor":
        return _run_doctor(settings)
    if arguments.command == "conformance":
        return _run_conformance()
    if arguments.command == "preflight":
        return _run_preflight(settings)
    return _run_serve(settings)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ravis", description="Local-first AI routing gateway")
    subcommands = parser.add_subparsers(dest="command", required=True)
    restore = subcommands.add_parser(
        "restore-database",
        help="put back the backup taken before a migration (runbook §13)")
    restore.add_argument(
        "--version", type=int, default=None,
        help="which backup, by the schema version it restores to")
    subcommands.add_parser(
        "doctor", help="check configuration and print the resolved routing table"
    )
    subcommands.add_parser("serve", help="run the gateway")
    conformance = subcommands.add_parser(
        "conformance", help="run a consumer's wire-contract suite"
    )
    conformance.add_argument("suite", choices=["clarvis"], help="which suite to run")
    preflight = subcommands.add_parser(
        "preflight",
        help="check whether a consumer pointed here right now would work",
    )
    preflight.add_argument("consumer", choices=["clarvis"], help="which consumer to check")
    return parser


def _run_doctor(settings: Settings) -> int:
    """Print what RAVIS would do with this configuration, contacting nothing.

    The no-network rule is what makes this usable during an incident, when the
    thing you are diagnosing is that something will not answer. It is also why
    the resolved table below is derived from configuration alone.
    """
    report = inspect_configuration(settings)
    _print_findings(report)
    _print_provider_map(settings)
    if not report.is_startable():
        # Flush stdout first. The two streams are buffered independently, so
        # without this the refusal reaches the terminal before the findings it
        # tells the reader to look above for.
        sys.stdout.flush()
        print("\nrefusing to serve: fix the fatal findings above", file=sys.stderr)
        return EXIT_FATAL_CONFIGURATION
    print("\nconfiguration is serveable")
    return EXIT_OK


def _print_findings(report: ConfigurationReport) -> None:
    """List what the configuration check noticed, fatal first."""
    print(f"listen           {'loopback' if not report.fatal_findings else 'see findings'}")
    if not report.findings:
        print("findings         none")
        return
    print("findings")
    for finding in report.findings:
        marker = "FATAL  " if finding.fatal else "note   "
        print(f"  {marker}{finding.setting}: {finding.message}")


def _print_provider_map(settings: Settings) -> None:
    """Print the model-to-provider truth table required by M0.

    The value is not the mapping itself but the third column: *which setting
    decided this*. At three in the morning the question is never "what did it
    choose", it is "why", and a table that answers only the first sends you
    reading configuration files to reconstruct the second.
    """
    # The same store the request path reads, so `doctor` and the router cannot
    # disagree about whether a provider has a credential. Built here rather than
    # passed in because this is a CLI command with no application to borrow one
    # from — and reading a local 0600 file contacts no upstream.
    entries = resolve_provider_map(
        settings,
        credentials=CredentialStore(
            allow_environment=settings.credentials_allow_environment
        ),
    )
    print("\nresolved model → provider (no upstream was contacted)")
    if not entries:
        print("  none configured — set RAVIS_UPSTREAM_BASE_URL or RAVIS_UPSTREAMS")
        return
    # Rules print top to bottom in the order the router applies them, so the
    # first row matching a model is the one that decided it. Saying so beats a
    # legend nobody reads, and it is the whole reason the order is preserved.
    print("  first matching rule wins; upstreams appear in declaration order")
    print(f"  {'model':<24} {'provider':<22} decided by")
    for entry in entries:
        print(f"  {entry.model:<24} {entry.provider:<22} {entry.decided_by}")
    _print_shared_names(settings)
    _print_policies()
    _print_prices()


def _print_prices() -> None:
    """How many models RAVIS can cost, and where those prices came from.

    On `doctor` because "why is my spend zero" is a configuration question, and
    the answer is nearly always that the provider publishes no pricing and
    nobody has written any down. A malformed file is reported rather than
    raised, like everything else here.
    """
    try:
        stated = load_prices()
    except PriceConfigurationError as failure:
        print(f"\n  prices: UNREADABLE — {failure}")
        print("          `serve` will refuse to start until this is fixed.")
        return
    print(f"\n  prices: {len(stated)} stated by the operator in prices.json")
    print("          OpenRouter and the local runtimes publish their own; OpenAI,")
    print("          Anthropic and Google publish none, so a call to those is")
    print("          costed only if a price is written down here.")


def _print_policies() -> None:
    """What policy is configured, or that none is — and never a crash.

    `serve` refuses to start on a malformed policy file, on purpose. `doctor`
    must do the opposite and report it, because it is the command someone runs
    *because* the service will not start, and a diagnostic that dies on the
    thing it is diagnosing is worse than none.
    """
    try:
        policies = load_policies()
    except PolicyConfigurationError as failure:
        print(f"\n  policy: UNREADABLE — {failure}")
        print("          `serve` will refuse to start until this is fixed.")
        return
    configured = sorted(policies.by_application)
    described = policies.default.describe()
    if not configured and not described:
        print("\n  policy: none configured (every application routes unrestricted)")
        return
    print("\n  policy")
    for line in described:
        print(f"    default: {line}")
    for name in configured:
        for line in policies.by_application[name].describe():
            print(f"    {name}: {line}")


def _print_shared_names(settings: Settings) -> None:
    """Warn where one name means two providers, and say which one wins.

    The table above cannot show this: it prints `ravis/google/*` once, and a
    reader has no way to tell that a transparent upstream answers to the same
    address. RAVIS resolves the collision perfectly well — it just used to do
    it in silence, which is how two defects lived in it unnoticed.
    """
    try:
        names = shared_provider_names(spec.name for spec in upstream_specs(settings))
    except UpstreamConfigurationError:
        return  # already reported as a row above; not this function's to repeat
    for name in names:
        print(
            f"\n  note: {name!r} names both a transparent upstream and a translated "
            f"provider.\n        ravis/{name}/<model> reaches the translated one while it "
            f"has a credential;\n        the upstream still contributes its catalogue to "
            f"/v1/models."
        )


def _run_conformance() -> int:
    """Run the Clarvis wire-contract suite and report (RAVIS.md §8.8).

    Imported here rather than at module scope so `doctor` stays runnable without
    the suite's dependencies, and so a broken suite cannot stop the gateway
    starting — a diagnostic that can take the service down is a liability.
    """
    import asyncio
    import logging

    from ravis.compatibility.clarvis.conformance import render, run_suite

    # The suite makes dozens of in-process HTTP calls and httpx logs each at
    # INFO. That buries the checklist this command exists to print, so the
    # transport chatter is silenced for the run — a diagnostic nobody can read
    # is a diagnostic nobody runs.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    result = asyncio.run(run_suite())
    print(render(result))
    return EXIT_OK if result.passed else EXIT_CONFORMANCE_FAILED


def _run_preflight(settings: Settings) -> int:
    """Print the Clarvis configuration and whether it would actually work.

    Unlike `doctor` this contacts the upstream, which is the whole point: the
    question is not "is the configuration coherent" but "would Clarvis get an
    answer". Imported here for the same reason the conformance suite is — so
    `doctor` stays runnable when an import in this path is broken.
    """
    import asyncio

    from ravis.compatibility.clarvis.preflight import render, run_preflight

    result = asyncio.run(run_preflight(settings))
    print(render(result, settings))
    return EXIT_OK if result.ready else EXIT_FATAL_CONFIGURATION


def _run_serve(settings: Settings) -> int:
    """Start the server, unless the configuration is unsafe.

    The refusal is deliberate and is the §4.4 startup rule: a non-loopback bind
    without both TLS and a credential fails to start rather than starting and
    hoping. Importing uvicorn here rather than at module scope keeps `doctor`
    runnable in an environment where the server dependency is not installed.
    """
    report = inspect_configuration(settings)
    if not report.is_startable():
        _print_findings(report)
        sys.stdout.flush()
        print("\nrefusing to start: fix the fatal findings above", file=sys.stderr)
        return EXIT_FATAL_CONFIGURATION

    import uvicorn

    from ravis.app import create_app

    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())


def _restore(settings: Settings, version: int | None) -> int:
    """Put a database backup back (runbook §13's rollback half).

    A command rather than a documented `cp`, because the documented `cp` is
    wrong: in WAL mode the live sidecar replays over a copied-in file and hands
    the operator back the state they were rolling away from, without an error.
    `restore_backup` goes through SQLite's own backup API, which cannot be
    outlived by a stale WAL.

    Refuses to run against a live service by *saying* so rather than checking:
    a lock probe would be a second thing to get wrong, and the honest ordering —
    stop the service, restore, start it — is one line of output away.
    """
    database = Path(resolved_path(settings.database_path))
    if not database.exists():
        print(f"no database at {database}")
        return EXIT_FATAL_CONFIGURATION

    backups = available_backups(database)
    if not backups:
        print(f"no backup beside {database}; nothing to restore")
        return EXIT_FATAL_CONFIGURATION

    if version is None and len(backups) > 1:
        # More than one is the case where guessing is worst: the newest is
        # usually right and "usually" is not good enough for a restore.
        offer = ", ".join(str(number) for number, _ in backups)
        print(f"several backups exist ({offer}); name one with --version")
        return EXIT_FATAL_CONFIGURATION

    print("stop the service before restoring; a running one holds its own connection")
    restored = restore_backup(database, version)
    print(f"{database} restored to schema version {restored}")
    return EXIT_OK
