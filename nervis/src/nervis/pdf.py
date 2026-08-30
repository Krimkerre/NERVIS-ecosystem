"""A PDF of plain text, written without a dependency.

**Why not a library.** The ladder says never add a dependency for what a few
lines can do, and a single-font text PDF is a few lines: the format is mostly
ASCII, the base-14 fonts need no embedding, and everything hard about PDF —
images, colour spaces, embedded fonts, transparency — is not being used. A
renderer that can typeset a text file is about a hundred lines; one that can
typeset a *document* is somebody's career, and this is deliberately the former.

**What this cannot do, stated so nobody is surprised by it.** One font,
Helvetica, at one size. No images, no tables, no styling, no unicode beyond
Latin-1 — the base-14 encoding cannot express the rest, and a silent `?` in
somebody's report is worse than a refusal. Wrapping is by character count rather
than by measured width, so a line of capitals runs wider than a line of commas.
That is the trade for having no dependency, and it is written on the page rather
than discovered.
"""
from __future__ import annotations

from dataclasses import dataclass

#: US Letter at 72 dpi, the format's own unit.
PAGE_WIDTH, PAGE_HEIGHT = 612, 792
MARGIN = 54
FONT_SIZE = 11
LINE_HEIGHT = 15

#: Characters per line before wrapping.
#:
#: Helvetica is proportional, so this is an approximation of the width the
#: margins allow — chosen to be safe for capitals rather than tight for
#: lowercase, because text running off the right edge is unreadable while text
#: stopping early is merely plain.
LINE_LENGTH = 88

#: Lines per page, from the usable height rather than a guess.
LINES_PER_PAGE = (PAGE_HEIGHT - 2 * MARGIN) // LINE_HEIGHT


@dataclass(frozen=True)
class Rendered:
    """The bytes, and what had to be done to the text to produce them."""

    data: bytes
    pages: int
    #: Characters that Latin-1 could not carry, if any. Reported rather than
    #: substituted: a silent `?` in a report is a defect nobody can see.
    unsupported: str


def _escape(line: str) -> bytes:
    """One line as a PDF string literal.

    Backslash first — escaping the parentheses before the backslashes would
    double-escape the backslashes this adds.
    """
    out = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return out.encode("latin-1", errors="replace")


def _wrap(text: str) -> list[str]:
    """Text as display lines, breaking on words where a word fits.

    A word longer than a line is broken rather than allowed to overflow — a URL
    or a hash is exactly the thing that runs off the edge, and half of it on the
    page beats none of it visible.
    """
    lines: list[str] = []
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for word in paragraph.split(" "):
            while len(word) > LINE_LENGTH:
                if current:
                    lines.append(current)
                    current = ""
                lines.append(word[:LINE_LENGTH])
                word = word[LINE_LENGTH:]
            if not current:
                current = word
            elif len(current) + 1 + len(word) <= LINE_LENGTH:
                current = f"{current} {word}"
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def render(title: str, text: str) -> Rendered:
    """`text` as a PDF, one page per `LINES_PER_PAGE` lines.

    Built as a list of objects and then assembled, because the cross-reference
    table needs each object's byte offset and those are only known once the
    bytes before it exist. Writing it in one pass means computing offsets twice
    and having them disagree.
    """
    unsupported = "".join(sorted({c for c in text + title if c.encode("latin-1", "ignore") == b""}))

    lines = _wrap(text)
    pages = [lines[at:at + LINES_PER_PAGE] for at in range(0, len(lines), LINES_PER_PAGE)] or [[]]

    # 1 catalog, 2 pages, 3 font, then a content stream and a page per sheet.
    objects: list[bytes] = []
    content_ids = [4 + n * 2 for n in range(len(pages))]
    page_ids = [5 + n * 2 for n in range(len(pages))]

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(
        f"<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>".encode("latin-1")
    )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for sheet, content_id, page_id in zip(pages, content_ids, page_ids):
        body = [b"BT", f"/F1 {FONT_SIZE} Tf".encode("latin-1"),
                f"{MARGIN} {PAGE_HEIGHT - MARGIN} Td".encode("latin-1"),
                f"{LINE_HEIGHT} TL".encode("latin-1")]
        for line in sheet:
            body.append(b"(" + _escape(line) + b") Tj T*")
        body.append(b"ET")
        stream = b"\n".join(body)
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
            + stream + b"\nendstream"
        )
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
            .encode("latin-1")
        )
        del page_id  # named for readability; the id is positional

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, payload in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("latin-1") + payload + b"\nendobj\n"

    start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n"
        .encode("latin-1")
    )
    return Rendered(data=bytes(out), pages=len(pages), unsupported=unsupported)
