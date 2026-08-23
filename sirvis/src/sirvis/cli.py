"""`sirvis doctor` and `sirvis serve` — §18, and M0's exit criterion.

`doctor` contacts nothing. That constraint is the feature: it has to be runnable
on a laptop with no runtime installed and useful during an incident, when the
thing being diagnosed is that something will not answer.
"""

from __future__ import annotations

import argparse
import logging
import os
import pathlib
import sys
from typing import Sequence

import uvicorn
from ecosystem_protocol import configure_logging

from sirvis.app import create_app
from sirvis.config import ConfigurationReport, Settings, inspect_configuration
from sirvis.storage import prepare_database
from sirvis.telemetry import detect_system

EXIT_OK = 0
EXIT_FATAL_CONFIGURATION = 2


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code rather than calling sys.exit.

    Returning the code keeps this testable: a test can assert on the value
    without trapping SystemExit.
    """
    arguments = _build_parser().parse_args(argv)
    settings = Settings()
    configure_logging(settings.log_level)
    # httpx logs a line per request at INFO, which is right for a running
    # service and wrong for a diagnostic: `doctor` prints a report, and three
    # JSON log lines interleaved through it obscure the thing the operator ran
    # it to read. The service keeps them; the CLI does not.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if arguments.command == "doctor":
        return _run_doctor(settings)
    if arguments.command == "token":
        return _run_token(settings, arguments.mint, arguments.scopes)
    return _run_serve(settings)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sirvis", description="Local model evidence plane")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "doctor", help="check configuration and the database, contacting nothing"
    )
    subcommands.add_parser("serve", help="run the service")
    token = subcommands.add_parser(
        "token", help="show the bootstrap API token, or mint a scoped one"
    )
    token.add_argument("--mint", metavar="LABEL", help="mint a new token under this label")
    token.add_argument(
        "--scopes", default="read",
        help="space-separated scopes for --mint: read, benchmark, runtime, admin",
    )
    return parser


def _run_doctor(settings: Settings) -> int:
    """Everything §18 says `doctor` checks, in one pass.

    §18 lists Apple Silicon, macOS, RAM, disk, LM Studio, the LM Studio API,
    installed models, GGUF and MLX capability, the database and the results
    directory. It reported configuration and migrations only — M1 and M2 landed
    and this was never revisited, which is the same drift STATUS.md exists to
    catch and did not, because nothing counts CLI output.

    **It contacts the runtime, and an absent runtime is a finding rather than a
    failure.** That reconciles §18 with §15.4's standalone requirement: a laptop
    with LM Studio closed is the ordinary case, and doctor is most useful
    precisely when something is not answering.
    """
    report = inspect_configuration(settings)
    _print_findings(report)
    _print_machine()
    _print_database(settings)
    _print_results_directory(settings)
    _print_runtime(settings)
    if not report.is_startable():
        # Flush first: the two streams are buffered independently, so without
        # this the refusal reaches the terminal before the findings it tells the
        # reader to look above for.
        sys.stdout.flush()
        print("\nrefusing to serve: fix the fatal findings above", file=sys.stderr)
        return EXIT_FATAL_CONFIGURATION
    print("\nconfiguration is serveable")
    return EXIT_OK


def _print_findings(report: ConfigurationReport) -> None:
    print(f"listen           {'loopback' if not report.fatal_findings else 'see findings'}")
    if not report.findings:
        print("findings         none")
        return
    print("findings")
    for finding in report.findings:
        marker = "FATAL  " if finding.fatal else "note   "
        print(f"  {marker}{finding.setting}: {finding.message}")


def _print_results_directory(settings: Settings) -> None:
    """Where raw results will be written (§11.9), and whether it is usable.

    §18 asks doctor to check this. Reported rather than created: a directory
    appearing as a side effect of a diagnostic is a surprise, and the benchmark
    engine that owns it can make it when it needs it.
    """
    location = pathlib.Path(settings.results_path).expanduser()
    if not location.exists():
        print(f"results          {location} (will be created on first run)")
        return
    writable = os.access(location, os.W_OK)
    runs = sum(1 for _ in location.iterdir()) if writable else 0
    print(f"results          {location} · {runs} experiment(s)"
          f"{'' if writable else ' — NOT WRITABLE'}")


def _print_database(settings: Settings) -> None:
    """Migrate and report the version.

    `doctor` migrating is deliberate. The alternative is discovering a failed
    migration when the first request arrives, which is the worst moment and the
    least informative place.
    """
    database = prepare_database(settings.database_path)
    print(f"database         {settings.database_path} at migration {database.version}")


def _print_machine() -> None:
    """The M1 snapshot: what this machine is, and what could not be read.

    §18 asks for Apple Silicon, RAM and disk. Anything the machine declines to
    report prints as `unknown` rather than as a plausible number — M1's rule,
    and the reason a snapshot is worth anything.
    """
    snapshot = detect_system()
    memory = _gigabytes(snapshot.unified_memory_bytes)
    free = _gigabytes(snapshot.disk_free_bytes)
    total = _gigabytes(snapshot.disk_total_bytes)
    print(f"\nmachine          {snapshot.chip or 'unknown chip'} · "
          f"{snapshot.platform_name} {snapshot.os_version or '?'}")
    print(f"  apple silicon  {'yes' if snapshot.is_apple_silicon else 'no'}")
    print(f"  cores          {snapshot.cpu_cores or '?'} cpu "
          f"({snapshot.performance_cores or '?'}P/{snapshot.efficiency_cores or '?'}E), "
          f"{snapshot.gpu_cores or '?'} gpu")
    print(f"  memory         {memory} unified · thermal {snapshot.thermal_state or 'unknown'}")
    print(f"  disk           {free} free of {total}")
    if snapshot.unknown_fields:
        print(f"  unknown        {', '.join(snapshot.unknown_fields)}")


def _print_runtime(settings: Settings) -> None:
    """Whether the runtime answers, and what it holds (§18).

    Contacting it is the point — "configured" tells an operator nothing they did
    not already type. An unreachable runtime prints as stopped with the reason
    and is never fatal, because §15.4 requires SIRVIS to work standalone.
    """
    import asyncio

    from sirvis.core.inventory import build_inventory
    from sirvis.runtimes import LMStudioAdapter, RuntimeUnavailableError

    adapter = LMStudioAdapter(
        base_url=settings.lmstudio_base_url, lms_path=settings.lmstudio_cli_path
    )
    print(f"\nruntime          lmstudio at {settings.lmstudio_base_url}")

    async def probe() -> tuple[str, list[dict[str, object]]]:
        info = await adapter.health()
        if info.state.value != "ready":
            return info.state.value + (f" — {info.detail}" if info.detail else ""), []
        return "ready", await adapter.list_models()

    try:
        state, records = asyncio.run(probe())
    except RuntimeUnavailableError as failure:
        state, records = f"unreachable — {failure}", []

    print(f"  api            {state}")
    if not records:
        print("  installed      none visible (SIRVIS works standalone; §15.4)")
        return

    inventory = build_inventory(records)
    formats: dict[str, int] = {}
    for variant in inventory.variants.values():
        formats[variant.runtime_format or "unknown"] = (
            formats.get(variant.runtime_format or "unknown", 0) + 1
        )
    print(f"  installed      {len(inventory.installed)} build(s), "
          f"{len(inventory.families)} famil(y/ies)")
    # §18 asks for GGUF and MLX capability specifically, because a machine with
    # only one of them cannot produce the format comparison §19 is built around.
    for wanted in ("gguf", "mlx"):
        count = formats.get(wanted, 0)
        print(f"  {wanted:<14} {count} build(s)" + ("" if count else "  — no comparison possible"))
    print(f"  loaded         {len(inventory.instances)} instance(s)")


def _gigabytes(value: int | None) -> str:
    """Bytes as GB, or `unknown` — never a zero standing in for a gap."""
    return f"{value / 2**30:.1f} GB" if value else "unknown"


def _run_token(settings: Settings, mint_label: str | None, scope_names: str) -> int:
    """Show or mint an API token (§4.5).

    The only place a token's plaintext is ever revealed, and only at the moment
    it is created. A token already minted cannot be shown again — the database
    holds a hash, not the value — so this prints what exists and how to replace
    it rather than pretending to recover it.
    """
    from sirvis.api.security import Scope, ensure_bootstrap_token, mint_token, token_summary

    database = prepare_database(settings.database_path)
    if mint_label:
        try:
            scopes = {Scope(name) for name in scope_names.split()}
        except ValueError:
            print(f"unknown scope in {scope_names!r}; valid: "
                  f"{', '.join(s.value for s in Scope)}", file=sys.stderr)
            return EXIT_FATAL_CONFIGURATION
        print(mint_token(database, mint_label, scopes))
        print(f"\n  label  {mint_label}\n  scopes {' '.join(sorted(s.value for s in scopes))}",
              file=sys.stderr)
        print("  This is the only time it can be read.", file=sys.stderr)
        return EXIT_OK

    fresh = ensure_bootstrap_token(database)
    if fresh:
        print(fresh)
        print("\n  label  bootstrap\n  scopes admin", file=sys.stderr)
        print("  This is the only time it can be read.", file=sys.stderr)
        return EXIT_OK

    print("a bootstrap token already exists and cannot be shown again — only a hash is stored",
          file=sys.stderr)
    for entry in token_summary(database):
        print(f"  {entry['label']:<16} {' '.join(entry['scopes'])}", file=sys.stderr)
    print("\n  mint another with: sirvis token --mint <label> --scopes \"read runtime\"",
          file=sys.stderr)
    return EXIT_OK


def _run_serve(settings: Settings) -> int:
    """Refuse an unsafe configuration, then serve."""
    report = inspect_configuration(settings)
    if not report.is_startable():
        _print_findings(report)
        print("\nrefusing to serve: fix the fatal findings above", file=sys.stderr)
        return EXIT_FATAL_CONFIGURATION
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)
    return EXIT_OK
