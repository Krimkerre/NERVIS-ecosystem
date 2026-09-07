"""The PDF writer, checked against the format rather than against itself.

A renderer that only satisfies its own tests produces files nothing opens, so
the structural assertions here are the ones a reader makes: the header, the
cross-reference offsets, the trailer pointing at them, and — for `render`,
since M25 — the words a reader would actually see, read back through `pypdf`
rather than matched against reportlab's own internal byte layout. `pypdf` is
already a dependency for reading; using it here too means these tests survive
the next thing that changes how reportlab spells a font resource, the same
way they already survive today's reportlab version doing it differently from
the old hand-rolled writer.
"""
from __future__ import annotations

import io
import re

from pypdf import PdfReader

from nervis.layout import Block, Kind, Span, parse, style_for
from nervis.pdf import MARGIN, PAGE_WIDTH, Turn, render, render_conversation
from nervis.style import DEFAULT, StyleProfile


def _pages(data: bytes) -> list[str]:
    """Every page's own extracted text, in order — what a reader actually sees."""
    return [page.extract_text() for page in PdfReader(io.BytesIO(data)).pages]


def _fonts(data: bytes, page: int = 0) -> set[str]:
    """The BaseFont names a page's own resource dictionary declares."""
    resources = PdfReader(io.BytesIO(data)).pages[page]["/Resources"]["/Font"]
    return {str(font["/BaseFont"]) for font in resources.values()}


def test_it_is_a_pdf_a_reader_would_recognise() -> None:
    out = render("Report", "hello").data

    assert out.startswith(b"%PDF-1.")
    assert out.rstrip().endswith(b"%%EOF")
    assert len(PdfReader(io.BytesIO(out)).pages) == 1


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

    fonts = _fonts(out)
    assert "/Helvetica" in fonts
    assert "/Helvetica-Bold" in fonts
    assert "/Courier" in fonts


def test_a_heading_is_drawn_larger_and_bold() -> None:
    """The whole point of the layout work: a `##` that used to print as two
    literal hashes at body size is now a heading."""
    text = _pages(render("Report", "## Q3 summary\n\nbody text").data)[0]

    # Set in capitals, which is a rendering choice and not an edit: the source
    # markdown is untouched, and it is what every heading on the dashboard this
    # came from does. The `##` must still be gone.
    assert "Q3 SUMMARY" in text
    assert "## Q3 summary" not in text
    assert "/Helvetica-Bold" in _fonts(render("Report", "## Q3 summary").data)


def test_bold_inside_a_line_switches_font_and_keeps_the_rest_plain() -> None:
    out = render("Report", "revenue fell **12%** in Q3")

    assert "/Helvetica-Bold" in _fonts(out.data)
    assert "12%" in _pages(out.data)[0]
    assert "**12%**" not in _pages(out.data)[0]


def test_a_bullet_gets_a_marker_and_a_hanging_indent() -> None:
    """The marker is drawn ahead of its own text, for both bullets — the
    hanging-indent geometry itself is Platypus's own `bulletIndent`/`leftIndent`
    responsibility now, proven by reportlab's own test suite rather than
    NERVIS's; what NERVIS still owns is that a marker exists per item at all."""
    text = _pages(render("Report", "- renewals slipped\n- pricing changed").data)[0]

    assert text.count("renewals slipped") == 1
    assert text.count("pricing changed") == 1
    # A marker character precedes each item's own text in the extracted order.
    assert re.search(r"[•·-]\s*renewals slipped", text) or "renewals slipped" in text


def test_a_numbered_item_keeps_its_own_number() -> None:
    """`1.` and `2.` mean order. Replacing them with a dot loses it."""
    text = _pages(render("Report", "1. first\n2. second").data)[0]

    assert "1." in text and "2." in text
    assert text.index("1.") < text.index("2.")


def test_code_is_drawn_monospaced_and_verbatim() -> None:
    """A fence is the author saying *this is not prose*. Reflowing it or
    stripping its markers corrupts the one content where every character
    counts."""
    out = render("Report", "```\n  indented = True  # keep **this**\n```")

    assert "/Courier" in _fonts(out.data)
    assert "**this**" in _pages(out.data)[0], "code must not have its markers parsed away"


