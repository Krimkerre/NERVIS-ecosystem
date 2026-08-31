"""Turning a model's reply into blocks a page can be laid out from.

**Why this exists separately from `pdf.py`.** One is "what does this text mean"
and the other is "where do the glyphs go", and mixing them produces a renderer
that can only ever draw one kind of document. Splitting them means the parsing
is testable without reading PDF bytes, and the drawing is testable without
parsing anything.

**The input is markdown because that is what the models write.** Nobody chose
it — ask any chat model for a summary and it comes back with `##` headings and
`-` bullets whether or not anything renders them. Today those characters are
printed literally into the PDF, which is the actual complaint: a reply that
looks structured on screen looks like punctuation on the page.

Deliberately a small subset. Headings, bullets, numbered items, bold, and
paragraphs — the things a summary is made of. No tables, no images, no nested
lists, no links: each is a real amount of work, and a renderer that half-draws a
table is worse than one that leaves the pipe characters visible and honest.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Kind(str, Enum):
    """What a block is, which decides how it is drawn rather than what it says."""

    HEADING = "heading"
    BULLET = "bullet"
    NUMBERED = "numbered"
    PARAGRAPH = "paragraph"
    BLANK = "blank"
    CODE = "code"


@dataclass(frozen=True)
class Span:
    """A run of text and whether it is bold.

    Bold is the only weight carried. Italic exists in the base-14 fonts and is
    not parsed, because `*` is used for emphasis and for multiplication and for
    footnote markers, and guessing wrong italicises half a sentence.
    """

    text: str
    bold: bool = False


@dataclass(frozen=True)
class Block:
    """One laid-out thing: a heading, a bullet, a paragraph."""

    kind: Kind
    spans: tuple[Span, ...] = ()
    #: Heading level, 1 the largest. Zero for everything else.
    level: int = 0
    #: The marker a list item is drawn with — a number keeps its own.
    marker: str = ""


_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+•]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*(\d{1,3})[.)]\s+(.*)$")
_FENCE = re.compile(r"^\s*```")

# `**bold**`. Non-greedy and requiring content, so `**` alone is not a marker
# and a line of asterisks stays a line of asterisks.
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _spans(text: str) -> tuple[Span, ...]:
    """Split one line into bold and plain runs.

    Returns a single plain span when nothing is marked, which keeps the common
    case free of allocation and the drawing code free of a special case.
    """
    parts: list[Span] = []
    at = 0
    for found in _BOLD.finditer(text):
        if found.start() > at:
            parts.append(Span(text[at:found.start()]))
        parts.append(Span(found.group(1), bold=True))
        at = found.end()
    if at < len(text):
        parts.append(Span(text[at:]))
    return tuple(parts) or (Span(""),)


def parse(text: str) -> list[Block]:
    """A reply as blocks, in order.

    Fenced code is passed through verbatim, markers and all: the fence is the
    author saying *this is not prose*, and reflowing it or stripping its
    asterisks would corrupt the one kind of content where every character is
    load-bearing.
    """
    blocks: list[Block] = []
    fenced = False

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            blocks.append(Block(Kind.CODE, (Span(line),)))
            continue

        if not line.strip():
            blocks.append(Block(Kind.BLANK))
            continue

        heading = _HEADING.match(line)
        if heading:
            blocks.append(Block(
                Kind.HEADING, _spans(heading.group(2).strip()), level=len(heading.group(1))
            ))
            continue

        numbered = _NUMBERED.match(line)
        if numbered:
            blocks.append(Block(
                Kind.NUMBERED, _spans(numbered.group(2).strip()), marker=f"{numbered.group(1)}."
            ))
            continue

        bullet = _BULLET.match(line)
        if bullet:
            # A middle dot rather than a bullet: Latin-1 carries `·` and not
            # `•`, and the base-14 encoding is what the PDF writer can express.
            # A visible dot beats a substituted question mark.
            blocks.append(Block(Kind.BULLET, _spans(bullet.group(1).strip()), marker="·"))
            continue

        blocks.append(Block(Kind.PARAGRAPH, _spans(line.strip())))

    return _collapse(blocks)


def _collapse(blocks: list[Block]) -> list[Block]:
    """Drop runs of blank blocks, join wrapped paragraphs, tidy the edges.

    Two blank lines in the source are one paragraph break on the page, and a
    reply that ends with a newline should not end with an empty band of
    whitespace.

    **And consecutive paragraph lines are one paragraph**, which is what
    markdown means by them and what makes the page look set rather than
    transcribed. Models hard-wrap their prose at whatever width they were
    trained to, and a renderer that honours those breaks reproduces somebody
    else's line length on a page of a different width — every paragraph ending
    two-thirds of the way across the measure. Joining them lets the wrapper
    break at the page's width instead.

    Only paragraphs join. A line under a bullet that is not itself a bullet is
    markdown's *lazy continuation* and belongs to the item above it; that is not
    implemented, so such a line stays its own paragraph and prints unindented.
    In practice models put a blank line there.
    """
    out: list[Block] = []
    for block in blocks:
        if block.kind is Kind.BLANK and (not out or out[-1].kind is Kind.BLANK):
            continue
        if block.kind is Kind.PARAGRAPH and out and out[-1].kind is Kind.PARAGRAPH:
            # Concatenated rather than joined with a space span: the wrapper
            # splits every run into words and re-joins them with single spaces,
            # so a separator here would be dropped and then reinserted.
            out[-1] = Block(Kind.PARAGRAPH, out[-1].spans + block.spans)
            continue
        out.append(block)
    while out and out[-1].kind is Kind.BLANK:
        out.pop()
    return out


@dataclass
class Style:
    """How each kind is drawn. One place, so the page has a consistent voice."""

    size: float
    bold: bool = False
    #: Blank space above, in points. Headings breathe; paragraphs do not.
    space_above: float = 0
    indent: float = 0
    monospace: bool = False


#: Heading sizes fall away quickly and then stop: past the third level the
#: difference stops being visible and starts being noise.
STYLES: dict[Kind, Style] = {
    Kind.HEADING: Style(size=15, bold=True, space_above=10),
    Kind.PARAGRAPH: Style(size=11),
    Kind.BULLET: Style(size=11, indent=16),
    Kind.NUMBERED: Style(size=11, indent=16),
    Kind.CODE: Style(size=9.5, monospace=True, indent=12),
    Kind.BLANK: Style(size=11),
}

HEADING_SIZES: dict[int, float] = {1: 17, 2: 15, 3: 12, 4: 11}


def style_for(block: Block) -> Style:
    """The style this block is drawn with, heading level included."""
    base = STYLES[block.kind]
    if block.kind is not Kind.HEADING:
        return base
    return Style(
        size=HEADING_SIZES.get(block.level, 11),
        bold=True,
        space_above=12 if block.level <= 2 else 8,
    )

