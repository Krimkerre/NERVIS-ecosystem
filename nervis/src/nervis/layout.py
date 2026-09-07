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
from dataclasses import dataclass, replace
from enum import Enum


class Kind(str, Enum):
    """What a block is, which decides how it is drawn rather than what it says."""

    HEADING = "heading"
    BULLET = "bullet"
    NUMBERED = "numbered"
    PARAGRAPH = "paragraph"
    BLANK = "blank"
    CODE = "code"
    TABLE_ROW = "table_row"


@dataclass(frozen=True)
class Span:
    """A run of text and whether it is bold.

    Bold, italic and inline code. **The last two were left out on purpose and
    the reasoning has been revised rather than reversed.**

    It read: `*` is used for emphasis and for multiplication and for footnote
    markers, and guessing wrong italicises half a sentence. That is true of a
    naive matcher and stays true — so italic requires the markers to *flank*
    their content the way CommonMark does: the opener followed by a non-space
    and the closer preceded by one. `2 * 3 * 4` is arithmetic under that rule
    and `*remote*` is emphasis, which is the distinction the original worry was
    about.

    Inline code never had that problem. A backtick means one thing in prose,
    models write them constantly, and leaving them unparsed printed the
    backticks — so a transcript quoting `ravis/local` showed the punctuation
    instead of the styling, which reads as the renderer being broken rather
    than as a considered omission.
    """

    text: str
    bold: bool = False
    #: `*emphasis*`, drawn in the oblique face.
    italic: bool = False
    #: `` `code` ``, drawn in the monospace face on a tint. Carried as a flag
    #: rather than as a block kind because it happens *inside* a sentence, and a
    #: block would break the line it belongs to.
    code: bool = False


@dataclass(frozen=True)
class Block:
    """One laid-out thing: a heading, a bullet, a paragraph."""

    kind: Kind
    spans: tuple[Span, ...] = ()
    #: Heading level, 1 the largest. Zero for everything else.
    level: int = 0
    #: The marker a list item is drawn with — a number keeps its own. A table
    #: row uses it for `header`, which is the only thing a row is or is not.
    marker: str = ""
    #: A table row's cells. Empty for everything else.
    #:
    #: **A row is one block, and `spans` still holds it as text.** A renderer
    #: that draws a real grid reads `cells`; every other consumer — the
    #: transcript exporter measuring lines, anything counting words — reads
    #: `spans` and gets the row as `a | b | c`, which is what it always got
    #: from a markdown table before this existed. Adding structure did not
    #: take the old reading away.
    cells: tuple[tuple[Span, ...], ...] = ()


_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+•]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*(\d{1,3})[.)]\s+(.*)$")
_FENCE = re.compile(r"^\s*```")

#: A markdown table row: at least one pipe, with content between the outer
#: ones. The leading and trailing pipes are optional, which is how people
#: actually write them.
_TABLE_ROW = re.compile(r"^\s*\|?(?:[^|\n]*\|)+[^|\n]*\|?\s*$")

#: The `|---|:--:|` line under a header. Its presence is what makes the rows
#: around it a table at all — a sentence containing a pipe is not one.
_TABLE_RULE = re.compile(r"^\s*\|?(?:\s*:?-{2,}:?\s*\|)+\s*:?-{2,}:?\s*\|?\s*$")

# `**bold**`. Non-greedy and requiring content, so `**` alone is not a marker
# and a line of asterisks stays a line of asterisks.
_BOLD = re.compile(r"\*\*(.+?)\*\*")

# `` `code` ``. Unambiguous: a backtick means one thing in prose, so this needs
# none of the flanking care italic does.
_CODE = re.compile(r"`([^`]+)`")

# `*emphasis*`, with CommonMark's flanking rule: the opener is followed by a
# non-space and the closer preceded by one. That is what keeps `2 * 3 * 4`
# arithmetic while `*remote*` is emphasis — the distinction the original
# decision not to parse italic at all was worried about.
_ITALIC = re.compile(r"(?<!\*)\*(?!\s)([^*]+?)(?<!\s)\*(?!\*)")

