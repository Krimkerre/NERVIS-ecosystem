"""The Codex relay contract fixtures are complete, unmodified and consistent with themselves.

`ECOSYSTEM_RUNBOOK.md` §2.2 makes RAVIS the owner of the fixtures both products build the Codex
engine against: `tests/fixtures/relay-contract/*.json` and `tests/fixtures/lock-rule-cases.json`.
Clarvis keeps a byte-identical copy and checks it against `codex-contract.sha256`, so that manifest
is the promise the copy is held to — which is why a fixture edited here without its manifest line
has to fail here first, before Clarvis quietly goes on building against the old one.

Nothing here exercises RAVIS behaviour, because none is built yet (`RAVIS.md` M29). These tests hold
the contract a later increment builds against to what it claims about itself: every error code in
its catalogue is shown at least once, at the status and retry flag the catalogue gives it, and the
lock rule's cases agree with the rule they state. RAVIS's real lock rule lands in R2 and must pass
the same cases; the small restatement below exists only to catch a mistyped expectation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MANIFEST = FIXTURES / "codex-contract.sha256"
ENVELOPE_FIELDS = {"code", "message", "retryable", "details", "request_id", "trace_id"}


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _contract_files() -> list[str]:
    """Every fixture the manifest must cover: the relay folder's JSON and the lock rule's cases."""
    relay = [
        path.relative_to(FIXTURES).as_posix()
        for path in (FIXTURES / "relay-contract").glob("*.json")
    ]
    return sorted([*relay, "lock-rule-cases.json"])


def _manifest() -> dict[str, str]:
    """`<sha256>  <path>` lines — the format `shasum -a 256 -c` reads — keyed by path."""
    entries = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        entries[name] = digest
    return entries


def _responses(node: Any) -> Iterator[dict[str, Any]]:
    """Every example response in a fixture: any mapping carrying an integer status and a body."""
    if isinstance(node, dict):
        if isinstance(node.get("status"), int) and "body" in node:
            yield node
        for value in node.values():
            yield from _responses(value)
    elif isinstance(node, list):
        for value in node:
            yield from _responses(value)


def _mep_errors() -> Iterator[tuple[str, int, dict[str, Any]]]:
    """(fixture, status, error) for every response whose body is the runbook §4.5 envelope.

    An `error` carrying `request_id` is the envelope. Success bodies, `/v1`'s OpenAI-shaped refusal
    and NERVIS's own refusals before forwarding carry none, and are checked on their own terms.
    """
    for name in _contract_files():
        for response in _responses(_load(name)):
            body = response["body"]
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict) and "request_id" in error:
                yield name, response["status"], error


def test_the_manifest_covers_every_contract_fixture_with_its_current_hash() -> None:
    manifest = _manifest()

    assert sorted(manifest) == _contract_files()
    for name, digest in manifest.items():
        actual = hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
        assert actual == digest, (
            f"{name} changed without its manifest line: update codex-contract.sha256 and copy the "
            "fixtures into Clarvis's src/test/fixtures/ in the same sitting"
        )


def test_every_error_example_is_a_catalogued_code_at_its_catalogued_status() -> None:
    catalogue = _load("relay-contract/conventions.json")["error_codes"]

    for name, status, error in _mep_errors():
        assert set(error) == ENVELOPE_FIELDS, (name, error)
        entry = catalogue[error["code"]]
        assert status in entry["status"], (name, error["code"], status)
        assert error["retryable"] is entry["retryable"], (name, error["code"])


def test_every_catalogued_code_is_shown_at_least_once() -> None:
    """The fixtures claim to show every error code the contract names; this makes it checkable."""
    catalogue = set(_load("relay-contract/conventions.json")["error_codes"])

    shown = {error["code"] for _, _, error in _mep_errors()}

    assert catalogue - shown == set()


def test_the_v1_refusal_is_one_openai_shaped_400() -> None:
    """Clients parse `/v1` errors, so the refusal keeps OpenAI's shape, and says one thing."""
    refusal = _load("relay-contract/openai-refusal.json")["examples"]

    bodies = {json.dumps(case["response"]["body"], sort_keys=True) for case in refusal}

    assert len(bodies) == 1
    assert all(case["response"]["status"] == 400 for case in refusal)
    assert refusal[0]["response"]["body"]["error"]["code"] == "agent_backend_not_a_chat_model"


def _verdict(case: dict[str, Any], threshold: int) -> str:
    """The shared lock rule as `lock-rule-cases.json` states it: only `gone` counts as dead."""
    lock, probe = case["lock"], case["probe"]
    if not probe["pid_running"] or probe["lstart"] != lock["pid_start"]:
        return "gone"
    stale = lock["heartbeat_age_seconds"] > threshold
    awake = case["observer_awake_seconds"] >= threshold
    return "unresponsive" if stale and awake else "alive"


def _adoption(case: dict[str, Any]) -> str:
    """The restart adoption rule: RAVIS rewrites only a lock file naming its previous instance."""
    if case["file"] is None:
        return "superseded" if case["create_race_lost"] else "create"
    return "rewrite" if case["file"]["names_previous_ravis_instance"] else "superseded"


def test_the_lock_rule_cases_agree_with_the_rule_they_state() -> None:
    cases = _load("lock-rule-cases.json")
    threshold = cases["rule"]["threshold_seconds"]

    for case in cases["verdict_cases"]:
        assert _verdict(case, threshold) == case["expected"], case["name"]
    for case in cases["adoption_cases"]:
        assert _adoption(case) == case["expected"]["action"], case["name"]
