#!/usr/bin/env python3
"""The released MEP schemas match the code, and every service matches them (§15).

**`schema_versions` was published to every peer and there were no schemas.**
`/ecosystem/version` has always answered `{"mep": PROTOCOL_VERSION}`, §15 asks
that "MEP schemas, fixtures and versions are released and pinned", and nothing in
either repository was a schema: no file, no fixture, no validator. A version
number for an artifact that does not exist is this repository's recurring shape,
and this one was being told to everybody who asked.

Two questions, and the second is the one that catches real drift:

1. **Do the released files still match the models?** They are generated, so a
   change to `schemas.py` that nobody re-rendered leaves consumers reading a
   description of last week's envelope. `--update` re-renders; the diff is the
   review.

2. **Does each service's own answer satisfy them?** Drift is not usually a
   mangled payload — it is a field quietly dropped, or a status nobody thought
   to declare. `conformance_check.py` asks whether three services spell one
   *error* envelope the same way; this asks whether what they publish is what the
   released schema says they publish, on every metadata route §4.1 requires.

**In-process, like its sibling, and for the same reason.** Importing the three
applications and driving them through `TestClient` means the gate runs from a
clean clone with nothing started. `tools/acceptance_run.py` is where the same
question gets asked of services that are actually listening.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
for package in ("nervis", "ravis", "sirvis", "protocol"):
    sys.path.insert(0, str(ROOT / package / "src"))

os.environ.setdefault("NERVIS_SERVED_HOSTS", '["127.0.0.1","localhost","::1","testserver"]')
os.environ.setdefault("RAVIS_ALLOWED_HOSTS", '["127.0.0.1","localhost","::1","testserver"]')
os.environ.setdefault("SIRVIS_ALLOWED_HOSTS", '["127.0.0.1","localhost","::1","testserver"]')

from ecosystem_protocol.schemas import (
    SCHEMA_DIR,
    schemas,
    validate,
    write,
)
from fastapi.testclient import TestClient

#: The §4.1 routes every service exposes, and the envelope each one answers with.
#: `/ecosystem/events` is absent: it is an SSE stream rather than a document, and
#: reading it here would hang the gate on a service that is behaving correctly.
ROUTES = (
    ("/ecosystem/health", "health"),
    ("/ecosystem/identity", "identity"),
    ("/ecosystem/capabilities", "capabilities"),
    ("/ecosystem/version", "version"),
)

failures: list[str] = []


def rendered_matches_released() -> None:
    """The committed files are what the models produce today."""
    import json

    current = schemas()
    for name, schema in current.items():
        path = SCHEMA_DIR / f"{name}.json"
        if not path.is_file():
            failures.append(f"{path.relative_to(ROOT)} is missing — run with --update")
            continue
        held = json.loads(path.read_text())
        if held != schema:
            failures.append(
                f"{path.relative_to(ROOT)} no longer matches `schemas.py`; a consumer "
                "reading it is reading an older envelope — run with --update and commit "
                "the diff")
    # `fixtures.json` is released beside them and is not one of them: it says what
    # a reader must accept and refuse, which no schema can state about itself.
    stale = {p.stem for p in SCHEMA_DIR.glob("*.json")} - set(current) - {"fixtures"}
    for name in sorted(stale):
        failures.append(f"schemas/{name}.json is released and no model produces it")


def fixtures_behave() -> None:
    """Every `valid` case is accepted and every `invalid` one is refused.

    **The second half is the point.** A schema that accepts everything passes the
    first half perfectly, and a permissive validator is how a payload that broke
    something gets called conformant. Each refusal names the rule it breaks, and
    they are drawn from what this ecosystem has actually done — the fields no
    producer sent, the status nobody emits, the capability id carrying its
    `@major` onto the wire.
    """
    import json

    path = SCHEMA_DIR / "fixtures.json"
    if not path.is_file():
        failures.append(f"{path.relative_to(ROOT)} is missing; §4.6 asks for fixtures")
        return
    cases = json.loads(path.read_text())
    for name, group in cases.items():
        if name.startswith("_"):
            continue
        for case in group.get("valid", []):
            wrong = validate(name, case["payload"])
            if wrong:
                failures.append(f"fixture {name}/valid ({case['why']}) was refused: {wrong}")
        for case in group.get("invalid", []):
            if not validate(name, case["payload"]):
                failures.append(
                    f"fixture {name}/invalid was accepted, and should not be — {case['why']}")


def answers_match(service: str, client: Any) -> None:
    """Every metadata route, against the schema it promises."""
    for path, envelope in ROUTES:
        answered = client.get(path)
        if answered.status_code != 200:
            failures.append(f"{service} {path}: HTTP {answered.status_code}, and §4.1 "
                            "requires 200 — even when `ready` is false")
            continue
        wrong = validate(envelope, answered.json())
        if wrong:
            failures.append(f"{service} {path} does not satisfy `{envelope}`: {wrong}")


def nervis_client() -> Any:
    from nervis.app import create_app
    from nervis.config import Settings

    settings = Settings(database_path=":memory:", workspace_path="/tmp", _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings))


def ravis_client() -> Any:
    from ravis.app import create_app
    from ravis.config import Settings

    return TestClient(create_app(Settings(database_path=":memory:", _env_file=None)))  # type: ignore[call-arg]


def sirvis_client() -> Any:
    from sirvis.app import create_app
    from sirvis.config import Settings

    return TestClient(create_app(Settings(database_path=":memory:", _env_file=None)))  # type: ignore[call-arg]


def main() -> int:
    if "--update" in sys.argv:
        written = write()
        print(f"rendered {len(written)} schemas into {SCHEMA_DIR.relative_to(ROOT)} — "
              "read the diff before committing")
        return 0

    rendered_matches_released()
    fixtures_behave()
    for service, build in (("NERVIS", nervis_client), ("RAVIS", ravis_client),
                           ("SIRVIS", sirvis_client)):
        with build() as client:
            answers_match(service, client)

    if failures:
        print("the released schemas and the services disagree:\n")
        for failure in failures:
            print(f"  • {failure}")
        print(
            "\nA published `schema_versions` is a promise that these files describe what"
            "\nthe services send. Either the envelope changed and the schema should, or"
            "\nthe schema is right and the service is not."
        )
        return 1
    import json
    cases = json.loads((SCHEMA_DIR / "fixtures.json").read_text())
    counted = sum(len(g.get("valid", [])) + len(g.get("invalid", []))
                  for name, g in cases.items() if not name.startswith("_"))
    print(f"{len(schemas())} released schemas match the models, {counted} fixtures behave, "
          f"and 3 services answer {len(ROUTES)} metadata routes that satisfy them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