# The same pattern minus the opening `(?<!\*)` lookbehind. `_marked` below
# scans left to right by re-searching from a moving `pos` rather than always
# from the start of the string (that quadratic mistake is the whole reason
# this file changed — see its docstring). A lookbehind evaluated against that
# moving `pos` sees whatever character happens to sit at `pos - 1`, which is
# real text one call and, the next, one character behind a span that was
# already sliced off and returned — the lookbehind can't tell those apart, so
# the same input silently parses two different ways depending on where the
# scan last stopped. `_marked` re-applies the boundary itself, checked against
# the true previous character in the *original* string, only once a candidate
# has actually won the position it starts at.
_ITALIC_OPEN_UNCHECKED = re.compile(r"\*(?!\s)([^*]+?)(?<!\s)\*(?!\*)")

#: Tried outermost first, and the order is load-bearing twice. `**bold**` before
#: `*italic*`, or the outer pair of a bold phrase matches as emphasis around a
#: starred word. And **code last**, which was wrong the first time: with code
#: first, ``**bold with `code` inside**`` split on the backticks and left the
#: asterisks as literal text on both sides, because the bold pattern never saw
#: an intact phrase to match.
_MARKS = ((_BOLD, "bold"), (_ITALIC_OPEN_UNCHECKED, "italic"), (_CODE, "code"))


def _spans(text: str) -> tuple[Span, ...]:
    """Split one line into bold and plain runs.

    Returns a single plain span when nothing is marked, which keeps the common
    case free of allocation and the drawing code free of a special case.
    """
    return _marked(text, 0) or (Span(""),)


def _find_from(
    pattern: re.Pattern[str],
    text: str,
    cache: dict[re.Pattern[str], re.Match[str] | None],
    exhausted: set[re.Pattern[str]],
    search_from: int,
) -> re.Match[str] | None:
    """`pattern.search(text, search_from)`, remembering the answer.

    A Claude Security scan found the previous version of this cache: it
    remembered a *match*, but treated "no match anywhere past here" as if it
    proved nothing, and re-ran the full remaining-text search on every single
    step of `_marked`'s scan — quadratic in the length of the line, and the
    reason a long run with only two of the three mark kinds present (long
    stretches of bold with no code, say) could hang. The fix is that within one
    call to `_marked`, `search_from` for a given pattern never goes backwards —
    it is always `max` of where the scan last gave up on that pattern and where
    the outer loop currently stands. So once `pattern.search` reports nothing
    at or after some position, nothing can appear there on a *later*, larger
    `search_from` either, and `exhausted` remembers that for the rest of this
    call instead of re-earning it every step.
    """
    if pattern in exhausted:
        return None
    cached = cache.get(pattern)
    if cached is None or cached.start() < search_from:
        found = pattern.search(text, search_from)
        cache[pattern] = found
        if found is None:
            exhausted.add(pattern)
        return found
    return cached


def _next_mark(
    text: str,
    pos: int,
    cache: dict[re.Pattern[str], re.Match[str] | None],
    exhausted: set[re.Pattern[str]],
    resume: dict[re.Pattern[str], int],
) -> tuple[int, int, re.Match[str], str] | None:
    """The earliest mark starting at or after `pos`, or `None` past the last one.

    Split out of `_marked` on its own — a rejected `_ITALIC_OPEN_UNCHECKED`
    candidate (see below) has to retry the whole "which mark opens first"
    question rather than settle for the next-best one, and that retry is what
    pushed `_marked` over ruff's complexity limit.
    """
    while True:
        hits = []
        for index, (pattern, mark) in enumerate(_MARKS):
            found = _find_from(pattern, text, cache, exhausted, max(resume[pattern], pos))
            if found is not None:
                hits.append((found.start(), index, found, mark))
        if not hits:
            return None
        start, index, found, mark = min(hits, key=lambda hit: (hit[0], hit[1]))
        # `_ITALIC_OPEN_UNCHECKED` dropped the real pattern's `(?<!\*)`, so a
        # candidate that opens right after a literal `*` — two stray asterisks,
        # not one already spent by a mark that starts exactly at `pos` — is not
        # really an opener at all. Reject it and make this pattern resume one
        # character later, same as the lookbehind would have, then look again;
        # the winning candidate can still be bold or code found in the meantime.
        if mark == "italic" and start > pos and text[start - 1] == "*":
            resume[_ITALIC_OPEN_UNCHECKED] = start + 1
            cache[_ITALIC_OPEN_UNCHECKED] = None
            continue
        return start, index, found, mark


