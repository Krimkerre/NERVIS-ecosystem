#!/usr/bin/env python3
"""Ask a provider's catalogue which of its models can actually answer.

**Why this exists.** A provider's model list is the provider's *claim* about
what it sells, and Google's is wrong in two directions at once. It advertises
`models/gemini-2.5-flash`, which answers 404 — *"no longer available to new
users"* — and it advertises music, speech and image models that pass every
metadata check a chat client can make, because the metadata is identical: a
Lyria music model reports the same capabilities and a 1,048,576-token context
window as Gemini Flash.

Nothing in the listing distinguishes them. The only honest signal is what
happens when you ask, so this asks — once per model, and remembers.

**It costs real requests, so it is a script and not a background sweep.** A
timer that probed on every catalogue refresh would bill somebody for the
privilege of rediscovering the same dead ids. Instead this keeps a record of
what it has already learned and, by default, probes only what is new — so the
run after a catalogue renewal costs one request per genuinely new model, and
nothing at all when the catalogue has not moved.

    tools/probe_models.py google              # only ids never probed before
    tools/probe_models.py google --all        # re-check everything
    tools/probe_models.py google --apply      # and write the dead ones into
                                              # the provider's exclude list

The excluded patterns are never probed. There is no point paying to confirm
that a model somebody deliberately filtered out is filtered out.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

RAVIS = os.environ.get("RAVIS_BASE_URL", "http://127.0.0.1:8731")

#: Where the verdicts live. Beside `models.json` and the rest of RAVIS's own
#: configuration, because that is where per-machine knowledge belongs — the
#: answer is about this account's access, not about the repository.
RECORD = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ravis" / "probed.json"

#: A generation small enough to be free in every sense that matters, and large
#: enough for the answer to mean something.
#:
#: It was one token, which made every verdict ambiguous: a text model truncated
#: at one token and an image model that returns no text both look like "answered
#: with nothing". Eight leaves room for a word.
PROBE = {"max_tokens": 8, "messages": [{"role": "user", "content": "Say ready."}]}

WORKS, QUIET, GONE, BROKEN = "works", "quiet", "gone", "broken"


def _call(path: str, credential: str, payload: Any = None, method: str = "GET") -> Any:
    """One request to RAVIS, returning the parsed body whatever the status.

    A failure body is the answer here rather than an exception: this script
    exists to read refusals, and raising on them would throw away the evidence.
    """
    request = urllib.request.Request(
        RAVIS + path,
        method=method,
        headers={"authorization": f"Bearer {credential}", "content-type": "application/json"},
        data=json.dumps(payload).encode() if payload is not None else None,
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as refused:
        try:
            return json.loads(refused.read() or b"{}")
        except ValueError:
            return {"error": {"message": f"HTTP {refused.code}"}}
    except OSError as unreachable:
        return {"error": {"message": f"{type(unreachable).__name__}: {unreachable}"}}


def _verdict(answer: dict[str, Any]) -> tuple[str, str]:
    """What one probe proved, and in one line why.

    **The question is whether the model accepts a chat request, not whether it
    said anything interesting.** An earlier version asked the second question
    and got the first one wrong: it gave each model eight tokens and called
    anything that produced no text the wrong kind of model — which condemned
    `gemini-3.6-flash`, a reasoning build that spends a small budget thinking
    and had already been observed answering perfectly well at a larger one.

    Excluding a working model is the expensive mistake. A useless one that
    answers merely ranks badly and is never chosen; a working one that has been
    excluded is capability thrown away, and nothing will ever discover it again.
    So a 200 is `works` — noted as `quiet` when no text came back, which is a
    remark for a person and never grounds for exclusion.

    Only a refusal condemns anything, and only when the provider's own words say
    the refusal is permanent.
    """
    error = answer.get("error") or {}
    if error:
        # **The upstream's words, not RAVIS's summary of them.** RAVIS reports a
        # failed chain as "No upstream attempt succeeded. Tried: X (unknown)",
        # which says nothing about *why* — the provider's own sentence is on the
        # attempt, and it is the sentence that distinguishes a retired model
        # from an overloaded one.
        attempts = (error.get("route") or {}).get("attempts") or []
        detail = str(
            next((a.get("detail") for a in attempts if a.get("detail")), "")
            or error.get("message") or ""
        )
        lowered = detail.lower()
        if "no upstream lists" in lowered:
            return GONE, "no upstream lists it"
        # Permanent by the provider's own account. "Only supports Interactions
        # API" belongs here with the retirements: it is not a fault to wait out,
        # it is this model saying it does not serve this kind of request.
        if any(mark in lowered for mark in (
            "404", "not found", "is not found", "does not exist",
            "no longer available", "deprecated", "no access",
            "only supports", "does not support", "not supported for",
        )):
            return GONE, detail[:90]
        # Everything else — a timeout, a 429, an overloaded region — is a
        # moment, not a fact. Reported so somebody sees it, never applied.
        return BROKEN, detail[:90]

    choices = answer.get("choices") or []
    if not choices:
        return WRONG_KIND, "answered with no choices — not a chat completion"
    message = (choices[0].get("message") or {})
    if message.get("content") or message.get("tool_calls") or message.get("reasoning_content"):
        return WORKS, ""
    # It accepted the request and returned a completion, which is the whole
    # question. No text at eight tokens means an image or audio model, or a
    # reasoning build that spent the budget thinking — and this probe cannot
    # tell those apart, so it says what it saw and condemns nothing.
    return QUIET, "answered with no text in 8 tokens — worth a look, not excluded"


def _stored() -> dict[str, dict[str, Any]]:
    try:
        with RECORD.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _store(record: dict[str, dict[str, Any]]) -> None:
    RECORD.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = RECORD.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, RECORD)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", nargs="?", default="google")
    parser.add_argument("--all", action="store_true",
                        help="re-probe models already recorded, not just new ones")
    parser.add_argument("--apply", action="store_true",
                        help="add every model found gone or of the wrong kind to the "
                             "provider's exclude list")
    parser.add_argument("--credential", default=os.environ.get("RAVIS_CREDENTIAL", ""))
    arguments = parser.parse_args()

    credential = arguments.credential
    if not credential:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import run

        credential = run.nervis_ravis_credential()

    listing = _call(f"/api/v1/providers/{arguments.provider}/models", credential)
    catalogue = listing.get("catalogue_sample") or []
    if not catalogue:
        print(f"no catalogue for {arguments.provider!r}: "
              f"{json.dumps(listing)[:200]}", file=sys.stderr)
        return 1

    excluded = tuple((listing.get("filter") or {}).get("exclude") or ())
    offered = [m for m in catalogue if not any(fnmatchcase(m, p) for p in excluded)]

    record = _stored().get(arguments.provider, {}) if not arguments.all else {}
    todo = [m for m in offered if m not in record]

    print(f"{arguments.provider}: {len(catalogue)} listed, {len(offered)} after the "
          f"exclude list, {len(todo)} to probe"
          + ("" if arguments.all else f" ({len(offered) - len(todo)} already known)"))
    if not todo:
        print("nothing new — the catalogue has not moved.")

    for model in todo:
        answer = _call(
            "/v1/chat/completions", credential,
            {"model": f"ravis/{arguments.provider}/{model}", **PROBE},
            method="POST",
        )
        verdict, why = _verdict(answer)
        record[model] = {"verdict": verdict, "detail": why}
        mark = {WORKS: "ok  ", QUIET: "hm  ", GONE: "GONE", BROKEN: "??  "}[verdict]
        print(f"  {mark} {model}" + (f"  — {why}" if why else ""))

    everything = _stored()
    everything[arguments.provider] = record
    _store(everything)

    dead = sorted(m for m, r in record.items() if r["verdict"] == GONE)
    answering = sorted(m for m, r in record.items() if r["verdict"] in (WORKS, QUIET))
    unclear = sorted(m for m, r in record.items() if r["verdict"] == BROKEN)
    print(f"\n{len(answering)} answer, {len(dead)} are gone"
          + (f", {len(unclear)} failed for some other reason and are left alone" if unclear else "."))
    if not dead:
        return 0

    if not arguments.apply:
        print("\nre-run with --apply to add these to the exclude list:")
        for model in dead:
            print("   ", model)
        return 0

    # Exact ids rather than patterns. A pattern is a guess about what a vendor
    # will name things next; a model that answered 404 today is a measurement.
    keep = list(excluded)
    keep += [m for m in dead if m not in keep]
    updated = _call(
        f"/api/v1/providers/{arguments.provider}/models", credential,
        {"include": list((listing.get("filter") or {}).get("include") or []), "exclude": keep},
        method="PUT",
    )
    print(f"\nexclude list now {len(keep)} entries; "
          f"{updated.get('matched_total')} of {updated.get('catalogue_total')} models offered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
