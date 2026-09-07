"""Two documents, two renderers, on purpose.

**`render_conversation` is unchanged, and still without a dependency.** A
transcript is bubbles of bounded width, one side each — the ladder still says
never add a library for what these few hundred lines already do, and nothing
about styling a document changes what a chat window looks like exported.

**`render` was the hand-rolled one, and no longer is.** It stopped clearing its
own rung of the ladder the moment a document needed to look like something
other than itself: real typography, a colour a template supplied, a table or
an image. Base-14 fonts placed by an estimated advance is a few lines; a layout
engine that wraps, paginates and colours to an arbitrary `style.StyleProfile`
is not — the same reasoning that already justified `pypdf` for reading a PDF's
words justifies reportlab for writing one that looks like something.
`layout.py` still decides what the text *means* (headings, bullets, bold runs);
only what draws that meaning changed.

**What `render` can do now**, beyond the old renderer: real typeface families
(serif/sans/mono, substituted from a template's own — see `style.py` — never
embedded), template colours, and page geometry matched to the source. **What it
still cannot**: the template's *exact* font (a substitution, named as one),
tables and images in the model's own markdown (`layout.py` has no syntax for
either yet, so nothing produces one to render).

**`render_conversation` cannot do any of that**, unchanged: no tables, no
images, no links, no nested lists, WinAnsi only, and headings/labels declared
in this file's own fixed dark palette (`layout.PAPER`/`ACCENT`/etc.) — a
transcript is a picture of the chat window, not a document with a look of its
own to borrow or lend.
"""
from __future__ import annotations

import functools
import io
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any
from xml.sax.saxutils import escape as _xml_escape

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from nervis import layout
from nervis.layout import Block, Kind, Span, Style, parse, style_for
from nervis.style import DEFAULT as DEFAULT_STYLE
from nervis.style import StyleProfile

#: US Letter at 72 dpi, the format's own unit.
PAGE_WIDTH, PAGE_HEIGHT = 612, 792
# Floats throughout: a point is a fractional unit and 9.5pt code says so.
MARGIN = 56

#: Leading as a multiple of the size. Tighter than 1.4 looks cramped in
#: Helvetica; looser wastes a page on a two-page summary.
LEADING = 1.36

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


def _unsupported(text: str) -> str:
    return "".join(sorted({c for c in text if c.encode(ENCODING, "ignore") == b""}))


#: Exact PostScript names of reportlab's base-14 set. Not a naming convention —
#: Helvetica and Courier spell their bold cut "<family>-Bold", but Times spells
#: its "Times-Bold" rather than "Times-Roman-Bold". Wrong for the one family
#: most templates with a serif body will actually pick.
_BASE14: dict[str, dict[str, str]] = {
    "Helvetica": {"regular": "Helvetica", "bold": "Helvetica-Bold",
                  "italic": "Helvetica-Oblique", "bold_italic": "Helvetica-BoldOblique"},
    "Times-Roman": {"regular": "Times-Roman", "bold": "Times-Bold",
                    "italic": "Times-Italic", "bold_italic": "Times-BoldItalic"},
    "Courier": {"regular": "Courier", "bold": "Courier-Bold",
                "italic": "Courier-Oblique", "bold_italic": "Courier-BoldOblique"},
}


def _base14_face(family: str, bold: bool, italic: bool) -> str:
    """The PostScript name reportlab actually registers, for `family`'s cut.

    Named apart from `_face` above deliberately — that one resolves an *old
    renderer resource name* ("F1".."F5") that `render_conversation` still
    uses, and reusing it here would silently return the wrong kind of string.
    """
    key = ("bold_italic" if bold and italic
           else "bold" if bold else "italic" if italic else "regular")
    return _BASE14[family][key]