def _marked(text: str, depth: int) -> tuple[Span, ...]:
    """Split on each mark in turn, left to right, then recurse into each one.

    Walks `text` once with a moving cursor `pos` rather than recursing on the
    unmatched remainder as the very first version of this function did —that
    version re-searched from the start of an ever-shrinking string, which is
    fine for the two symmetric marks but cannot see a genuine `_ITALIC` open
    boundary once the character before it has already been sliced away. This
    version searches the one string that has everything: `text` itself, moving
    `pos` forward instead of moving `text`'s start.

    `depth` stops the recursion at the number of mark kinds there are: a run
    that has been through all of them has nothing left to find, and without the
    bound a pattern that matched its own output would not terminate.
    """
    if not text or depth >= len(_MARKS):
        return (Span(text),) if text else ()
    spans: list[Span] = []
    pos = 0
    cache: dict[re.Pattern[str], re.Match[str] | None] = {}
    exhausted: set[re.Pattern[str]] = set()
    # Where each pattern is allowed to resume from — separate from `pos`
    # because a rejected italic candidate (see `_next_mark`) needs to be
    # skipped without also skipping past text no other mark has looked at yet.
    resume = {pattern: 0 for pattern, _ in _MARKS}
    while pos < len(text):
        # **Whichever mark opens first, not whichever kind is listed first.**
        # A fixed order gets one nesting right and the other wrong: code-first
        # leaves the asterisks in ``**bold with `code` inside**``, and
        # bold-first lets emphasis run inside a code span and print the
        # backticks. Which one encloses the other is a fact about *this*
        # string, and where each opens is how to read it. Ties keep `_MARKS`
        # order, so `**` beats `*` at the same position.
        hit = _next_mark(text, pos, cache, exhausted, resume)
        if hit is None:
            break
        start, index, found, mark = hit
        if start > pos:
            spans.append(Span(text[pos:start]))
        # **Recursed into, except for code.** A bold phrase may contain a
        # backtick and the run inside it is both; wrapping the match in one span
        # would keep the backticks as text. Code is the exception on purpose:
        # what is inside one is not markup, so an asterisk there stays an
        # asterisk.
        if mark == "code":
            spans.append(Span(found.group(1), code=True))
        else:
            # `# type: ignore` with its sentence, per §14.1's rule for an
            # exemption: `mark` is one of `_MARKS`' three field names, chosen by
            # the loop above, and `dataclasses.replace` cannot be checked
            # against a keyword the checker only sees as `str`. The alternative
            # is three near-identical branches to tell mypy what `_MARKS`
            # already says — more code, saying it twice, to check nothing that
            # is actually in doubt.
            spans.extend(
                replace(span, **{mark: True})  # type: ignore[arg-type]
                for span in _marked(found.group(1), index)
            )
        pos = found.end()
        for pattern in resume:
            resume[pattern] = max(resume[pattern], pos)
    if pos < len(text):
        spans.append(Span(text[pos:]))
    return tuple(spans) if spans else (Span(text),)


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
        if _TABLE_RULE.match(line):
            # The rule itself is never drawn: it says the row above was a
            # header, which is the one thing a table row is or is not.
            if blocks and blocks[-1].kind is Kind.TABLE_ROW:
                blocks[-1] = replace(blocks[-1], marker="header")
            continue
        blocks.append(_block_of(line))

    return _collapse(_settled(blocks))


