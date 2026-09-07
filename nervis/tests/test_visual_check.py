"""Rasterising a page and reading a model's verdict on it — the two pure
halves of `visual_check.py`. What actually calls RAVIS and reaches the save
response lives in `test_visual_check_wiring.py`; this file is only about
whether a PDF becomes a picture, and whether a reply becomes a verdict.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

from pypdf import PdfWriter
from reportlab.pdfgen import canvas

from nervis.visual_check import first_page, verdict_of


def _a_pdf(path: Path) -> bytes:
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.setFont("Helvetica", 11)
    c.drawString(72, 700, "A page worth looking at.")
    c.save()
    return path.read_bytes()


def test_first_page_returns_a_png_data_url(tmp_path: Path) -> None:
    snapshot = first_page(_a_pdf(tmp_path / "one.pdf"))

    assert snapshot is not None
    assert snapshot.data_url.startswith("data:image/png;base64,")
    encoded = snapshot.data_url.removeprefix("data:image/png;base64,")
    assert base64.b64decode(encoded)[:8] == b"\x89PNG\r\n\x1a\n"


def test_first_page_on_garbage_bytes_returns_none() -> None:
    assert first_page(b"not a pdf at all") is None


def test_first_page_on_a_zero_page_pdf_returns_none() -> None:
    buffer = io.BytesIO()
    PdfWriter().write(buffer)

    assert first_page(buffer.getvalue()) is None


def test_verdict_of_a_clean_no_is_no_defect() -> None:
    assert verdict_of("No.") is None


def test_verdict_of_a_no_with_trailing_explanation_is_no_defect() -> None:
    assert verdict_of("No, it looks fine.") is None


def test_verdict_of_empty_reply_is_no_defect() -> None:
    assert verdict_of("") is None
    assert verdict_of("   ") is None


def test_verdict_of_a_reply_that_ignores_the_format_is_no_defect() -> None:
    """Fails open — the documented behaviour, not an accident. A model that
    prepends a preamble instead of leading with yes/no is not distinguishable
    here from one that had nothing to flag, and this module never surfaces a
    defect it cannot actually point to."""
    assert verdict_of("Looking at this page... no, everything looks fine.") is None


def test_verdict_of_a_yes_with_a_colon_names_the_defect() -> None:
    assert verdict_of("Yes: the code block runs off the right edge.") == (
        "the code block runs off the right edge."
    )


def test_verdict_of_a_lowercase_yes_with_a_dash_names_the_defect() -> None:
    assert verdict_of("yes - heading overlaps the paragraph below it") == (
        "heading overlaps the paragraph below it"
    )


def test_verdict_of_a_bare_yes_gets_a_fallback_description() -> None:
    assert verdict_of("Yes") == "flagged as visually broken, but did not say how"
