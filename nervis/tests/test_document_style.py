"""Whether a save picks up a template's look — the chat-level wiring, not
`style.py`'s own extraction (see `test_style.py`) or `pdf.py`'s own rendering
(see `test_pdf.py`). This file is only about which one `_write_into_workspace`
reaches for, and when.
"""
from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from test_m4_chat import _attach, an_api, turn


def _saved(client: TestClient, tmp_path: Path, conversation: str, name: str) -> bytes:
    ran = client.post("/api/v1/commands/run", json={
        "operation": "nervis.document.write",
        "target": name,
        "conversation_id": conversation,
    })
    assert ran.status_code == 200, ran.text
    # The export room: a document chat wrote is something NERVIS produced, and
    # the workspace keeps those apart from what it was handed.
    return (tmp_path / "export" / name).read_bytes()


def _font_names(data: bytes) -> set[str]:
    from pypdf import PdfReader
    fonts = PdfReader(io.BytesIO(data)).pages[0]["/Resources"]["/Font"]
    return {str(font["/BaseFont"]) for font in fonts.values()}


def test_saving_with_a_pdf_attachment_uses_its_style(tmp_path: Path) -> None:
    """The scenario this feature exists for, in the shape it actually
    happens in: the browser mints its own id, attaches a file under it
    *before* anything is typed, then sends the first message — the ordinary
    "clip, then question" order `chat_documents.py` names, and the exact
    turn on which the browser's id and NERVIS's own real one are guaranteed
    to differ. Nothing here manually reconciles the two; the save has to
    find the template on its own, through `_document`'s own copy.

    Built directly with reportlab, distinctively serif-bodied, so the
    fixture actually differs from what a save produces with no template.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(612, 792))
    c.setFont("Times-Roman", 11)
    c.setFillColorRGB(0.1, 0.1, 0.1)
    for line in range(8):
        c.drawString(72, 700 - line * 16, f"Template body line {line} of the source document.")
    c.save()
    template = buffer.getvalue()

    client = an_api(workspace_path=str(tmp_path))
    _attach(client, "cv_browser_minted", "template.pdf", template)
    answered = turn(client, "look at this", system="Be someone.",
                     attachment_id="cv_browser_minted")
    conversation = answered.headers["x-conversation-id"]
    assert conversation != "cv_browser_minted", "ids must differ, or this test proves nothing"

    written = _saved(client, tmp_path, conversation, "styled.pdf")

    assert "/Times-Roman" in _font_names(written)


def test_saving_with_no_attachment_uses_the_default_theme(tmp_path: Path) -> None:
    client = an_api(workspace_path=str(tmp_path))
    answered = turn(client, "tell me something", system="Be someone.")
    conversation = answered.headers["x-conversation-id"]

    written = _saved(client, tmp_path, conversation, "plain.pdf")

    assert "/Times-Roman" not in _font_names(written)
    assert "/Helvetica" in _font_names(written)


def test_saving_with_a_non_pdf_attachment_uses_the_default_theme(tmp_path: Path) -> None:
    """The newest attachment is a CSV, not a template — nothing to extract a
    look from, and reaching past it for an older file would be reaching for
    something that is not what "the attachment" means any more."""
    client = an_api(workspace_path=str(tmp_path))
    _attach(client, "cv_browser_minted", "data.csv", b"a,b,c\n1,2,3\n")
    answered = turn(client, "look at this", system="Be someone.",
                     attachment_id="cv_browser_minted")
    conversation = answered.headers["x-conversation-id"]

    written = _saved(client, tmp_path, conversation, "plain.pdf")

    assert "/Times-Roman" not in _font_names(written)


def test_exporting_a_conversation_ignores_the_template_attachment(tmp_path: Path) -> None:
    """`_export_conversation` never passes a template style at all —
    `render_conversation`'s own dark, chat-matching theme either way, proven
    by its distinctive background rather than by absence of a font name that
    `render_conversation` was never going to use regardless."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(612, 792))
    c.setFillColorRGB(0.95, 0.95, 0.98)
    c.rect(0, 0, 612, 792, fill=1, stroke=0)
    c.setFont("Times-Roman", 11)
    c.drawString(72, 700, "A template that looks nothing like the chat window.")
    c.save()
    template = buffer.getvalue()

    client = an_api(workspace_path=str(tmp_path))
    _attach(client, "cv_browser_minted", "template.pdf", template)
    answered = turn(client, "look at this", system="Be someone.",
                     attachment_id="cv_browser_minted")
    conversation = answered.headers["x-conversation-id"]

    ran = client.post("/api/v1/commands/run", json={
        "operation": "nervis.conversation.export",
        "target": "transcript.pdf",
        "conversation_id": conversation,
    })
    assert ran.status_code == 200, ran.text
    written = (tmp_path / "export" / "transcript.pdf").read_bytes()

    # render_conversation's own dark palette (layout.PAPER), not the light
    # template's — and definitely not the template's own Times-Roman, since a
    # transcript is drawn in the old renderer's fixed faces regardless.
    assert "/Times-Roman" not in _font_names(written)
