"""The skill store's fixture, `fixtures/skill-store/contract.json`, held to the code (RAVIS 0.28.0).

NERVIS's Skills page is built against this fixture, so it has to say what RAVIS does:

- **every route it names is served, and every route served is named**, each forwarded by one of
  NERVIS's control routes when it writes;
- **every code the store raises is catalogued at its status**, and **every refusal reason is
  listed, none stale**;
- **every error example is the MEP envelope** with a catalogued code, retryable exactly at 503;
- **each example answer has the real answer's shape**, from a flow against the fake GitHub and the
  recorded skills.sh search.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from tests.agent_rig import keys, serving
from tests.skill_store_rig import ADMIN, body_of, skill_md, store_rig

from ravis.api.management import skill_store

SOURCE = Path(__file__).resolve().parents[1] / "src" / "ravis" / "agent"
FIXTURES = Path(__file__).parent / "fixtures" / "skill-store"
CONTRACT = json.loads((FIXTURES / "contract.json").read_text())
ENVELOPE = {"code", "message", "retryable", "details", "request_id", "trace_id"}


def example(method: str, path: str, name: str) -> dict[str, Any]:
    route = next(route for route in CONTRACT["routes"]
                 if (route["method"], route["path"]) == (method, path))
    return next(case for case in route["examples"] if case["name"] == name)  # type: ignore[no-any-return]


def test_every_route_named_is_served_and_every_route_served_is_named() -> None:
    served = {(method, route.path) for route in skill_store.router.routes  # type: ignore[attr-defined]
              for method in route.methods}  # type: ignore[attr-defined]
    named = {(route["method"], route["path"]) for route in CONTRACT["routes"]}
    forwarded = {route["forwards"] for route in CONTRACT["nervis_control_routes"]}

    assert served == named
    assert forwarded == {f"{method} {path}" for method, path in named if method == "POST"}


def test_every_code_the_store_raises_is_catalogued_at_its_status() -> None:
    raised = set(re.findall(r'SkillStoreRefusalError\(\s*"([A-Z_]+)",\s*(\d+)',
                            (SOURCE / "store_refusals.py").read_text()))
    catalogue = CONTRACT["error_codes"]

    assert raised and all(int(status) in catalogue[code]["status"] for code, status in raised)
    # FORBIDDEN is the guards' own (`require_admin`, `require_board_reader`).
    assert {code for code, _ in raised} | {"FORBIDDEN"} == set(catalogue)


def test_every_refusal_reason_is_listed_and_none_is_stale() -> None:
    package = "".join((SOURCE / name).read_text()
                      for name in ("skill_package.py", "skill_github.py", "skill_sites.py"))
    web = (SOURCE / "skill_web.py").read_text()
    source_reasons = set(CONTRACT["error_codes"]["SKILL_SOURCE_REFUSED"]["reasons"])

    assert set(re.findall(r'PackageRefusedError\(\s*"([a-z_]+)"', package)) == set(
        CONTRACT["error_codes"]["SKILL_REFUSED"]["reasons"])
    assert set(re.findall(r'RefusedError\(\s*"([a-z_]+)"', web)) <= source_reasons
    assert all(f'"{reason}"' in web for reason in source_reasons - {"not_a_github_link"})
    assert 'reason="not_a_github_link"' in (SOURCE / "store_refusals.py").read_text()


def test_every_error_example_is_the_envelope_with_a_catalogued_code() -> None:
    errors = [case["response"] for route in CONTRACT["routes"] for case in route["examples"]
              if case["response"]["status"] >= 400]

    assert errors
    for response in errors:
        error, status = response["body"]["error"], response["status"]
        entry = CONTRACT["error_codes"][error["code"]]
        assert set(error) == ENVELOPE and status in entry["status"]
        assert error["retryable"] is (status == 503) and entry["retryable"] is (status == 503)
        assert "reasons" not in entry or error["details"]["reason"] in entry["reasons"]


PDF = {
    "skills/pdf/SKILL.md": skill_md("pdf", extra="license: Proprietary. LICENSE.txt has complete "
                                                 "terms\n"),
    "skills/pdf/scripts/extract.py": b"print('pdf')\n",
    "skills/pdf/LICENSE.txt": b"Proprietary\n",
}


def test_each_example_answer_has_the_real_answers_shape(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    store = store_rig(tmp_path, monkeypatch)
    store.github.repository("anthropics/skills", PDF)
    recorded = json.loads((FIXTURES / "skills-sh-search.json").read_text())["answer"]
    store.internet.page("https://skills.sh/api/search?q=pdf&limit=20", recorded)
    live: dict[tuple[str, str, str], Any] = {}

    def post(path: str, body: dict[str, Any], name: str) -> Any:
        live[("POST", path, name)] = answer = body_of(
            relay.call("POST", path, caller=ADMIN, body=body))
        return answer

    with serving(store.rig) as relay:
        preview = post("/api/v1/skills/previews", {
            "origin": "github", "url": "https://github.com/anthropics/skills/tree/main/skills/pdf"},
            "a GitHub folder")
        post("/api/v1/skills/installs", {"preview_id": preview["preview_id"]}, "installed")
        live[("GET", "/api/v1/skills/installs", "one skill installed from GitHub")] = body_of(
            relay.call("GET", "/api/v1/skills/installs", caller=ADMIN))
        post("/api/v1/skills/installs/update-preview", {"name": "pdf"}, "nothing newer")
        store.github.push("anthropics/skills", {
            **PDF, "skills/pdf/SKILL.md": skill_md("pdf", body="# New steps\n")})
        update = post("/api/v1/skills/installs/update-preview", {"name": "pdf"},
                      "SKILL.md changed")
        post("/api/v1/skills/installs", {"preview_id": update["preview_id"]}, "updated")
        post("/api/v1/skills/market/refresh", {"source": "anthropics"}, "unused")
        live[("GET", "/api/v1/skills/market", "after a refresh")] = body_of(
            relay.call("GET", "/api/v1/skills/market", caller=ADMIN))
        post("/api/v1/skills/market/search", {"query": "pdf"}, "results")
        post("/api/v1/skills/previews/discard", {"preview_id": "sp_gone"}, "closed")
        post("/api/v1/skills/installs/remove", {"name": "pdf"}, "to the Trash")

    shown = {name: answer for name, answer in live.items() if name[2] != "unused"}
    assert len(shown) == 10
    for (method, path, name), answer in shown.items():
        assert keys(answer) == keys(example(method, path, name)["response"]["body"]), name