#: Characters WinAnsi carries that reportlab's base-14 text path still draws
#: wrong — confirmed directly, not assumed: `•` (U+2022) is declared under
#: `/WinAnsiEncoding` like every other glyph here, but reportlab writes byte
#: `0x7F` for it rather than WinAnsi's own `0x95`, and a reader extracts that
#: byte back as a control character. `layout.py` never draws one — its own
#: bullet marker is already `·` — this exists only for the character
#: appearing in a model's own prose. One substitution rather than a general
#: workaround, because it is the one glyph this was ever seen to mishandle.
_MISMAPPED = {"•": "·"}


def _sanitised(text: str) -> str:
    """`text`, with anything WinAnsi cannot carry turned into a literal `?`,
    and the handful of characters reportlab itself mismaps substituted first.

    Computed before `layout.parse` ever sees it, so reportlab is never handed a
    character outside the base-14 fonts' encoding — whatever it would do with
    one is not worth depending on. `Rendered.unsupported` is computed
    separately, from the original text, so what was lost is still reported even
    though it never reaches the page.
    """
    for bad, good in _MISMAPPED.items():
        text = text.replace(bad, good)
    return text.encode(ENCODING, errors="replace").decode(ENCODING)


def _markup(spans: tuple[Span, ...]) -> str:
    """A block's spans as the inline markup reportlab's `Paragraph` parses.

    Escaped first, tagged second: escaping after tagging would turn the `<b>`
    this adds into text the moment a span's own content held a `&` or `<`.
    """
    out = []
    for span in spans:
        piece = _xml_escape(span.text)
        if span.code:
            piece = f'<font face="Courier">{piece}</font>'
        if span.bold:
            piece = f"<b>{piece}</b>"
        if span.italic:
            piece = f"<i>{piece}</i>"
        out.append(piece)
    return "".join(out)


def _paragraph_style(block: Block, profile: StyleProfile) -> ParagraphStyle:
    """A block's structural role (from `layout.style_for`, unchanged) mapped
    onto `profile`'s concrete fonts, sizes and colours.

    Structure and appearance stay separate on purpose: `style_for` decides
    *that* a heading is bold, letterspaced, uppercase, ruled above — facts
    about what a heading *is*, true of every theme — and this function alone
    decides what colour and how large, the one part a template may override.
    """
    style = style_for(block)
    heading = block.kind is Kind.HEADING
    scale = (profile.heading_size / DEFAULT_STYLE.heading_size) if heading \
        else (profile.body_size / DEFAULT_STYLE.body_size)
    family = ("Courier" if style.monospace
              else profile.heading_family if heading else profile.body_family)
    colour = profile.heading_colour if heading else profile.text_colour
    size = style.size * scale
    return ParagraphStyle(
        name=f"nervis-{block.kind.value}-{block.level}",
        fontName=_base14_face(family, style.bold, False),
        fontSize=size, leading=size * LEADING,
        textColor=colors.Color(*colour),
        leftIndent=style.indent * scale,
        bulletIndent=max(0.0, (style.indent - 12) * scale),
        spaceBefore=style.space_above * scale,
        letterSpacing=style.tracking,
        keepWithNext=heading,
    )


def _paragraph(block: Block, profile: StyleProfile) -> list[Flowable]:
    """One non-code block, as the flowables it becomes.

    A list rather than one `Flowable`, because a heading's rule is its own
    element ahead of the paragraph it introduces — Platypus flows each of a
    list's items in order, so returning both keeps them together without a
    container `Flowable` neither needs.
    """
    style = style_for(block)
    para_style = _paragraph_style(block, profile)
    spans = (tuple(replace(s, text=s.text.upper()) for s in block.spans)
             if style.upper else block.spans)
    out: list[Flowable] = []
    if style.rule_above:
        out.append(HRFlowable(width="100%", thickness=0.6,
                               color=colors.Color(*profile.rule_colour),
                               spaceBefore=0, spaceAfter=style.rule_above))
    out.append(Paragraph(_markup(spans), para_style, bulletText=block.marker or None))
    return out


