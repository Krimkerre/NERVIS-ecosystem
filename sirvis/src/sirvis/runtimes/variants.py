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
    executable = binary or cli_path()
    if not executable:
        return None
    try:
        finished = subprocess.run(
            [executable, "ps", "--json"],
            capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if finished.returncode != 0:
        return None
    try:
        rows = json.loads(finished.stdout or "[]")
    except ValueError:
        return None
    if not isinstance(rows, list):
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
    quantization = row.get("quantization")
    name = ""
    if isinstance(quantization, dict):
        name = str(quantization.get("name") or "")
    elif isinstance(quantization, str):
        name = quantization
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
