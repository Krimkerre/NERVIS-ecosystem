"""Checking a Codex build RAVIS hasn't tested, accepting it, and revoking it (design §3.4, §4.3).

The contract is `codex-admin.json` (`version-check`, `accept-version`); the rules are the design's:

- **the seven checks**, the first six refusing acceptance: signature, sha256, version, both schema
  trees, the used methods, a handshake on a throwaway home, and the file rules' surface;
- **a binary OpenAI didn't sign is never run** — checks 2 to 7 say "not run";
- **the protocol report** names used definitions that changed and counts the others, against the
  pinned build's committed definition hashes;
- **acceptance is not a test**: an accepted build is recorded with unproven file rules, stays
  paused, and gets a Codex process (for sign-in, the allowance and the re-test) but no task;
- **revoking** pauses it again and stops its process;
- **the pin lists what RAVIS sends** (R6): every call production code makes with a Codex method
  names one the pin lists, and each table of messages RAVIS reads holds only listed ones.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.codex_fakes import (
    EXPERIMENTAL_SCHEMA,
    FakeAppServer,
    FakeCodesign,
    FakeCodex,
    fake_bundle,
    pin_entry,
    write_definitions,
    write_pin,
)
from tests.codex_rig import (
    call,
    codex_rig,
    eventually,
    fixture_response,
    reaches,
    refused_as,
    state_of,
)

import ravis
from ravis.agent.requests import KINDS
from ravis.agent.session import AgentSession
from ravis.codex.acceptance import CHECK_NAMES, check_version
from ravis.codex.pin import TESTED_RUNTIMES, definitions_file, read_pin, used_surface
from ravis.codex.routing import ELICITATION_DECLINED
from ravis.codex.runtime import file_sha256
from ravis.codex.schema_report import COMBINED_BUNDLE, DefinitionRecord
from ravis.codex.service import MESSAGE_REASONS
from ravis.config import Settings

ACCEPT = "/api/v1/codex/accept-version"
VERSION_CHECK = "/api/v1/codex/version-check"


def changed_schema(**definitions: Any) -> dict[str, str]:
    """The experimental tree with some definitions changed, as a later Codex would change them."""
    changed = fake_bundle(experimental=True, changed=definitions)
    return {**EXPERIMENTAL_SCHEMA, COMBINED_BUNDLE: changed}


def pinned_one(tmp_path: Path) -> dict[str, Any]:
    """A tested entry for another build, with the default schema and its definition hashes."""
    one = FakeCodex.install(tmp_path / "pinned" / "codex", build="pinned-one")
    definitions = write_definitions(tmp_path / "pinned" / "definitions.json")
    return pin_entry(one, proven=True, definitions=str(definitions))


def settings_for(codex: FakeCodex) -> Settings:
    return Settings(
        database_path=":memory:",
        _env_file=None,  # type: ignore[call-arg]
        codex_enabled=True,
        codex_executable=str(codex.path),
    )


def test_the_committed_definition_record_is_the_pinned_builds_and_holds_the_surface() -> None:
    pin = read_pin()
    entry = pin["tested"][0]
    raw = definitions_file(entry, TESTED_RUNTIMES)

    assert raw is not None
    assert (raw["codex_version"], raw["experimental_tree"]) == (
        entry["codex_version"], entry["experimental_tree"],
    )
    record = DefinitionRecord.from_json(raw)
    assert record is not None
    # Some live at the bundle's top level (the approval params), most under `v2/`.
    for name in used_surface(pin).surface_definitions:
        assert name in record.hashes or f"v2/{name}" in record.hashes, name
    assert {"v2/GetAccountParams", "v2/GetAccountRateLimitsResponse", "v2/RateLimitWindow"} <= (
        record.used_definitions
    )


def test_every_used_method_resolves_in_the_pinned_builds_combined_bundle() -> None:
    """Design §4.3: every method the pin lists is in 0.154.0's bundle, and each request answers
    with a definition the record holds — including the optional-params and no-params ones."""
    pin = read_pin()
    raw = definitions_file(pin["tested"][0], TESTED_RUNTIMES)
    assert raw is not None
    record = DefinitionRecord.from_json(raw)
    assert record is not None
    resolution = raw["used_methods"]

    for kind, methods in used_surface(pin).methods.items():
        assert sorted(resolution[kind]) == sorted(methods), kind
    for method, names in [*resolution["client_requests"].items(),
                          *resolution["server_requests"].items()]:
        assert names["response"] in record.used_definitions, method
    assert resolution["client_requests"]["account/logout"] == {
        "params": None, "response": "v2/LogoutAccountResponse",
    }
    assert resolution["client_requests"]["account/rateLimits/read"] == {
        "params": "v2/GetAccountRateLimitsParams", "response": "v2/GetAccountRateLimitsResponse",
    }
    # R6: there is no `ConfigBatchWriteResponse`; the answer is the one `config/value/write` shares.
    assert resolution["client_requests"]["config/batchWrite"] == {
        "params": "v2/ConfigBatchWriteParams", "response": "v2/ConfigWriteResponse",
    }


#: A Codex method as the protocol names one: `thread/start`, `account/rateLimits/read`.
CODEX_METHOD = re.compile(r"^[a-z][A-Za-z]*(?:/[a-z][A-Za-z]*)+$")
#: What RAVIS sends Codex a request through; the first argument is the method even when it has no
#: slash (`initialize`).
SENDERS = frozenset({"request", "_request"})
#: Requests RAVIS sends that Codex 0.154.0's stable bundle lacks. Check 5 looks for a used method
#: in both bundles, so these can't be listed there: the strict-rules surface holds them, and
#: check 7a names one a new build dropped.
EXPERIMENTAL_ONLY_REQUESTS = frozenset(
    {"thread/backgroundTerminals/list", "thread/backgroundTerminals/terminate"}
)


def _callee(call: ast.Call) -> str | None:
    """`request` for `codex.request(…)` and for `request(…)`; None for anything else called."""
    function = call.func
    if isinstance(function, ast.Attribute):
        return function.attr
    return function.id if isinstance(function, ast.Name) else None


def _sends(module: ast.Module) -> Iterator[tuple[str, int]]:
    """(method, line) for each call in a module that sends Codex a request.

    A send is a call whose first argument is a method written out, whatever the call is named, so
    a new wrapper is found too; or a call through `SENDERS` with any string first.
    """
    for node in ast.walk(module):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        if CODEX_METHOD.match(first.value) or _callee(node) in SENDERS:
            yield first.value, node.lineno


def sent_codex_methods() -> dict[str, list[str]]:
    """Each Codex method RAVIS's own code sends, with every place it is sent from.

    Calibration is left out: it runs only on the owner's command, never in RAVIS's service, and
    asks Codex things no task does (`thread/fork`).
    """
    package = Path(ravis.__file__).parent
    sent: dict[str, list[str]] = {}
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package)
        if relative.parts[:2] == ("codex", "calibration"):
            continue
        for method, line in _sends(ast.parse(path.read_text(encoding="utf-8"))):
            sent.setdefault(method, []).append(f"{relative}:{line}")
    return sent


def test_every_codex_request_ravis_sends_is_one_the_pin_lists() -> None:
    """R6: a request the pin doesn't list isn't in check 5, so a Codex build without it would be
    accepted and then break whatever sends it. `config/read` and `config/batchWrite` (the sites)
    and `permissionProfile/list` (check 7b) were sent unlisted until R6."""
    used = used_surface(read_pin())
    listed = set(used.methods["client_requests"])
    surface_only = EXPERIMENTAL_ONLY_REQUESTS & set(used.surface_methods)

    unlisted = [
        f"{method} at {', '.join(places)}"
        for method, places in sorted(sent_codex_methods().items())
        if method not in listed | surface_only
    ]

    assert unlisted == [], (
        "RAVIS sends Codex requests the pin doesn't list: add each to tested_runtimes.json → "
        "used_methods.client_requests and regenerate the definition hashes (build notes, From R6)"
    )


def test_the_scan_finds_every_request_the_pin_lists() -> None:
    """The scan above can't pass by finding nothing: each listed request is found where it is sent.
    One no code sends any more should leave the pin, or the scan learn how it is sent now."""
    used = used_surface(read_pin())
    sent = sent_codex_methods()

    assert sorted(set(used.methods["client_requests"]) - sent.keys()) == []
    assert EXPERIMENTAL_ONLY_REQUESTS - sent.keys() == set()
    assert EXPERIMENTAL_ONLY_REQUESTS - set(used.surface_methods) == set()


def test_the_messages_ravis_reads_from_codex_are_ones_the_pin_lists() -> None:
    """The tables RAVIS dispatches Codex's messages from hold only listed methods, so check 5 covers
    what they read too. `ELICITATION_DECLINED` is RAVIS's own message, never Codex's."""
    methods = used_surface(read_pin()).methods

    assert set(KINDS) - set(methods["server_requests"]) == set()
    assert set(MESSAGE_REASONS) - set(methods["server_notifications"]) == set()
    notifications = set(AgentSession._NOTIFICATIONS) - {ELICITATION_DECLINED}
    assert notifications - set(methods["server_notifications"]) == set()


def test_a_version_check_reports_all_seven_checks_and_what_changed(tmp_path: Path) -> None:
    schema = changed_schema(
        AdditionalPermissionProfile={"title": "AdditionalPermissionProfile", "changed": True},
        AccountReadParams={"title": "AccountReadParams", "changed": True},
    )
    rig = codex_rig(tmp_path, tested=False, extra_entries=(pinned_one(tmp_path),),
                    experimental_schema=schema)

    with TestClient(rig.app) as client:
        reaches(client, "untested_version")
        answer = call(client, rig, "GET", VERSION_CHECK, "admin.launcher")

    fixture = fixture_response("codex-admin.json", "GET", VERSION_CHECK,
                               "a new binary whose strict-rules surface changed")
    report = answer.json()["version_check"]
    assert answer.status_code == 200
    assert set(report) == set(fixture["body"]["version_check"])
    assert [check["name"] for check in report["checks"]] == list(CHECK_NAMES)
    assert all(check["ok"] for check in report["checks"][:6]), report["checks"]
    assert report["checks"][6] == {
        "name": "strict_rules", "ok": False, "detail": "AdditionalPermissionProfile changed",
    }
    assert report["protocol"] == {
        "stable_tree_changed": False,
        "experimental_tree_changed": True,
        "strict_rules_surface_changed": True,
        "used_methods_missing": [],
        "used_definitions_changed": ["AccountReadParams"],
        "other_definitions_changed": 1,
    }
    assert (report["verdict"], report["strict_rules"]) == ("untested", "unproven")


async def test_a_binary_openai_didnt_sign_is_never_run(tmp_path: Path) -> None:
    codex = FakeCodex.install(tmp_path / "bin" / "codex")
    other_team = FakeCodesign.install(tmp_path / "tools" / "codesign", team="ABCDEFGHIJ")
    pin = write_pin(tmp_path / "pin.json")

    report = await check_version(
        codex.path, settings_for(codex), installed_sha256=file_sha256(codex.path),
        codesign=str(other_team.path), pin=pin, profile=None,
    )

    assert report.checks[0].ok is False
    assert all(check.detail == "not run: the signature check failed" for check in report.checks[1:])
    assert codex.runs() == []
    assert not report.acceptable


async def test_a_schema_that_wont_generate_fails_check_four_and_leaves_five_unrun(
    tmp_path: Path,
) -> None:
    server = FakeAppServer.create(tmp_path / "app-server")
    codex = FakeCodex.install(tmp_path / "bin" / "codex", fail_schema=True, app_server=server)
    codesign = FakeCodesign.install(tmp_path / "tools" / "codesign")

    report = await check_version(
        codex.path, settings_for(codex), installed_sha256=file_sha256(codex.path),
        codesign=str(codesign.path), pin=write_pin(tmp_path / "pin.json"), profile=None,
    )

    checks = {check.name: check for check in report.checks}
    assert checks["schema_generation"].ok is False
    assert checks["used_methods_present"].detail == "not run: the schema trees didn't generate"
    assert checks["handshake"].ok is True
    assert not report.acceptable


async def test_a_missing_used_method_fails_check_five_by_name(tmp_path: Path) -> None:
    server = FakeAppServer.create(tmp_path / "app-server")
    schema = {
        **EXPERIMENTAL_SCHEMA,
        COMBINED_BUNDLE: fake_bundle(experimental=True, missing=["turn/steer"]),
    }
    codex = FakeCodex.install(tmp_path / "bin" / "codex", app_server=server,
                              experimental_schema=schema)
    codesign = FakeCodesign.install(tmp_path / "tools" / "codesign")

    report = await check_version(
        codex.path, settings_for(codex), installed_sha256=file_sha256(codex.path),
        codesign=str(codesign.path), pin=write_pin(tmp_path / "pin.json"), profile=None,
    )

    checks = {check.name: check for check in report.checks}
    assert checks["used_methods_present"].ok is False
    assert checks["used_methods_present"].detail == "missing: turn/steer"
    assert report.protocol["used_methods_missing"] == []  # no pinned build to compare against


async def test_a_build_whose_app_server_wont_answer_fails_the_handshake(tmp_path: Path) -> None:
    codex = FakeCodex.install(tmp_path / "bin" / "codex")
    codesign = FakeCodesign.install(tmp_path / "tools" / "codesign")

    report = await check_version(
        codex.path, settings_for(codex), installed_sha256=file_sha256(codex.path),
        codesign=str(codesign.path), pin=write_pin(tmp_path / "pin.json"), profile=None,
    )

    checks = {check.name: check for check in report.checks}
    assert checks["handshake"].ok is False
    assert not report.acceptable


def test_accepting_records_the_build_with_unproven_rules_and_gives_it_a_process(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig = codex_rig(tmp_path, tested=False, scenario={"account": None})

    with TestClient(rig.app) as client:
        installed = reaches(client, "untested_version")["runtime"]["installed_sha256"]
        no_process_before = rig.supervised_starts() == []
        wrong = call(client, rig, "POST", ACCEPT, "admin.launcher", {"sha256": "0" * 64})
        malformed = call(client, rig, "POST", ACCEPT, "admin.launcher", {"sha256": "nope"})
        accepted = call(client, rig, "POST", ACCEPT, "admin.launcher", {"sha256": installed})
        eventually(rig.supervised_starts, what="a process for the accepted build")
        later = state_of(client)

    not_installed = fixture_response("codex-admin.json", "POST", ACCEPT, "not the installed binary")
    refused_as(wrong, not_installed)
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "INVALID_REQUEST_BODY"
    fixture = fixture_response("codex-admin.json", "POST", ACCEPT,
                               "accepted, and still paused until the re-test proves the file rules")
    body = accepted.json()
    assert no_process_before
    assert accepted.status_code == 200
    assert set(body) == set(fixture["body"])
    assert (body["state"], body["runtime"]["verdict"], body["runtime"]["strict_rules"]) == (
        "untested_version", "accepted", "unproven",
    )
    assert "accepted without re-testing" in later["reason"]
    [record] = rig.record()["accepted"]
    assert (record["sha256"], record["strict_rules"], record["accepted_by"]) == (
        installed, "unproven", "launcher",
    )
    assert rig.published("ravis.codex.version_accepted") == [
        {"request_id": rig.published("ravis.codex.version_accepted")[0]["request_id"],
         "application_id": "launcher", "sha256": installed}
    ]
    # RAVIS's log says, once, that it runs an accepted build: by whom, and what changed.
    logged = [
        record.getMessage() for record in caplog.records
        if "accepted without a test" in record.getMessage()
    ]
    assert len(logged) == 1
    assert "by launcher at " in logged[0]
    assert '"used_methods_missing": []' in logged[0]


def test_a_build_failing_a_refusing_check_cannot_be_accepted(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, tested=False, app_server=False)

    with TestClient(rig.app) as client:
        installed = reaches(client, "untested_version")["runtime"]["installed_sha256"]
        answer = call(client, rig, "POST", ACCEPT, "admin.launcher", {"sha256": installed})

    refused_as(answer, fixture_response("codex-admin.json", "POST", ACCEPT,
                                        "a check from 1 to 6 failed"))
    assert rig.record().get("accepted", []) == []


def test_revoking_an_acceptance_pauses_the_build_and_stops_its_process(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, tested=False, scenario={"account": None})

    with TestClient(rig.app) as client:
        installed = reaches(client, "untested_version")["runtime"]["installed_sha256"]
        call(client, rig, "POST", ACCEPT, "admin.launcher", {"sha256": installed})
        eventually(rig.supervised_starts)
        revoked = call(client, rig, "DELETE", f"{ACCEPT}/{installed}", "admin.launcher")
        eventually(lambda: rig.server.records("stdin_closed"), what="the process stopped")
        nothing = call(client, rig, "DELETE", f"{ACCEPT}/{installed}", "admin.launcher")

    assert revoked.status_code == 200
    assert revoked.json()["runtime"]["verdict"] == "untested"
    assert nothing.status_code == 200
    assert rig.record()["accepted"] == []
    assert len(rig.published("ravis.codex.version_acceptance_revoked")) == 1


def test_accepting_a_build_that_is_already_tested_records_nothing(tmp_path: Path) -> None:
    rig = codex_rig(tmp_path, proven=True, scenario={"account": None})

    with TestClient(rig.app) as client:
        installed = reaches(client, "signed_out")["runtime"]["installed_sha256"]
        answer = call(client, rig, "POST", ACCEPT, "admin.launcher", {"sha256": installed})

    assert answer.status_code == 200
    assert json.loads(json.dumps(rig.record())).get("accepted", []) == []
    assert rig.published("ravis.codex.version_accepted") == []