class _CodeBlock(Flowable):
    """A fenced code block: verbatim lines, monospaced, on a tint.

    Its own `Flowable` rather than a styled `Table` — a code block is the one
    place the old renderer already drew a background by hand rather than
    asking a layout engine for one, and `Table`'s cell-padding/border model
    solves a harder problem than "a rectangle behind left-aligned text."
    """

    def __init__(self, lines: list[str], profile: StyleProfile) -> None:
        super().__init__()
        self._lines, self._profile = lines, profile
        self._size = 9.5
        self._leading = self._size * LEADING
        self._pad = 6.0
        self.height = self._leading * len(lines) + 2 * self._pad
        self.width = 0.0

    def wrap(self, available_width: float, _available_height: float) -> tuple[float, float]:
        self.width = available_width
        return self.width, self.height

    def draw(self) -> None:
        profile = self._profile
        # Darker on a light page, lighter on a dark one — the same "tint,
        # not a fixed colour" idea `layout.py`'s own `PANEL` already is.
        light = sum(profile.background_colour) > 1.5
        tint = tuple(max(0.0, c - 0.06) if light else min(1.0, c + 0.08)
                     for c in profile.background_colour)
        self.canv.setFillColor(colors.Color(*tint))
        self.canv.rect(0, 0, self.width, self.height, fill=1, stroke=0)
        self.canv.setFont("Courier", self._size)
        self.canv.setFillColor(colors.Color(*profile.text_colour))
        y = self.height - self._pad - self._size * 0.82
        for line in self._lines:
            self.canv.drawString(self._pad, y, line)
            y -= self._leading


#: The padding inside a table cell, in points, and the rule between them.
CELL_PAD = 4.0
CELL_RULE = 0.4


def _widest_word(cells: list[tuple[Span, ...]], profile: StyleProfile, size: float) -> float:
    """The narrowest this column can be without breaking a word in half.

    **Measured, not guessed at as a share.** The first version floored every
    column at twelve percent of the measure, which on a three-column table
    put `ravis.routes` in fifty-five points and wrapped it to `ravis.route` /
    `s` — a share is a guess about content, and the actual constraint is that
    a cell wraps between words and never inside one. So the floor is what the
    column's longest word actually measures in the font it is drawn in.
    """
    widest = 0.0
    for cell in cells:
        for span in cell:
            for word in span.text.split():
                face = _base14_face(profile.body_family, span.bold, span.italic)
                widest = max(widest, stringWidth(word, face, size))
    return widest + 2 * CELL_PAD


def _column_widths(
    rows: list[Block], columns: int, profile: StyleProfile, width: float, size: float
) -> list[float]:
    """How wide each column is drawn.

    Weighted by how much text a column holds, so a `Purpose` of sentences
    earns more room than a `Read-only` of two words — an even split is a
    stack of wrapped fragments beside acres of white, and the whole reason to
    draw a grid is that the eye can run down it. Every column keeps at least
    its longest word, and the weighting shares out what is left.

    A table whose words alone overflow the page gets proportional columns and
    wraps mid-word, which is the honest outcome: the alternative is drawing
    past the margin, and reportlab would do that silently.
    """
    per_column = [
        [row.cells[index] for row in rows if index < len(row.cells)]
        for index in range(columns)
    ]
    floors = [_widest_word(cells, profile, size) for cells in per_column]
    if sum(floors) >= width:
        total = sum(floors) or 1.0
        return [width * floor / total for floor in floors]

    weights = [
        max(float(sum(len(span.text) for span in cell)) for cell in cells) if cells else 1.0
        for cells in per_column
    ]
    spare = width - sum(floors)
    total = sum(weights) or 1.0
    return [floor + spare * weight / total for floor, weight in zip(floors, weights)]


