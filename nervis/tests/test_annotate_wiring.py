"""Whether "put your comments in the document" becomes an Annotate button, and
what pressing it writes — the chat-level wiring around `annotate.py`, whose
own placing logic is covered in `test_annotate.py`.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import httpx
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from nervis import commands
from test_m4_chat import _attach, _with_models, an_api, frames, turn


def _two_pages() -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(612, 792))
    for words in ("Alpha section about mornings.", "Bravo section about harbours and tides."):
        c.setFont("Helvetica", 12)
        c.drawString(72, 700, words)
        c.showPage()
    c.save()
    return buffer.getvalue()


# ── The offer ─────────────────────────────────────────────────────────────────


def test_asking_for_comments_in_the_document_offers_annotate(tmp_path: Path) -> None:
    """The sentence from the conversation this was built for, in the shape it
    happened: a document attached, comments given, then "put them in"."""
    client = an_api(workspace_path=str(tmp_path))
    _attach(client, "cv_annot", "blueprint.pdf", _two_pages())

    answered = turn(client, "insert your comments into the document",
                    system="Be someone.", attachment_id="cv_annot")

    offer = json.loads(answered.headers["x-command-offer"])
    assert offer["operation"] == "nervis.document.annotate"
    assert offer["target"].startswith("blueprint-annotated-")
    assert offer["target"].endswith(".pdf")


def test_a_plain_save_with_a_document_attached_is_still_a_plain_save(tmp_path: Path) -> None:
    """The falsifier. Attaching a file must not turn every save into an
    annotate — "save that as notes.pdf" wants the reply, as it always did."""
    client = an_api(workspace_path=str(tmp_path))
    _attach(client, "cv_plain", "blueprint.pdf", _two_pages())

    answered = turn(client, "save that as notes.pdf",
                    system="Be someone.", attachment_id="cv_plain")

    assert json.loads(answered.headers["x-command-offer"])["operation"] == "nervis.document.write"


def test_comments_with_nothing_attached_cannot_be_an_annotate() -> None:
    offer = commands.propose("put your comments into the original", [],
                             default_name="x-2026-09-07.pdf", attachment="")

    assert offer is None or offer.operation != "nervis.document.annotate"


def test_a_text_original_gets_a_text_copy() -> None:
    offer = commands.propose("merge your findings into the file", [],
                             default_name="notes-annotated-2026-09-07.pdf",
                             attachment="notes.md")

    assert offer is not None and offer.operation == "nervis.document.annotate"
    assert offer.target == "notes-annotated-2026-09-07.md"


def test_the_model_is_told_to_quote_and_never_retype() -> None:
    offer = commands.propose("add your comments to the document", [],
                             default_name="doc-annotated-2026-09-07.pdf", attachment="doc.pdf")

    assert offer is not None
    said = commands.told(offer)
    assert "Never retype" in said
    assert "`> `" in said
    assert "write them out here now" in said


# ── Pressing it ───────────────────────────────────────────────────────────────


def test_pressing_annotate_writes_the_original_pages_with_the_comment_beside_its_text(
    tmp_path: Path,
) -> None:
    client = an_api(frames("> harbours and tides\nNeeds a tide table."),
                    workspace_path=str(tmp_path))
    _attach(client, "cv_press", "blueprint.pdf", _two_pages())
    answered = turn(client, "insert your comments into the document",
                    system="Be someone.", attachment_id="cv_press")
    conversation = answered.headers["x-conversation-id"]

    ran = client.post("/api/v1/commands/run", json={
        "operation": "nervis.document.annotate",
        "target": "blueprint-annotated.pdf",
        "conversation_id": conversation,
    })

    assert ran.status_code == 200, ran.text
    pages = [p.extract_text() or "" for p in
             PdfReader(io.BytesIO((tmp_path / "blueprint-annotated.pdf").read_bytes())).pages]
    assert len(pages) == 2, "the comment is in the margin, not on a page of its own"
    assert "Alpha section" in pages[0]
    assert "Bravo section" in pages[1] and "Needs a tide table." in pages[1]


def test_an_annotated_pdf_refuses_a_non_pdf_name(tmp_path: Path) -> None:
    """Its pages are copied, not re-rendered — there is no `.md` of that."""
    client = an_api(frames("> harbours\nHm."), workspace_path=str(tmp_path))
    _attach(client, "cv_md", "blueprint.pdf", _two_pages())
    answered = turn(client, "insert your comments into the document",
                    system="Be someone.", attachment_id="cv_md")

    ran = client.post("/api/v1/commands/run", json={
        "operation": "nervis.document.annotate",
        "target": "blueprint-annotated.md",
        "conversation_id": answered.headers["x-conversation-id"],
    })

    assert ran.status_code == 422, ran.text
    assert "PDF" in ran.text


def test_the_newest_anchored_reply_wins_over_a_newer_reply_that_only_describes_the_button(
    tmp_path: Path,
) -> None:
    """Observed live: findings with quotes, then "put them in", then a reply
    that only said what pressing the button would do. The newest reply placed
    nothing; the one before it was what the person meant."""
    client = an_api(frames("> harbours and tides\nNeeds a tide table."),
                    workspace_path=str(tmp_path))
    _attach(client, "cv_newest", "blueprint.pdf", _two_pages())
    first = turn(client, "what do you think of this pdf",
                 system="Be someone.", attachment_id="cv_newest")
    conversation = first.headers["x-conversation-id"]

    def describes_the_button(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, stream=httpx.ByteStream(
            b"".join(frames("Press the Annotate button and I will place them."))
        ))

    client.app.state.probe_client._transport = httpx.MockTransport(  # type: ignore[attr-defined]
        describes_the_button
    )
    turn(client, "insert your comments into the document", system="Be someone.",
         attachment_id="cv_newest", conversation_id=conversation)

    ran = client.post("/api/v1/commands/run", json={
        "operation": "nervis.document.annotate",
        "target": "blueprint-annotated.pdf",
        "conversation_id": conversation,
    })

    assert ran.status_code == 200, ran.text
    pages = [p.extract_text() or "" for p in
             PdfReader(io.BytesIO((tmp_path / "blueprint-annotated.pdf").read_bytes())).pages]
    assert "Needs a tide table." in pages[1]
    assert not any("Press the Annotate button" in p for p in pages)


def test_reading_a_document_asks_for_anchored_comments_from_the_start(tmp_path: Path) -> None:
    """So the findings are placeable when they are first written, and a later
    "put them in" needs no rewriting."""
    sent: list[dict[str, Any]] = []
    client = an_api(workspace_path=str(tmp_path))
    _with_models(client, sent, ["qwen/qwen3-4b-2507"])
    _attach(client, "cv_head", "blueprint.pdf", _two_pages())

    turn(client, "read this pdf and give me your findings",
         system="Be someone.", attachment_id="cv_head")

    prompt = " ".join(str(m.get("content", "")) for b in sent for m in b.get("messages", []))
    assert "line beginning `> `" in prompt


def test_annotate_with_nothing_attached_says_so(tmp_path: Path) -> None:
    client = an_api(workspace_path=str(tmp_path))
    answered = turn(client, "hello", system="Be someone.")

    ran = client.post("/api/v1/commands/run", json={
        "operation": "nervis.document.annotate",
        "target": "x.pdf",
        "conversation_id": answered.headers["x-conversation-id"],
    })

    assert ran.status_code == 422
    assert "nothing is attached" in ran.text
