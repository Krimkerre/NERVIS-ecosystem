"""Extracting a template's own look, checked against real PDFs it never
authored: fixtures built with reportlab directly, read back with pdfplumber,
the way the feature's two halves actually meet in production.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.pdfgen import canvas

from nervis.style import DEFAULT, _family_of, extract_style


def _fixture(path: Path, *, body_font: str = "Helvetica", body_size: float = 11.0,
             heading_font: str = "Helvetica-Bold", heading_size: float = 24.0,
             body_colour: tuple[float, float, float] = (0.1, 0.1, 0.1),
             heading_colour: tuple[float, float, float] = (0.2, 0.0, 0.4),
             background: tuple[float, float, float] | None = None) -> None:
    """A one-page PDF with a known, deliberate look — a template in miniature."""
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    if background is not None:
        c.setFillColorRGB(*background)
        c.rect(0, 0, 612, 792, fill=1, stroke=0)
    c.setFont(heading_font, heading_size)
    c.setFillColorRGB(*heading_colour)
    c.drawString(100, 700, "A Heading")
    c.setFont(body_font, body_size)
    c.setFillColorRGB(*body_colour)
    for line in range(6):
        c.drawString(100, 650 - line * 16, f"Body text on line {line}, filling out the page.")
    c.save()


def test_extract_style_reads_the_body_font(tmp_path: Path) -> None:
    path = tmp_path / "template.pdf"
    _fixture(path, body_font="Times-Roman")

    profile = extract_style(path)

    assert profile is not None
    assert profile.body_family == "Times-Roman"


def test_extract_style_reads_the_heading_size_larger_than_body(tmp_path: Path) -> None:
    path = tmp_path / "template.pdf"
    _fixture(path, body_size=11.0, heading_size=28.0)

    profile = extract_style(path)

    assert profile is not None
    assert profile.heading_size > profile.body_size
    assert profile.heading_size == 28.0


def test_extract_style_reads_the_text_colour(tmp_path: Path) -> None:
    path = tmp_path / "template.pdf"
    _fixture(path, body_colour=(0.8, 0.1, 0.1))

    profile = extract_style(path)

    assert profile is not None
    r, g, b = profile.text_colour
    assert abs(r - 0.8) < 0.02 and abs(g - 0.1) < 0.02 and abs(b - 0.1) < 0.02


def test_extract_style_reads_a_full_bleed_background(tmp_path: Path) -> None:
    path = tmp_path / "template.pdf"
    _fixture(path, background=(0.95, 0.93, 0.88))

    profile = extract_style(path)

    assert profile is not None
    r, g, b = profile.background_colour
    assert abs(r - 0.95) < 0.02 and abs(g - 0.93) < 0.02 and abs(b - 0.88) < 0.02


def test_extract_style_falls_back_to_white_with_no_background_painted(tmp_path: Path) -> None:
    """No full-page rect drawn — the default's own paper-white, not a guess."""
    path = tmp_path / "template.pdf"
    _fixture(path, background=None)

    profile = extract_style(path)

    assert profile is not None
    assert profile.background_colour == DEFAULT.background_colour


def test_extract_style_reads_the_page_geometry(tmp_path: Path) -> None:
    path = tmp_path / "template.pdf"
    _fixture(path)

    profile = extract_style(path)

    assert profile is not None
    assert profile.page_width == 612.0
    assert profile.page_height == 792.0


def test_extract_style_clamps_a_margin_from_sparse_content(tmp_path: Path) -> None:
    """A short template leaves most of the page blank, which is not the same
    as a wide margin — the clamp is what keeps the two from being confused."""
    path = tmp_path / "sparse.pdf"
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.setFont("Helvetica", 14)
    c.drawString(72, 700, "One short line.")
    c.save()

    profile = extract_style(path)

    assert profile is not None
    assert profile.margin_bottom <= 792 / 6


def test_extract_style_on_a_broken_file_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "not-a-pdf.pdf"
    path.write_bytes(b"this is not a PDF at all")

    assert extract_style(path) is None


def test_extract_style_on_a_scanned_pdf_with_no_text_returns_none(tmp_path: Path) -> None:
    """A page with no characters at all — a photograph of a page, not a page."""
    path = tmp_path / "scanned.pdf"
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.showPage()
    c.save()

    assert extract_style(path) is None


def test_extract_style_on_a_missing_file_returns_none(tmp_path: Path) -> None:
    assert extract_style(tmp_path / "does-not-exist.pdf") is None


def test_family_of_recognises_serif_names() -> None:
    assert _family_of("Times-Bold") == "Times-Roman"
    assert _family_of("Georgia") == "Times-Roman"


def test_family_of_recognises_a_subset_tagged_bold_name() -> None:
    """`ABCDEF+TimesNewRomanPS-BoldMT` — a subset prefix and a platform-specific
    suffix around a name a template's own PDF producer chose, not NERVIS."""
    assert _family_of("ABCDEF+TimesNewRomanPS-BoldMT") == "Times-Roman"


def test_family_of_recognises_monospace_names() -> None:
    assert _family_of("Courier-Bold") == "Courier"
    assert _family_of("Consolas") == "Courier"


def test_family_of_falls_back_to_helvetica_for_an_unknown_sans_name() -> None:
    """Every sans face a template might use, including ones never seen
    before — the fallback that makes an unrecognised name safe rather than a
    `KeyError` on the first font this has not been told about."""
    assert _family_of("ABCDEF+SomeCorporateBrandSans-Regular") == "Helvetica"
