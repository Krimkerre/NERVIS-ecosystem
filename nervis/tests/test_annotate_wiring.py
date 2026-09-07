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
    assert offer["operation"] == "nervis.document.annotate.notes"
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

    assert offer is None or not offer.operation.startswith("nervis.document.annotate")


def test_a_text_original_gets_a_text_copy() -> None:
    offer = commands.propose("merge your findings into the file", [],
                             default_name="notes-annotated-2026-09-07.pdf",
                             attachment="notes.md")

    assert offer is not None and offer.operation == "nervis.document.annotate.notes"
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
        "operation": "nervis.document.annotate.margin",
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
        "operation": "nervis.document.annotate.notes",
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
        "operation": "nervis.document.annotate.margin",
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
        "operation": "nervis.document.annotate.notes",
        "target": "x.pdf",
        "conversation_id": answered.headers["x-conversation-id"],
    })

    assert ran.status_code == 422
    assert "nothing is attached" in ran.text


# ── Which of the three ────────────────────────────────────────────────────────


def test_naming_a_style_picks_that_copy() -> None:
    for asked, wanted in (
        ("put your comments in the margin of the document", "margin"),
        ("add your comments to the document as sticky notes only", "notes"),
        ("insert your comments into the document inline", "inline"),
        ("re-render the document with your comments under each passage", "inline"),
    ):
        offer = commands.propose(asked, [], default_name="doc-annotated-2026-09-07.pdf",
                                 attachment="doc.pdf")
        assert offer is not None, asked
        assert offer.operation == f"nervis.document.annotate.{wanted}", asked


def test_a_bare_choice_after_the_offer_picks_that_copy() -> None:
    """The reply to "you can say margin notes or inline" is just that — with
    a document attached, nobody types "margin notes" for any other reason."""
    for asked, wanted in (
        ("margin notes", "margin"),
        ("sticky notes only", "notes"),
        ("inline please", "inline"),
        ("go with the margin one", "margin"),
    ):
        offer = commands.propose(asked, [], default_name="doc-annotated-2026-09-07.pdf",
                                 attachment="doc.pdf")
        assert offer is not None, asked
        assert offer.operation == f"nervis.document.annotate.{wanted}", asked


def test_a_bare_choice_with_nothing_attached_is_nothing() -> None:
    assert commands.propose("margin notes", [], default_name="x.pdf", attachment="") is None


def test_the_default_offer_tells_the_model_to_name_the_other_two() -> None:
    """Soft default: the sticky-notes button is there at once, and the reply
    says the other two are a word away — no question blocks the button."""
    offer = commands.propose("add your comments to the document", [],
                             default_name="doc-annotated-2026-09-07.pdf", attachment="doc.pdf")

    assert offer is not None and offer.operation == "nervis.document.annotate.notes"
    said = commands.told(offer)
    assert "**margin notes**" in said and "**inline**" in said


def test_a_chosen_copy_is_not_told_to_offer_alternatives() -> None:
    offer = commands.propose("put your comments in the margin", [],
                             default_name="doc-annotated-2026-09-07.pdf", attachment="doc.pdf")

    assert offer is not None
    assert "**margin notes**" not in commands.told(offer)


def test_the_inline_copy_of_a_pdf_is_text_with_comments_under_the_passages(
    tmp_path: Path,
) -> None:
    client = an_api(frames("> harbours and tides\nNeeds a tide table."),
                    workspace_path=str(tmp_path))
    _attach(client, "cv_inline", "blueprint.pdf", _two_pages())
    answered = turn(client, "insert your comments into the document inline",
                    system="Be someone.", attachment_id="cv_inline")
    assert json.loads(answered.headers["x-command-offer"])["operation"] == (
        "nervis.document.annotate.inline"
    )

    ran = client.post("/api/v1/commands/run", json={
        "operation": "nervis.document.annotate.inline",
        "target": "blueprint-inline.md",
        "conversation_id": answered.headers["x-conversation-id"],
    })

    assert ran.status_code == 200, ran.text
    text = (tmp_path / "blueprint-inline.md").read_text()
    assert "Bravo section" in text and "**Comment:** Needs a tide table." in text
    assert text.index("Bravo section") < text.index("**Comment:**")


def test_the_default_offer_carries_the_other_two_as_alternatives() -> None:
    """Stated by the screen, not left to the model: the chips are the soft
    default made visible."""
    offer = commands.propose("add your comments to the document", [],
                             default_name="doc-annotated-2026-09-07.pdf", attachment="doc.pdf")

    assert offer is not None
    assert offer.as_dict()["alternatives"] == [
        {"operation": "nervis.document.annotate.margin", "action": "Annotate"},
        {"operation": "nervis.document.annotate.inline", "action": "Annotate inline"},
    ]


