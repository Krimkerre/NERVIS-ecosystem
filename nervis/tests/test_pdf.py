"""The PDF writer, checked against the format rather than against itself.

A renderer that only satisfies its own tests produces files nothing opens, so
the structural assertions here are the ones a reader makes: the header, the
cross-reference offsets, and the trailer pointing at them.
"""
from __future__ import annotations

import re

from nervis.layout import Block, Kind, Span, parse, style_for
from nervis.pdf import PAGE_HEIGHT, render


def test_it_is_a_pdf_a_reader_would_recognise() -> None:
    out = render("Report", "hello").data

    assert out.startswith(b"%PDF-1.4")
    assert out.rstrip().endswith(b"%%EOF")
    assert b"/Type /Catalog" in out
    assert b"/Type /Page " in out


def test_the_xref_offsets_point_at_their_objects() -> None:
    """The part a reader actually uses, and the part that is silently wrong if
    the file is assembled in one pass: every offset must land on `N 0 obj`."""
    out = render("Report", "hello").data

    # Located through `startxref`, the way a reader does — and not by searching
    # for "xref", which matches inside "startxref" and finds the pointer.
    table = out[int(re.search(rb"startxref\n(\d+)", out).group(1)):]
    offsets = [int(m) for m in re.findall(rb"^(\d{10}) 00000 n", table, re.M)]
    assert offsets, "no in-use entries in the cross-reference table"
    for number, offset in enumerate(offsets, start=1):
        assert out[offset:].startswith(f"{number} 0 obj".encode())


def test_startxref_points_at_the_table() -> None:
    out = render("Report", "hello").data

    start = int(re.search(rb"startxref\n(\d+)", out).group(1))
    assert out[start:start + 4] == b"xref"


def test_all_three_faces_are_declared_on_every_page() -> None:
    """A page whose resources omit a font it uses renders that run as nothing —
    the file opens, and the bold half of a sentence is missing."""
    out = render("Report", "## Heading\n\nplain and **bold**\n\n```\ncode\n```").data

    assert b"/BaseFont /Helvetica " in out or b"/BaseFont /Helvetica\n" in out
    assert b"/BaseFont /Helvetica-Bold" in out
    assert b"/BaseFont /Courier" in out
    assert out.count(b"/F1 3 0 R /F2 4 0 R /F3 5 0 R") == out.count(b"/Type /Page ")


def test_a_heading_is_drawn_larger_and_bold() -> None:
    """The whole point of the layout work: a `##` that used to print as two
    literal hashes at body size is now a heading."""
    out = render("Report", "## Q3 summary\n\nbody text").data

    assert b"(Q3 summary)" in out
    assert b"(## Q3 summary)" not in out
    assert re.search(rb"/F2 1[0-9](?:\.\d+)? Tf", out), "heading should be bold and larger"


def test_bold_inside_a_line_switches_font_and_keeps_the_rest_plain() -> None:
    out = render("Report", "revenue fell **12%** in Q3").data

    assert b"/F2" in out and b"(12%)" in out
    assert b"(**12%**)" not in out


def test_a_bullet_gets_a_marker_and_a_hanging_indent() -> None:
    """The marker is drawn at the indent and the text further right, so a
    wrapped second line aligns under the first rather than under the dot."""
    out = render("Report", "- renewals slipped\n- pricing changed").data

    marker_x = [float(x) for x in
                re.findall(rb"Tf ([\d.]+) [\d.]+ Td \(\xb7\) Tj", out)]
    text_x = [float(x) for x in
              re.findall(rb"Tf ([\d.]+) [\d.]+ Td \(renewals slipped\) Tj", out)]

    assert len(marker_x) == 2, "both bullets should carry a marker"
    assert text_x, "the bullet's text should be drawn"
    # The hanging indent: text starts to the right of its own marker, so a
    # wrapped second line aligns under the first rather than under the dot.
    assert text_x[0] > marker_x[0]


def test_a_numbered_item_keeps_its_own_number() -> None:
    """`1.` and `2.` mean order. Replacing them with a dot loses it."""
    out = render("Report", "1. first\n2. second").data

    assert b"(1.)" in out and b"(2.)" in out


def test_code_is_drawn_monospaced_and_verbatim() -> None:
    """A fence is the author saying *this is not prose*. Reflowing it or
    stripping its markers corrupts the one content where every character
    counts."""
    out = render("Report", "```\n  indented = True  # keep **this**\n```").data

    assert b"/F3" in out
    assert b"**this**" in out, "code must not have its markers parsed away"


def test_parentheses_and_backslashes_do_not_break_the_syntax() -> None:
    """Unescaped, `)` closes the string early and everything after it is read as
    PDF operators — the file opens and renders garbage, which is worse than
    failing."""
    out = render("Report", r"a (b) c \ d").data

    assert rb"\(b\)" in out
    assert rb"\\" in out


def test_a_backslash_is_escaped_once_not_twice() -> None:
    out = render("Report", "a\\b").data

    assert rb"(a\\b)" in out


def test_long_text_becomes_more_than_one_page() -> None:
    out = render("Report", "\n".join(f"line {n} of the report" for n in range(120)))

    assert out.pages > 1
    assert out.data.count(b"/Type /Page ") == out.pages


def test_nothing_is_drawn_below_the_bottom_margin() -> None:
    """The check that a page break actually happened rather than the text
    running off the sheet — every drawn baseline must sit on the page."""
    out = render("Report", "\n".join(f"line {n}" for n in range(200))).data

    baselines = [float(y) for y in re.findall(rb"Tf [\d.]+ ([\d.]+) Td", out)]
    assert baselines
    assert min(baselines) >= 0
    assert max(baselines) <= PAGE_HEIGHT


def test_a_heading_is_not_stranded_at_the_foot_of_a_page() -> None:
    """A heading alone at the bottom reads as a caption for nothing. It has to
    move to the next page with the line it introduces."""
    filler = "\n".join(f"line {n}" for n in range(44))
    out = render("Report", f"{filler}\n\n## A late heading\n\nits paragraph").data

    heading = re.search(rb"BT /F2 1[0-9](?:\.\d+)? Tf [\d.]+ ([\d.]+) Td \(A late heading\)", out)
    assert heading, "the heading should be drawn"
    assert float(heading.group(1)) > 100, "a heading should not sit at the foot of a page"


def test_characters_latin_1_cannot_carry_are_reported_not_swallowed() -> None:
    out = render("Report", "temperature 20°C and 漢字")

    assert "漢" in out.unsupported
    assert "°" not in out.unsupported, "Latin-1 carries the degree sign"


def test_empty_text_still_produces_a_readable_file() -> None:
    """An empty report is a legitimate outcome — a summary of nothing found —
    and a zero-page PDF is not a file any reader opens."""
    out = render("Report", "")

    assert out.pages == 1
    assert out.data.startswith(b"%PDF-1.4")


def test_a_bold_phrase_spanning_a_wrap_stays_bold_on_both_lines() -> None:
    """The naive wrapper resets weight at every break, producing a sentence that
    changes voice mid-air."""
    phrase = " ".join(["emphasis"] * 40)
    out = render("Report", f"lead in **{phrase}** tail").data

    assert out.count(b"/F2") >= 2


def test_the_parser_and_the_renderer_agree_on_what_a_block_is() -> None:
    """`layout` decides meaning and `pdf` decides placement. If the two disagree
    about the set of kinds, one of them silently draws nothing."""
    for block in parse("# h\n\ntext\n\n- b\n\n1. n\n\n```\nc\n```"):
        assert style_for(block).size > 0
    assert style_for(Block(Kind.PARAGRAPH, (Span("x"),))).size > 0