def _block_of(line: str) -> Block:
    """One line of prose as the block it is.

    Split from `parse` at the complexity gate, which was pointing at exactly
    what it usually points at: a loop doing two jobs. `parse` now owns the
    things that need the lines *around* them — a fence being open, a rule
    naming the row above it — and this owns the ones a line answers alone.
    """
    if not line.strip():
        return Block(Kind.BLANK)

    heading = _HEADING.match(line)
    if heading:
        return Block(
            Kind.HEADING, _spans(heading.group(2).strip()), level=len(heading.group(1))
        )

    numbered = _NUMBERED.match(line)
    if numbered:
        return Block(
            Kind.NUMBERED, _spans(numbered.group(2).strip()), marker=f"{numbered.group(1)}."
        )

    bullet = _BULLET.match(line)
    if bullet:
        # A middle dot rather than a bullet: Latin-1 carries `·` and not
        # `•`, and the base-14 encoding is what the PDF writer can express.
        # A visible dot beats a substituted question mark.
        return Block(Kind.BULLET, _spans(bullet.group(1).strip()), marker="·")

    if "|" in line and _TABLE_ROW.match(line):
        cells = _row_cells(line)
        return Block(
            Kind.TABLE_ROW,
            _spans(" | ".join(cell.strip() for cell in cells)),
            cells=tuple(_spans(cell.strip()) for cell in cells),
        )

    return Block(Kind.PARAGRAPH, _spans(line.strip()))