def _table(rows: list[Block], profile: StyleProfile, width: float) -> Table:
    """One run of table rows as a drawn grid.

    **Cells are paragraphs, so they wrap.** A table of strings sized to fit
    its longest cell is a table that runs off the page the first time
    somebody writes a sentence in one, which is most tables — and reportlab
    only wraps what it is given as a flowable.

    `_column_widths` decides how wide each one is drawn.
    """
    body = _paragraph_style(Block(Kind.TABLE_ROW), profile)
    head = ParagraphStyle("cell-head", parent=body, fontName=_base14_face(
        profile.body_family, True, False), textColor=colors.Color(*profile.heading_colour))

    columns = max(len(row.cells) for row in rows)
    # The size cells are actually drawn at, asked of the style that draws
    # them rather than recomputed — two answers to that question would
    # disagree the first time a template changed its body size.
    widths = _column_widths(rows, columns, profile, width, body.fontSize)

    drawn = [
        [Paragraph(_markup(cell), head if row.marker == "header" else body)
         # Ragged rows need no padding here: reportlab fills a short row
         # itself, measured rather than assumed — an earlier version padded
         # them on the stated grounds that it "refuses a ragged table
         # outright", which it does not.
         for cell in row.cells]
        for row in rows
    ]
    table = Table(drawn, colWidths=widths, repeatRows=1 if rows[0].marker == "header" else 0)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), CELL_RULE, colors.Color(*profile.rule_colour)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), CELL_PAD),
        ("RIGHTPADDING", (0, 0), (-1, -1), CELL_PAD),
        ("TOPPADDING", (0, 0), (-1, -1), CELL_PAD * 0.7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), CELL_PAD * 0.7),
    ]))
    return table



def _story(blocks: list[Block], profile: StyleProfile) -> list[Flowable]:
    """`layout.parse()`'s blocks, as a reportlab Platypus story.

    Consecutive fenced-code blocks are grouped here, not in `layout.py`:
    `layout`'s own `_collapse` only joins paragraphs, because a renderer that
    draws prose needs them joined and a renderer that counts lines does not.
    Grouping code into one tinted region is a drawing concern, so it lives here.
    """
    story: list[Flowable] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        if block.kind is Kind.CODE:
            run: list[str] = []
            while index < len(blocks) and blocks[index].kind is Kind.CODE:
                run.append(blocks[index].spans[0].text if blocks[index].spans else "")
                index += 1
            story.append(_CodeBlock(run, profile))
            continue
        if block.kind is Kind.TABLE_ROW:
            rows: list[Block] = []
            while index < len(blocks) and blocks[index].kind is Kind.TABLE_ROW:
                rows.append(blocks[index])
                index += 1
            story.append(_table(rows, profile, _measure_width(profile)))
            continue
        if block.kind is Kind.BLANK:
            story.append(Spacer(1, style_for(block).size * LEADING * 0.55))
            index += 1
            continue
        story.extend(_paragraph(block, profile))
        index += 1
    return story


def _measure_width(profile: StyleProfile) -> float:
    """The width a flowable actually has, which is the page less its margins."""
    return profile.page_width - profile.margin_left - profile.margin_right


def _paint_background(profile: StyleProfile, canvas_: rl_canvas.Canvas, _doc: Any) -> None:
    """The page's ground, painted before Platypus draws anything onto it.

    `onFirstPage`/`onLaterPages` run before that page's flowables — the
    ordering a background rect always needed, and reportlab's own hook for it.
    """
    canvas_.saveState()
    canvas_.setFillColor(colors.Color(*profile.background_colour))
    canvas_.rect(0, 0, profile.page_width, profile.page_height, fill=1, stroke=0)
    canvas_.restoreState()


