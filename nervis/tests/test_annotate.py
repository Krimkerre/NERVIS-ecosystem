"""Placing a reply's comments into a copy of the document — the pure halves
of `annotate.py`: reading comments out of a reply, finding where a quote came
from, and building the copy. What offers it and what runs it live in
`test_m4_chat.py` and `test_annotate_wiring.py`.
"""
from __future__ import annotations

import io

from pypdf import PdfReader
from reportlab.pdfgen import canvas

from nervis.annotate import (
    Comment,
    annotate_pdf,
    annotate_text,
    find_in,
    has_anchors,
    parse_comments,
)


def _three_pages() -> bytes:
    """Three pages, each with its own unmistakable sentence."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(612, 792))
    for words in (
        "Alpha section. The first page talks about mornings.",
        "Bravo section. The second page is entirely about harbours and tides.",
        "Charlie section. The third page closes with a list of ports.",
    ):
        c.setFont("Helvetica", 12)
        c.drawString(72, 700, words)
        c.showPage()
    c.save()
    return buffer.getvalue()


def _page_texts(data: bytes) -> list[str]:
    return [page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages]


# ── parse_comments ────────────────────────────────────────────────────────────


def test_a_quoted_line_starts_a_comment_and_the_lines_below_are_its_text() -> None:
    reply = "> Bravo section\nThis needs a tide table.\nAnd a map.\n\n> Charlie section\nToo short."

    found = parse_comments(reply)

    assert found == [
        Comment(anchor="Bravo section", text="This needs a tide table.\nAnd a map."),
        Comment(anchor="Charlie section", text="Too short."),
    ]


def test_text_before_the_first_quote_is_kept_as_an_unanchored_comment() -> None:
    found = parse_comments("Here is what I think overall.\n\n> Alpha section\nFine.")

    assert found[0] == Comment(anchor="", text="Here is what I think overall.")
    assert found[1].anchor == "Alpha section"


def test_a_reply_with_no_quotes_at_all_is_one_unanchored_comment() -> None:
    """A model that ignored the format still said something the person asked
    for — it goes to the end, it is not dropped."""
    assert parse_comments("Just some thoughts, no structure.") == [
        Comment(anchor="", text="Just some thoughts, no structure.")
    ]


def test_consecutive_quote_lines_are_one_anchor() -> None:
    found = parse_comments('> "The second page is\n> entirely about harbours"\nSo?')

    assert found[0].anchor == "The second page is entirely about harbours"


def test_has_anchors_is_whether_any_comment_can_be_placed() -> None:
    assert has_anchors("> Bravo section\nFine.")
    assert not has_anchors("Press the button and I will place them.")
    assert not has_anchors("")


# ── find_in ───────────────────────────────────────────────────────────────────


def test_a_quote_is_found_across_a_line_break_and_regardless_of_case() -> None:
    passages = ["Alpha morning", "the second PAGE is\nentirely about harbours", "Charlie"]

    assert find_in("The second page is entirely about harbours", passages) == 1


def test_a_paraphrased_tail_still_finds_the_passage_by_its_opening() -> None:
    passages = ["Alpha", "the second page is entirely about harbours and tides"]

    assert find_in("the second page is entirely about boats", passages) == 1


def test_a_heading_listed_in_the_contents_is_placed_at_its_section_not_the_contents() -> None:
    """Measured on the real blueprint: every section heading appears on the
    contents page first, and first-match put the comments there."""
    passages = [
        "Contents\n01 Executive brief 4\n02 The product idea 5",
        "Something else entirely",
        "01\nExecutive brief\nThe one-sentence design is a bounded task runtime.",
    ]

    assert find_in("Executive brief", passages) == 2


def test_an_anchor_that_is_nowhere_is_none_not_a_guess() -> None:
    assert find_in("nothing like this appears", ["Alpha", "Bravo"]) is None


def test_a_tiny_anchor_is_not_trusted() -> None:
    """"the" would be found on every page; placing a comment by it would be
    placing it at random."""
    assert find_in("the", ["the alpha", "the bravo"]) is None


# ── annotate_pdf ──────────────────────────────────────────────────────────────


def test_a_comment_page_lands_right_after_the_page_it_quotes() -> None:
    copied = annotate_pdf(
        _three_pages(),
        [Comment(anchor="entirely about harbours and tides", text="Needs a tide table.")],
        None,
    )

    texts = _page_texts(copied.data)
    assert copied.pages == 4
    assert "Bravo section" in texts[1]
    assert "Comments on page 2" in texts[2]
    assert "Needs a tide table." in texts[2]
    assert "Charlie section" in texts[3]


def test_every_original_page_survives_in_order() -> None:
    copied = annotate_pdf(_three_pages(), [Comment(anchor="", text="Overall: fine.")], None)

    texts = _page_texts(copied.data)
    assert [t.split(".")[0] for t in texts[:3]] == ["Alpha section", "Bravo section",
                                                    "Charlie section"]


def test_unanchored_comments_go_last_under_their_own_heading() -> None:
    copied = annotate_pdf(_three_pages(), [Comment(anchor="", text="Overall: fine.")], None)

    texts = _page_texts(copied.data)
    assert copied.pages == 4
    assert "Further comments" in texts[-1]
    assert "Overall: fine." in texts[-1]


def test_a_quote_nowhere_in_the_document_is_not_placed_by_guesswork() -> None:
    copied = annotate_pdf(
        _three_pages(), [Comment(anchor="a sentence that is on no page", text="Hm.")], None
    )

    texts = _page_texts(copied.data)
    assert copied.pages == 4
    assert "Further comments" in texts[-1]
    assert not any("Comments on page" in t for t in texts)


def test_no_comments_at_all_is_just_the_original() -> None:
    copied = annotate_pdf(_three_pages(), [], None)

    assert copied.pages == 3


# ── annotate_text ─────────────────────────────────────────────────────────────


def test_a_text_comment_goes_under_the_paragraph_it_quotes() -> None:
    original = "# Policy\n\nCore hours are nine to five.\n\nEquipment is provided."

    merged = annotate_text(original, [Comment(anchor="Core hours", text="Too rigid.")])

    assert merged == (
        "# Policy\n\nCore hours are nine to five.\n\n**Comment:** Too rigid.\n\n"
        "Equipment is provided."
    )


def test_text_comments_with_no_home_go_under_further_comments() -> None:
    merged = annotate_text("One paragraph.", [Comment(anchor="", text="Overall fine.")])

    assert merged.endswith("## Further comments\n\nOverall fine.")
