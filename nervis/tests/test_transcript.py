"""A conversation, turned into something a page can be made of."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from nervis.pdf import render
from nervis.transcript import as_markdown, suggested_name

WHEN = datetime(2026, 8, 31, 19, 4)


def _turn(role: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(role=role, content=content)


def test_every_turn_survives_in_order() -> None:
    """**Nothing summarised and nothing dropped.** An export that quietly
    omitted a turn is worse than no export, because a transcript is the one
    document whose whole value is being complete."""
    out = as_markdown("Q3", [
        _turn("user", "first question"),
        _turn("assistant", "first answer"),
        _turn("user", "second question"),
    ], WHEN)

    assert out.index("first question") < out.index("first answer") < out.index("second question")
    assert out.count("## You") == 2
    assert out.count("## NERVIS") == 1


def test_an_empty_reply_is_kept_and_marked() -> None:
    """A model that spent its budget thinking and returned nothing is a thing
    that happened. Hiding it makes the next question unreadable."""
    out = as_markdown("Q3", [_turn("user", "hello"), _turn("assistant", "")], WHEN)

    assert "no text in this reply" in out


def test_an_unknown_speaker_keeps_its_own_name() -> None:
    """A transcript that renames a speaker is not a transcript."""
    out = as_markdown("Q3", [_turn("operator", "said something")], WHEN)

    assert "## operator" in out


def test_a_conversation_with_nothing_in_it_says_so() -> None:
    out = as_markdown("Empty", [], WHEN)

    assert "no messages" in out


def test_the_filename_comes_from_the_title_and_the_date() -> None:
    assert suggested_name("Why is RAVIS slow?", WHEN) == "why-is-ravis-slow-2026-08-31.pdf"
    assert suggested_name("", WHEN) == "conversation-2026-08-31.pdf"


def test_a_title_cannot_become_a_path() -> None:
    """The name is generated from text a person typed, so it is untrusted at the
    moment it is built rather than only at the moment it is used."""
    for hostile in ("../../etc/passwd", "/absolute", "..", "a/b/c"):
        made = suggested_name(hostile, WHEN)
        assert "/" not in made and ".." not in made.replace(".pdf", ""), hostile


def test_a_very_long_title_is_bounded() -> None:
    made = suggested_name("word " * 200, WHEN)

    assert len(made) < 70


def test_the_markdown_renders_as_a_pdf() -> None:
    """The round trip. `transcript` decides what it reads like and `pdf` decides
    where the glyphs go; a transcript that produced markdown the renderer choked
    on would pass every test above."""
    out = render("Q3", as_markdown("Q3 review", [
        _turn("user", "why is ravis slow?"),
        _turn("assistant", "It was going through OpenRouter, sir."),
    ], WHEN))

    assert out.data.startswith(b"%PDF-1.4")
    assert b"(why is ravis slow?)" in out.data
    assert b"(Q3 review)" in out.data
