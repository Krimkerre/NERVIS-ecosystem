"""§4.3's error shape, which the service shipped wrong and now does not.

A wire shape is a promise to other people's software. SIRVIS published
FastAPI's default `{"detail": "..."}` while the specification says every failure
carries a code from a closed list plus correlation IDs — so a consumer written
against the document would not find the field it was told to branch on.

These tests exist because the cost of changing a wire shape rises with every
consumer, and right now there are none.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.conftest_lmstudio import transport

from sirvis.app import create_app
from sirvis.config import Settings
from sirvis.errors import ResourceBusyError, SirvisError
from sirvis.runtimes import LMStudioAdapter


def _client() -> TestClient:
    settings = Settings(database_path=":memory:", lmstudio_base_url="http://127.0.0.1:9",
                        _env_file=None)  # type: ignore[call-arg]
    app = create_app(
        settings,
        runtime=LMStudioAdapter(
            "http://runtime.invalid", client=httpx.AsyncClient(transport=transport())
        ),
    )
    return TestClient(app)


def test_a_failure_carries_the_shape_the_specification_publishes() -> None:
    """§4.3, field for field. A consumer branches on `code`, not on prose."""
    response = _client().get("/api/v1/models", params={"runtime_key": "nope"})

    body = response.json()

    assert response.status_code == 404
    # `retryable` joined the set at §16 item 10 — §4.5 always listed it and this
    # envelope always omitted it, so a client could not tell "wait and try
    # again" from "this will never work" without reading the status itself.
    assert set(body["error"]) == {
        "code", "message", "retryable", "details", "request_id", "trace_id",
    }
    assert body["error"]["code"] == "MODEL_NOT_FOUND"


def test_the_correlation_id_is_attached_without_the_raiser_doing_it() -> None:
    """A failure nobody can find in the log is the one reported as "it broke".

    Attached at the single translation point so no code path can forget it.
    """
    client = _client()

    body = client.get(
        "/api/v1/models", params={"runtime_key": "nope"},
        headers={"x-request-id": "trace-me"},
    ).json()

    assert body["error"]["request_id"] == "trace-me"


def test_details_carry_facts_a_caller_can_act_on() -> None:
    """Not prose to re-parse: the key that failed, so a retry can be built."""
    body = _client().get("/api/v1/models", params={"runtime_key": "nope"}).json()

    assert body["error"]["details"] == {"runtime_key": "nope"}


def test_every_published_code_comes_from_the_closed_list() -> None:
    """§4.3 names the codes. One invented outside that list is a contract
    change nobody agreed to, and the point of a closed list is that a consumer
    can enumerate what it must handle."""
    published = {
        "MODEL_NOT_FOUND", "MODEL_NOT_INSTALLED", "RUNTIME_UNAVAILABLE",
        "INSUFFICIENT_MEMORY", "RESOURCE_BUSY", "INVALID_CONFIGURATION",
        "UNSUPPORTED_PARAMETER", "LOAD_FAILED", "BENCHMARK_NOT_FOUND", "TIMEOUT",
        "DOWNLOAD_FAILED",
        # M11's, added to §4.3 when the model browser and downloads shipped.
        "CATALOG_UNAVAILABLE", "DISK_SPACE", "DOWNLOAD_NOT_FOUND",
        # §8's Reveal and Delete, added to §4.3 when they shipped (SIRVIS 0.19.7).
        "MODEL_FILES_REFUSED",
        # MEP codes, which §4.3 admits alongside its own.
        "UNAUTHENTICATED", "FORBIDDEN", "UNSUPPORTED_MEDIA_TYPE", "INTERNAL_ERROR",
    }

    defined = {cls.code for cls in _descendants(SirvisError)} | {SirvisError.code}

    assert defined <= published, f"undeclared codes: {sorted(defined - published)}"


def _descendants(cls: type) -> set[type[SirvisError]]:
    found: set[type[SirvisError]] = set()
    for child in cls.__subclasses__():
        found.add(child)
        found |= _descendants(child)
    return found


def test_details_never_carry_a_credential() -> None:
    """The dict is serialised straight onto the wire, so what goes in matters."""
    error = ResourceBusyError("at capacity", held_by=["session-1"])

    assert "token" not in repr(error.details)
    assert error.details == {"held_by": ["session-1"]}


@pytest.mark.parametrize(
    ("path", "expected"),
    [("/api/v1/models?runtime_key=nope", 404), ("/api/v1/tokens", 401)],
)
def test_the_status_matches_the_code(path: str, expected: int) -> None:
    """A code and a status that disagree make a client choose which to believe."""
    assert _client().get(path).status_code == expected
