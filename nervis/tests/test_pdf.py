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

    # Set in capitals, which is a rendering choice and not an edit: the source
    # markdown is untouched, and it is what every heading on the dashboard this
    # came from does. The `##` must still be gone.
    assert b"(Q3 SUMMARY)" in out
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
    # Separated by blank lines: consecutive lines are one paragraph now, and a
    # single reflowed paragraph of this length still fits on one page.
    out = render("Report", "\n\n".join(f"line {n} of the report" for n in range(120)))

    assert out.pages > 1
    assert out.data.count(b"/Type /Page ") == out.pages


def test_nothing_is_drawn_below_the_bottom_margin() -> None:
    """The check that a page break actually happened rather than the text
    running off the sheet — every drawn baseline must sit on the page."""
    out = render("Report", "\n\n".join(f"line {n}" for n in range(200))).data

    baselines = [float(y) for y in re.findall(rb"Tf [\d.]+ ([\d.]+) Td", out)]
    assert baselines
    assert min(baselines) >= 0
    assert max(baselines) <= PAGE_HEIGHT


def test_a_heading_is_not_stranded_at_the_foot_of_a_page() -> None:
    """A heading alone at the bottom reads as a caption for nothing. It has to
    move to the next page with the line it introduces."""
    filler = "\n\n".join(f"line {n}" for n in range(44))
    out = render("Report", f"{filler}\n\n## A late heading\n\nits paragraph").data

    heading = re.search(
        rb"BT [\d.]+ Tc /F2 1[0-9](?:\.\d+)? Tf [\d.]+ ([\d.]+) Td \(A LATE HEADING\)", out
    )
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


def test_a_hard_wrapped_paragraph_is_reflowed_to_the_page() -> None:
    """Models wrap their prose at whatever width they were trained to. Honouring
    those breaks reproduces somebody else's line length on a page of a different
    width, and every paragraph ends two-thirds of the way across the measure."""
    out = render("Report", "Prepared for the meeting and every\nfigure comes from the index.").data

    assert b"(Prepared for the meeting and every figure comes from the index.) Tj" in out


def test_a_blank_line_still_starts_a_new_paragraph() -> None:
    """The falsifier. Joining every paragraph would run a whole document into
    one block and lose every break its author meant."""
    out = render("Report", "first para\n\nsecond para").data

    assert b"(first para) Tj" in out
    assert b"(second para) Tj" in out


def test_a_bold_run_is_not_followed_by_a_gap() -> None:
    """One text object per line, so the viewer advances the pen from the font's
    real metrics. Positioning each run from the width estimate — which errs wide
    — printed "Revenue fell    12%    against Q2"."""
    out = render("Report", "revenue fell **12%** in Q3").data

    line = next(part for part in out.split(b"BT ") if b"revenue fell" in part)
    assert line.count(b"Td") == 1, "a line is placed once; the viewer advances the rest"
    assert b"/F2" in line.split(b"ET")[0], "the weight change happens inside that object"


def test_the_page_has_a_ground_under_it() -> None:
    """A dark page is a rectangle painted before anything else. Without it the
    light text sits on whatever the reader's viewer calls paper, which for this
    palette is nothing at all."""
    out = render("Report", "hello").data

    # The full sheet, filled, before any text object on that page.
    assert re.search(rb"0 0 612 792 re f", out)
    assert out.index(b"re f") < out.index(b"BT")


def test_every_page_is_numbered() -> None:
    out = render("Report", "\n\n".join(f"line {n}" for n in range(200)))

    assert out.pages > 1
    for number in range(1, out.pages + 1):
        assert f"({number} / {out.pages})".encode() in out.data


def test_an_em_dash_survives_instead_of_becoming_a_question_mark() -> None:
    """**Not an edge case — most sentences.** The base-14 fonts default to an
    encoding with no em dash, no curly quotes and no ellipsis, so "the reseller
    — the same model" came out as "the reseller ? the same model". Those are
    exactly the characters a language model writes."""
    out = render("Report", "the reseller — the same model … “quoted” and a • bullet").data

    assert b"?" not in out.split(b"stream")[1].split(b"endstream")[0]
    assert b"/Encoding /WinAnsiEncoding" in out
    assert render("Report", "— … “ ” •").unsupported == ""


def test_something_genuinely_outside_the_encoding_is_still_reported() -> None:
    """The falsifier. WinAnsi is wider than Latin-1 and is still one byte."""
    assert "漢" in render("Report", "temperature 20°C and 漢字").unsupported
    assert "°" not in render("Report", "20°C").unsupported


def test_code_is_drawn_on_a_tint() -> None:
    """The tint goes down before the glyphs do — PDF paints in order, and a
    rectangle drawn after its text hides it."""
    out = render("Report", "```\nravis/chat\n```").data
    stream = out.split(b"stream")[1]

    tint = stream.index(b"re f", stream.index(b"0 0 612 792 re f") + 4)
    assert tint < stream.index(b"(ravis/chat)")
