"""`GET /{provider}/catalogue` — the whole list, for a screen that ticks it.

`read_model_filter` caps what it returns on purpose: `sample` shows an operator
what a pattern *did*, and a truncated list presented as the whole answer is the
exact failure the filter exists to prevent. That cap is right there and wrong
here. A checkbox list that omits models is a model the operator cannot see and
cannot select, which is worse than a long response — so this endpoint enumerates
exhaustively and carries `total` and a cursor on every page to say so.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ravis.app import create_app
from ravis.config import Settings


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def a_client(catalogue: list[str]) -> TestClient:
    client = TestClient(create_app(Settings()))
    client.__enter__()

    class _Registry:
        def model_ids(self) -> list[str]:
            return list(catalogue)

    class _Built:
        registry = _Registry()

    # `create_app` returns the body-size wrapper, so the FastAPI instance —
    # and the state the request path reads — is one level in.
    inner = client.app.app  # type: ignore[attr-defined]
    inner.state.transparents = {"demo": _Built()}
    return client


CATALOGUE = [f"vendor/model-{n:03d}" for n in range(600)]


def test_the_whole_catalogue_is_reachable_by_paging() -> None:
    """Nothing may be unreachable. The screen ticks through what it is given."""
    client = a_client(CATALOGUE)

    seen: list[str] = []
    offset: int | None = 0
    while offset is not None:
        page = client.get(f"/api/v1/providers/demo/catalogue?offset={offset}").json()
        seen.extend(item["id"] for item in page["items"])
        offset = page["next_offset"]

    assert seen == CATALOGUE
    client.__exit__(None, None, None)


def test_a_page_reports_the_total_it_is_a_page_of() -> None:
    client = a_client(CATALOGUE)

    page = client.get("/api/v1/providers/demo/catalogue?limit=10").json()

    assert len(page["items"]) == 10
    assert page["total"] == 600
    assert page["next_offset"] == 10
    client.__exit__(None, None, None)


def test_the_last_page_has_no_next_offset() -> None:
    """Absent rather than equal to `total`, so a client loops on "is there
    another page" instead of on arithmetic it can get wrong."""
    client = a_client(CATALOGUE)

    page = client.get("/api/v1/providers/demo/catalogue?offset=550&limit=250").json()

    assert len(page["items"]) == 50
    assert page["next_offset"] is None
    client.__exit__(None, None, None)


def test_a_page_size_beyond_the_cap_is_clamped_not_refused() -> None:
    client = a_client(CATALOGUE)

    page = client.get("/api/v1/providers/demo/catalogue?limit=99999").json()

    assert page["limit"] == 1000
    client.__exit__(None, None, None)


def test_a_malformed_cursor_restarts_the_listing() -> None:
    """The operator did not type it, a client did. Restarting is recoverable;
    a 422 in the middle of a paging loop is not."""
    client = a_client(CATALOGUE)

    page = client.get("/api/v1/providers/demo/catalogue?offset=banana").json()

    assert page["offset"] == 0
    client.__exit__(None, None, None)


def test_selection_is_computed_from_the_live_filter() -> None:
    """Not echoed back from the page.

    A filter changed in another tab shows up on the next open, instead of being
    silently overwritten by whatever checkboxes this one was holding.
    """
    client = a_client(["a/one", "a/two", "b/three"])

    client.put(
        "/api/v1/providers/demo/models", json={"include": ["a/*"], "exclude": []}
    )
    page = client.get("/api/v1/providers/demo/catalogue").json()

    assert {i["id"]: i["selected"] for i in page["items"]} == {
        "a/one": True, "a/two": True, "b/three": False,
    }
    assert page["filtered"] is True
    client.__exit__(None, None, None)


def test_an_unfiltered_provider_has_everything_selected() -> None:
    """Empty filter means all, and the screen must open with every box ticked
    rather than none — the two look identical in a filter and opposite here."""
    client = a_client(["a/one", "b/two"])

    page = client.get("/api/v1/providers/demo/catalogue").json()

    assert all(i["selected"] for i in page["items"])
    assert page["filtered"] is False
    client.__exit__(None, None, None)


# ── Saving a key is usually why the catalogue was empty ────────────────────


def test_saving_a_credential_re_reads_the_catalogue() -> None:
    """The credential took effect on the next request; the model list did not.

    So a provider keyed a moment ago reported no models, and a request to one of
    them came back `no_route` — which reads as "that model does not exist"
    rather than "I have not looked since you gave me the key". A five-minute
    timer eventually fixed it, which is the worst duration for a bug: long
    enough to be reported, short enough to have healed before anyone looks.
    """
    calls: list[int] = []
    catalogue: list[str] = []

    class _Registry:
        async def refresh(self) -> None:
            calls.append(1)
            # What a real one does once a key exists: the fetch that returned
            # nothing now returns the catalogue.
            catalogue[:] = ["demo/one", "demo/two"]

        def model_ids(self) -> list[str]:
            return list(catalogue)

    class _Spec:
        kind = "demo"

    class _Built:
        registry = _Registry()
        spec = _Spec()

    client = TestClient(create_app(Settings()))
    client.__enter__()
    client.app.app.state.transparents = {"demo": _Built()}  # type: ignore[attr-defined]

    assert client.get("/api/v1/providers/demo/catalogue").json()["total"] == 0

    saved = client.put(
        "/api/v1/providers/credentials/demo", json={"secret": "sk-something"}
    ).json()

    assert calls == [1]
    assert saved["catalogue_total"] == 2
    assert client.get("/api/v1/providers/demo/catalogue").json()["total"] == 2
    client.__exit__(None, None, None)


def test_a_credential_for_a_provider_with_no_upstream_reports_no_catalogue() -> None:
    """`None`, not zero. Nothing was refreshed because nothing is declared, and
    "no catalogue to read" is a different fact from "the catalogue is empty"."""
    client = TestClient(create_app(Settings()))
    client.__enter__()
    client.app.app.state.transparents = {}  # type: ignore[attr-defined]

    saved = client.put(
        "/api/v1/providers/credentials/nowhere", json={"secret": "sk-x"}
    ).json()

    assert saved["catalogue_total"] is None
    client.__exit__(None, None, None)
