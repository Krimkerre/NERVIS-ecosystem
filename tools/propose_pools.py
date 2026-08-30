#!/usr/bin/env python3
"""Ask a model which of this machine's models belong in which pool.

    tools/propose_pools.py                 # every curated pool
    tools/propose_pools.py --pool chat     # one of them
    tools/propose_pools.py --model anthropic/claude-sonnet-5

**It proposes. It does not route.** The output is a Python fragment printed to
stdout and written beside this file; applying it means reading it and editing
`ravis/core/pools.py` by hand. That separation is not caution for its own sake —
CLARVIS.md §11.5 is explicit that retrieved content is evidence and never
intent, and a model's opinion about which models are good is retrieved content
by construction. A version of this that wrote `pools.py` would be a path from
model output to what every request on this machine is answered by.

**Every suggestion is checked against the catalogue before it is printed.** A
model asked to name model families will occasionally name one that does not
exist here — a plausible sibling of a real product, a version that was never
released. Each fragment is required to match at least one model id RAVIS
actually publishes, and the ones that match nothing are reported separately
rather than dropped, because "the model invented four of these" is the single
most useful thing to know about a proposal.

**Why a model at all.** The curated lists in `pools.py` are a judgement about
what each model is *for*, and they were written by hand against a catalogue of
591 that turns over every few weeks. Nothing in a `/v1/models` listing says
whether a model is a conversational assistant or a code-completion engine —
that knowledge is in the world, and a model is the only reader here that has
it. What it must not be trusted with is the consequence.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RAVIS = "http://127.0.0.1:8731"

# Enough to judge a catalogue, few enough that the request stays cheap. A
# proposal for one pool at a time is also easier to read than fifteen.
CATALOGUE_LIMIT = 1000

# What each curated pool is for, in the words the pool itself uses. Taken from
# the pool definitions rather than rewritten here: a prompt that describes the
# pool differently from `pools.py` is asking about a pool that does not exist.
POOLS: dict[str, str] = {
    "chat": (
        "Ordinary conversation with a person: instruction following, staying on "
        "topic, explaining things. General-purpose assistants only."
    ),
    "coding": (
        "Writing and reasoning about code, reading a repository, producing a "
        "patch. Code specialists and the general models that are strong at code."
    ),
    "reasoning": (
        "Problems that need working through step by step. Models built to "
        "reason, not models that merely accept a reasoning parameter."
    ),
    "clarvis-chat": (
        "Conversation, planning and instruction following inside an editor. "
        "The same class as chat."
    ),
}

INSTRUCTION = """You are sorting a catalogue of language models into one pool.

The pool is "{pool}": {purpose}

Below is every model available on this machine, one id per line.

Reply with a JSON object and nothing else:

  {{"families": ["fragment", ...], "excluded": ["fragment", ...], "why": "one sentence"}}

`families` are substrings of model ids — "claude-haiku", "qwen3-coder" — that
select the models belonging in this pool, ordered best-first for this purpose.
Order matters: the first entries are what the pool will reach for by default.

Two rules about that order:
  - put cheap, capable models first. An expensive frontier model must appear
    only at the END of the list, as a last resort.
  - a fragment must appear literally in at least one id from the list below.
    Do not invent model names or versions.

`excluded` are substrings that disqualify a model even when a family matched
it — for example a vision or code variant of a conversational family.