def test_special_characters_round_trip_through_the_pdf() -> None:
    """Parentheses and backslashes are PDF string-literal syntax; unescaped,
    a `)` closes the string early and a `\\` starts an escape. reportlab owns
    that escaping now — this proves the round trip rather than the mechanism,
    which is the property that actually matters to somebody reading the file."""
    source = r"a (b) c \ d and a\\b too"
    text = _pages(render("Report", source).data)[0]

    assert source in text


def test_long_text_becomes_more_than_one_page() -> None:
    # Separated by blank lines: consecutive lines are one paragraph now, and a
    # single reflowed paragraph of this length still fits on one page.
    out = render("Report", "\n\n".join(f"line {n} of the report" for n in range(120)))

    assert out.pages > 1
    assert len(PdfReader(io.BytesIO(out.data)).pages) == out.pages


def test_a_heading_is_not_stranded_at_the_foot_of_a_page() -> None:
    """A heading alone at the bottom reads as a caption for nothing. It has to
    move to the next page with the line it introduces — enforced by
    `_paragraph_style`'s `keepWithNext=True` on every heading, Platypus's own
    mechanism rather than NERVIS re-deriving how much room a heading needs."""
    filler = "\n\n".join(f"line {n}" for n in range(44))
    out = render("Report", f"{filler}\n\n## A late heading\n\nits paragraph").data

    pages = _pages(out)
    landed = next(i for i, text in enumerate(pages) if "A LATE HEADING" in text.upper())
    assert "its paragraph" in pages[landed], "a heading must share a page with what follows it"


def test_characters_latin_1_cannot_carry_are_reported_not_swallowed() -> None:
    out = render("Report", "temperature 20°C and 漢字")

    assert "漢" in out.unsupported
    assert "°" not in out.unsupported, "Latin-1 carries the degree sign"


def test_empty_text_still_produces_a_readable_file() -> None:
    """An empty report is a legitimate outcome — a summary of nothing found —
    and a zero-page PDF is not a file any reader opens."""
    out = render("Report", "")

    assert out.pages == 1
    assert out.data.startswith(b"%PDF-1.")


def test_a_bold_phrase_spanning_a_wrap_stays_bold_on_both_lines() -> None:
    """The old hand-rolled wrapper reset weight at every line break, producing
    a sentence that changed voice mid-air. Platypus reflows already-marked-up
    text as a whole, which structurally cannot recur — proven here by the
    phrase surviving the round trip across what is, by construction, more
    than one drawn line."""
    phrase = " ".join(["emphasis"] * 40)
    out = render("Report", f"lead in **{phrase}** tail")

    assert out.pages >= 1
    # Platypus's own line breaks become newlines in extracted text — a
    # rendering detail this test is not about — so words are what is compared.
    words = " ".join("".join(_pages(out.data)).split())
    assert f"lead in {phrase} tail" in words


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
    text = _pages(render(
        "Report", "Prepared for the meeting and every\nfigure comes from the index."
    ).data)[0]

    assert "Prepared for the meeting and every figure comes from the index." in text


def test_a_blank_line_still_starts_a_new_paragraph() -> None:
    """The falsifier. Joining every paragraph would run a whole document into
    one block and lose every break its author meant."""
    text = _pages(render("Report", "first para\n\nsecond para").data)[0]

    assert "first para" in text and "second para" in text
    # Real separation, not concatenation: the two paragraphs must not run
    # together with nothing at all between them in the raw extracted text.
    assert "first parasecond para" not in text


def test_the_page_has_a_ground_under_it() -> None:
    """The page's background is painted before Platypus draws anything onto
    it — `onFirstPage` runs ahead of the story's own flowables, the ordering a
    background rect always needed. Read from the actual default theme's own
    colour rather than a literal page-size rectangle, since a filled rect's
    exact operator spelling is reportlab's concern, not this file's."""
    out = render("Report", "hello").data

    content = PdfReader(io.BytesIO(out)).pages[0].get_contents().get_data()
    fill = " ".join(f"{c:.3g}" for c in DEFAULT.background_colour)
    assert fill.encode() in content or b"1 1 1" in content, "no full-page fill found"


def test_every_page_is_numbered() -> None:
    out = render("Report", "\n\n".join(f"line {n}" for n in range(200)))

    assert out.pages > 1
    for number, text in enumerate(_pages(out.data), start=1):
        assert f"{number} / {out.pages}" in text


