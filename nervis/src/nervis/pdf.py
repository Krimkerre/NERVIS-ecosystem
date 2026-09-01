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
from dataclasses import dataclass, replace
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
#: The oblique cuts, for `*emphasis*`. Base-14 like the others, so still no
#: embedding — the only cost of italic is two more font objects.
ITALIC, BOLD_ITALIC = "F4", "F5"

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
#: Every font the file declares, in the order it declares them. The resource
#: dictionary, the font objects and the first content stream's number all come
#: from this, so adding a face is adding one row here.
FACES = (
    (REGULAR, b"/Helvetica"),
    (BOLD, b"/Helvetica-Bold"),
    (MONO, b"/Courier"),
    (ITALIC, b"/Helvetica-Oblique"),
    (BOLD_ITALIC, b"/Helvetica-BoldOblique"),
)

WIDTHS = {REGULAR: 0.52, BOLD: 0.55, MONO: 0.60,
          # The oblique cuts share their upright's metrics — Helvetica-Oblique
          # is a slanted Helvetica, not a redrawn face — so the estimate that
          # holds for one holds for the other. Listed rather than defaulted,
          # because a missing key here is a `KeyError` on the first italic word
          # somebody writes, which is a crash in a PDF renderer for a slant.
          ITALIC: 0.52, BOLD_ITALIC: 0.55}


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
        face = _face(bold or span.bold, mono or span.code, span.italic)
        total += len(span.text) * size * WIDTHS[face]
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
    # **Whether the source actually had a space here.** Runs are split
    # independently, so at a weight boundary the code below has to decide
    # whether to put one back — and it used to always say yes. `**bound**, and`
    # became `bound , and`, a space the author never typed, appearing at every
    # bold run that ended on a comma or a full stop. The answer is in the text:
    # a boundary needs a space only where one of the two sides had one.
    owed = False

    for span in spans:
        starts_spaced = span.text.startswith((" ", "\t"))
        ends_spaced = span.text.endswith((" ", "\t"))
        for index, word in enumerate(span.text.split(" ")):
            if not word:
                continue
            joiner = bool(index) or starts_spaced or owed
            joined = tuple(current + [replace(span, text=word)])
            if current and not _fits(joined, size, width, mono, bold, tracking):
                lines.append(tuple(current))
                current = [replace(span, text=word)]
            else:
                # Re-join adjacent runs of one weight so the drawn output has
                # one `Tj` per weight rather than one per word.
                if current and _same_mark(current[-1], span):
                    current[-1] = replace(
                        current[-1],
                        text=current[-1].text + (" " if joiner else "") + word,
                    )
                else:
                    if current and joiner:
                        # A run of its own rather than riding on a neighbour.
                        # At a weight boundary one side has to carry the space,
                        # and appending it to the previous run put a trailing
                        # space *inside* the bold phrase — so `**12%**` drew as
                        # `(12% )`, a bold space, and the phrase stopped being
                        # exactly the characters the author marked.
                        current.append(replace(current[-1], text=" "))
                    current.append(replace(span, text=word))
        owed = ends_spaced
    if current:
        lines.append(tuple(current))
    return lines or [(Span(""),)]


def _same_mark(a: Span, b: Span) -> bool:
    """Whether two runs are drawn identically, so they can be joined.

    Was `a.bold == b.bold`, which stopped being the whole question the moment a
    run could also be italic or code — joining a plain word onto a code run
    would draw it in the monospace face.
    """
    return (a.bold, a.italic, a.code) == (b.bold, b.italic, b.code)


def _face(bold: bool, mono: bool, italic: bool = False) -> str:
    """Which font resource a run is drawn with.

    One place, because the weight comes from two directions — the block's style
    (a heading is bold) and the span's own marks (`**this**` is bold) — and
    resolving that in each caller is how a heading ends up drawn in the regular
    face while claiming to be bold, which is exactly what happened.
    """
    if mono:
        return MONO
    if italic:
        return BOLD_ITALIC if bold else ITALIC
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


#: How far a Bézier control point sits from the end of a quarter arc, as a
#: fraction of the radius. The circle constant — four curves approximating a
#: circle to about one part in a thousand, which is well past what a 7pt mark
#: printed on paper can show.
KAPPA = 0.5523