class _FooterCanvas(rl_canvas.Canvas):
    """Defers the footer until every page exists, so it can say "N of TOTAL".

    reportlab draws forward, one page at a time — nothing drawn during a page
    can know how many more are coming. The standard answer: hold each finished
    page back instead of emitting it, and only at `save()`, once the total is
    known, paint the footer onto each and let it go.
    """

    def __init__(self, *args: Any, profile: StyleProfile, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._profile = profile
        self._held: list[dict[str, Any]] = []

    def showPage(self) -> None:  # noqa: N802 - overrides reportlab.Canvas's own name
        self._held.append(dict(self.__dict__))
        # Real at runtime (confirmed directly against the installed
        # reportlab), just absent from the third-party stub package.
        self._startPage()  # type: ignore[attr-defined]

    def save(self) -> None:
        total = len(self._held)
        for number, state in enumerate(self._held, start=1):
            self.__dict__.update(state)
            _paint_footer(self, self._profile, number, total)
            super().showPage()
        super().save()


def _paint_footer(
    canvas_: rl_canvas.Canvas, profile: StyleProfile, number: int, total: int,
) -> None:
    """A hairline and a page number, the same voice `render_conversation`'s
    `_footer` speaks in — monospaced, letterspaced, naming the machine."""
    y = MARGIN * 0.55
    canvas_.saveState()
    canvas_.setStrokeColor(colors.Color(*profile.rule_colour))
    canvas_.line(profile.margin_left, y + 13, profile.page_width - profile.margin_right, y + 13)
    canvas_.setFont("Courier", 7.5)
    canvas_.setFillColor(colors.Color(*profile.rule_colour))
    canvas_.drawString(profile.margin_left, y, "NERVIS")
    canvas_.drawRightString(profile.page_width - profile.margin_right, y, f"{number} / {total}")
    canvas_.restoreState()


def render(title: str, text: str, style: StyleProfile | None = None) -> Rendered:
    """`text`, laid out in `style`'s look, as a PDF.

    Delegates measurement, wrapping and page-breaking to reportlab's Platypus
    — the one part of the old hand-rolled writer that could not be extended to
    a template's own fonts and colours without becoming a second reportlab.
    `layout.parse()` still decides what the text means; this only decides how
    what it means is drawn. `render_conversation` is untouched: this function
    and it no longer share an implementation, only a handful of constants.
    """
    profile = style or DEFAULT_STYLE
    unsupported = _unsupported(text + title)
    blocks = parse(_sanitised(text))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=(profile.page_width, profile.page_height),
        leftMargin=profile.margin_left, rightMargin=profile.margin_right,
        topMargin=profile.margin_top, bottomMargin=profile.margin_bottom,
        title=_sanitised(title),
    )
    paint_background = functools.partial(_paint_background, profile)
    # **A story with nothing in it is still one page, not zero.** Platypus
    # emits a page only for a flowable that actually occupies one — an empty
    # `text` parses to no blocks at all, and `doc.build([])` produces a
    # correctly-structured, entirely blank PDF with a page count of zero.
    # "a summary of nothing found" is a legitimate reply; a file no reader can
    # open is not, so an empty story gets a single empty paragraph to anchor
    # the one page it should still be.
    story = _story(blocks, profile) or [Paragraph("", _paragraph_style(
        Block(Kind.PARAGRAPH), profile))]
    doc.build(
        story,
        onFirstPage=paint_background, onLaterPages=paint_background,
        canvasmaker=functools.partial(_FooterCanvas, profile=profile),
    )
    data = buffer.getvalue()
    # Counted by reading the file back rather than trusting reportlab's own
    # page counter's exact semantics — `pypdf` is already a dependency, and a
    # count that can only be wrong if the PDF itself is malformed is a count
    # worth having regardless of which library wrote the bytes.
    pages = len(PdfReader(io.BytesIO(data)).pages)
    return Rendered(data=data, pages=pages, unsupported=unsupported)


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


def _mark(cx: float, cy: float, r: float, glow: bool = True) -> list[bytes]:
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
    # **Only where the mark is large enough to carry it.** In the header it is
    # the letterhead and the glow is most of what makes it look drawn rather
    # than typed. Beside a speaker's name it is four points across, and three
    # haloes around something that small read as a smudge — the same reason the
    # window does not put one on every avatar in the transcript.
    if glow:
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

    # **No title above the bubbles.** It used to draw the conversation's name as
    # a heading and the date under it, which was right when there was nothing
    # else on the page — and became a second copy the moment the header carried
    # both. The window itself has no title over the messages either.
    y -= 6

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
        out.extend(_mark(speaker_x + 4, y + 2.6, 4.2, glow=False))
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
