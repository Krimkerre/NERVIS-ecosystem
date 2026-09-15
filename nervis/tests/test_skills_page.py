"""NERVIS → Skills' control route, and how the page reaches RAVIS's skills (NERVIS 0.32.0).

The owner's decisions of 15 September 2026 gave skills a page of their own, with one switch per
skill for Codex and one for the other models (Clarvis's own engine and NERVIS chat). The page reads
`GET /api/v1/skills` through the GET relay and switches through one control route,
`POST /api/v1/ravis/skills`, which checks the page's control token and forwards to RAVIS's
`POST /api/v1/skills` with NERVIS's RAVIS admin credential. What can go wrong quietly at that hop,
each replayed from RAVIS's own contract (`skills.json`):

- anything but the skill's path, the engine and the switch reaching RAVIS;
- RAVIS's refusal flattened, or the answer cached or carrying the credential;
- the credential spent with none configured;
- NERVIS serving a skills control route RAVIS's contract doesn't list, or still the Codex-only one
  NERVIS 0.30.0 added;
- a read of the skills going through a control route rather than the relay.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.test_ravis_codex_card import ADMIN, CONTRACT, call, served_routes, settings_for

from nervis.app import create_app
from nervis.peers import ravis as ravis_peer

#: RAVIS's contract for skills (RAVIS 0.27.0), owned by RAVIS and read here rather than copied.
SKILLS = json.loads((CONTRACT / "skills.json").read_text(encoding="utf-8"))
SWITCH = "/api/v1/ravis/skills"


class SkillsRavis:
    """Answers every call to RAVIS's skills routes with one status and body, and remembers them."""

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status, self.body = status, body if body is not None else {}
        self.seen: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith(("/api/v1/skills", "/api/v1/codex")):
            self.seen.append(request)
        return httpx.Response(self.status, json=self.body)


def behind(client: TestClient, ravis: SkillsRavis) -> SkillsRavis:
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(ravis.handle)
    )
    return ravis


def switch_example(name: str) -> dict[str, Any]:
    route = next(route for route in SKILLS["routes"]
                 if (route["method"], route["path"]) == ("POST", "/api/v1/skills"))
    return next(example for example in route["examples"] if example["name"] == name)  # type: ignore[no-any-return]


def refusals() -> list[tuple[str, Any, int, Any]]:
    """Every refusal the contract gives for the switch: name, page body, status, RAVIS's body."""
    route = next(route for route in SKILLS["routes"]
                 if (route["method"], route["path"]) == ("POST", "/api/v1/skills"))
    return [(example["name"], example["request"]["body"], example["response"]["status"],
             example["response"]["body"])
            for example in route["examples"] if example["response"]["status"] >= 400]


@pytest.fixture()
def nervis(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(settings_for(tmp_path))) as client:
        yield client


def test_the_contract_still_has_what_this_file_replays() -> None:
    codes = {body["error"]["code"] for *_, body in refusals()}
    assert {"SKILL_NOT_FOUND", "SKILL_NOT_CHANGED", "CODEX_RUNTIME_UNAVAILABLE",
            "INVALID_REQUEST_BODY", "FORBIDDEN"} <= codes
    assert switch_example("a personal skill switched on for the other models")["response"][
        "status"] == 200


def test_switching_a_skill_forwards_only_its_path_the_engine_and_the_switch(
    nervis: TestClient,
) -> None:
    example = switch_example("a personal skill switched on for the other models")
    status, board = example["response"]["status"], example["response"]["body"]
    ravis = behind(nervis, SkillsRavis(status, board))

    page = {**example["request"]["body"], "source": "built_in", "id": "nervis/other",
            "folder": "/elsewhere"}
    answered = call(nervis, "POST", SWITCH, page)

    assert (answered.status_code, answered.json()) == (status, board)
    [sent] = ravis.seen
    assert (sent.method, sent.url.path) == ("POST", "/api/v1/skills")
    assert json.loads(sent.content) == example["request"]["body"]
    assert set(json.loads(sent.content)) == {"path", "engine", "enabled"}
    assert sent.headers["authorization"] == f"Bearer {ADMIN}"


def test_a_body_that_isnt_a_switch_is_ravis_s_to_word(nervis: TestClient) -> None:
    example = switch_example("not a switch")
    status, refusal = example["response"]["status"], example["response"]["body"]
    ravis = behind(nervis, SkillsRavis(status, refusal))

    answered = call(nervis, "POST", SWITCH, example["request"]["body"])

    assert (answered.status_code, answered.json()) == (status, refusal)
    [sent] = ravis.seen
    assert json.loads(sent.content) == {**example["request"]["body"], "engine": None}


@pytest.mark.parametrize(("name", "body", "status", "refusal"), refusals(),
                         ids=[name for name, *_ in refusals()])
def test_ravis_s_refusal_reaches_the_page_as_ravis_said_it(
    nervis: TestClient, name: str, body: Any, status: int, refusal: Any,
) -> None:
    behind(nervis, SkillsRavis(status, refusal))

    answered = call(nervis, "POST", SWITCH, body)

    assert (answered.status_code, answered.json()) == (status, refusal), name


def test_no_answer_is_cached_or_carries_the_credential(nervis: TestClient) -> None:
    body = switch_example("a personal skill switched on for the other models")["request"]["body"]
    behind(nervis, SkillsRavis(200, {"skills": []}))

    answered = call(nervis, "POST", SWITCH, body)

    assert answered.headers["cache-control"] == "no-store"
    assert ADMIN not in answered.text
    assert all(ADMIN not in value for value in answered.headers.values())


def test_without_an_admin_credential_nothing_is_sent_and_the_page_is_told_why(
    tmp_path: Path,
) -> None:
    body = switch_example("a personal skill switched on for the other models")["request"]["body"]
    with TestClient(create_app(settings_for(tmp_path, credential=""))) as client:
        ravis = behind(client, SkillsRavis(200, {}))
        answered = call(client, "POST", SWITCH, body)

    assert answered.status_code == 403
    assert "admin credential" in answered.json()["message"]
    assert ravis.seen == []


def test_nervis_serves_exactly_the_skills_control_route_ravis_lists(nervis: TestClient) -> None:
    """`skills.json` → `nervis_control_routes`; and the Codex-only switch NERVIS 0.30.0 added is
    gone, since the Skills page switches Codex through the same route."""
    listed = {tuple(entry["nervis"].split(" ", 1))
              for entry in SKILLS["nervis_control_routes"]["routes"]}
    served = {route for route in served_routes(nervis)
              if route[1].startswith("/api/v1/ravis") and "skills" in route[1]
              # The skill store's routes (NERVIS 0.33.0) are its own contract's, held in
              # `test_skill_store_routes.py`; this one is `skills.json`'s alone.
              and not route[1].startswith("/api/v1/ravis/skills/")}

    assert served == listed == {("POST", SWITCH)}


def test_the_skills_are_read_through_the_relay_and_no_control_route(nervis: TestClient) -> None:
    assert ravis_peer.relayable("api/v1/skills")
    assert not any(method == "GET" and "skills" in path and path.startswith("/api/v1/ravis")
                   for method, path in served_routes(nervis))