The catalogue:
{catalogue}
"""


def catalogue() -> list[str]:
    """Every model RAVIS publishes, or a clear failure."""
    try:
        with urllib.request.urlopen(
            f"{RAVIS}/api/v1/models?limit={CATALOGUE_LIMIT}", timeout=20
        ) as answer:
            payload = json.load(answer)
    except (urllib.error.URLError, OSError, ValueError) as failure:
        raise SystemExit(f"RAVIS did not answer at {RAVIS}: {failure}") from failure
    items = payload.get("items") or []
    return sorted(str(item.get("model_id") or "") for item in items if item.get("model_id"))


def ask(model: str, prompt: str) -> str:
    """One completion through RAVIS, returned as text."""
    request = urllib.request.Request(
        f"{RAVIS}/v1/chat/completions",
        data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            # A pool proposal is a list of fragments, not an essay. Generous
            # enough that a reasoning model can think and still answer.
            "max_tokens": 2000,
        }).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as answer:
            body = json.load(answer)
    except (urllib.error.URLError, OSError, ValueError) as failure:
        raise SystemExit(f"the completion failed: {failure}") from failure
    choices = body.get("choices") or []
    if not choices:
        raise SystemExit("the model returned no choices")
    return str((choices[0].get("message") or {}).get("content") or "")


# A reasoning model puts its thinking in the content, and the JSON is after it.
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parsed(text: str) -> dict[str, Any]:
    """The JSON object in a reply, however the model wrapped it."""
    for candidate in (_FENCE.search(text), _OBJECT.search(text)):
        if candidate is None:
            continue
        with_braces = candidate.group(1) if candidate.re is _FENCE else candidate.group(0)
        try:
            found = json.loads(with_braces)
        except ValueError:
            continue
        if isinstance(found, dict):
            return found
    raise SystemExit(f"the reply held no JSON object:\n\n{text[:600]}")


def checked(fragments: list[str], models: list[str]) -> tuple[list[str], list[str]]:
    """Split suggestions into those that match real models and those that do not.

    The invented ones are returned rather than dropped. A proposal where three
    of twenty fragments match nothing is a proposal to read carefully, and
    silently filtering them would hide exactly that signal.
    """
    real, invented = [], []
    for fragment in fragments:
        text = str(fragment).strip().lower()
        if not text:
            continue
        (real if any(text in model.lower() for model in models) else invented).append(text)
    return real, invented


# Kept in step with `VirtualModelPool._is_routable`, and deliberately a copy
# rather than an import: this script talks to RAVIS over HTTP and must run
# against a RAVIS it did not import, including one on another machine.
NOT_ROUTABLE = (
    "embedding", "embed", "whisper", "tts", "dall-e", "moderation",
    "rerank", "guard", "transcribe", "image", "video", "voice",
)


def routable(model: str) -> bool:
    """Whether a model can answer a chat completion at all."""
    lowered = model.lower()
    return not lowered.endswith(":batch") and not any(
        word in lowered for word in NOT_ROUTABLE
    )


def proposal(pool: str, answer: dict[str, Any], models: list[str]) -> str:
    """The fragment an operator would paste, with everything they need to judge it."""
    families, invented = checked(list(answer.get("families") or []), models)
    excluded, _ = checked(list(answer.get("excluded") or []), models)
    # Filtered the way membership is, so the preview is what the pool would
    # actually hold. Without this it listed `…:batch` variants the routable
    # baseline drops, and a proposal that previews models the pool cannot admit
    # is a proposal nobody can check.
    covered = sorted({
        m for m in models
        if routable(m) and any(f in m.lower() for f in families)
    })
    lines = [
        f"# ── proposed for ravis/{pool} " + "─" * 40,
        f"# {answer.get('why', '')}".rstrip(),
        f"# {len(covered)} of {len(models)} models would be members.",
    ]
    if invented:
        lines += [
            "#",
            "# MATCHED NOTHING — these name no model this machine has, which is",
            "# the model inventing plausible siblings of real products:",
            *(f"#   {fragment}" for fragment in invented),
        ]
    lines += [
        "#",
        "# The models this selects, in the order proposed:",
        *(f"#   {model}" for model in covered[:40]),
        *(["#   …"] if len(covered) > 40 else []),
        "",
        f"{pool.upper().replace('-', '_')}_FAMILIES: tuple[str, ...] = (",
        *(f'    "{fragment}",' for fragment in families),
        ")",
    ]
    if excluded:
        lines += [
            "",
            f"{pool.upper().replace('-', '_')}_EXCLUDED: tuple[str, ...] = (",
            *(f'    "{fragment}",' for fragment in excluded),
            ")",
        ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", choices=sorted(POOLS), action="append",
                        help="propose for one pool; repeatable, default all")
    parser.add_argument("--model", default="ravis/chat",
                        help="which model judges (a pool id or a model id)")
    parser.add_argument("--out", default=str(ROOT / "tools" / "proposed-pools.py"),
                        help="where to write the proposal")
    arguments = parser.parse_args()

    models = catalogue()
    if not models:
        raise SystemExit("RAVIS published no models — nothing to sort")
    print(f"{len(models)} models in the catalogue, judged by {arguments.model}\n")

    pieces = []
    for pool in arguments.pool or sorted(POOLS):
        print(f"asking about ravis/{pool}…", flush=True)
        reply = ask(arguments.model, INSTRUCTION.format(
            pool=pool, purpose=POOLS[pool], catalogue="\n".join(models),
        ))
        pieces.append(proposal(pool, parsed(reply), models))

    document = (
        "# Proposed pool memberships — NOT APPLIED.\n"
        "# Written by tools/propose_pools.py. A model's opinion about which\n"
        "# models are good is retrieved content (CLARVIS.md §11.5): read it,\n"
        "# disagree with it, and edit ravis/core/pools.py by hand.\n\n"
        + "\n\n".join(pieces) + "\n"
    )
    Path(arguments.out).write_text(document, encoding="utf-8")
    print("\n" + document)
    print(f"written to {arguments.out} — nothing was applied", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
