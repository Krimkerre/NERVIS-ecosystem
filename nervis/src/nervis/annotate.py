"""A copy of the attached document with this reply's comments placed in it.

**The model never retypes the document.** That was the design this replaces:
"save the last reply" asked a model to reproduce a forty-two-page blueprint
plus its comments in one reply, and it did what any model does with ninety-
seven thousand characters it cannot retype — it wrote `[Original intact]`
where the original should have been, and the saved file was placeholders. A
model quotes reliably where it does not retype reliably, so the reply carries
comments only, each one anchored by a short line quoted verbatim from the
document, and NERVIS — which holds the original — does the placing.

**A PDF cannot be reflowed, so the comments go beside the text, not into
it.** Nothing inserts a paragraph into a fixed-layout page. The first version
of this put each comment on its own page after the page it quoted, which
kept the original intact and read as an appendix; the operator wanted the
comments *with* the text. So each commented page is now a reviewer's copy:
the original page scaled to two-thirds width and set left, byte for byte,
and the comments in a column to its right, each one drawn level with the
passage it quotes and joined to it by a hairline. Every comment is also a
real PDF comment — a sticky note at the quoted passage — so a viewer that
shows those (Preview, Acrobat) shows them too. A comment the column has no
room for continues on a page inserted straight after, under a heading that
says so, rather than being shrunk until it fits or dropped.

A text original — `.md`, `.txt` — *is* reflowable, so there the comment goes
straight under the paragraph it quotes. Anything the model wrote without a
quote is not lost either way: it lands at the end, under its own heading,
rather than being placed by guesswork.
"""

from __future__ import annotations

import io
import re
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape

import pdfplumber
from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from pypdf.annotations import Popup
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    FloatObject,
    IndirectObject,
    NameObject,
    NumberObject,
    StreamObject,
    TextStringObject,
)
from reportlab.lib.colors import Color
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from nervis import layout, pdf
from nervis.style import DEFAULT, StyleProfile


#: One comment, as the model is asked to write it: a quoted line, then the
#: comment. `anchor` is empty for text that arrived with no quote at all.
@dataclass(frozen=True)
class Comment:
    anchor: str
    text: str


#: Where a comment's quote was found on a page: `(top, x0)` in the page's own
#: y-down points, or nothing when the words could not be found even though the
#: page's text matched.
Located = tuple[Comment, tuple[float, float] | None]

#: A sticky note to attach: its rectangle in the composed page's coordinates,
#: and its text.
Pin = tuple[tuple[float, float, float, float], str]

#: How far a quoted anchor is trusted before it is shortened. A model quoting a
#: heading gets it exactly; one quoting a sentence sometimes paraphrases the
#: tail, and the first thirty characters are usually still verbatim.
_ANCHOR_PREFIX = 30

#: The shortest thing worth searching a document for. "the" is on every page.
_ANCHOR_FLOOR = 8

_QUOTE_LINE = re.compile(r"^\s*>\s?(.*)$")

#: A model naming a heading and then a topic under it — "Adjacent product
#: expansions: Inbox and Reusable Recipes" — where only the heading is in the
#: document. Measured on the blueprint: twenty-one of forty-four anchors were
#: this shape, and every one of them fell to the end.
_TOPIC_SEPARATOR = re.compile(r"\s*(?::|—|–|\s-\s)\s*")

# The reviewer's-copy geometry, in points. Two-thirds keeps a letter or A4
# page legible and leaves a column about a third of the width — forty-odd
# characters a line at this size, which is a margin note rather than an essay.
# Anything longer than the column can hold continues on its own page.
SCALE = 0.66
GUTTER = 10.0
EDGE = 18.0
PAD = 5.0
GAP = 7.0
RULE = 2.0
RULE_INSET = 8.0
NOTE_SIZE = 8.5
NOTE_LEADING = 10.5
LABEL_SIZE = 7.5
LABEL_LEADING = 9.0

#: The comment icon's box, in points. Every sticky note is this size so one
#: appearance can serve all of them.
BUBBLE = 20.0


