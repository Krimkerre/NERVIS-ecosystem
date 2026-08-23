"""§4.4 admission control — one negative test per limit, as the gate requires."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.conftest_upstream import RecordingUpstream

from ravis.admission import RateLimiter, check_origin, client_address
from ravis.app import create_app
from ravis.config import Settings
from ravis.content import check_image_count, check_no_remote_urls
from ravis.errors import (
    OriginRejectedError,
    RateLimitedError,
    RemoteUrlRefusedError,
    TooManyImagesError,
)


def _app_with_origins(
    origins: list[str], **overrides: object
) -> tuple[TestClient, Settings]:
    """The real app with a browser allowlist, and a fake upstream underneath.

    Only the transport is swapped, so the middleware nesting §4.4 depends on is
    the nesting under test rather than a rearranged one.
    """
    settings = Settings(
        database_path=":memory:",
        upstream_base_url="http://upstream.invalid",
        allowed_origins=origins,
        _env_file=None,  # type: ignore[call-arg]
        **overrides,  # type: ignore[arg-type]
    )
    app = create_app(settings)
    fake_client = httpx.AsyncClient(transport=RecordingUpstream().transport())
    app.app.state.upstream_client = fake_client
    app.app.state.model_registry.use_client(fake_client)
    return TestClient(app), settings


def _image_message(url: str) -> dict:
    """One user message carrying a single image part."""
    return {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}


def test_more_images_than_the_limit_are_refused(settings: Settings) -> None:
    too_many = settings.max_images_per_request + 1
    messages = [_image_message("data:image/png;base64,AAAA")] * too_many

    with pytest.raises(TooManyImagesError):
        check_image_count(messages, settings)


def test_images_at_the_limit_are_accepted(settings: Settings) -> None:
    messages = [_image_message("data:image/png;base64,AAAA")] * settings.max_images_per_request

    check_image_count(messages, settings)  # does not raise


def test_remote_image_urls_are_refused() -> None:
    """RAVIS does not fetch on a caller's behalf — refusal is the feature."""
    with pytest.raises(RemoteUrlRefusedError):
        check_no_remote_urls([_image_message("https://example.invalid/cat.png")])


def test_inline_data_images_are_accepted() -> None:
    """The supported form for binary input carries the same image, no SSRF."""
    check_no_remote_urls([_image_message("data:image/png;base64,AAAA")])


def test_disallowed_origin_is_rejected(settings: Settings) -> None:
    with pytest.raises(OriginRejectedError):
        check_origin({"origin": "https://evil.invalid"}, "POST", settings)


def test_absent_origin_is_allowed(settings: Settings) -> None:
    """No Origin header means no browser sent it — SDKs and CLIs never set one."""
    check_origin({}, "POST", settings)


def test_rate_limit_refuses_past_the_allowance() -> None:
    limiter = RateLimiter()
    for _ in range(3):
        limiter.check("app:127.0.0.1", limit_per_minute=3, now=100.0)

    with pytest.raises(RateLimitedError):
        limiter.check("app:127.0.0.1", limit_per_minute=3, now=100.0)


def test_rate_limit_window_slides() -> None:
    """A caller refused a minute ago is not refused forever."""
    limiter = RateLimiter()
    limiter.check("app:127.0.0.1", limit_per_minute=1, now=100.0)

    limiter.check("app:127.0.0.1", limit_per_minute=1, now=200.0)  # does not raise


def test_forwarded_address_is_ignored_from_an_untrusted_peer(settings: Settings) -> None:
    """Otherwise a caller picks its own rate-limit bucket by setting a header."""
    address = client_address({"x-forwarded-for": "10.0.0.9"}, peer="203.0.113.5", settings=settings)

    assert address == "203.0.113.5"


def test_forwarded_address_is_honoured_from_a_trusted_proxy() -> None:
    settings = Settings(  # type: ignore[call-arg]
        trusted_proxies=["203.0.113.5"], database_path=":memory:", _env_file=None
    )

    address = client_address({"x-forwarded-for": "10.0.0.9"}, peer="203.0.113.5", settings=settings)

    assert address == "10.0.0.9"


def test_a_preflight_from_an_allow_listed_origin_is_answered_without_charge() -> None:
    """A preflight does no work, so it must not consume a rate-limit allowance.

    Charging it would halve every browser client's effective allowance: each
    real request would cost two. Asserted by preflighting past the limit and
    then checking a real request still gets through.
    """
    client, _ = _app_with_origins(["http://127.0.0.1:8080"], rate_limit_per_minute=3)
    with client:
        for _ in range(5):
            preflight = client.options(
                "/api/v1/pools",
                headers={
                    "origin": "http://127.0.0.1:8080",
                    "access-control-request-method": "GET",
                },
            )
            assert preflight.status_code == 204

        real = client.get("/api/v1/pools", headers={"origin": "http://127.0.0.1:8080"})

    assert real.status_code == 200


def test_an_allow_listed_origin_gets_headers_a_browser_will_accept() -> None:
    """Without these the request succeeds and the browser throws the answer away."""
    client, _ = _app_with_origins(["http://127.0.0.1:8080"])
    with client:
        response = client.get("/api/v1/pools", headers={"origin": "http://127.0.0.1:8080"})

    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:8080"
    assert response.headers["vary"] == "Origin"
    # Never a wildcard, and never with credentials: the two together are what
    # would let a hostile page make authenticated reads.
    assert "*" not in response.headers["access-control-allow-origin"]
    assert "access-control-allow-credentials" not in response.headers


def test_an_unlisted_origin_is_refused_and_told_nothing() -> None:
    """An origin nobody listed should not learn what would have been permitted."""
    client, _ = _app_with_origins(["http://127.0.0.1:8080"])
    with client:
        preflight = client.options(
            "/api/v1/pools",
            headers={"origin": "http://evil.example", "access-control-request-method": "GET"},
        )
        read = client.get("/api/v1/pools", headers={"origin": "http://evil.example"})

    assert preflight.status_code == 403
    assert "access-control-allow-origin" not in preflight.headers
    assert read.status_code == 403


def test_a_non_browser_caller_is_unaffected() -> None:
    """No Origin header means no browser, and the ordinary caller sends none."""
    client, _ = _app_with_origins([])
    with client:
        response = client.get("/api/v1/pools")

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