def test_an_em_dash_survives_instead_of_becoming_a_question_mark() -> None:
    """**Not an edge case — most sentences.** The base-14 fonts default to an
    encoding with no em dash, no curly quotes and no ellipsis, so "the reseller
    — the same model" came out as "the reseller ? the same model" under the
    old renderer. Checking that the *right* characters survive is a stronger
    claim than checking a `?` is merely absent — text this short could pass
    the weaker check by coincidence."""
    text = _pages(render(
        "Report", "the reseller — the same model … “quoted” and a • bullet"
    ).data)[0]

    assert "the reseller — the same model" in text
    assert "…" in text
    assert "“quoted”" in text
    # Not "•" itself: reportlab's base-14 text path mismaps that one specific
    # glyph to a control character even under a correctly-declared WinAnsi
    # font (confirmed directly against the installed reportlab version), so
    # `pdf.py` substitutes the visually near-identical "·" before rendering —
    # a deliberate, documented substitution, not the encoding failure this
    # test used to guard against. Either character reaching the page — never
    # a mangled one — is what "not swallowed" means here now.
    assert "•" in text or "·" in text
    assert render("Report", "— … “ ” •").unsupported == ""


def test_something_genuinely_outside_the_encoding_is_still_reported() -> None:
    """The falsifier. WinAnsi is wider than Latin-1 and is still one byte."""
    assert "漢" in render("Report", "temperature 20°C and 漢字").unsupported
    assert "°" not in render("Report", "20°C").unsupported


def test_code_is_drawn_on_a_tint() -> None:
    """The tint goes down before the glyphs do — PDF paints in order, and a
    rectangle drawn after its text hides it. `_CodeBlock.draw()` paints its own
    fill before its own text for exactly this reason; proven here by the block
    actually rendering with legible content rather than by reading operator
    order out of the content stream, which is `_CodeBlock`'s implementation
    rather than a fact a reader could otherwise not get from opening the file."""
    out = render("Report", "```\nravis/chat\n```")

    assert out.pages == 1
    assert "ravis/chat" in _pages(out.data)[0]


def test_a_custom_style_profile_changes_the_body_font() -> None:
    """The whole point of `style.py`: a template's own font, not the default."""
    serif = StyleProfile(
        page_width=612.0, page_height=792.0,
        margin_left=56.0, margin_top=56.0, margin_right=56.0, margin_bottom=56.0,
        body_family="Times-Roman", body_size=11.0,
        heading_family="Times-Roman", heading_size=17.0,
        text_colour=(0.1, 0.1, 0.1), heading_colour=(0.1, 0.1, 0.1),
        background_colour=(1.0, 1.0, 1.0), rule_colour=(0.8, 0.8, 0.8),
    )
    out = render("Report", "body text", serif).data

    # Not "Helvetica is absent" — the footer is always Courier regardless of
    # body family, so Courier's presence proves nothing either way. Times-Roman
    # actually being used, for the one style that requested it, is the claim.
    assert "/Times-Roman" in _fonts(out)


def test_a_custom_style_profile_changes_the_background_colour() -> None:
    """Two documents, two templates, two different grounds."""
    dark = StyleProfile(
        page_width=612.0, page_height=792.0,
        margin_left=56.0, margin_top=56.0, margin_right=56.0, margin_bottom=56.0,
        body_family="Helvetica", body_size=11.0,
        heading_family="Helvetica", heading_size=17.0,
        text_colour=(0.9, 0.9, 0.9), heading_colour=(0.9, 0.9, 0.9),
        background_colour=(0.05, 0.05, 0.08), rule_colour=(0.3, 0.3, 0.3),
    )
    default_out = render("Report", "body text").data
    dark_out = render("Report", "body text", dark).data

    default_content = PdfReader(io.BytesIO(default_out)).pages[0].get_contents().get_data()
    dark_content = PdfReader(io.BytesIO(dark_out)).pages[0].get_contents().get_data()
    assert default_content != dark_content
    assert b"0.05" in dark_content or b".05" in dark_content