def parse_comments(reply: str) -> list[Comment]:
    """The comments in a reply, in the order they were written.

    A comment starts at a `>` line — the quote — and runs until the next one.
    Consecutive `>` lines are one quote, the way markdown reads them. Text
    before the first quote, or a reply with no quotes at all, is one comment
    with no anchor: it will be placed at the end rather than dropped, because
    a model that ignored the format still said something the person asked for.
    """
    comments: list[Comment] = []
    anchor: list[str] = []
    body: list[str] = []
    in_quote = False
    started = False

    def flush() -> None:
        text = "\n".join(body).strip()
        quoted = " ".join(anchor).strip().strip("\"'“”‘’")
        if text or quoted:
            comments.append(Comment(anchor=quoted, text=text))

    for line in (reply or "").splitlines():
        quoted = _QUOTE_LINE.match(line)
        if quoted:
            if not in_quote:
                if started:
                    flush()
                anchor, body, in_quote, started = [], [], True, True
            anchor.append(quoted.group(1))
            continue
        in_quote = False
        started = True
        body.append(line)
    if started:
        flush()
    return comments


def has_anchors(reply: str) -> bool:
    """Whether a reply carries at least one comment with a quote to place it by."""
    return any(comment.anchor for comment in parse_comments(reply))


def _normalised(text: str) -> str:
    return re.sub(r"\s+", " ", text).casefold().strip()


def _attempts(anchor: str) -> Iterator[str]:
    """What to search for, most exact first, nothing shorter than the floor.

    The quote as written; then its opening characters, for a tail the model
    paraphrased; then the part before a colon or dash, for a heading the model
    extended with a topic of its own. Each is a weaker claim than the last,
    which is why the order matters and why nothing looser follows.
    """
    wanted = _normalised(anchor)
    seen: set[str] = set()
    head = _TOPIC_SEPARATOR.split(wanted, maxsplit=1)[0]
    for attempt in (wanted, wanted[:_ANCHOR_PREFIX], head, head[:_ANCHOR_PREFIX]):
        attempt = attempt.strip()
        if len(attempt) >= _ANCHOR_FLOOR and attempt not in seen:
            seen.add(attempt)
            yield attempt


def find_in(anchor: str, passages: list[str]) -> int | None:
    """Which passage the anchor was quoted from, or `None`.

    Whitespace and case are ignored on both sides because a PDF's extracted
    text breaks lines where the page did, not where the sentence did. Tried
    exactly first, then by its opening characters, then by the heading before
    a topic separator — and never by anything looser: a comment placed on the
    wrong page is worse than one placed at the end with an honest heading over
    it.

    **The last passage it appears in, not the first.** Measured on the
    blueprint this was built for: forty-four comments anchored on section
    headings, and nearly all of them landed after page two — the table of
    contents, where every heading appears once before it appears at its
    section. A heading's last appearance is the section itself, or its final
    page under a running header, and either is where "under the section"
    means. A quoted sentence from a passage appears once, so for it the two
    rules agree.
    """
    for attempt in _attempts(anchor):
        found = [index for index, passage in enumerate(passages)
                 if attempt in _normalised(passage)]
        if found:
            return found[-1]
    return None


def _placed_by(
    comments: list[Comment], passages: list[str]
) -> tuple[dict[int, list[Comment]], list[Comment]]:
    """Each comment under the passage it quotes, and the ones with no home."""
    placed: dict[int, list[Comment]] = {}
    loose: list[Comment] = []
    for comment in comments:
        where = find_in(comment.anchor, passages) if comment.anchor else None
        if where is None:
            loose.append(comment)
        else:
            placed.setdefault(where, []).append(comment)
    return placed, loose


def _as_markdown(comments: list[Comment]) -> str:
    parts: list[str] = []
    for comment in comments:
        if comment.anchor:
            parts.append(f"**On:** “{comment.anchor}”")
        parts.append(comment.text)
    return "\n\n".join(part for part in parts if part)


def _located(page: Any, comments: list[Comment]) -> list[Located]:
    """Where on this page each comment's quote sits.

    Matched over the page's words rather than its text so a quote that the
    extraction broke across a line still finds its first word, which is the
    one whose height the note is drawn level with.
    """
    words = page.extract_words()
    texts = [_normalised(word["text"]) for word in words]
    starts: list[int] = []
    offset = 0
    for text in texts:
        starts.append(offset)
        offset += len(text) + 1
    joined = " ".join(texts)

    placed: list[Located] = []
    for comment in comments:
        hit: tuple[float, float] | None = None
        for attempt in _attempts(comment.anchor):
            at = joined.rfind(attempt)
            if at >= 0:
                word = words[bisect_right(starts, at) - 1]
                hit = (float(word["top"]), float(word["x0"]))
                break
        placed.append((comment, hit))
    return placed


