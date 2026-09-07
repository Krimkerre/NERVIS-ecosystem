"""A copy of the attached document with this reply's comments placed in it.

**The model never retypes the document.** That was the design this replaces:
"save the last reply" asked a model to reproduce a forty-two-page blueprint
plus its comments in one reply, and it did what any model does with ninety-
seven thousand characters it cannot retype — it wrote `[Original intact]`
where the original should have been, and the saved file was placeholders. A
model quotes reliably where it does not retype reliably, so the reply carries
comments only, each one anchored by a short line quoted verbatim from the
document, and NERVIS — which holds the original — does the placing.

**Two shapes of original, two ways of placing.** A PDF is kept page for page,
byte for byte, the way an annotated copy actually is: the pages are the
person's own, and a comment becomes a page inserted after the one it quotes,
rendered in the original's own extracted style so it reads as part of the
same document. Re-rendering the original's extracted text would have flattened
whatever design it had, and a PDF is not reflowable in a way that lets a
paragraph be inserted under a section anyway. A text original — `.md`,
`.txt` — *is* reflowable, so there the comment goes straight under the
paragraph it quotes.

Anything the model wrote without a quote is not lost: it lands at the end,
under its own heading, rather than being placed by guesswork.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from pypdf import PdfReader, PdfWriter

from nervis import pdf
from nervis.style import StyleProfile


#: One comment, as the model is asked to write it: a quoted line, then the
#: comment. `anchor` is empty for text that arrived with no quote at all.
@dataclass(frozen=True)
class Comment:
    anchor: str
    text: str


#: How far a quoted anchor is trusted before it is shortened. A model quoting a
#: heading gets it exactly; one quoting a sentence sometimes paraphrases the
#: tail, and the first thirty characters are usually still verbatim.
_ANCHOR_PREFIX = 30

_QUOTE_LINE = re.compile(r"^\s*>\s?(.*)$")


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


def find_in(anchor: str, passages: list[str]) -> int | None:
    """Which passage the anchor was quoted from, or `None`.

    Whitespace and case are ignored on both sides because a PDF's extracted
    text breaks lines where the page did, not where the sentence did. Tried
    exactly first, then by its opening characters, and never by anything
    looser: a comment placed on the wrong page is worse than one placed at the
    end with an honest heading over it.

    **The last passage it appears in, not the first.** Measured on the
    blueprint this was built for: forty-four comments anchored on section
    headings, and nearly all of them landed after page two — the table of
    contents, where every heading appears once before it appears at its
    section. A heading's last appearance is the section itself, or its final
    page under a running header, and either is where "under the section"
    means. A quoted sentence from a passage appears once, so for it the two
    rules agree.
    """
    wanted = _normalised(anchor)
    if not wanted:
        return None
    for attempt in (wanted, wanted[:_ANCHOR_PREFIX]):
        if len(attempt) < 8:
            break
        found = [index for index, passage in enumerate(passages)
                 if attempt in _normalised(passage)]
        if found:
            return found[-1]
    return None


def _as_markdown(comments: list[Comment]) -> str:
    parts: list[str] = []
    for comment in comments:
        if comment.anchor:
            parts.append(f"**On:** “{comment.anchor}”")
        parts.append(comment.text)
    return "\n\n".join(part for part in parts if part)


def annotate_pdf(
    original: bytes, comments: list[Comment], style: StyleProfile | None
) -> pdf.Rendered:
    """The original PDF, every page kept, with comment pages placed after the
    pages they quote and the rest at the end."""
    reader = PdfReader(io.BytesIO(original))
    passages = [page.extract_text() or "" for page in reader.pages]
    placed: dict[int, list[Comment]] = {}
    loose: list[Comment] = []
    for comment in comments:
        where = find_in(comment.anchor, passages) if comment.anchor else None
        if where is None:
            loose.append(comment)
        else:
            placed.setdefault(where, []).append(comment)

    writer = PdfWriter()
    unsupported = ""
    for index, page in enumerate(reader.pages):
        writer.add_page(page)
        if index in placed:
            note = pdf.render(
                f"Comments on page {index + 1}",
                f"# Comments on page {index + 1}\n\n{_as_markdown(placed[index])}",
                style,
            )
            unsupported += note.unsupported
            for extra in PdfReader(io.BytesIO(note.data)).pages:
                writer.add_page(extra)
    if loose:
        note = pdf.render(
            "Further comments", f"# Further comments\n\n{_as_markdown(loose)}", style
        )
        unsupported += note.unsupported
        for extra in PdfReader(io.BytesIO(note.data)).pages:
            writer.add_page(extra)

    out = io.BytesIO()
    writer.write(out)
    return pdf.Rendered(data=out.getvalue(), pages=len(writer.pages), unsupported=unsupported)


def annotate_text(original: str, comments: list[Comment]) -> str:
    """A text original with each comment under the paragraph it quotes."""
    paragraphs = re.split(r"\n\s*\n", original.strip()) if original.strip() else []
    placed: dict[int, list[Comment]] = {}
    loose: list[Comment] = []
    for comment in comments:
        where = find_in(comment.anchor, paragraphs) if comment.anchor else None
        if where is None:
            loose.append(comment)
        else:
            placed.setdefault(where, []).append(comment)

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
