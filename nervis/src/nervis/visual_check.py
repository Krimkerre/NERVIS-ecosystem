"""Whether a generated PDF's first page actually looks right.

Not by re-deriving rules `pdf.py` and `style.py` already encode — font and
colour extraction can tell you a document borrowed the right typeface; it
cannot tell you a code block ran off the page edge or a heading collided with
the paragraph under it. This module rasterises the page and hands the picture
to a model that can see, the same way a person would glance at the result
before trusting it.

This is an extra glance at output already written to disk, not a gate a save
depends on: everything here fails open. `first_page` returns `None` for any
PDF it cannot render into something worth looking at; `verdict_of` returns
`None` for any reply that does not clearly say something is wrong, including
one that does not follow the shape asked for. Both choices mean a save can
never be blocked, delayed indefinitely, or reported as broken by this code —
at worst, the extra glance quietly does not happen.
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass

import pdfplumber

# 100dpi is enough to read a heading's weight and spot a run-off-the-edge code
# block — a QA glance, not a proofread, and print resolution would only add
# tokens and latency to a check that is already looking at every save.
RESOLUTION = 100

PROMPT = (
    "This is the first page of a generated PDF, rendered as an image. Check "
    "only whether it rendered correctly — ignore whether the writing itself "
    "is any good. Is anything visually broken: text or a code block cut off "
    "at the page edge, lines overlapping, a heading crowded against the text "
    "below it, a badly broken layout? Answer with exactly one word first, "
    '"Yes" or "No". If "Yes", follow it with a colon and one short sentence '
    "naming the specific defect."
)


@dataclass(frozen=True)
class Snapshot:
    """One rendered page, ready to hand to a model that can see."""

    data_url: str


def first_page(pdf_bytes: bytes) -> Snapshot | None:
    """The first page of `pdf_bytes` as an inline PNG data URL, or `None`.

    `None` covers every way this can fail to produce something worth looking
    at — no pages, a corrupt render, `pdfplumber` raising on a malformed file
    — the same broad catch `style.extract_style` uses, and for the same
    reason stated in this module's own docstring.
    """
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as opened:
            if not opened.pages:
                return None
            image = opened.pages[0].to_image(resolution=RESOLUTION).original
    except Exception:  # noqa: BLE001 — any failure here means "skip the glance"
        return None
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return Snapshot(data_url=f"data:image/png;base64,{encoded}")


def verdict_of(reply: str) -> str | None:
    """The defect a model named, or `None` — including when it did not
    answer in the shape asked for.

    **Checked by first word, not by exact phrasing.** `commands.told` already
    exists because a model asked for one exact string does not reliably send
    it — a reasoning model prepends a paragraph, a small local model adds
    trailing punctuation or drops the colon. Reading only the first word as
    "yes" or "no" survives all of that; requiring the full line to match
    would not.

    Failing open on an unparseable reply, on purpose: this is an optional
    extra glance (this module's own docstring), so a reply that does not
    parse is treated the same as "looked fine" rather than surfaced as a
    defect it never actually named.
    """
    said = reply.strip()
    if not said:
        return None
    first_token = said.split(None, 1)[0]
    if first_token.strip(".,:;!\"'").lower() != "yes":
        return None
    rest = said[len(first_token):].strip(" :,-")
    return rest or "flagged as visually broken, but did not say how"