def _bubble_ops() -> bytes:
    """The comment icon: NERVIS's own mark inside a speech bubble.

    **The letterhead, not a viewer's yellow note.** The mark is drawn by the
    same code the transcript export draws it with — `pdf._mark`, the diamond
    with the cyan core — so it is the logo and not a drawing of one. It sits
    in a rounded bubble with a tail at the lower left, which is what says
    "somebody said something here" the way a note icon does. Raw page
    operators rather than reportlab calls, because the same bytes serve twice:
    as the annotation's own appearance, which a viewer draws in place of its
    icon, and as ink on the margin copy's overlay, which every viewer shows.
    """
    b = BUBBLE
    # Filled with the dashboard's own page colour (`layout.PAPER`), so the
    # bubble reads as a piece of NERVIS on somebody else's page rather than a
    # white sticker; the edge and the mark are the letterhead's cyan.
    bubble = [
        pdf._round_rect(1.0, 5.0, b - 2.0, b - 6.0, 3.5, layout.PAPER),
        pdf._round_rect(1.0, 5.0, b - 2.0, b - 6.0, 3.5, layout.LOGO_EDGE, stroke=0.8),
        pdf._poly([(5.0, 5.4), (9.0, 5.4), (4.2, 1.2)], layout.PAPER),
        pdf._poly([(4.6, 5.0), (9.4, 5.0), (4.2, 1.2), (4.6, 5.0)], layout.LOGO_EDGE, stroke=0.8),
    ]
    # Joined by newlines, not concatenated: each helper's bytes end on an
    # operator with nothing after it, and `f` followed straight by `0.333`
    # reads as one token, `f0.333`, which is not an operator — the whole
    # stream then draws nothing. Found by rendering the ops alone.
    return b"\n".join(bubble + pdf._mark(b / 2.0, 12.0, 4.4, glow=False))


def _appearance(writer: PdfWriter) -> IndirectObject:
    """One form XObject drawing the bubble, shared by every note in the file."""
    stream = StreamObject()
    stream[NameObject("/Type")] = NameObject("/XObject")
    stream[NameObject("/Subtype")] = NameObject("/Form")
    stream[NameObject("/BBox")] = ArrayObject(
        [FloatObject(0.0), FloatObject(0.0), FloatObject(BUBBLE), FloatObject(BUBBLE)]
    )
    stream.set_data(_bubble_ops())
    return writer._add_object(stream)


def _pinned(
    writer: PdfWriter, page_index: int, pins: list[Pin], appearance: IndirectObject
) -> None:
    """Attach each comment to the page as a stamp wearing the bubble, with a
    popup carrying the text.

    **A stamp, not a sticky note.** The first version used `/Text` — the PDF
    sticky note — with the bubble as its appearance. Apple's Preview draws
    its own icon for a `/Text` note and ignores the appearance entirely,
    which is what the operator saw: a faint grey box where the logo should
    be, and Preview then rewrote the note on its own terms when it was
    clicked. Every viewer draws a `/Stamp` from its appearance, because a
    stamp *is* its appearance; the comment rides in `/Contents` and in a
    `/Popup` child, which is how Acrobat, Preview and the browsers show a
    stamp's note when it is clicked.
    """
    for (x0, y0, x1, y1), text in pins:
        stamp = writer.add_annotation(page_index, DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Stamp"),
            NameObject("/Name"): NameObject("/NervisComment"),
            NameObject("/Rect"): ArrayObject(
                [FloatObject(x0), FloatObject(y0), FloatObject(x1), FloatObject(y1)]
            ),
            NameObject("/Contents"): TextStringObject(text),
            NameObject("/T"): TextStringObject("NERVIS"),
            NameObject("/F"): NumberObject(4),
            NameObject("/AP"): DictionaryObject({NameObject("/N"): appearance}),
        }))
        popup = writer.add_annotation(page_index, Popup(
            rect=(x1 + 4.0, y0 - 110.0, x1 + 4.0 + 220.0, y1), parent=stamp, open=False,
        ))
        stamp[NameObject("/Popup")] = popup.indirect_reference


def _paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(pdf._sanitised(text)).replace("\n", "<br/>"), style)