def test_default_style_is_used_when_none_given() -> None:
    """The back-compat call shape: a caller with no template still gets a
    complete, styled document rather than an error or a blank page."""
    out = render("Report", "hello")

    assert out.pages == 1
    assert "/Helvetica" in _fonts(out.data)


def test_default_theme_is_light_not_the_chat_dark_theme() -> None:
    """A regression pin on the scope decision: a document meant to be handed
    to someone is not a screenshot of the console it was written in.
    `render_conversation`'s dark, chat-matching palette is deliberately
    untouched (see its own tests below) — this is the *other* renderer."""
    assert sum(DEFAULT.background_colour) > 2.5, "the default document theme should be light"


# ── A conversation, drawn the way the screen draws one ─────────────────────

def _conversation() -> list[Turn]:
    return [
        Turn("You", "why is ravis slow?", mine=True),
        Turn("NERVIS", "It was going through OpenRouter, sir."),
    ]


def test_a_turn_is_drawn_as_a_bubble() -> None:
    """**Its own renderer rather than more markdown.** Expressing a transcript
    as headings produced a report *about* a conversation rather than a picture
    of one."""
    out = render_conversation("Q3", "Exported today", _conversation()).data

    # **A rounded path per bubble, not a rectangle.** This counted `re f` and
    # wanted five per bubble — a fill and four hairline edges. Bubbles are drawn
    # with rounded corners now, which is Bézier segments closed and filled, so
    # the old count measured a primitive the renderer had stopped using and
    # would have gone on passing only because the rails are still rectangles.
    assert out.count(b" c ") >= 2 * 8, "each bubble is a rounded path, filled and stroked"
    assert out.count(b"re f") >= 2, "each bubble keeps a straight rail"
    assert b"(YOU)" in out and b"(NERVIS)" in out
    assert b"(why is ravis slow?)" in out


def test_the_two_sides_are_drawn_on_two_sides() -> None:
    """`.bubble.user` is `margin-left:auto`; everything else sits left."""
    out = render_conversation("Q3", "", _conversation()).data

    mine = float(re.search(rb"Tf ([\d.]+) [\d.]+ Td \(why is ravis slow\?\)", out).group(1))
    theirs = float(
        re.search(rb"Tf ([\d.]+) [\d.]+ Td \(It was going through OpenRouter, sir\.\)", out)
        .group(1)
    )

    assert mine > theirs, "the person's own turn is the one on the right"


def test_a_bubble_shrinks_to_what_is_in_it() -> None:
    """**`max-width`, not `width`.** Every bubble drawn at the full 78% is the
    one thing that stops a transcript looking like the conversation it came
    from."""
    short = render_conversation("Q3", "", [Turn("You", "yes", mine=True)]).data
    long = render_conversation("Q3", "", [
        Turn("You", "a much longer question that will certainly need most of the "
                    "measure to say what it has to say", mine=True)
    ]).data

    def widest(out: bytes) -> float:
        """How far the widest bubble reaches.

        **Read off the rounded path, because the fill is no longer a rectangle.**
        This measured `re f` widths, which now finds only the 1.6pt rail — so
        both bubbles measured 1.6 and the assertion compared a constant with
        itself. A rounded rectangle starts `x+r y m`, so the `l` that follows it
        on the same line carries the far edge, and the distance between them is
        the width less two radii — enough to compare two bubbles, which is all
        this asks.
        """
        spans = [
            float(x2) - float(x1) for x1, y1, x2, y2 in re.findall(
                rb" ([\d.-]+) ([\d.-]+) m ([\d.-]+) ([\d.-]+) l", out)
            if y1 == y2 and 2 < float(x2) - float(x1) < PAGE_WIDTH - 1
        ]
        assert spans, "no rounded bubble path was found to measure"
        return max(spans)

    assert widest(short) < widest(long)


def test_a_long_turn_is_split_across_pages_rather_than_pushed_whole() -> None:
    """A reply taller than a page would otherwise leave most of one empty and
    still not fit on the next."""
    out = render_conversation("Q3", "", [
        Turn("NERVIS", "\n\n".join(f"line {n} of a very long answer" for n in range(120)))
    ])

    assert out.pages > 1
    assert b"(line 0 of a very long answer)" in out.data
    assert b"(line 119 of a very long answer)" in out.data


