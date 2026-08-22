"""§4.4 admission control — one negative test per limit, as the gate requires."""

from __future__ import annotations

import pytest

from ravis.admission import RateLimiter, check_origin, client_address
from ravis.config import Settings
from ravis.content import check_image_count, check_no_remote_urls
from ravis.errors import (
    OriginRejectedError,
    RateLimitedError,
    RemoteUrlRefusedError,
    TooManyImagesError,
)


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