def _margin_page(
    page: PageObject, located: list[Located], profile: StyleProfile
) -> tuple[PageObject, list[Comment], list[Pin], str]:
    """One reviewer's page: the original scaled left, the comments beside it.

    Returns the composed page, the comments the column had no room for, the
    sticky notes to attach, and the characters Latin-1 could not carry.
    """
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)
    column_x = width * SCALE + GUTTER
    column_w = width - column_x - EDGE
    accent = Color(*profile.heading_colour)
    ink = Color(*profile.text_colour)
    note_style = ParagraphStyle(
        "note", fontName=pdf._base14_face(profile.body_family, False, False),
        fontSize=NOTE_SIZE, leading=NOTE_LEADING, textColor=ink,
    )
    label_style = ParagraphStyle(
        "label", fontName=pdf._base14_face(profile.body_family, True, False),
        fontSize=LABEL_SIZE, leading=LABEL_LEADING, textColor=accent,
    )

    overlay = io.BytesIO()
    c = canvas.Canvas(overlay, pagesize=(width, height))
    c.setStrokeColor(accent)
    c.setFillColor(accent)
    cursor = EDGE
    overflow: list[Comment] = []
    pins: list[Pin] = []
    unsupported = ""

    # Top to bottom by where the quote sits; a comment with no found position
    # goes at the top, above anything positioned.
    ordered = sorted(located, key=lambda item: item[1][0] if item[1] else -1.0)
    for comment, where in ordered:
        wanted_top = where[0] * SCALE if where else EDGE
        top = max(wanted_top, cursor)
        label = _paragraph(f"On “{comment.anchor}”", label_style) if comment.anchor else None
        body = _paragraph(comment.text, note_style)
        label_h = label.wrap(column_w - RULE_INSET, height)[1] if label else 0.0
        body_h = body.wrap(column_w - RULE_INSET, height)[1]
        block = PAD + label_h + (2.0 if label else 0.0) + body_h + PAD
        if top + block > height - EDGE:
            overflow.append(comment)
            continue

        unsupported += pdf._unsupported(comment.anchor + comment.text)
        c.rect(column_x, height - (top + block), RULE, block, stroke=0, fill=1)
        y = top + PAD
        if label:
            label.drawOn(c, column_x + RULE_INSET, height - (y + label_h))
            y += label_h + 2.0
        body.drawOn(c, column_x + RULE_INSET, height - (y + body_h))
        if where:
            anchor_y = height - where[0] * SCALE
            c.setLineWidth(0.5)
            c.line(width * SCALE + 1.0, anchor_y, column_x - 1.0, height - (top + PAD + 4.0))
            box = (width * SCALE - BUBBLE - 2.0, anchor_y - BUBBLE / 2.0)
            pins.append(((box[0], box[1], box[0] + BUBBLE, box[1] + BUBBLE),
                         f"On “{comment.anchor}”\n\n{comment.text}"))
        else:
            box = (width * SCALE - BUBBLE - 2.0, height - top - BUBBLE)
            pins.append(((box[0], box[1], box[0] + BUBBLE, box[1] + BUBBLE), comment.text))
        # The bubble is drawn on the page too, so the mark is there in a viewer
        # that ignores an annotation's own appearance and draws its own icon.
        c.saveState()
        c.addLiteral(f"1 0 0 1 {box[0]:g} {box[1]:g} cm")
        c.addLiteral(_bubble_ops().decode("latin-1"))
        c.restoreState()
        cursor = top + block + GAP
    c.save()

    composed = PageObject.create_blank_page(width=width, height=height)
    composed.merge_transformed_page(
        page, Transformation().scale(SCALE, SCALE).translate(0.0, height - height * SCALE)
    )
    composed.merge_page(PdfReader(io.BytesIO(overlay.getvalue())).pages[0])
    return composed, overflow, pins, unsupported


def _text_edges(page: Any) -> tuple[float, float]:
    """Where the page's text starts and stops, left to right, in points."""
    words = page.extract_words()
    if not words:
        return 0.0, 0.0
    return min(float(w["x0"]) for w in words), max(float(w["x1"]) for w in words)


def _margin_x(width: float, edges: tuple[float, float]) -> float:
    """The bubble's left edge, in the page's own margin, never over text.

    The left margin if it is wide enough, centred; the right margin if only
    that is; and only when neither is — a page printed to its edges — the
    left edge of the page, where it covers the least.
    """
    left, right = edges
    if left >= BUBBLE + 4.0:
        return (left - BUBBLE) / 2.0
    if width - right >= BUBBLE + 4.0:
        return right + (width - right - BUBBLE) / 2.0
    return 2.0


