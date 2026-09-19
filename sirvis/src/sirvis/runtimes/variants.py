"""Which build of a model is actually loaded, when the HTTP API will not say.

**LM Studio groups several builds under one entry, and its API describes the
wrong one.** A machine holding both an MLX and a GGUF of `google/gemma-4-e4b`
sees one entry in `/api/v0/models`, and that entry reports whichever variant the
app has selected — not the one that is loaded and answering. Measured here:

    lms ps           google/gemma-4-e4b@q4_k_m   gguf   Q4_K_M   (loaded)
    /api/v0/models   google/gemma-4-e4b          mlx    4bit

The API is also inconsistent about the qualified key: a completion addressed to
`google/gemma-4-e4b@q4_k_m` is answered, while `/api/v0/models/google/gemma-4-e4b@q4_k_m`
returns *"Model with identifier … not found"*. So the runtime will happily
measure a build it refuses to describe.

**Why that cannot be shrugged off.** §12.2 makes format and quantization part of
evidence identity — a Qwen MLX 4-bit under MLX is different evidence from the
same base model as GGUF Q4_K_M under llama.cpp, and on this machine two builds
of one family reach 1/8 and 8/8 on the same tool-call trial. Filing a GGUF
measurement under an MLX identity is not a cosmetic error: it is evidence about
one build attributed to another, and RAVIS admits and excludes on it.

So SIRVIS asks LM Studio's own CLI, which does know, and **refuses to record
evidence when nothing can confirm the variant**. A benchmark that cannot say
which build it measured is a benchmark whose result nobody can use twice.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass

# Where LM Studio installs its CLI. Looked up on PATH first, because an operator
# who put it somewhere else meant it.
DEFAULT_CLI = os.path.expanduser("~/.lmstudio/bin/lms")

# Short: this runs before a benchmark, and a CLI that does not answer promptly
# is one that cannot be relied on for an identity either.
TIMEOUT_SECONDS = 5.0


# The CLI and the HTTP catalogue use different words for one format: `lms ps`
# reports `safetensors` where `/api/v0/models` says `mlx`. Evidence identity has
# to be stable across runs, and a build that is `mlx` when read one way and
# `safetensors` when read the other is two identities for one thing — so the
# catalogue's vocabulary wins, because that is what every other reader sees.
FORMAT_NAMES = {"safetensors": "mlx"}


#: A build's (family key, format) → the devices that hold it: `None` for this machine, an LM Link
#: device id for another. From `lms ls --json`, whose rows carry `deviceIdentifier` — null here,
#: the other machine's id for a build reached through LM Link (19 September 2026, read off the
#: owner's ThinkPad: its REST listing showed the Mac's models with no mark at all).
LinkTable = dict[tuple[str, str], frozenset[str | None]]


def linked_devices(plain: list[object]) -> LinkTable:
    """Which device holds each listed build, from `lms ls --json`'s rows."""
    table: dict[tuple[str, str], set[str | None]] = {}
    for row in plain:
        if not isinstance(row, dict):
            continue
        key = str(row.get("modelKey") or "")
        raw_format = str(row.get("format") or "")
        runtime_format = FORMAT_NAMES.get(raw_format, raw_format)
        if not key or not runtime_format:
            continue
        device = row.get("deviceIdentifier")
        table.setdefault((key, runtime_format), set()).add(
            device if isinstance(device, str) and device else None
        )
    return {build: frozenset(devices) for build, devices in table.items()}


def linked_device(runtime_key: str, runtime_format: str | None, table: LinkTable) -> str | None:
    """The other device a build runs on through LM Link, or None for this machine.

    None too when this machine holds a copy as well: LM Studio's own listing shows one of the
    two and says not which, so the build is not claimed for another machine on a guess.
    """
    found = table.get((runtime_key.split("@", 1)[0], runtime_format or ""), frozenset())
    remote = sorted(device for device in found if device)
    return remote[0] if remote and None not in found else None


def link_device_names(binary: str | None = None) -> dict[str, str]:
    """LM Link's other devices by identifier, under the names their owner gave them.

    From `lms link status --json` (19 September 2026: `peers` of `deviceIdentifier` and
    `deviceName`, "ThinkPadX13G2" on the Mac). Empty when LM Link is off or the CLI can't be
    asked, which leaves a linked build shown as "another device".
    """
    status = _ask(binary, ["link", "status", "--json"])
    peers = status.get("peers") if isinstance(status, dict) else None
    return {
        str(peer["deviceIdentifier"]): str(peer["deviceName"])
        for peer in (peers if isinstance(peers, list) else [])
        if isinstance(peer, dict) and peer.get("deviceIdentifier") and peer.get("deviceName")
    }


