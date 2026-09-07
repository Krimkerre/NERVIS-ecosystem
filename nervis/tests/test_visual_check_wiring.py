"""Whether a save actually asks a vision-capable model, and when — the
chat-level wiring, not `visual_check.py`'s own rasterising or verdict-reading
(see `test_visual_check.py`). Mirrors `test_document_style.py`'s split for
the style feature: that file is about which look a save borrows, this one is
about whether a save gets a second glance.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from reportlab.pdfgen import canvas

from nervis.api.commands import _visual_defect
from test_m4_chat import an_api, turn


def _a_pdf_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / "fixture.pdf"
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.drawString(72, 700, "A page worth looking at.")
    c.save()
    return path.read_bytes()


def _run(coroutine: Any) -> Any:
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coroutine)


def _request(app: Any) -> SimpleNamespace:
    return SimpleNamespace(app=app, state=SimpleNamespace())


# ── `_visual_defect` directly ────────────────────────────────────────────────


def test_no_credential_skips_the_call_entirely(tmp_path: Path) -> None:
    class _Client:
        @staticmethod
        async def post(url: str, **kwargs: Any) -> Any:
            del url, kwargs
            raise AssertionError("should never be called with no RAVIS credential")

    client = an_api()
    client.app.state.probe_client = _Client()

    assert _run(_visual_defect(_request(client.app), _a_pdf_bytes(tmp_path))) is None


def test_a_clean_page_returns_no_defect(tmp_path: Path) -> None:
    posted: list[dict[str, Any]] = []

    class _Reply:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {"choices": [{"message": {"content": "No, it looks fine."}}]}

    class _Client:
        @staticmethod
        async def post(url: str, **kwargs: Any) -> Any:
            del url
            posted.append(kwargs["json"])
            return _Reply()

    client = an_api()
    client.app.state.settings.ravis_client_credential = "secret"
    client.app.state.probe_client = _Client()
    entry = client.app.state.registry.get("ravis")
    assert entry is not None and entry.is_usable, "the fake registry must offer a usable RAVIS"

    result = _run(_visual_defect(_request(client.app), _a_pdf_bytes(tmp_path)))

    assert result is None
    assert posted[0]["model"] == "ravis/vision"
    assert posted[0]["metadata"] == {"background": True}


def test_a_defect_is_surfaced_with_the_page_attached_as_an_image(tmp_path: Path) -> None:
    posted: list[dict[str, Any]] = []

    class _Reply:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {"choices": [{"message": {"content": "Yes: the heading overlaps the body."}}]}

    class _Client:
        @staticmethod
        async def post(url: str, **kwargs: Any) -> Any:
            del url
            posted.append(kwargs["json"])
            return _Reply()

    client = an_api()
    client.app.state.settings.ravis_client_credential = "secret"
    client.app.state.probe_client = _Client()

    result = _run(_visual_defect(_request(client.app), _a_pdf_bytes(tmp_path)))

    assert result == "the heading overlaps the body."
    parts = posted[0]["messages"][0]["content"]
    images = [part for part in parts if part["type"] == "image_url"]
    assert len(images) == 1
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_ravis_refusing_the_call_returns_no_defect(tmp_path: Path) -> None:
    class _Reply:
        status_code = 503

        @staticmethod
        def json() -> dict[str, Any]:
            return {}

    class _Client:
        @staticmethod
        async def post(url: str, **kwargs: Any) -> Any:
            del url, kwargs
            return _Reply()

    client = an_api()
    client.app.state.settings.ravis_client_credential = "secret"
    client.app.state.probe_client = _Client()

    assert _run(_visual_defect(_request(client.app), _a_pdf_bytes(tmp_path))) is None


def test_ravis_unreachable_returns_no_defect(tmp_path: Path) -> None:
    class _Client:
        @staticmethod
        async def post(url: str, **kwargs: Any) -> Any:
            del url, kwargs
            raise httpx.ConnectError("refused")

    client = an_api()
    client.app.state.settings.ravis_client_credential = "secret"
    client.app.state.probe_client = _Client()

    assert _run(_visual_defect(_request(client.app), _a_pdf_bytes(tmp_path))) is None


# ── Through the real save endpoint ───────────────────────────────────────────


def test_saving_a_pdf_surfaces_a_visual_defect_in_the_response(tmp_path: Path) -> None:
    client = an_api(workspace_path=str(tmp_path))
    client.app.state.settings.ravis_client_credential = "secret"
    answered = turn(client, "tell me something", system="Be someone.")
    conversation = answered.headers["x-conversation-id"]

    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Yes: text runs off the page."}}]}
        )

    client.app.state.probe_client._transport = httpx.MockTransport(  # type: ignore[attr-defined]
        handle
    )

    ran = client.post("/api/v1/commands/run", json={
        "operation": "nervis.document.write",
        "target": "styled.pdf",
        "conversation_id": conversation,
    })

    assert ran.status_code == 200, ran.text
    assert "text runs off the page." in ran.json()["file"]["detail"]


def test_saving_a_pdf_that_looks_fine_adds_nothing_extra(tmp_path: Path) -> None:
    client = an_api(workspace_path=str(tmp_path))
    client.app.state.settings.ravis_client_credential = "secret"
    answered = turn(client, "tell me something", system="Be someone.")
    conversation = answered.headers["x-conversation-id"]

    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"choices": [{"message": {"content": "No."}}]})

    client.app.state.probe_client._transport = httpx.MockTransport(  # type: ignore[attr-defined]
        handle
    )

    ran = client.post("/api/v1/commands/run", json={
        "operation": "nervis.document.write",
        "target": "plain.pdf",
        "conversation_id": conversation,
    })

    assert ran.status_code == 200, ran.text
    assert "looked over" not in ran.json()["file"]["detail"]


def test_exporting_a_conversation_is_never_visually_checked(tmp_path: Path) -> None:
    """`_write_into_workspace` calls `_visual_defect` only on the plain-reply
    path (`not conversation`) — proven with a fake RAVIS that would always
    report a defect, and a detail string that never mentions it."""
    client = an_api(workspace_path=str(tmp_path))
    client.app.state.settings.ravis_client_credential = "secret"
    answered = turn(client, "tell me something", system="Be someone.")
    conversation = answered.headers["x-conversation-id"]

    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Yes: everything is on fire."}}]}
        )

    client.app.state.probe_client._transport = httpx.MockTransport(  # type: ignore[attr-defined]
        handle
    )

    ran = client.post("/api/v1/commands/run", json={
        "operation": "nervis.conversation.export",
        "target": "transcript.pdf",
        "conversation_id": conversation,
    })

    assert ran.status_code == 200, ran.text
    assert "on fire" not in ran.json()["file"]["detail"]
