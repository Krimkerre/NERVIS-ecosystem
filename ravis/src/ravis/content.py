"""Limits on what a request body may contain (RAVIS.md §4.4).

Separate from `admission.py` because these need a parsed body, while everything
there runs before parsing. They are pure functions over an already-decoded
payload: no I/O, no framework, trivially testable, and callable from whichever
endpoint eventually accepts chat completions.
"""

from __future__ import annotations

from typing import Any

from ravis.config import Settings
from ravis.errors import RemoteUrlRefusedError, TooManyImagesError

# Schemes RAVIS will not dereference on a caller's behalf. `data:` is the
# supported form for inline binary input, so it is absent from this set.
REFUSED_URL_SCHEMES = ("http://", "https://")


def _iter_image_parts(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every image part across every message, flattened.

    OpenAI's content format is either a plain string or a list of typed parts, so
    a message may hold zero, one or many images. Flattening first keeps the two
    checks below from each re-walking the same nested structure.
    """
    parts: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        parts.extend(
            part for part in content
            if isinstance(part, dict) and part.get("type") == "image_url"
        )
    return parts


def check_image_count(messages: list[dict[str, Any]], settings: Settings) -> None:
    """Refuse a request carrying more inline images than allowed.

    This is not covered by the body-size cap: a thousand small images sit
    comfortably under any byte ceiling and still overwhelm a vision backend,
    which is why §4.4 states the two limits separately.
    """
    count = len(_iter_image_parts(messages))
    if count > settings.max_images_per_request:
        raise TooManyImagesError(
            "Too many images in one request",
            images=count,
            maximum=settings.max_images_per_request,
        )


def check_no_remote_urls(messages: list[dict[str, Any]]) -> None:
    """Refuse image parts that ask RAVIS to fetch a URL.

    Refusal is the feature (§4.4). A gateway that dereferences caller-supplied
    URLs is an SSRF proxy wearing RAVIS's network position: it can be aimed at a
    cloud metadata endpoint, a neighbouring container, or a loopback admin port
    that trusts local traffic. Inline `data:` payloads carry the same images with
    none of that, so nothing is lost by refusing.

    Note this applies to the *translated* path, where RAVIS builds the upstream
    request itself. On the transparent path the field is forwarded untouched and
    the upstream owns its own policy — that asymmetry is intentional and is why
    this is a separate function rather than a middleware.
    """
    for part in _iter_image_parts(messages):
        url = part.get("image_url", {})
        location = url.get("url", "") if isinstance(url, dict) else ""
        if location.lower().startswith(REFUSED_URL_SCHEMES):
            raise RemoteUrlRefusedError(
                "Remote image URLs are not fetched; inline the image as a data: URI instead",
                param="messages[].content[].image_url.url",
            )
