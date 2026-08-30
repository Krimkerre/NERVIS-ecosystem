"""The PDF writer, checked against the format rather than against itself.

A renderer that only satisfies its own tests produces files nothing opens, so
the structural assertions here are the ones a reader makes: the header, the
cross-reference offsets, and the trailer pointing at them.
"""
from __future__ import annotations

import re

from nervis.pdf import LINE_LENGTH, LINES_PER_PAGE, render


def test_it_is_a_pdf_a_reader_would_recognise() -> None:
    out = render("Report", "hello").data

    assert out.startswith(b"%PDF-1.4")
    assert out.rstrip().endswith(b"%%EOF")
    assert b"/Type /Catalog" in out
    assert b"/Type /Page " in out or b"/Type /Page\n" in out


def test_the_xref_offsets_point_at_their_objects() -> None:
    """The part a reader actually uses, and the part that is silently wrong if
    the file is assembled in one pass: every offset must land on `N 0 obj`."""
    out = render("Report", "hello").data

    # Located through `startxref`, the way a reader does — and not by searching
    # for "xref", which matches inside "startxref" and finds the pointer rather
    # than the table.
    table = out[int(re.search(rb"startxref\n(\d+)", out).group(1)):]
    offsets = [int(m) for m in re.findall(rb"^(\d{10}) 00000 n", table, re.M)]
    assert offsets, "no in-use entries in the cross-reference table"
    for number, offset in enumerate(offsets, start=1):
        assert out[offset:].startswith(f"{number} 0 obj".encode())


def test_startxref_points_at_the_table() -> None:
    out = render("Report", "hello").data

    start = int(re.search(rb"startxref\n(\d+)", out).group(1))
    assert out[start:start + 4] == b"xref"


def test_long_text_becomes_more_than_one_page() -> None:
    out = render("Report", "\n".join(f"line {n}" for n in range(LINES_PER_PAGE * 2 + 5)))

    assert out.pages == 3
    assert out.data.count(b"/Type /Page ") >= 3 or out.data.count(b"/Type /Page\n") >= 3


def test_parentheses_and_backslashes_do_not_break_the_syntax() -> None:
    """Unescaped, `)` closes the string early and everything after it is read as
    PDF operators — the file opens and renders garbage, which is worse than
    failing."""
    out = render("Report", r"a (b) c \ d").data

    assert rb"\(b\)" in out
    assert rb"\\" in out


def test_a_backslash_is_escaped_once_not_twice() -> None:
    """Escaping the parentheses first would double the backslashes this adds,
    turning one `\\` into `\\\\\\\\` and printing it wrong."""
    out = render("Report", "a\\b").data

    assert rb"(a\\b)" in out


def test_a_word_longer_than_a_line_is_broken_rather_than_overflowing() -> None:
    """A URL or a hash is exactly the thing that runs off the edge. Half of it
    on the page beats none of it visible."""
    out = render("Report", "x" * (LINE_LENGTH * 2 + 10)).data

    assert b"(" + b"x" * LINE_LENGTH + b") Tj" in out


def test_characters_latin_1_cannot_carry_are_reported_not_swallowed() -> None:
    """A silent `?` in somebody's report is a defect nobody can see. The caller
    is told, and decides whether to say so."""
    out = render("Report", "temperature 20°C and a bullet • here and 漢字")

    assert "漢" in out.unsupported
    assert "°" not in out.unsupported, "Latin-1 carries the degree sign"


def test_empty_text_still_produces_a_readable_file() -> None:
    """An empty report is a legitimate outcome — a summary of nothing found —
    and a zero-page PDF is not a file any reader opens."""
    out = render("Report", "")

    assert out.pages == 1
    assert out.data.startswith(b"%PDF-1.4")


def test_blank_lines_survive_as_blank_lines() -> None:
    """Paragraph breaks are most of what makes prose readable, and a wrapper
    that collapses them turns a report into a wall."""
    out = render("Report", "first\n\nsecond").data

    assert b"() Tj" in out