def test_a_chosen_style_carries_no_alternatives() -> None:
    offer = commands.propose("put your comments in the margin", [],
                             default_name="doc-annotated-2026-09-07.pdf", attachment="doc.pdf")

    assert offer is not None and offer.as_dict()["alternatives"] == []


# ── page images on the turn ───────────────────────────────────────────────────


def _tabled() -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

    buffer = io.BytesIO()
    grid = TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)])
    SimpleDocTemplate(buffer, pagesize=letter).build([
        Table([["Tool", "Effect"], ["logs.query", "Read-only"], ["ravis.routes", "Read"]],
              style=grid),
    ])
    return buffer.getvalue()


def _sent_content(sent: list[dict[str, Any]]) -> Any:
    return [m for body in sent for m in body.get("messages", []) if m["role"] == "user"][-1]


def test_a_tables_page_rides_on_the_asking_turn_as_an_image(tmp_path: Path) -> None:
    """The picture belongs to the turn that asked about the document, as an
    image part beside the question — which is the only place an image part
    means anything."""
    sent: list[dict[str, Any]] = []
    client = an_api(workspace_path=str(tmp_path))
    _with_models(client, sent, ["qwen/qwen2.5vl-3b"], vision=True)
    _attach(client, "cv_pages", "spec.pdf", _tabled())

    turn(client, "read this pdf and tell me about the table",
         system="Be someone.", attachment_id="cv_pages")

    content = _sent_content(sent)["content"]
    assert isinstance(content, list), "a text part and an image part, not a bare string"
    assert content[0]["type"] == "text" and "read this pdf" in content[0]["text"]
    images = [part for part in content if part["type"] == "image_url"]
    assert len(images) == 1
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_no_vision_capable_model_means_no_images_are_sent(tmp_path: Path) -> None:
    """RAVIS reads an image as a hard requirement, so attaching one where
    nothing can see would turn an ordinary question into a refusal to route."""
    sent: list[dict[str, Any]] = []
    client = an_api(workspace_path=str(tmp_path))
    _with_models(client, sent, ["qwen/qwen3-4b-2507"], vision=False)
    _attach(client, "cv_blind", "spec.pdf", _tabled())

    turn(client, "read this pdf and tell me about the table",
         system="Be someone.", attachment_id="cv_blind")

    content = _sent_content(sent)["content"]
    assert isinstance(content, str), "the question alone, exactly as before"


def test_a_turn_that_opens_no_document_sends_no_images(tmp_path: Path) -> None:
    sent: list[dict[str, Any]] = []
    client = an_api(workspace_path=str(tmp_path))
    _with_models(client, sent, ["qwen/qwen2.5vl-3b"], vision=True)

    turn(client, "how are the services doing", system="Be someone.")

    assert isinstance(_sent_content(sent)["content"], str)


def test_an_annotated_copy_is_not_visually_checked(tmp_path: Path) -> None:
    """The glance is for pages NERVIS laid out. An annotated copy's first page
    is the person's own cover, and the glance duly reported the heading on it
    as "crowded against the text below" — a model reviewing somebody's design,
    on a page NERVIS did not draw."""
    client = an_api(frames("> harbours and tides\nNeeds a tide table."),
                    workspace_path=str(tmp_path))
    client.app.state.settings.ravis_client_credential = "secret"
    _attach(client, "cv_glance", "blueprint.pdf", _two_pages())
    answered = turn(client, "insert your comments into the document",
                    system="Be someone.", attachment_id="cv_glance")

    def would_flag(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "Yes: the heading is crowded."}}]
        })

    client.app.state.probe_client._transport = httpx.MockTransport(  # type: ignore[attr-defined]
        would_flag
    )

    ran = client.post("/api/v1/commands/run", json={
        "operation": "nervis.document.annotate.notes",
        "target": "copy.pdf",
        "conversation_id": answered.headers["x-conversation-id"],
    })

    assert ran.status_code == 200, ran.text
    assert "looked over" not in ran.json()["file"]["detail"]


def test_a_saved_reply_is_still_visually_checked(tmp_path: Path) -> None:
    """The falsifier: NERVIS drew that page, so the glance still applies."""
    client = an_api(workspace_path=str(tmp_path))
    client.app.state.settings.ravis_client_credential = "secret"
    answered = turn(client, "tell me something", system="Be someone.")

    def would_flag(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "Yes: text runs off the page."}}]
        })

    client.app.state.probe_client._transport = httpx.MockTransport(  # type: ignore[attr-defined]
        would_flag
    )

    ran = client.post("/api/v1/commands/run", json={
        "operation": "nervis.document.write",
        "target": "plain.pdf",
        "conversation_id": answered.headers["x-conversation-id"],
    })

    assert "text runs off the page." in ran.json()["file"]["detail"]