def _row_cells(line: str) -> list[str]:
    """One table line's cells, outer pipes dropped."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return stripped.split("|")


def _settled(blocks: list[Block]) -> list[Block]:
    """Rows that turned out not to be a table become paragraphs again.

    **A separator line is what makes a table.** Markdown says so, and it is
    also the only defensible rule here: `a | b` is a perfectly ordinary
    sentence about alternatives, and a renderer that drew every line
    containing a pipe as a grid would turn prose into furniture. So rows are
    collected optimistically while parsing and a run without a header is put
    back, unchanged — the `spans` they carry are the line's own text.
    """
    out: list[Block] = []
    index = 0
    while index < len(blocks):
        if blocks[index].kind is not Kind.TABLE_ROW:
            out.append(blocks[index])
            index += 1
            continue
        start = index
        while index < len(blocks) and blocks[index].kind is Kind.TABLE_ROW:
            index += 1
        run = blocks[start:index]
        if any(block.marker == "header" for block in run):
            out.extend(run)
        else:
            out.extend(replace(block, kind=Kind.PARAGRAPH, cells=()) for block in run)
    return out


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
            # **The space goes in here, where the line break was.** This used
            # to concatenate, on the reasoning that the wrapper splits every run
            # into words and re-joins them with single spaces so a separator
            # would be dropped and reinserted. That was true only because the
            # wrapper inserted a space at *every* run boundary — including ones
            # the author never wrote, so `**bound**,` drew as `bound ,`. Once
            # the wrapper started asking whether the source had a space there,
            # this join had to answer honestly: a hard-wrapped line ends with a
            # word break, and `every` + `figure` is `everyfigure` without it.
            joined = out[-1].spans
            if joined and block.spans:
                joined = joined[:-1] + (
                    replace(joined[-1], text=joined[-1].text + " "),
                )
            out[-1] = Block(Kind.PARAGRAPH, joined + block.spans)
            continue
        out.append(block)
    while out and out[-1].kind is Kind.BLANK:
        out.pop()
    return out


#: The dashboard's own palette, in PDF's 0-1 RGB.
#:
#: **The same values the screen uses**, read off its CSS rather than chosen
#: again here: an export that arrived in tasteful greys would look like it came
#: from a different program than the one that produced it. This is a dark page
#: on purpose — it is a record of a conversation held on a dark console, and it
#: is read on a screen far more often than it is printed.
PAPER = (0.027, 0.035, 0.051)     # --bg  #07090d
PANEL = (0.043, 0.067, 0.098)     # --surface, the tint behind code
INK = (0.847, 0.882, 0.918)       # --text #d8e1ea
ACCENT = (0.490, 0.424, 1.000)    # --accent #7d6cff
CYAN = (0.204, 0.902, 0.949)      # --cyan #34e6f2
MUTED = (0.443, 0.506, 0.584)     # --muted #718195
RULE = (0.125, 0.165, 0.208)      # --line #202a35

#: The chat window's two bubbles, which the transcript is drawn as.
#:
#: Read off `.bubble` and `.bubble.user` rather than invented: an export meant to
#: look like the conversation has to use the conversation's own two fills, or it
#: is a different design that happens to share a palette.
#: The logo's own two colours, and **neither of them is the accent.** The rule
#: in the stylesheet reads `border: 1px solid var(--accent)`, which is the
#: purple — and a later rule overrides it. Read off the running page with
#: `getComputedStyle` rather than off the source, because the source says
#: something that is not true by the time it renders.
LOGO_EDGE = (0.333, 0.863, 1.000)     # rgb(85, 220, 255), the mark's outline
LOGO_CORE = (0.204, 0.902, 0.949)     # --cyan #34e6f2, the square inside it

BUBBLE = (0.071, 0.106, 0.141)        # .bubble #121b24
BUBBLE_EDGE = (0.125, 0.165, 0.208)   # --line
MINE = (0.090, 0.227, 0.380)          # .bubble.user #173a61
MINE_EDGE = (0.153, 0.349, 0.541)     # .bubble.user border #27598a


@dataclass
class Style:
    """How each kind is drawn. One place, so the page has a consistent voice."""

    size: float
    bold: bool = False
    #: Blank space above, in points. Headings breathe; paragraphs do not.
    space_above: float = 0
    indent: float = 0
    monospace: bool = False
    #: The ink this block is set in.
    colour: tuple[float, float, float] = INK
    #: A hairline across the measure above this block, and how far above it.
    rule_above: float = 0
    #: A tint behind the block, for code.
    tint: tuple[float, float, float] | None = None
    #: Extra space between characters, in points. What makes a label a label.
    tracking: float = 0
    #: Whether the text is set in capitals.
    upper: bool = False


#: Heading sizes fall away quickly and then stop: past the third level the
#: difference stops being visible and starts being noise.
STYLES: dict[Kind, Style] = {
    Kind.HEADING: Style(size=15, bold=True, space_above=10, colour=ACCENT),
    Kind.PARAGRAPH: Style(size=11),
    Kind.BULLET: Style(size=11, indent=16),
    Kind.NUMBERED: Style(size=11, indent=16),
    Kind.CODE: Style(size=9.5, monospace=True, indent=12, colour=CYAN, tint=PANEL),
    Kind.BLANK: Style(size=11),
    # A shade smaller than prose: a table is read by scanning columns, and a
    # size that fits more of one across the measure is worth more than one
    # that matches the paragraph beside it.
    Kind.TABLE_ROW: Style(size=10),
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
        # The title in cyan, the speakers in the accent — the same two the
        # dashboard's own header uses, in the same order.
        colour=CYAN if block.level == 1 else ACCENT,
        # Uppercase and letterspaced at level two, which is what turns a speaker
        # name into a label rather than a sentence. The screen does this to
        # every heading it draws.
        tracking=1.6 if block.level == 2 else (0.8 if block.level == 1 else 0),
        upper=block.level == 2,
        # A hairline above every heading below the title. It is what separates
        # one turn of a transcript from the next without an empty band of
        # whitespace doing the work, and the title needs no line above it
        # because the top of the page already is one.
        rule_above=7 if block.level == 2 else 0,
    )

