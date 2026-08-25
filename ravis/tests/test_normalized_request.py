"""Normalizing a request without losing the bytes it arrived as."""

from __future__ import annotations

import json

from ravis.core.requests import normalize


def _envelope(payload: dict) -> bytes:
    return json.dumps(payload).encode()


def test_the_normalized_view_does_not_carry_the_client_s_bytes() -> None:
    """§7's guarantee is real; this object is not where it lives.

    `NormalizedRequest` used to have an `original_envelope` field, described in
    two docstrings as the one doing "more work than the rest combined" — and
    read nowhere. The transparent path forwards `_Call.body` and
    `_Call.body_for`, which hold the same bytes and always did.

    The guarantee itself is asserted where it happens, by
    `test_streamed_bytes_arrive_exactly_as_the_upstream_sent_them` in
    `ravis/tests/test_transparent_proxy.py`. What this pins is the absence, so
    the field cannot quietly return and be believed again.
    """
    payload = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}

    request = normalize(_envelope(payload), payload)

    assert not hasattr(request, "original_envelope")


def test_a_system_message_is_lifted_out_for_providers_that_need_it() -> None:
    """Anthropic and Gemini take the system prompt as a top-level field."""
    payload = {"messages": [{"role": "system", "content": "be brief"},
                            {"role": "user", "content": "hi"}]}

    assert normalize(_envelope(payload), payload).system == "be brief"


def test_structured_system_content_is_left_in_place() -> None:
    """Flattening it would discard whatever structure it carried."""
    payload = {"messages": [{"role": "system", "content": [{"type": "text", "text": "hi"}]}]}

    assert normalize(_envelope(payload), payload).system is None


def test_tools_are_a_signal_and_are_reported_as_one() -> None:
    payload = {"messages": [], "tools": [{"type": "function", "function": {"name": "f"}}]}

    assert normalize(_envelope(payload), payload).carries_tools is True


def test_images_are_detected_in_structured_content() -> None:
    payload = {"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]}]}

    assert normalize(_envelope(payload), payload).carries_images is True


def test_either_max_tokens_spelling_is_understood() -> None:
    """Clients differ; the newer name should not silently mean no limit."""
    assert normalize(b"{}", {"max_completion_tokens": 64}).max_output_tokens == 64
    assert normalize(b"{}", {"max_tokens": 32}).max_output_tokens == 32


def test_a_json_schema_response_format_is_extracted() -> None:
    payload = {"response_format": {"type": "json_schema", "json_schema": {"name": "x"}}}

    assert normalize(_envelope(payload), payload).response_schema == {"name": "x"}


def test_a_plain_json_object_response_format_is_not_a_schema() -> None:
    """`json_object` constrains the shape but supplies no schema to validate."""
    payload = {"response_format": {"type": "json_object"}}

    assert normalize(_envelope(payload), payload).response_schema is None
