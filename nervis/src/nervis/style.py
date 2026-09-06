"""What a template PDF looks like, extracted so a new document can match it.

**Why this exists separately from `pdf.py` and `documents.py`.** `documents.py`
reads a PDF for its words, for a model to answer from; this reads one for its
appearance, for a renderer to match — the same file, two completely different
questions, answered by two different libraries because neither answers both.
`pypdf`'s `extract_text` throws away position, font and colour on purpose; this
module exists because those are exactly what it throws away.

**Why pdfplumber and not a few more lines of pypdf.** A font's name, size and
colour live per-character in a PDF's content stream, addressed through the
resource dictionary and the current graphics state at the point each glyph was
shown — the same layer of indirection `pypdf` was brought in for on the reading
side (`nervis/pyproject.toml`), for the same reason: parsing it correctly is not
a few lines, and parsing it incorrectly returns a confident, wrong style nobody
asked for.

**What this does not attempt.** Recovering the template's exact font — out of
scope; the closest base-14 family is substituted instead, named here rather
than silently approximated. No table/list structure, no image extraction, no
OCR fallback for a scanned template: `extract_style` returns `None` and the
caller falls back to `DEFAULT`, an absence stated plainly rather than a style
half-guessed from nothing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RGB = tuple[float, float, float]


@dataclass(frozen=True)
class StyleProfile:
    """How a document should look — enough to drive a reportlab story.

    Deliberately not a reconstruction of the template. Recovering *structure* —
    which run was a heading, where a list began — is what `layout.py` already
    decides from the model's own markdown; this is only *appearance*: the
    handful of visual facts that make two documents in the same family look
    related.
    """

    page_width: float
    page_height: float
    margin_left: float
    margin_top: float
    margin_right: float
    margin_bottom: float
    body_family: str  # "Helvetica" | "Times-Roman" | "Courier"
    body_size: float
    heading_family: str
    heading_size: float
    text_colour: RGB
    heading_colour: RGB
    background_colour: RGB
    rule_colour: RGB


#: The look of a document with no template behind it: paper-white, a body face
#: every viewer already has, and colour spent on headings alone. Not
#: `layout.py`'s dark, chat-matching palette — a file meant to be handed to
#: someone else is not a screenshot of the console it was written in. Sizes
#: match `layout.py`'s existing absolute scale exactly (body 11, heading-1 17):
#: this is the anchor `pdf.py`'s scaling is relative to, so the default theme
#: reproduces today's proportions untouched, on a different palette.
DEFAULT = StyleProfile(
    page_width=612.0, page_height=792.0,
    margin_left=56.0, margin_top=56.0, margin_right=56.0, margin_bottom=56.0,
    body_family="Helvetica", body_size=11.0,
    heading_family="Helvetica", heading_size=17.0,
    text_colour=(0.10, 0.10, 0.10),
    heading_colour=(0.14, 0.12, 0.45),
    background_colour=(1.0, 1.0, 1.0),
    rule_colour=(0.82, 0.82, 0.85),
)

_SERIF = ("times", "georgia", "garamond", "cambria", "book", "minion",
          "palatino", "caslon", "baskerville", "serif", "roman")
_MONO = ("courier", "mono", "consolas", "menlo", "andale", "code")


def _family_of(fontname: str) -> str:
    """The closest base-14 family for a font pdfplumber names.

    **Never the template's actual font.** Embedding it is a real feature and a
    real amount of work — parsing and re-embedding a font program is its own
    project, not a rung up from this one. Serif and monospace are named
    explicitly by substring; everything else — every sans face a template is
    likely to use, including ones never seen before — falls to Helvetica.
    Robust to a "-Bold"/"-Italic" suffix and a subset tag
    (`ABCDEF+TimesNewRomanPS-BoldMT`), since the substring check does not care
    what comes before or after the family name.
    """
    lowered = fontname.lower()
    if any(mark in lowered for mark in _MONO):
        return "Courier"
    if any(mark in lowered for mark in _SERIF):
        return "Times-Roman"
    return "Helvetica"


def _normalise(value: Any) -> RGB | None:
    """pdfplumber's colour, as the 0-1 RGB `layout.py` already uses everywhere.

    A PDF fill colour is one float (greyscale), three (RGB) or four (CMYK),
    whichever colour space was current when the operator ran — pdfplumber
    reports whichever it was and does not normalise it. Once normalised here,
    the tuple is already what `reportlab.lib.colors.Color(*rgb)` wants.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return (float(value),) * 3
    if len(value) == 3:
        return (float(value[0]), float(value[1]), float(value[2]))
    if len(value) == 4:
        c, m, y, k = (float(v) for v in value)
        return (1 - min(1.0, c + k), 1 - min(1.0, m + k), 1 - min(1.0, y + k))
    return None


