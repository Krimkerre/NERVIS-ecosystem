"""A conversation, as something a PDF can be made of.

**Separate from `pdf.py` for the reason `layout.py` is.** One module decides
what a conversation *reads like* and another decides where the glyphs go, so
this is testable without opening a single PDF byte, and the renderer stays
testable without a conversation.

The output is markdown because that is what `layout.py` already parses — the
same path a saved reply takes, so an exported transcript and a saved answer come
out of one renderer rather than two that drift.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

#: How a stored role is introduced in the transcript.
#:
#: `clarvis` appears because a Bridge's replies are stored in the same table;
#: anything unrecognised keeps its own name rather than being flattened into
#: "assistant", since a transcript that renames a speaker is not a transcript.
SPEAKERS = {"user": "You", "assistant": "NERVIS", "clarvis": "Clarvis"}

#: A filename made from a title. Lowercase, punctuation to hyphens, and nothing
#: that could climb out of a directory — the same untrusted-name discipline the
#: upload path uses, applied earlier because this name is *generated* from text
#: a person typed.
_UNSAFE = re.compile(r"[^a-z0-9]+")


def suggested_name(title: str, when: datetime) -> str:
    """A filename for this conversation, from its own title and the date.

    **Derived rather than invented**, which is the distinction §12 cares about.
    The title is NERVIS's own record — taken from the opening message, or typed
    by the person — and the date disambiguates two exports of conversations that
    happen to share one. Nothing here comes from a model.
    """
    stem = _UNSAFE.sub("-", (title or "").lower()).strip("-")[:48].strip("-")
    return f"{stem or 'conversation'}-{when:%Y-%m-%d}.pdf"


def as_markdown(
    title: str,
    messages: Sequence[Any],
    when: datetime,
    speaker_for: dict[str, str] | None = None,
) -> str:
    """The whole conversation, in the markdown `layout.py` understands.

    Every turn in order, each under its speaker. **Nothing is summarised and
    nothing is dropped**: an export that quietly omitted a turn would be worse
    than no export, because a transcript is the one document whose value is
    being complete.

    An empty reply is kept and marked. A model that spent its budget thinking
    and returned nothing is a thing that happened, and a transcript that hides
    it makes the next question in the conversation unreadable.
    """
    names = {**SPEAKERS, **(speaker_for or {})}
    lines = [f"# {title or 'Conversation'}", "", f"Exported {when:%d %B %Y at %H:%M}.", ""]
    for message in messages:
        role = str(getattr(message, "role", "") or "")
        content = str(getattr(message, "content", "") or "").strip()
        lines.append(f"## {names.get(role, role or 'Unknown')}")
        lines.append("")
        lines.append(content or "*(no text in this reply)*")
        lines.append("")
    if not messages:
        lines.append("*This conversation has no messages.*")
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["SPEAKERS", "as_markdown", "suggested_name"]