@dataclass(frozen=True)
class LoadedVariant:
    """One resident build, as the runtime's own tooling names it."""

    model_key: str
    """The qualified key — `google/gemma-4-e4b@q4_k_m` — which is also what a
    completion may be addressed to."""
    family: str
    """The unqualified name the HTTP API uses, so the two can be matched up."""
    runtime_format: str
    quantization: str


def cli_path() -> str | None:
    """The `lms` binary, or nothing when it is not on this machine."""
    found = shutil.which("lms")
    if found:
        return found
    return DEFAULT_CLI if os.path.isfile(DEFAULT_CLI) else None


def loaded_variants(binary: str | None = None) -> list[LoadedVariant] | None:
    """What is resident, per the CLI — or `None` when it cannot be asked.

    `None` and `[]` are different answers and the caller depends on the
    difference: an empty list means the runtime holds nothing, and `None` means
    nobody could be asked, which is the case that must refuse rather than guess.
    """
    rows = _run(binary, ["ps", "--json"])
    if rows is None:
        return None
    return [variant for variant in map(_read, rows) if variant is not None]


def _read(row: object) -> LoadedVariant | None:
    """One row of `lms ps --json`, or nothing when it is not the shape expected.

    Nothing rather than a partial variant: a record with a key and no format is
    exactly the ambiguity this module exists to remove.
    """
    if not isinstance(row, dict):
        return None
    key = str(row.get("modelKey") or "")
    runtime_format = str(row.get("format") or "")
    name = _quantization_name(row.get("quantization"))
    if not key or not runtime_format or not name:
        return None
    return LoadedVariant(
        model_key=key,
        # `google/gemma-4-e4b@q4_k_m` → `google/gemma-4-e4b`, which is the id the
        # HTTP catalogue uses for the whole group.
        family=key.split("@", 1)[0],
        runtime_format=FORMAT_NAMES.get(runtime_format, runtime_format),
        quantization=name,
    )


@dataclass(frozen=True)
class InstalledVariant:
    """One build on disk, as the runtime's own tooling names it.

    Separate from `LoadedVariant` because the two answer different questions —
    *what is resident* and *what is installed* — and a type that served both
    would need every field to be optional for one of them.
    """

    model_key: str
    """The qualified key: `google/gemma-4-e4b@4bit`."""
    family: str
    runtime_format: str
    quantization: str
    publisher: str
    architecture: str
    max_context: int | None
    size_bytes: int | None
    model_type: str


def installed_variants(binary: str | None = None) -> list[InstalledVariant] | None:
    """Every build on disk, per the CLI — or `None` when it cannot be asked.

    **The HTTP catalogue does not list them all.** It publishes a loaded build
    under its qualified key and the rest of a group under the plain one, showing
    whichever variant the app has *selected* — so a machine holding an MLX and a
    GGUF of one model publishes two entries while one is loaded, and exactly one
    once it is not. The build that vanishes is still installed, still loadable
    and still benchmarkable; it is only unnameable.

    `lms ls --variants --json` names every one of them, so that is what is
    asked. It is not a superset of the catalogue — it indexes fewer models than
    the HTTP API reports on this machine — which is why the caller adds from it
    and never replaces with it.

    `None` and `[]` are different answers, as with `loaded_variants`: nothing
    installed is a fact, and nobody to ask is not.
    """
    rows = _run(binary, ["ls", "--variants", "--json"])
    if rows is None:
        return None
    builds: list[InstalledVariant] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # A group carries its builds in `variants`; an ungrouped model is its
        # own single build and describes itself.
        nested = row.get("variants")
        for entry in (nested if isinstance(nested, list) and nested else [row]):
            build = _read_installed(entry)
            if build is not None:
                builds.append(build)
    return builds


def _read_installed(row: object) -> InstalledVariant | None:
    """One build from `lms ls --variants --json`, or nothing when malformed."""
    if not isinstance(row, dict):
        return None
    key = str(row.get("modelKey") or "")
    runtime_format = str(row.get("format") or "")
    name = _quantization_name(row.get("quantization"))
    if not key or not runtime_format or not name:
        return None
    return InstalledVariant(
        model_key=key,
        family=key.split("@", 1)[0],
        runtime_format=FORMAT_NAMES.get(runtime_format, runtime_format),
        quantization=name,
        publisher=str(row.get("publisher") or ""),
        architecture=str(row.get("architecture") or ""),
        max_context=_int(row.get("maxContextLength")),
        size_bytes=_int(row.get("sizeBytes")),
        # `vision` is a separate flag in the CLI and folded into the type in the
        # HTTP catalogue. Folded the same way here so one vocabulary reaches the
        # domain, whichever reader produced the record.
        model_type="vlm" if row.get("vision") and row.get("type") == "llm"
        else str(row.get("type") or "llm"),
    )


