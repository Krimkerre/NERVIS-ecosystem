"""A PDF with a layout, written without a dependency.

**Why not a library.** The ladder says never add one for what a few lines can
do. The base-14 fonts need no embedding, so Helvetica, its bold, and Courier for
code are available by name — which is most of what a typeset summary needs. What
this does not attempt is everything else about PDF: images, colour spaces,
embedded fonts, transparency.

**What it can do**, driven by `layout.py`: headings at three sizes, bold runs
inside a line, bullets and numbered items with hanging indents, code in a
monospace face, and page breaks that do not strand a heading at the foot of a
page.

**What it cannot**, stated here rather than discovered: no tables, no images, no
links, no nested lists, and WinAnsi only — a single-byte encoding cannot express
the rest, and characters it cannot carry are reported rather than replaced with
a `?` nobody can see.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from nervis import layout
from nervis.layout import Block, Kind, Span, Style, parse, style_for

#: US Letter at 72 dpi, the format's own unit.
PAGE_WIDTH, PAGE_HEIGHT = 612, 792
# Floats throughout: a point is a fractional unit and 9.5pt code says so.
MARGIN = 56

#: Leading as a multiple of the size. Tighter than 1.4 looks cramped in
#: Helvetica; looser wastes a page on a two-page summary.
LEADING = 1.36

TOP = PAGE_HEIGHT - MARGIN
#: Where the text stops. Above the footer rather than at the margin, or the last
#: line of a page lands on the page number.
BOTTOM = MARGIN + 18
USABLE = PAGE_WIDTH - 2 * MARGIN

#: Font resource names, in the order they are declared in the page's resources.
REGULAR, BOLD, MONO = "F1", "F2", "F3"

#: The single-byte encoding the fonts are declared with.
#:
#: **WinAnsi rather than Latin-1, and the difference is visible on every page.**
#: The base-14 fonts default to an encoding with no em dash, no curly quotes and
#: no ellipsis — so a reply reading "the reseller — the same model" came out as
#: "the reseller ? the same model", and a `?` where punctuation should be is a
#: defect the writer can see and the reader cannot explain. Those characters are
#: exactly what a language model writes; they are not an edge case here, they
#: are most sentences.
#:
#: `cp1252` is Python's name for the same set. Declaring the encoding on the
#: font and encoding the bytes to match is the whole of it.
ENCODING = "cp1252"

#: Average glyph width as a fraction of the point size, per face.
#:
#: **An approximation used only to decide where to wrap.** Real width needs the font's
#: metrics table; Helvetica's average lowercase advance is near 0.5em and its
#: capitals nearer 0.72, so this errs wide and a line of capitals breaks a word
#: early rather than running off the page. Courier is monospaced at exactly 0.6.
#:
#: Nothing is *positioned* from these. Drawing asks the viewer to advance the
#: pen, which it does from the font's real metrics — see `_draw`.
WIDTHS = {REGULAR: 0.52, BOLD: 0.55, MONO: 0.60}


@dataclass(frozen=True)
class Rendered:
    """The bytes, and what had to be done to produce them."""

    data: bytes
    pages: int
    #: Characters Latin-1 could not carry. Reported rather than substituted: a
    #: silent `?` in somebody's report is a defect only the writer can see.
    unsupported: str


def _escape(text: str) -> bytes:
    """One run as a PDF string literal.

    Backslash first — escaping the parentheses before the backslashes would
    double-escape the backslashes this adds.
    """
    out = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return out.encode(ENCODING, errors="replace")


def _fits(spans: tuple[Span, ...], size: float, width: float, mono: bool,
          bold: bool = False, tracking: float = 0.0) -> bool:
    """Whether these runs fit one line of `width` points at `size`."""
    return _measure(spans, size, mono, bold, tracking) <= width


def _measure(spans: tuple[Span, ...], size: float, mono: bool, bold: bool = False,
             tracking: float = 0.0) -> float:
    """The width these runs occupy, in points.

    Takes the block's weight as well as each span's, because a bold heading is
    wider than the same words plain — measuring it as regular wraps it a word
    late and it runs past the margin.
    """
    total = 0.0
    for span in spans:
        total += len(span.text) * size * WIDTHS[_face(bold or span.bold, mono)]
        total += len(span.text) * tracking
    return total


def _wrap_spans(
    spans: tuple[Span, ...], size: float, width: float, mono: bool, bold: bool = False,
    tracking: float = 0.0
) -> list[tuple[Span, ...]]:
    """Break runs into lines, keeping each run's weight across the break.

    Word-by-word rather than character-by-character, so a bold phrase spanning a
    line break stays bold on both lines — the naive version resets weight at
    every wrap and produces a sentence that changes voice mid-air.
    """
    lines: list[tuple[Span, ...]] = []
    current: list[Span] = []

    for span in spans:
        for word in span.text.split(" "):
            if not word:
                continue
            candidate = current + [Span(word, span.bold)]
            joined = tuple(candidate)
            if current and not _fits(joined, size, width, mono, bold, tracking):
                lines.append(tuple(current))
                current = [Span(word, span.bold)]
            else:
                # Re-join adjacent runs of one weight so the drawn output has
                # one `Tj` per weight rather than one per word.
                if current and current[-1].bold == span.bold:
                    current[-1] = Span(f"{current[-1].text} {word}", span.bold)
                else:
                    if current:
                        # A run of its own rather than riding on a neighbour.
                        # At a weight boundary one side has to carry the space,
                        # and appending it to the previous run put a trailing
                        # space *inside* the bold phrase — so `**12%**` drew as
                        # `(12% )`, a bold space, and the phrase stopped being
                        # exactly the characters the author marked.
                        current.append(Span(" ", current[-1].bold))
                    current.append(Span(word, span.bold))
    if current:
        lines.append(tuple(current))
    return lines or [(Span(""),)]


def _face(bold: bool, mono: bool) -> str:
    """Which font resource a run is drawn with.

    One place, because the weight comes from two directions — the block's style
    (a heading is bold) and the span's own marks (`**this**` is bold) — and
    resolving that in each caller is how a heading ends up drawn in the regular
    face while claiming to be bold, which is exactly what happened.
    """
    if mono:
        return MONO
    return BOLD if bold else REGULAR


def _colour(rgb: tuple[float, float, float]) -> bytes:
    """A fill colour, in PDF's own 0-1 RGB.

    Set inside each text object rather than once per page: colour is graphics
    state and persists across `BT`/`ET`, so a heading that set it and did not
    reset it would tint every paragraph after it — including on the next page,
    since the state carries across content streams within a page tree.
    """
    return f"{rgb[0]:.3g} {rgb[1]:.3g} {rgb[2]:.3g} rg".encode("latin-1")


def _rect(x: float, y: float, width: float, height: float,
          rgb: tuple[float, float, float]) -> bytes:
    """One filled rectangle: a hairline, or the tint behind a code block."""
    return _colour(rgb) + f" {x:g} {y:g} {width:g} {height:g} re f".encode("latin-1")


def _draw(spans: tuple[Span, ...], x: float, y: float, size: float, mono: bool,
          bold: bool = False, rgb: tuple[float, float, float] = (0.0, 0.0, 0.0),
          tracking: float = 0.0) -> list[bytes]:
    """One line of runs, as a single text object.

    **The viewer advances the pen, not this code.** Inside one `BT`/`ET`,
    consecutive `Tj` operators move the text position by the glyphs' real widths
    — which the viewer knows exactly from the font, and this module only
    approximates. Only the first run needs a position.

    An earlier version placed every run absolutely from `WIDTHS`, and because
    that estimate errs wide, every bold phrase came out followed by a visible
    gap: *"Revenue fell    12%    against Q2"*. The estimate is still needed to
    decide where to **wrap**, and erring wide there is harmless — it breaks a
    line one word early, which nobody can see.
    """
    drawn = [span for span in spans if span.text]
    if not drawn:
        return []

    face = _face(bold or drawn[0].bold, mono)
    # `Tc` is character spacing, and it is graphics state like colour — set on
    # every object rather than once, or a tracked label spaces out everything
    # drawn after it.
    out = [_colour(rgb) + f" BT {tracking:g} Tc /{face} {size:g} Tf "
           f"{x:g} {y:g} Td (".encode("latin-1")
           + _escape(drawn[0].text) + b") Tj"]
    for span in drawn[1:]:
        want = _face(bold or span.bold, mono)
        if want != face:
            out.append(f"/{want} {size:g} Tf".encode("latin-1"))
            face = want
        out.append(b"(" + _escape(span.text) + b") Tj")
    out.append(b"ET")
    return [b" ".join(out)]


def _block(
    lines: list[tuple[Span, ...]], block: Block, style: Any, top: float, marker_width: float
) -> list[bytes]:
    """One block's tint, marker and text, in painting order.

    Split out of `render` for the complexity gate, and it reads better for it:
    the loop above is now about *where a block goes* and this is about *what a
    block looks like*, which were two jobs in one function.
    """
    out: list[bytes] = []
    leading = style.size * LEADING
    y = top
    for line_number, line in enumerate(lines):
        x = MARGIN + style.indent + (marker_width if block.marker else 0)
        # The tint goes down before the glyphs do — PDF paints in order, and a
        # rectangle drawn after its text hides it.
        if style.tint:
            out.append(_rect(MARGIN, y - leading * 0.28, USABLE, leading, style.tint))
        if block.marker and line_number == 0:
            out.extend(_draw((Span(block.marker),), MARGIN + style.indent, y,
                             style.size, False, rgb=style.colour))
        out.extend(_draw(line, x, y, style.size, style.monospace, style.bold,
                         rgb=style.colour, tracking=style.tracking))
        y -= leading
    return out


def _unsupported(text: str) -> str:
    return "".join(sorted({c for c in text if c.encode(ENCODING, "ignore") == b""}))


def render(title: str, text: str) -> Rendered:
    """`text`, laid out, as a PDF.

    Two passes by construction: blocks are placed into pages first, then the
    pages are turned into objects. The cross-reference table needs each object's
    byte offset, and those are only knowable once the bytes before them exist.
    """
    blocks = parse(text)
    pages: list[list[bytes]] = []
    current: list[bytes] = []
    y: float = TOP

    for index, block in enumerate(blocks):
        style = style_for(block)
        leading = style.size * LEADING

        if block.kind is Kind.BLANK:
            y -= leading * 0.55
            continue

        marker_width = 0.0
        if block.marker:
            marker_width = max(style.indent, len(block.marker) * style.size * 0.62)

        spans = block.spans
        if style.upper:
            spans = tuple(Span(span.text.upper(), span.bold) for span in spans)

        width = USABLE - style.indent - (marker_width if block.marker else 0)
        lines = _wrap_spans(spans, style.size, width, style.monospace, style.bold,
                            style.tracking)

        # A heading alone at the foot of a page reads as a caption for nothing.
        # It moves with the first line of what follows it.
        needed = leading * (len(lines) + (1 if block.kind is Kind.HEADING else 0))
        if y - style.space_above - needed < BOTTOM and current:
            pages.append(current)
            current, y = [], float(TOP)

        y -= style.space_above
        # A hairline across the measure, above the space rather than in it, so a
        # heading sits under its own rule rather than on top of one.
        if style.rule_above and current:
            current.append(_rect(MARGIN, y + style.rule_above, USABLE, 0.6, layout.RULE))
        current.extend(_block(lines, block, style, y, marker_width))
        y -= leading * len(lines)
        del index

    if current or not pages:
        pages.append(current)

    return _assemble(pages, _unsupported(text + title))


def _footer(number: int, total: int) -> list[bytes]:
    """A hairline and a page number, in the machine's own voice.

    Monospaced and letterspaced because that is what every label on the screen
    this came from looks like, and because "3 / 7" set in a book face reads as
    a fraction rather than a position.
    """
    y = MARGIN * 0.55
    return [
        _rect(MARGIN, y + 13, USABLE, 0.6, layout.RULE),
        *_draw((Span("NERVIS"),), MARGIN, y, 7.5, mono=True,
               rgb=layout.MUTED, tracking=1.4),
        *_draw((Span(f"{number} / {total}"),), PAGE_WIDTH - MARGIN - 34, y, 7.5,
               mono=True, rgb=layout.MUTED, tracking=1.4),
    ]


def _assemble(pages: list[list[bytes]], unsupported: str) -> Rendered:
    """Pages of drawing operators as a finished file."""
    objects: list[bytes] = []
    # 1 catalog, 2 pages, 3-5 fonts, then a content stream and a page per sheet.
    first_content = 6
    content_ids = [first_content + n * 2 for n in range(len(pages))]
    page_ids = [first_content + 1 + n * 2 for n in range(len(pages))]

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>".encode("latin-1"))
    for base in (b"/Helvetica", b"/Helvetica-Bold", b"/Courier"):
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont " + base
                       + b" /Encoding /WinAnsiEncoding >>")

    for number, (sheet, content_id) in enumerate(zip(pages, content_ids), start=1):
        # **The ground first, and every page gets one.** A dark page is a
        # rectangle painted before anything else; without it the text would sit
        # on whatever the reader's viewer uses for paper, which for light text
        # is nothing at all.
        stream = b"\n".join([
            _rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, layout.PAPER),
            *sheet,
            *_footer(number, len(pages)),
        ])
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream"
        )
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /{REGULAR} 3 0 R /{BOLD} 4 0 R /{MONO} 5 0 R >> >> "
            f"/Contents {content_id} 0 R >>".encode("latin-1")
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, payload in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("latin-1") + payload + b"\nendobj\n"

    start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n"
        .encode("latin-1")
    )
    return Rendered(data=bytes(out), pages=len(pages), unsupported=unsupported)


__all__ = ["Rendered", "Turn", "render", "render_conversation", "Block"]


# ── A conversation, drawn the way the screen draws one ──────────────────────

#: How wide a bubble may be, as a fraction of the measure. `.bubble` says 78%.
BUBBLE_WIDTH = 0.78
#: `.bubble` padding: 10px 12px.
PAD_X, PAD_Y = 12.0, 10.0
#: `margin:7px 0` between bubbles.
BUBBLE_GAP = 7.0


@dataclass(frozen=True)
class Turn:
    """One thing somebody said, and which side it belongs on."""

    speaker: str
    body: str
    #: Right-aligned and blue, the way `.bubble.user` is. The person's own turns.
    mine: bool = False


def _laid_out(body: str, width: float) -> list[tuple[Style, tuple[Span, ...], str]]:
    """Every line of a turn, with the style and marker it is drawn under.

    Flattened here rather than in the drawing loop because a bubble has to be
    *measured* before it can be drawn — the rectangle goes down first, and its
    height is the sum of what has not been laid out yet.
    """
    out: list[tuple[Style, tuple[Span, ...], str]] = []
    for block in parse(body):
        style = style_for(block)
        if block.kind is Kind.BLANK:
            out.append((style, (), ""))
            continue
        spans = block.spans
        if style.upper:
            spans = tuple(Span(span.text.upper(), span.bold) for span in spans)
        inner = width - 2 * PAD_X - style.indent
        for number, line in enumerate(
            _wrap_spans(spans, style.size, inner, style.monospace, style.bold, style.tracking)
        ):
            out.append((style, line, block.marker if number == 0 else ""))
    return out


def _line_height(style: Style, spans: tuple[Span, ...]) -> float:
    """What one laid-out line costs vertically, blank lines included."""
    return float(style.size) * LEADING * (0.55 if not spans else 1.0)


def render_conversation(title: str, subtitle: str, turns: Sequence[Turn]) -> Rendered:
    """A transcript drawn as the chat window draws it.

    **Its own renderer rather than more markdown.** `render` lays out a
    document: headings, paragraphs, one column. A conversation is a different
    shape — bubbles of bounded width, one side each, sized to their contents —
    and expressing that as headings produced a report *about* a conversation
    rather than a picture of one.

    A bubble is measured before it is drawn, because the rectangle goes down
    first and its height is the sum of lines not yet laid out. A bubble too tall
    for what remains of a page is split rather than pushed whole: a long reply
    would otherwise leave most of a page empty and still not fit on the next.
    """
    pages: list[list[bytes]] = []
    current: list[bytes] = []
    y = float(TOP)

    for block in parse(f"# {title}"):
        style = style_for(block)
        y -= style.space_above
        current.extend(_block(_wrap_spans(
            tuple(Span(s.text.upper(), s.bold) for s in block.spans)
            if style.upper else block.spans,
            style.size, USABLE, style.monospace, style.bold, style.tracking),
            block, style, y, 0.0))
        y -= style.size * LEADING
    if subtitle:
        y -= 4
        current.extend(_draw((Span(subtitle),), MARGIN, y, 9, mono=False,
                             rgb=layout.MUTED))
        y -= 9 * LEADING

    width = USABLE * BUBBLE_WIDTH
    for turn in turns:
        y, current = _bubble(turn, width, y, current, pages)

    if current or not pages:
        pages.append(current)
    said = " ".join(f"{turn.speaker} {turn.body}" for turn in turns)
    return _assemble(pages, _unsupported(said + title + subtitle))


def _bubble(
    turn: Turn, width: float, y: float, current: list[bytes], pages: list[list[bytes]]
) -> tuple[float, list[bytes]]:
    """One turn, across as many pages as it needs.

    Returns where the next bubble starts and which page it is being drawn on —
    a long reply legitimately ends on a page its own bubble began two sheets
    earlier, and the caller cannot know that without being told.
    """
    left = MARGIN + (USABLE - width) if turn.mine else MARGIN
    fill = layout.MINE if turn.mine else layout.BUBBLE
    edge = layout.MINE_EDGE if turn.mine else layout.BUBBLE_EDGE

    lines = _laid_out(turn.body, width)
    # **`max-width`, not `width`.** The rule on `.bubble` is a maximum, so a
    # bubble shrinks to what is in it — and every bubble drawn at the full 78%
    # is the one thing that stops a transcript looking like the conversation it
    # came from. Measured after wrapping, which is the order the browser does it
    # in: wrap at the maximum, then take the widest line that resulted.
    longest = max(
        (_measure(spans, style.size, style.monospace, style.bold, style.tracking)
         for style, spans, _ in lines if spans),
        default=0.0,
    )
    label_width = _measure((Span(turn.speaker.upper()),), 10, mono=True, bold=True,
                           tracking=1.5)
    width = min(width, max(longest, label_width) + 2 * PAD_X + 2)
    left = MARGIN + (USABLE - width) if turn.mine else MARGIN
    label = 10 * LEADING
    y -= BUBBLE_GAP

    at = 0
    while at < len(lines) or at == 0:
        # What fits between here and the foot of the page, less the padding the
        # bubble needs at both ends and the speaker label at the top.
        room = y - BOTTOM - 2 * PAD_Y - (label if at == 0 else 0)
        taken, height = _fills(lines[at:], room)
        if taken == 0 and current:
            pages.append(current)
            current, y = [], float(TOP)
            continue
        box = height + 2 * PAD_Y + (label if at == 0 else 0)
        current.append(_rect(left, y - box, width, box, fill))
        current.append(_rect(left, y - box, width, 0.6, edge))
        current.append(_rect(left, y - 0.6, width, 0.6, edge))
        current.append(_rect(left, y - box, 0.6, box, edge))
        current.append(_rect(left + width - 0.6, y - box, 0.6, box, edge))

        inner = y - PAD_Y
        if at == 0:
            inner -= 10 * 0.8
            current.extend(_draw((Span(turn.speaker.upper()),), left + PAD_X, inner,
                                 10, mono=True, bold=True,
                                 rgb=layout.CYAN if turn.mine else layout.ACCENT,
                                 tracking=1.5))
            inner -= label - 10 * 0.8
        for style, spans, marker in lines[at:at + taken]:
            step = _line_height(style, spans)
            if spans:
                inner -= style.size * 0.82
                x = left + PAD_X + style.indent
                if marker:
                    current.extend(_draw((Span(marker),), x, inner, style.size,
                                         False, rgb=style.colour))
                current.extend(_draw(spans, x + (12 if marker else 0), inner,
                                     style.size, style.monospace, style.bold,
                                     rgb=style.colour, tracking=style.tracking))
                inner -= step - style.size * 0.82
            else:
                inner -= step
        y -= box
        at += taken
        if at >= len(lines):
            break
    return y, current


def _fills(lines: Sequence[tuple[Style, tuple[Span, ...], str]], room: float) -> tuple[int, float]:
    """How many of these lines fit in `room`, and what they measure."""
    used = 0.0
    for count, (style, spans, _) in enumerate(lines):
        step = _line_height(style, spans)
        if used + step > room:
            return count, used
        used += step
    return len(lines), used