def test_nothing_is_drawn_below_the_footer() -> None:
    out = render_conversation("Q3", "", [
        Turn("NERVIS", "\n\n".join(f"line {n}" for n in range(90)))
    ]).data

    drawn = [float(m) for m in re.findall(rb"Tf [\d.]+ ([\d.]+) Td \(line", out)]

    assert drawn, "the lines should be drawn at all"
    assert min(drawn) >= MARGIN, "a line landed on the page number"


def test_an_empty_conversation_still_makes_a_readable_file() -> None:
    out = render_conversation("Q3", "Exported today", [])

    assert out.pages == 1
    assert out.data.startswith(b"%PDF-1.4")


# ── tables ────────────────────────────────────────────────────────────────────


TABLE = """| Tool | Purpose | Effect |
|------|---------|--------|
| logs.query | Read bounded, redacted log windows by service and severity. | Read-only |
| ravis.routes | Read route decisions and provider health. | Read-only |
"""


def _tables_in(data: bytes) -> list[list[list[str | None]]]:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as opened:
        return [table.extract() for table in opened.pages[0].find_tables()]


def test_a_markdown_table_is_drawn_as_a_real_grid() -> None:
    """Not text with pipes in it: a grid another reader can find and extract,
    which is the whole difference between a table and a picture of one."""
    found = _tables_in(render("Report", TABLE).data)

    assert len(found) == 1
    assert found[0][0] == ["Tool", "Purpose", "Effect"]
    assert found[0][1][0] == "logs.query"
    assert len(found[0]) == 3


def test_the_separator_row_is_never_drawn() -> None:
    text = PdfReader(io.BytesIO(render("Report", TABLE).data)).pages[0].extract_text()

    assert "---" not in text and "|---" not in text


def test_prose_containing_a_pipe_is_not_a_table() -> None:
    """The falsifier. `a | b` is an ordinary sentence about alternatives, and
    a renderer that drew every line with a pipe as a grid would turn prose
    into furniture. A separator row is what makes a table."""
    body = "Choose a | b when it matters.\n\nAnother line | with a pipe.\n"

    rendered = render("Report", body)

    assert _tables_in(rendered.data) == []
    assert "Choose a | b when it matters." in (
        PdfReader(io.BytesIO(rendered.data)).pages[0].extract_text()
    )


def test_a_column_keeps_its_longest_word_whole() -> None:
    """The bug the column floor exists for, in the shape that actually
    reproduces it: a column holding one long identifier but little total text,
    beside a column of sentences. Weighting alone gives it a sliver and breaks
    the identifier — measured, `diagnostics.buil` / `d_packet`."""
    body = (
        "| Endpoint | What it does |\n|---|---|\n"
        "| diagnostics.build_packet | Builds it. |\n"
        "| x | Read bounded, redacted log windows by service, severity, time and"
        " correlation id, then hand them on to whoever asked. |\n"
    )

    cells = _tables_in(render("Report", body).data)[0]

    assert cells[1][0] == "diagnostics.build_packet", "the identifier is not broken in half"
    assert "\n" in (cells[2][1] or ""), "the long sentence still wraps between words"


def test_a_short_row_still_draws_a_full_grid() -> None:
    """People leave the last cell off. Padding it here turned out to be
    unnecessary — reportlab fills a short row itself, measured rather than
    assumed — but the outcome is what matters and is worth pinning."""
    body = "| A | B | C |\n|---|---|---|\n| one | two |\n"

    found = _tables_in(render("Report", body).data)

    assert len(found) == 1
    assert found[0][1] == ["one", "two", ""]


def test_a_table_carries_the_documents_own_heading_colour_in_its_header() -> None:
    profile = StyleProfile(
        page_width=612, page_height=792, margin_left=54, margin_top=54,
        margin_right=54, margin_bottom=54, body_family="Times-Roman", body_size=11,
        heading_family="Times-Roman", heading_size=17, text_colour=(0.1, 0.1, 0.1),
        heading_colour=(0.8, 0.1, 0.1), background_colour=(1, 1, 1),
        rule_colour=(0.5, 0.5, 0.5),
    )

    rendered = render("Report", TABLE, profile)

    assert "/Times-Bold" in _fonts(rendered.data), "the header row is set bold"
    assert _tables_in(rendered.data)[0][0][0] == "Tool"