def _circle(cx: float, cy: float, r: float,
            rgb: tuple[float, float, float], stroke: float = 0.0) -> bytes:
    """A circle, filled or stroked, from four Bézier arcs.

    PDF has no circle operator, which is why this file had only rectangles: the
    transcript was drawn square because squares were what the renderer could
    say. Everything round in the dashboard's mark needs this.
    """
    k = r * KAPPA
    body = (
        f"{cx + r:g} {cy:g} m "
        f"{cx + r:g} {cy + k:g} {cx + k:g} {cy + r:g} {cx:g} {cy + r:g} c "
        f"{cx - k:g} {cy + r:g} {cx - r:g} {cy + k:g} {cx - r:g} {cy:g} c "
        f"{cx - r:g} {cy - k:g} {cx - k:g} {cy - r:g} {cx:g} {cy - r:g} c "
        f"{cx + k:g} {cy - r:g} {cx + r:g} {cy - k:g} {cx + r:g} {cy:g} c h "
    )
    if stroke:
        head = _stroke_colour(rgb) + f" {stroke:g} w ".encode("latin-1")
        return head + body.encode("latin-1") + b"S"
    return _colour(rgb) + b" " + body.encode("latin-1") + b"f"


def _round_rect(x: float, y: float, width: float, height: float, r: float,
                rgb: tuple[float, float, float], stroke: float = 0.0) -> bytes:
    """A rectangle with rounded corners.

    The radius is clamped to half the shorter side, because a bubble one line
    tall with a 6pt radius is a lozenge rather than a rectangle — and a bubble
    that changes shape with its content reads as two different kinds of thing.
    """
    r = max(0.0, min(r, width / 2, height / 2))
    k = r * KAPPA
    x2, y2 = x + width, y + height
    body = (
        f"{x + r:g} {y:g} m {x2 - r:g} {y:g} l "
        f"{x2 - r + k:g} {y:g} {x2:g} {y + r - k:g} {x2:g} {y + r:g} c "
        f"{x2:g} {y2 - r:g} l "
        f"{x2:g} {y2 - r + k:g} {x2 - r + k:g} {y2:g} {x2 - r:g} {y2:g} c "
        f"{x + r:g} {y2:g} l "
        f"{x + r - k:g} {y2:g} {x:g} {y2 - r + k:g} {x:g} {y2 - r:g} c "
        f"{x:g} {y + r:g} l "
        f"{x:g} {y + r - k:g} {x + r - k:g} {y:g} {x + r:g} {y:g} c h "
    )
    if stroke:
        head = _stroke_colour(rgb) + f" {stroke:g} w ".encode("latin-1")
        return head + body.encode("latin-1") + b"S"
    return _colour(rgb) + b" " + body.encode("latin-1") + b"f"


def _poly(points: Sequence[tuple[float, float]],
          rgb: tuple[float, float, float], stroke: float = 0.0) -> bytes:
    """A closed polygon — the hexagon at the centre of the mark."""
    if not points:
        return b""
    first, rest = points[0], points[1:]
    body = f"{first[0]:g} {first[1]:g} m " + "".join(
        f"{px:g} {py:g} l " for px, py in rest
    ) + "h "
    if stroke:
        head = _stroke_colour(rgb) + f" {stroke:g} w ".encode("latin-1")
        return head + body.encode("latin-1") + b"S"
    return _colour(rgb) + b" " + body.encode("latin-1") + b"f"


def _line(x1: float, y1: float, x2: float, y2: float,
          rgb: tuple[float, float, float], width: float = 0.6) -> bytes:
    """One stroked segment — the spokes out to the satellites."""
    return _stroke_colour(rgb) + (
        f" {width:g} w {x1:g} {y1:g} m {x2:g} {y2:g} l S"
    ).encode("latin-1")


#: Opacities the file declares an `ExtGState` for. Named rather than emitted per
#: use because a graphics state is a *resource*: it lives in the page's resource
#: dictionary and is selected by name, so the set has to be known when the page
#: is written rather than discovered while drawing it.
ALPHAS = (0.10, 0.18, 0.30, 1.0)


def _alpha(value: float) -> bytes:
    """Select a constant opacity for everything drawn after it.

    Both `/ca` and `/CA` — fill and stroke alpha are separate in PDF, and a glow
    drawn with only one of them set comes out solid on whichever half was
    forgotten. Reset to 1.0 afterwards, always: alpha is graphics state and
    persists, so a mark that left it at 0.1 would fade the rest of the page.
    """
    nearest = min(ALPHAS, key=lambda known: abs(known - value))
    return f"/GS{ALPHAS.index(nearest)} gs".encode("latin-1")