def installed_paths(binary: str | None = None) -> frozenset[str] | None:
    """Where every installed model came from, per both of the CLI's listings (M11).

    **Both, because neither lists them all.** `lms ls --variants --json` names the
    builds of models installed through LM Studio's own catalogue and leaves out every
    model downloaded by link: measured on 12 September 2026, it listed 7 builds while
    `lms ls --json` listed 19, among them the SmolLM2 file SIRVIS had just downloaded.
    Discovery marked that file not installed, and asking again queued a second
    download. The plain listing names link downloads but not the builds inside a
    catalogue group, so the answer is the union of the two.

    A path is the Hugging Face repository a model came from: `owner/repo/file.gguf`
    for a GGUF file, `owner/repo` for an MLX folder. LM Studio indexes a catalogue
    build as `qwen/qwen3.5-9b@lmstudio-community/Qwen3.5-9B-MLX-4bit`, so the
    repository is what follows the `@`. `None` when neither listing could be read.
    """
    grouped = _run(binary, ["ls", "--variants", "--json"])
    plain = _run(binary, ["ls", "--json"])
    if grouped is None and plain is None:
        return None
    paths: set[str] = set()
    for row in [*(grouped or []), *(plain or [])]:
        if not isinstance(row, dict):
            continue
        nested = row.get("variants")
        # In the variants listing a group carries its builds as objects; in the plain
        # listing `variants` is a list of names, and the row describes itself.
        entries = (
            nested if isinstance(nested, list) and nested and isinstance(nested[0], dict)
            else [row]
        )
        for entry in entries:
            identifier = str(entry.get("indexedModelIdentifier") or entry.get("path") or "")
            if identifier:
                paths.add(identifier.split("@", 1)[-1])
    return frozenset(paths)


def listings(binary: str | None = None) -> tuple[list[object], list[object]] | None:
    """Both CLI listings verbatim — plain, then variants — for `model_files.locate`, or None
    when neither could be read."""
    plain = _run(binary, ["ls", "--json"])
    grouped = _run(binary, ["ls", "--variants", "--json"])
    if plain is None and grouped is None:
        return None
    return plain or [], grouped or []


def installed_sizes(binary: str | None = None) -> dict[str, int] | None:
    """Every installed model's size on disk, under the plain key LM Studio lists it by.

    **The variants listing names too few models to size them all.** It carries a size
    per build but lists only models installed through LM Studio's catalogue — 7 builds
    against 19 models on 12 September 2026 — so sizes read from it alone left thirteen
    of twenty models unsized. The plain listing (`lms ls --json`) names every model once,
    with the size of the variant LM Studio has selected rather than all its variants
    added together: the same day it gave `google/gemma-4-e4b` 6,326,918,619 bytes, the
    5.9 GB of its selected Q4_K_M build, where the two builds together are over 12 GB.
    `None` when the listing cannot be read.
    """
    rows = _run(binary, ["ls", "--json"])
    if rows is None:
        return None
    sizes: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key, size = str(row.get("modelKey") or ""), _int(row.get("sizeBytes"))
        if key and size is not None:
            sizes[key] = size
    return sizes


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _quantization_name(value: object) -> str:
    """The quantization name, from either shape the CLI uses for it."""
    if isinstance(value, dict):
        return str(value.get("name") or "")
    return str(value or "") if isinstance(value, str) else ""


def _run(binary: str | None, arguments: list[str]) -> list[object] | None:
    """A CLI call that returns a JSON list, or `None` for every way it can fail.

    One place, because both readers here fail identically — no CLI, a non-zero
    exit, a timeout, output that is not a JSON list — and a second copy of that
    ladder is a second place for the None-versus-empty distinction to rot.
    """
    rows = _ask(binary, arguments)
    return rows if isinstance(rows, list) else None


def _ask(binary: str | None, arguments: list[str]) -> object | None:
    """A CLI call's JSON answer, whatever its shape, or `None` for every way it can fail."""
    executable = binary or cli_path()
    if not executable:
        return None
    try:
        finished = subprocess.run(
            [executable, *arguments],
            capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if finished.returncode != 0:
        return None
    try:
        answer: object = json.loads(finished.stdout or "[]")
    except ValueError:
        return None
    return answer


def confirm(model_key: str, variants: list[LoadedVariant] | None) -> LoadedVariant | None:
    """The loaded build behind a model key, or nothing when it is not confirmed.

    Matches the qualified key first and falls back to the family, because a
    caller may address either — `--model google/gemma-4-e4b` is the ordinary
    case and `--model google/gemma-4-e4b@q4_k_m` is how somebody picks a build
    deliberately.

    Ambiguity is not resolved by choosing: two loaded builds of one family with
    an unqualified key returns nothing, and the caller refuses. Picking the
    first would be the guess this module exists to prevent.
    """
    if variants is None:
        return None
    exact = [variant for variant in variants if variant.model_key == model_key]
    if len(exact) == 1:
        return exact[0]
    family = model_key.split("@", 1)[0]
    matching = [variant for variant in variants if variant.family == family]
    return matching[0] if len(matching) == 1 else None
