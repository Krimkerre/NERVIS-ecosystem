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
links, no nested lists, and Latin-1 only — the base-14 encoding cannot express
the rest, and characters it cannot carry are reported rather than replaced with
a `?` nobody can see.
"""
from __future__ import annotations

from dataclasses import dataclass

from nervis.layout import Block, Kind, Span, parse, style_for

#: US Letter at 72 dpi, the format's own unit.
PAGE_WIDTH, PAGE_HEIGHT = 612, 792
# Floats throughout: a point is a fractional unit and 9.5pt code says so.
MARGIN = 56

#: Leading as a multiple of the size. Tighter than 1.4 looks cramped in
#: Helvetica; looser wastes a page on a two-page summary.
LEADING = 1.36

TOP = PAGE_HEIGHT - MARGIN
BOTTOM = MARGIN
USABLE = PAGE_WIDTH - 2 * MARGIN

#: Font resource names, in the order they are declared in the page's resources.
REGULAR, BOLD, MONO = "F1", "F2", "F3"

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
    return out.encode("latin-1", errors="replace")


def _fits(spans: tuple[Span, ...], size: float, width: float, mono: bool,
          bold: bool = False) -> bool:
    """Whether these runs fit one line of `width` points at `size`."""
    return _measure(spans, size, mono, bold) <= width


def _measure(spans: tuple[Span, ...], size: float, mono: bool, bold: bool = False) -> float:
    """The width these runs occupy, in points.

    Takes the block's weight as well as each span's, because a bold heading is
    wider than the same words plain — measuring it as regular wraps it a word
    late and it runs past the margin.
    """
    total = 0.0
    for span in spans:
        total += len(span.text) * size * WIDTHS[_face(bold or span.bold, mono)]
    return total


def _wrap_spans(
    spans: tuple[Span, ...], size: float, width: float, mono: bool, bold: bool = False
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
            if current and not _fits(joined, size, width, mono, bold):
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


def _draw(spans: tuple[Span, ...], x: float, y: float, size: float, mono: bool,
          bold: bool = False) -> list[bytes]:
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
    out = [f"BT /{face} {size:g} Tf {x:g} {y:g} Td (".encode("latin-1")
           + _escape(drawn[0].text) + b") Tj"]
    for span in drawn[1:]:
        want = _face(bold or span.bold, mono)
        if want != face:
            out.append(f"/{want} {size:g} Tf".encode("latin-1"))
            face = want
        out.append(b"(" + _escape(span.text) + b") Tj")
    out.append(b"ET")
    return [b" ".join(out)]


def _unsupported(text: str) -> str:
    return "".join(sorted({c for c in text if c.encode("latin-1", "ignore") == b""}))


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

        width = USABLE - style.indent - (marker_width if block.marker else 0)
        lines = _wrap_spans(block.spans, style.size, width, style.monospace, style.bold)

        # A heading alone at the foot of a page reads as a caption for nothing.
        # It moves with the first line of what follows it.
        needed = leading * (len(lines) + (1 if block.kind is Kind.HEADING else 0))
        if y - style.space_above - needed < BOTTOM and current:
            pages.append(current)
            current, y = [], float(TOP)

        y -= style.space_above
        for line_number, line in enumerate(lines):
            x = MARGIN + style.indent + (marker_width if block.marker else 0)
            if block.marker and line_number == 0:
                current.extend(_draw(
                    (Span(block.marker),), MARGIN + style.indent, y, style.size, False
                ))
            current.extend(_draw(line, x, y, style.size, style.monospace, style.bold))
            y -= leading
        del index

    if current or not pages:
        pages.append(current)

    return _assemble(pages, _unsupported(text + title))


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
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont " + base + b" >>")

    for sheet, content_id in zip(pages, content_ids):
        stream = b"\n".join(sheet)
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


__all__ = ["Rendered", "render", "Block"]
