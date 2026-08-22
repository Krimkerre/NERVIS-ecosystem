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
from typing import Sequence

from ravis.config import ConfigurationReport, Settings, inspect_configuration
from ravis.observability import configure_logging
from ravis.providers_map import resolve_provider_map

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
    if arguments.command == "doctor":
        return _run_doctor(settings)
    if arguments.command == "conformance":
        return _run_conformance()
    return _run_serve(settings)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ravis", description="Local-first AI routing gateway")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "doctor", help="check configuration and print the resolved routing table"
    )
    subcommands.add_parser("serve", help="run the gateway")
    conformance = subcommands.add_parser(
        "conformance", help="run a consumer's wire-contract suite"
    )
    conformance.add_argument("suite", choices=["clarvis"], help="which suite to run")
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
    entries = resolve_provider_map(settings)
    print("\nresolved model → provider (no upstream was contacted)")
    if not entries:
        print("  none configured — no provider adapters exist until M8")
        return
    print(f"  {'model':<28} {'provider':<18} decided by")
    for entry in entries:
        print(f"  {entry.model:<28} {entry.provider:<18} {entry.decided_by}")


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