def _stroke_colour(rgb: tuple[float, float, float]) -> bytes:
    """`RG`, not `rg`. Stroking and filling are separate colours in PDF, and
    setting the fill and then stroking paints the previous stroke colour —
    which is black by default, so a mark drawn that way comes out invisible on
    a dark page."""
    return f"{rgb[0]:.3g} {rgb[1]:.3g} {rgb[2]:.3g} RG".encode("latin-1")


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

    face = _face(bold or drawn[0].bold, mono or drawn[0].code, drawn[0].italic)
    # `Tc` is character spacing, and it is graphics state like colour — set on
    # every object rather than once, or a tracked label spaces out everything
    # drawn after it.
    out = [_colour(rgb) + f" BT {tracking:g} Tc /{face} {size:g} Tf "
           f"{x:g} {y:g} Td (".encode("latin-1")
           + _escape(drawn[0].text) + b") Tj"]
    for span in drawn[1:]:
        want = _face(bold or span.bold, mono or span.code, span.italic)
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
            spans = tuple(replace(span, text=span.text.upper()) for span in spans)

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
    # 1 catalog, 2 pages, then one object per font, then a content stream and a
    # page per sheet.
    #
    # **Derived from `FACES`, and it was the literal 6.** Adding the two oblique
    # cuts moved every content stream up by two while this still pointed at the
    # old first one, so `/Contents` named a font and the page tree named a
    # stream. `sips` and Preview rendered it anyway — they are forgiving about
    # object types — and `pypdf` reported the file as zero pages, which is what
    # a strict reader sees. A hand-maintained offset next to a list that can
    # grow is the whole bug.
    first_content = 3 + len(FACES)
    content_ids = [first_content + n * 2 for n in range(len(pages))]
    page_ids = [first_content + 1 + n * 2 for n in range(len(pages))]

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>".encode("latin-1"))
    for _, base in FACES:
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
        # Assembled as one string and encoded once. Written as adjacent literals
        # around a `+`, Python binds the neighbouring pieces first and `.encode`
        # lands on the tail alone — which raises `str + bytes` from a line that
        # reads as though it produced bytes throughout.
        fonts = " ".join(f"/{name} {3 + n} 0 R" for n, (name, _) in enumerate(FACES))
        # Inline dictionaries rather than objects of their own, which keeps the
        # numbering above derived from the fonts alone.
        states = " ".join(
            f"/GS{n} << /Type /ExtGState /ca {a:g} /CA {a:g} >>"
            for n, a in enumerate(ALPHAS)
        )
        objects.append((
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << {fonts} >> /ExtGState << {states} >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode("latin-1"))

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
    #: What the window prints beside the name — a time, or which model answered.
    #: Optional because a transcript assembled from an older store has neither,
    #: and a bubble is not worth refusing over a missing timestamp.
    stamp: str = ""


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
            spans = tuple(replace(span, text=span.text.upper()) for span in spans)
        inner = width - 2 * PAD_X - style.indent
        for number, line in enumerate(
            _wrap_spans(spans, style.size, inner, style.monospace, style.bold, style.tracking)
        ):
            out.append((style, line, block.marker if number == 0 else ""))
    return out


def _line_height(style: Style, spans: tuple[Span, ...]) -> float:
    """What one laid-out line costs vertically, blank lines included."""
    return float(style.size) * LEADING * (0.55 if not spans else 1.0)


#: How round a bubble's corners are. The dashboard's `.bubble` is 10px at a
#: nominal 16px root; this is the same proportion at PDF's 72-per-inch.
RADIUS = 5.0

#: How far the mark sits from the page's left edge. Deliberately inside
#: `MARGIN`: the header is letterhead rather than content, so it starts where
#: the paper does rather than where the column does.
CORNER = 22.0

#: The header band across the top of the first page, and the room it takes.
HEADER = 62.0
#: A slimmer one on every page after, so a transcript reads as one document
#: without spending a sixth of every sheet saying so again.
HEADER_AGAIN = 26.0


def _mark(cx: float, cy: float, r: float) -> list[bytes]:
    """The NERVIS logo: a diamond with a cyan core.

    **The mark in the dashboard's own top-left corner**, which is `.brand i` —
    a square rotated 45 degrees with a one-pixel accent border, and a smaller
    square inside it filled cyan. Both rotate together, because the inner one is
    a child of the outer, so the core is a diamond too rather than a square
    sitting at an angle inside one.

    This drew the *avatar* first — the orbital instrument with three satellites
    — which is a different thing entirely: the avatar is the character, the logo
    is the letterhead. Corrected.

    `r` is the half-diagonal, so a logo of `r` reaches `r` above and below its
    centre. That is the useful measurement here because the header positions it
    by how much vertical room it needs, and a side length would have to be
    converted at every call site.
    """
    core = r * 0.25
    diamond = [(cx, cy + r), (cx + r, cy), (cx, cy - r), (cx - r, cy)]
    out: list[bytes] = []
    # **The glow, as three strokes fading outward.** `box-shadow: 0 0 6px` is a
    # blur, and PDF has no blur — but it does have constant alpha, and a handful
    # of concentric strokes at falling opacity is what a blur looks like at
    # this size. Widest and faintest first, so each is laid under the last.
    for spread, alpha in ((3.4, 0.10), (2.2, 0.18), (1.2, 0.30)):
        out.append(_alpha(alpha))
        out.append(_poly(
            [(cx + (px - cx) * (1 + spread / r), cy + (py - cy) * (1 + spread / r))
             for px, py in diamond],
            layout.LOGO_EDGE, stroke=spread,
        ))
    out.append(_alpha(1.0))
    out.append(_poly(diamond, layout.LOGO_EDGE, stroke=0.9))
    out.append(_poly(
        [(cx, cy + core), (cx + core, cy), (cx, cy - core), (cx - core, cy)],
        layout.LOGO_CORE))
    return out


def _header(title: str, subtitle: str, first: bool) -> tuple[list[bytes], float]:
    """The band at the top of a page, and where content may start below it.

    **On every page, not only the first.** A transcript that carried its
    identity on page one and nothing afterwards read as a stack of unrelated
    sheets the moment anybody printed it — and a page pulled out of a folder
    should still say what it is. The later ones are a quarter the height: the
    mark, the wordmark and the title, on one line.
    """
    band = HEADER if first else HEADER_AGAIN
    top = PAGE_HEIGHT - band
    out = [
        _rect(0, top, PAGE_WIDTH, band, layout.PANEL),
        # The accent hairline is what makes it a header rather than a grey box,
        # and it is the same rule the dashboard draws under its own top bar.
        _rect(0, top, PAGE_WIDTH, 0.8, layout.ACCENT),
    ]
    if first:
        # **In the corner, not on the text margin.** The mark sat at `MARGIN`,
        # lined up with the body below it, which made the band look like a first
        # paragraph with a picture in it. Pulled out to the page's own corner it
        # reads as letterhead: the thing the sheet belongs to, outside the
        # column the writing lives in.
        out.extend(_mark(CORNER + 16, top + band / 2, 16))
        out.extend(_draw((Span("NERVIS", True),), CORNER + 40, top + band / 2 + 2,
                         15, mono=False, bold=True, rgb=layout.INK, tracking=2.4))
        out.extend(_draw((Span("NETWORKED ECOSYSTEM RUNTIME VISUALIZATION & INTELLIGENCE SYSTEM"),),
                         CORNER + 40, top + band / 2 - 10, 5.4, mono=True,
                         rgb=layout.ACCENT, tracking=0.7))
        stamp = subtitle or ""
        if stamp:
            width = _measure((Span(stamp),), 7.5, mono=True, tracking=0.5)
            out.extend(_draw((Span(stamp),), PAGE_WIDTH - MARGIN - width,
                             top + band / 2 - 10, 7.5, mono=True,
                             rgb=layout.MUTED, tracking=0.5))
        shown = _clipped(title, PAGE_WIDTH - MARGIN * 2 - 210, 9)
        width = _measure((Span(shown, True),), 9, mono=False, bold=True)
        out.extend(_draw((Span(shown, True),), PAGE_WIDTH - MARGIN - width,
                         top + band / 2 + 3, 9, mono=False, bold=True, rgb=layout.INK))
    else:
        out.extend(_mark(CORNER + 7, top + band / 2, 7))
        out.extend(_draw((Span("NERVIS", True),), CORNER + 20, top + band / 2 - 2.6,
                         7.5, mono=False, bold=True, rgb=layout.MUTED, tracking=1.6))
        shown = _clipped(title, PAGE_WIDTH - MARGIN * 2 - 90, 7.5)
        width = _measure((Span(shown),), 7.5, mono=True, tracking=0.4)
        out.extend(_draw((Span(shown),), PAGE_WIDTH - MARGIN - width,
                         top + band / 2 - 2.6, 7.5, mono=True, rgb=layout.MUTED,
                         tracking=0.4))
    return out, top - 20


def _clipped(text: str, room: float, size: float) -> str:
    """As much of a title as fits, with an ellipsis where it was cut.

    A long title otherwise runs under the wordmark and out of the page, which is
    the one failure a header cannot hide — it is the part everybody looks at.
    """
    if _measure((Span(text),), size, mono=False) <= room:
        return text
    cut = text
    while cut and _measure((Span(cut + "…"),), size, mono=False) > room:
        cut = cut[:-1]
    return (cut.rstrip() + "…") if cut else ""


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
    banner, start = _header(title, subtitle, first=True)
    current: list[bytes] = list(banner)
    y = start

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
        y, current = _bubble(turn, width, y, current, pages, title)

    if current or not pages:
        pages.append(current)
    said = " ".join(f"{turn.speaker} {turn.body}" for turn in turns)
    return _assemble(pages, _unsupported(said + title + subtitle))


def _bubble(
    turn: Turn, width: float, y: float, current: list[bytes], pages: list[list[bytes]],
    header_title: str = "",
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
            # The slim band, drawn as the page opens rather than stamped on at
            # assembly: a page is a list of operators and the header has to be
            # the first of them, or the bubbles paint under it.
            banner, start = _header(header_title, "", first=False)
            current, y = list(banner), start
            continue
        box = height + 2 * PAD_Y + (label if at == 0 else 0)
        # **Rounded, and the same radius the dashboard uses.** Square corners
        # were what the renderer could draw rather than what the window looks
        # like, and it is most of why an exported transcript read as a report
        # about a conversation instead of a picture of one.
        current.append(_round_rect(left, y - box, width, box, RADIUS, fill))
        current.append(_round_rect(left, y - box, width, box, RADIUS, edge, stroke=0.6))
        # The rail: accent on NERVIS's side, cyan on the reader's, exactly as
        # the tint behind each bubble already differs. Inset a hair so it sits
        # inside the rounded edge rather than crossing it.
        rail = layout.CYAN if turn.mine else layout.ACCENT
        current.append(_rect(left + 0.6, y - box + RADIUS, 1.6, box - 2 * RADIUS, rail))

        inner = y - PAD_Y
        if at == 0:
            inner -= 10 * 0.8
            current.extend(_label_row(turn, left, width, inner))
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


def _label_row(turn: Turn, left: float, width: float, y: float) -> list[bytes]:
    """A bubble's top line: the logo, the speaker, and what the window stamps.

    Its own function because `_bubble` crossed the complexity ratchet when the
    stamp joined it — and the three belong together anyway. They are one row in
    the window too.
    """
    out: list[bytes] = []
    # The logo marks NERVIS's turns only. The reader's own messages carry no
    # mark in the window either, and giving them one would invent a second
    # character in the conversation.
    speaker_x = left + PAD_X
    if not turn.mine:
        out.extend(_mark(speaker_x + 4, y + 2.6, 4.2))
        speaker_x += 14
    out.extend(_draw((Span(turn.speaker.upper()),), speaker_x, y, 10, mono=True,
                     bold=True, rgb=layout.CYAN if turn.mine else layout.ACCENT,
                     tracking=1.5))
    if turn.stamp:
        stamp_width = _measure((Span(turn.stamp),), 7.5, mono=True, tracking=0.4)
        out.extend(_draw((Span(turn.stamp),), left + width - PAD_X - stamp_width, y,
                         7.5, mono=True, rgb=layout.MUTED, tracking=0.4))
    return out


def _fills(lines: Sequence[tuple[Style, tuple[Span, ...], str]], room: float) -> tuple[int, float]:
    """How many of these lines fit in `room`, and what they measure."""
    used = 0.0
    for count, (style, spans, _) in enumerate(lines):
        step = _line_height(style, spans)
        if used + step > room:
            return count, used
        used += step
    return len(lines), used
