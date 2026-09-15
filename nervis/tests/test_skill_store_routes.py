"""NERVIS → Skills' skill store control routes (NERVIS 0.33.0, with RAVIS 0.28.0).

The page installs, updates and removes skills, and browses the marketplace, through twelve control
routes, each forwarding to RAVIS's skill store with NERVIS's RAVIS admin credential
(`ravis/tests/fixtures/skill-store/contract.json` → `nervis_control_routes`, read here, never
copied). What can go wrong quietly at that hop, each held here:

- a route the contract names not served, or a skill store route served that it doesn't name;
- any of them reached without the page's control token, or the credential spent with none held;
- a field a route doesn't carry reaching RAVIS, a field the page left out arriving as `null`, or
  anything the page sends choosing where the call goes;
- RAVIS's answer changed on the way — its refusal flattened, the answer cached or carrying the
  credential;
- a zip file arriving as anything but the page's bytes, or one over 8 MB reaching RAVIS at all;
- the page's reads of installs and of the marketplace not going through the GET relay.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.test_ravis_codex_card import ADMIN, REPOSITORY, call, served_routes, settings_for

from nervis.app import create_app

STORE = json.loads((REPOSITORY / "ravis" / "tests" / "fixtures" / "skill-store" / "contract.json")
                   .read_text(encoding="utf-8"))
ROUTES = STORE["nervis_control_routes"]
ZIP = "/api/v1/ravis/skills/previews/zip"
JSON_ROUTES = [route for route in ROUTES if route["path"] != ZIP]


class StoreRavis:
    """Answers every call to RAVIS's skill store with one status and body, and remembers them."""

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status, self.body = status, body if body is not None else {}
        self.seen: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v1/skills"):
            self.seen.append(request)
        return httpx.Response(self.status, json=self.body)


def behind(client: TestClient, ravis: StoreRavis) -> StoreRavis:
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(ravis.handle)
    )
    return ravis


def control(client: TestClient) -> dict[str, str]:
    return {"x-nervis-control": str(client.app.state.control_token)}  # type: ignore[attr-defined]


def example(method: str, path: str, name: str) -> dict[str, Any]:
    route = next(route for route in STORE["routes"]
                 if (route["method"], route["path"]) == (method, path))
    return next(case for case in route["examples"] if case["name"] == name)["response"]  # type: ignore[no-any-return]


@pytest.fixture()
def nervis(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(settings_for(tmp_path))) as client:
        yield client


def test_every_route_the_contract_names_is_served_and_no_other(nervis: TestClient) -> None:
    served = {(method, path) for method, path in served_routes(nervis)
              if path.startswith("/api/v1/ravis/skills/")}

    assert served == {(route["method"], route["path"]) for route in ROUTES}


@pytest.mark.parametrize("route", JSON_ROUTES, ids=lambda route: route["path"])
def test_each_route_sends_only_its_fields_to_its_own_ravis_path_with_the_admin_credential(
    nervis: TestClient, route: dict[str, Any],
) -> None:
    ravis = behind(nervis, StoreRavis(200, {"answered": True}))
    carried = {field: f"the page's {field}" for field in route["carries"]}
    page = {"address": "http://127.0.0.1:1/elsewhere", "authorization": "Bearer another",
            "credential": "stolen", **carried}

    answer = call(nervis, "POST", route["path"], page)

    [sent] = ravis.seen
    method, path = route["forwards"].split(" ", 1)
    assert (answer.status_code, answer.json()) == (200, {"answered": True})
    assert answer.headers["cache-control"] == "no-store" and ADMIN not in answer.text
    assert (sent.method, sent.url.path, sent.url.query) == (method, path, b"")
    assert sent.headers["authorization"] == f"Bearer {ADMIN}"
    assert json.loads(sent.content) == carried


@pytest.mark.parametrize("route", JSON_ROUTES, ids=lambda route: route["path"])
def test_a_field_the_page_left_out_stays_out(nervis: TestClient, route: dict[str, Any]) -> None:
    ravis = behind(nervis, StoreRavis())

    call(nervis, "POST", route["path"], {})

    assert json.loads(ravis.seen[0].content) == {}


@pytest.mark.parametrize("route", ROUTES, ids=lambda route: route["path"])
def test_no_route_reaches_ravis_without_the_pages_control_token(nervis: TestClient,
                                                               route: dict[str, Any]) -> None:
    ravis = behind(nervis, StoreRavis())

    refused = nervis.post(route["path"], json={}, headers={"x-nervis-control": "a guess"})
    missing = nervis.post(route["path"], json={})

    assert (refused.status_code, missing.status_code) == (403, 403) and ravis.seen == []


@pytest.mark.parametrize("path, name", [
    ("/api/v1/skills/previews", "GitHub is rate-limiting"),
    ("/api/v1/skills/previews", "a name that isn't its folder's"),
    ("/api/v1/skills/installs", "a name already in the folder"),
    ("/api/v1/skills/installs/remove", "put there by hand"),
])
def test_ravis_s_refusals_reach_the_page_as_ravis_gave_them(nervis: TestClient, path: str,
                                                           name: str) -> None:
    refused = example("POST", path, name)
    behind(nervis, StoreRavis(refused["status"], refused["body"]))

    answer = call(nervis, "POST", "/api/v1/ravis" + path.removeprefix("/api/v1"), {})

    assert (answer.status_code, answer.json()) == (refused["status"], refused["body"])


def test_without_a_credential_nothing_reaches_ravis(tmp_path: Path) -> None:
    with TestClient(create_app(settings_for(tmp_path, credential=""))) as client:
        ravis = behind(client, StoreRavis())
        answers = [call(client, "POST", route["path"], {}) for route in JSON_ROUTES]
        answers.append(client.post(ZIP, content=b"PK\x03\x04", headers=control(client)))

    assert {answer.status_code for answer in answers} == {403} and ravis.seen == []


def test_a_zip_file_reaches_ravis_as_the_pages_own_bytes(nervis: TestClient) -> None:
    preview = example("POST", "/api/v1/skills/previews", "a GitHub folder")["body"]
    ravis = behind(nervis, StoreRavis(200, preview))
    data = b"PK\x03\x04" + bytes(range(256)) * 40

    answer = nervis.post(ZIP, content=data, headers={**control(nervis),
                                                     "content-type": "application/zip"})

    [sent] = ravis.seen
    assert (answer.status_code, answer.json()) == (200, preview)
    assert (sent.method, sent.url.path) == ("POST", "/api/v1/skills/previews/zip")
    assert sent.content == data
    assert sent.headers["content-type"] == "application/zip"
    assert sent.headers["authorization"] == f"Bearer {ADMIN}"


def test_a_zip_over_eight_mb_never_reaches_ravis(nervis: TestClient) -> None:
    ravis = behind(nervis, StoreRavis())

    answer = nervis.post(ZIP, content=b"\0" * (8 * 1024 * 1024 + 1), headers=control(nervis))

    assert answer.status_code == 413 and "8 MB" in answer.text and ravis.seen == []


def test_installs_and_the_marketplace_are_read_through_the_relay_not_a_control_route(
    nervis: TestClient,
) -> None:
    served = served_routes(nervis)

    assert ("GET", "/api/v1/relay/ravis/{path:path}") in served
    assert not [route for route in served if route[0] == "GET"
                and route[1].startswith("/api/v1/ravis/skills")]