def _note_pins(page: PageObject, located: list[Located], edges: tuple[float, float]) -> list[Pin]:
    """Comments on an untouched page, each level with its quoted passage.

    The page itself is not changed at all — this is the copy for somebody who
    wants their document exactly as it was and the comments where a viewer
    shows comments. The bubble sits in the margin beside the line it belongs
    to, never over the text; a comment whose words could not be found sits at
    the top of that margin rather than nowhere.
    """
    height = float(page.mediabox.height)
    x = _margin_x(float(page.mediabox.width), edges)
    pins: list[Pin] = []
    for comment, where in located:
        if where:
            top = where[0]
            pins.append(((x, height - top - BUBBLE + 4.0, x + BUBBLE, height - top + 4.0),
                         f"On “{comment.anchor}”\n\n{comment.text}"))
        else:
            pins.append(((x, height - EDGE - BUBBLE, x + BUBBLE, height - EDGE), comment.text))
    return pins


def extracted_text(original: bytes) -> str:
    """A PDF's text, page after page, for the copy that gives the layout up."""
    with pdfplumber.open(io.BytesIO(original)) as plumbed:
        return "\n\n".join(page.extract_text() or "" for page in plumbed.pages)


def _appended(writer: PdfWriter, title: str, markdown: str, profile: StyleProfile) -> str:
    """Pages rendered from markdown, added to the writer; what Latin-1 lost."""
    note = pdf.render(title, f"# {title}\n\n{markdown}", profile)
    for extra in PdfReader(io.BytesIO(note.data)).pages:
        writer.add_page(extra)
    return note.unsupported


def annotate_pdf(
    original: bytes, comments: list[Comment], style: StyleProfile | None, mode: str = "margin"
) -> pdf.Rendered:
    """The original PDF with the comments in it: every page kept either way.

    `margin` — each commented page scaled left with its comments drawn beside
    it, and as sticky notes. `notes` — the pages untouched, the comments as
    sticky notes only. Comments with no page go at the end in both.
    """
    profile = style or DEFAULT
    reader = PdfReader(io.BytesIO(original))
    writer = PdfWriter()
    appearance = _appearance(writer)
    unsupported = ""
    with pdfplumber.open(io.BytesIO(original)) as plumbed:
        placed, loose = _placed_by(comments, [page.extract_text() or "" for page in plumbed.pages])
        for index, page in enumerate(reader.pages):
            if index not in placed:
                writer.add_page(page)
                continue
            located = _located(plumbed.pages[index], placed[index])
            overflow: list[Comment] = []
            if mode == "notes":
                edges = _text_edges(plumbed.pages[index])
                composed, pins, lost = page, _note_pins(page, located, edges), ""
            else:
                composed, overflow, pins, lost = _margin_page(page, located, profile)
            unsupported += lost
            writer.add_page(composed)
            _pinned(writer, len(writer.pages) - 1, pins, appearance)
            if overflow:
                unsupported += _appended(
                    writer, f"Comments on page {index + 1}, continued",
                    _as_markdown(overflow), profile,
                )
    if loose:
        unsupported += _appended(writer, "Further comments", _as_markdown(loose), profile)

    out = io.BytesIO()
    writer.write(out)
    return pdf.Rendered(data=out.getvalue(), pages=len(writer.pages), unsupported=unsupported)


def annotate_text(original: str, comments: list[Comment]) -> str:
    """A text original with each comment under the paragraph it quotes."""
    paragraphs = re.split(r"\n\s*\n", original.strip()) if original.strip() else []
    placed, loose = _placed_by(comments, paragraphs)

    parts: list[str] = []
    for index, paragraph in enumerate(paragraphs):
        parts.append(paragraph)
        for comment in placed.get(index, []):
            # A plain paragraph rather than a `>` blockquote: `layout.parse`
            # has no blockquote kind, so a `>` would be drawn literally when
            # this text is rendered to PDF, and read as one in `.md` either way.
            parts.append(f"**Comment:** {comment.text}")
    if loose:
        parts.append("## Further comments")
        parts.extend(_as_markdown([comment]) for comment in loose)
    return "\n\n".join(parts)