def extract_style(path: Path) -> StyleProfile | None:
    """The template's own look, or `None` when there is nothing safe to read.

    **Every failure returns `None` rather than raising**, unlike
    `documents.read_document`. Nothing is said to the person about this: a save
    offers a better look when a template can be read and the plain default
    otherwise, and there is exactly one way the caller acts on any failure. A
    broken PDF, an encrypted one, a scan with no text layer, or a page
    pdfplumber itself cannot parse are the same outcome here, for the same
    reason `documents._read_pdf` catches broadly — a malformed *template*
    producing a 500 would be a worse failure than the plain theme it can fall
    back to instead.
    """
    import pdfplumber

    try:
        with pdfplumber.open(path) as document:
            if not document.pages:
                return None
            page = document.pages[0]
            chars = page.chars
            if not chars:
                return None
            return _profile_from(page, chars)
    except Exception:
        return None


def _profile_from(page: Any, chars: list[dict[str, Any]]) -> StyleProfile:
    """One page's characters and shapes, reduced to a `StyleProfile`.

    Split out of `extract_style` so the try/except there covers only "can this
    library open the file" — a `KeyError` on a malformed style would otherwise
    hide behind the same broad except as a genuinely unreadable PDF.
    """
    sizes = Counter(round(float(c["size"]), 1) for c in chars)
    body_size = sizes.most_common(1)[0][0]
    body_chars = [c for c in chars if round(float(c["size"]), 1) == body_size]
    body_family = _family_of(_common_font(body_chars))

    larger = [c for c in chars if float(c["size"]) > body_size * 1.15]
    if larger:
        heading_size = max(float(c["size"]) for c in larger)
        heading_family = _family_of(_common_font(
            [c for c in larger if float(c["size"]) == heading_size]))
    else:
        heading_size, heading_family = body_size * 1.5, body_family

    text_colour = (_dominant_colour(c.get("non_stroking_color") for c in body_chars)
                   or DEFAULT.text_colour)
    heading_colour = (_dominant_colour(c.get("non_stroking_color") for c in larger)
                       or text_colour)

    width, height = float(page.width), float(page.height)
    margin_left = _clamped(min(float(c["x0"]) for c in chars), width)
    margin_right = _clamped(width - max(float(c["x1"]) for c in chars), width)
    margin_top = _clamped(min(float(c["top"]) for c in chars), height)
    margin_bottom = _clamped(height - max(float(c["bottom"]) for c in chars), height)

    return StyleProfile(
        page_width=width, page_height=height,
        margin_left=margin_left, margin_top=margin_top,
        margin_right=margin_right, margin_bottom=margin_bottom,
        body_family=body_family, body_size=float(body_size),
        heading_family=heading_family, heading_size=float(heading_size),
        text_colour=text_colour, heading_colour=heading_colour,
        background_colour=_background_colour(page) or DEFAULT.background_colour,
        rule_colour=_rule_colour(page) or heading_colour,
    )


def _clamped(margin: float, page_dimension: float) -> float:
    """A measured margin, bounded to a sixth of the page it sits on.

    A one- or two-line template — a cover page, a short letter — leaves its
    unused two-thirds of the page looking like margin to a measurement that
    only sees where the characters stopped. `DEFAULT`'s own 56pt is a fifth of
    a fifth of US Letter's shorter side; a sixth is generous beyond it without
    ever reproducing "half the page is margin" as a real document's layout.
    """
    return max(0.0, min(margin, page_dimension / 6))


def _common_font(chars: list[dict[str, Any]]) -> str:
    return str(Counter(c["fontname"] for c in chars).most_common(1)[0][0])


def _dominant_colour(values: Any) -> RGB | None:
    """The most common colour among `values`, normalised, or `None` from none."""
    found = [rgb for rgb in (_normalise(v) for v in values) if rgb is not None]
    return Counter(found).most_common(1)[0][0] if found else None


def _background_colour(page: Any) -> RGB | None:
    """A full-bleed fill behind everything else, if the template painted one.

    Only a rect covering nearly the whole page counts — a template's decorative
    sidebar or table shading is not its *background*, and reading one as such
    would come out one solid colour.
    """
    candidates = [
        r for r in page.rects
        if r.get("width", 0) > page.width * 0.9 and r.get("height", 0) > page.height * 0.9
    ]
    if not candidates:
        return None
    return _normalise(candidates[0].get("non_stroking_color"))


def _rule_colour(page: Any) -> RGB | None:
    """A thin horizontal rule's own colour, for headings to borrow.

    Thin and wide is the signature of a hairline under a heading rather than a
    table border or a filled box — the same distinction `_background_colour`
    draws in the other direction.
    """
    thin = [
        shape for shape in (*page.rects, *page.lines)
        if shape.get("height", shape.get("width", 0)) <= 3
        and shape.get("width", 0) > page.width * 0.4
    ]
    if not thin:
        return None
    colour = thin[0].get("non_stroking_color") or thin[0].get("stroking_color")
    return _normalise(colour)


__all__ = ["StyleProfile", "DEFAULT", "extract_style"]
