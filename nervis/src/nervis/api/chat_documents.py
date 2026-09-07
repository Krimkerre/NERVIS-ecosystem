"""Finding the file a question means, inside the workspace and nowhere else.

Three things happen here and they are deliberately separate. A question may
**name** a file, in which case the name is resolved inside the configured
workspace and refused if it resolves outside. A question may instead **mean the
attachment** — "read this pdf", with no name — in which case the newest readable
file the conversation carries is the answer. And a file that was found is
**read**, which is the only step that touches its contents.

**Nothing here is a tool the model holds.** The person names a file, NERVIS
opens it inside the boundary, and the text arrives fenced like every other
retrieved thing. A document that says "now open ~/.ssh/id_rsa" is a document
saying that — a sentence, not an instruction — and `workspace.py` does not read
English either way.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import Request

from nervis import documents
from nervis.workspace import OutsideWorkspaceError

_NAMES_A_FILE = re.compile(
    r"""["'`]([\w./\- ]{1,120}\.\w{1,8})["'`]"""
    r"""|\b(?:read|open|summari[sz]e|explain|check|look\s+at)\s+"""
    r"""(?:the\s+|my\s+|this\s+)?([\w./\-]{1,120}\.\w{1,8})""",
    re.IGNORECASE,
)


def _target(root: Path, question: str, conversation_id: str) -> tuple[Path, str, bool] | str | None:
    """Which file to read and where from, or a refusal, or nothing.

    Three outcomes because the question has three answers: a file to open, a
    thing to tell the model when the person clearly meant a document and there
    is none, and silence for an ordinary sentence that named no file at all.

    **Two places, and the order is the point.** This conversation's attachments
    come first, because *"this pdf"* means the one just handed over. The
    workspace root holds what chat was asked to *write*, which is a different
    kind of file and stays reachable by name.
    """
    attachments = documents.attachment_dir(root, conversation_id)
    found = _NAMES_A_FILE.search(question or "")
    named = (found.group(1) or found.group(2)) if found else ""

    # Named beats referred-to. "summarise report.pdf" is unambiguous and must
    # not be overridden by a newer file just because the sentence also contains
    # the word "the pdf".
    if named:
        return _holding(root, attachments, named), named, False
    if attachments is not None:
        chosen = _attachment(attachments, question)
        if chosen:
            return attachments, chosen, True
    if _means_the_attachment(question):
        # The person meant a document and there is none. Answering "I can't see
        # your screen" — which is what actually happened — is true and useless.
        return (
            "The person referred to an attached document, and nothing is"
            " attached to this conversation. Tell them so, and that the clip"
            " beside the message box attaches one. Attachments belong to the"
            " conversation they were added to, so an older one is not here."
        )

    # **A file is attached and this turn did not ask about it.** Say that it is
    # there anyway, in one line, without the content.
    #
    # This is the floor under every matcher. Whatever phrasing the matching
    # misses next — and it will miss one — the failure becomes "you attached
    # this, want me to read it?" instead of *"I don't see a PDF anywhere,
    # Matty. You'd need to actually hand it to me"*, said to somebody looking
    # at the filename on their own screen. Flatly denying a file the person can
    # see is the worst answer available, and it costs about fifteen tokens to
    # make it impossible.
    if attachments is not None:
        present = documents.list_files(attachments)
        if present:
            names = ", ".join(
                item.name + ("" if item.readable else " (not readable as text)")
                for item in present[:5]
            )
            return (
                f"Attached to this conversation: {names}. The person has not"
                f" asked about it in this message, so it has not been opened —"
                f" mention it only if it is relevant, and say you can read it if"
                f" they ask."
            )
    return None


def _holding(root: Path, attachments: Path | None, named: str) -> Path:
    """Which directory a named file should be read from.

    The conversation's attachments if it is there, the workspace root otherwise
    — so the root's refusal is the one the person sees when the file is nowhere,
    and "there is no notes.md in the workspace" stays the wording it had.
    """
    if attachments is not None and (attachments / Path(named).name).is_file():
        return attachments
    return root


#: A word for the thing somebody attached.
#:
#: Bare, with no determiner in front of it. Requiring `this|that|the|my` was
#: what missed *"i supplied **a** pdf here"* — and "a" was never going to be the
#: last article anybody used.
_A_DOCUMENT = re.compile(
    r"\b(?:(pdf|csv|markdown|spreadsheet|log)|document|file|attachment|doc|docs)\b",
    re.IGNORECASE,
)

#: Asking for something that is only ever asked of a document.
#:
#: `summar\w*` rather than `summari[sz]e`, because the miss that prompted all of
#: this was the word **summary** — a noun, and the most ordinary way anybody
#: asks for one.
#:
#: Every entry here is a request that makes no sense about anything else. You do
#: not ask for the gist of a service, or the key points of a restart. That is
#: what earns them the right to fire on their own.
_WANTS_A_READING = re.compile(
    r"""\b(?:summar\w*|tl;?dr|gist|recap|key\s+points?|takeaways?)\b""",
    re.IGNORECASE,
)

#: Verbs that mean reading *only when a document is named beside them*.
#:
#: **Deliberately not enough on their own**, which the falsifier proved twice
#: over: `read` fires on "read the room" and `what is` on "what is the plan for
#: today", and letting either through put a person's whole document into the
#: prompt for a sentence that had nothing to do with it. They earn nothing that
#: `_A_DOCUMENT` does not already earn, so they are here for documentation and
#: are not consulted.
#:
#: The floor under them is the presence note in `_target`: an unmatched question
#: still learns that a file is attached, which is the failure worth preventing.
_WEAK_READING_VERBS = ("read", "explain", "review", "analyse", "analyze",
                       "go through", "walk me through", "what is", "what does")


def _means_the_attachment(question: str) -> re.Match[str] | None:
    """Whether this question is about the document attached to the conversation.

    **Either signal, not both**, and the ordering is gone. The first version
    demanded a reading verb *followed within sixty characters* by a determiner
    and a document word, on the reasoning that a verb alone fires on "read the
    room" and a noun alone on "the file system is broken". Sound in the
    abstract, and it missed this:

        well then... i supplied a pdf here.. why don't you give me a summary?

    Three ways at once. "summary" is not "summarise". "a pdf" is not "the pdf".
    And the noun came *before* the verb, while `[^.?!]{0,60}` — meant to keep
    the match inside one clause — could not cross the `..` anyway.

    Patching alternatives onto that regex would lose the same way next week, so
    the rule is now: **a document word, or a request only ever made of a
    document.** Either alone; neither needs the other; order does not matter.

    What is deliberately *not* enough is a bare reading verb. `read` and `what
    is` were tried and reverted within the hour — they fire on "read the room"
    and "what is the plan for today", and each one put a person's whole document
    into a prompt that had nothing to do with it. `_WEAK_READING_VERBS` records
    which ones those are.

    The floor under all of it is the presence note in `_target`. Whatever
    phrasing this misses next, the model still learns a file is attached, so the
    failure is "you attached this, want me to read it?" rather than a flat
    denial.
    """
    return _A_DOCUMENT.search(question or "") or _WANTS_A_READING.search(question or "")


#: The shortest word from a filename allowed to stand in for the file itself.
#:
#: "policy", "invoice", "budget", "contract", "notes" identify a document.
#: "work", "plan", "spec" are just as often about anything else, and a file
#: called `remote-work-policy.pdf` must not open because somebody asked whether
#: something works. Five is where those two groups separate.
#:
#: **A length rather than a list**, deliberately. This module's own history is a
#: vocabulary of document words that kept missing the next phrasing; a list of
#: nouns that count as documents would go stale the same way. A length has
#: nothing to keep current.
_MEANINGFUL_NAME_WORD = 5


def _names_an_attached_file(place: Path, question: str) -> bool:
    """Whether the question uses a word out of an attached file's own name.

    **The third signal, and the one that needed no vocabulary.** Somebody
    attached `remote-work-policy.pdf` and asked to "read this policy": the
    file was there, reconciled and readable, and the answer came back asking
    what the policy was about. Neither pattern above can see it — "policy" is
    not a document word, and `read` alone is deliberately not enough — but the
    person did name the file. They used the word its own filename carries.

    Derived from NERVIS's own records rather than from a list somebody has to
    keep current: the evidence is the name the person's file already has.
    """
    asked = {word.lower() for word in re.findall(r"[\w']+", question or "")}
    if not asked:
        return False
    for item in documents.list_files(place):
        for word in re.split(r"[\W_]+", Path(item.name).stem):
            if len(word) >= _MEANINGFUL_NAME_WORD and word.lower() in asked:
                return True
    return False


def _attachment(place: Path, question: str) -> str:
    """The file *"this pdf"* refers to, or an empty string.

    Resolved from the directory's own timestamps, never from anything a model
    said. A type word narrows it — "the pdf" should not open a `.csv` that
    happens to be newer — and a reference with no type takes whatever was put
    there last, which is what "this" means after an upload.
    """
    if not (_means_the_attachment(question) or _names_an_attached_file(place, question)):
        return ""
    # A type word narrows the choice — "the pdf" should not open a newer `.md`.
    # Read from the document pattern specifically, because a question that only
    # said "summary" matched the other one and has no type to offer.
    named = _A_DOCUMENT.search(question or "")
    word = ((named.group(1) if named else "") or "").lower()
    narrowed = documents.TYPE_WORDS.get(word)
    # **A type word narrows the choice; it does not veto it.** "the pdf" should
    # not open a newer `.md` when a PDF is attached — but when none is, the
    # person calling their attachment a pdf is being loose, not wrong, and
    # giving up sends them "nothing is attached" about a file they can see.
    return (
        (documents.newest_readable(place, narrowed) if narrowed else "")
        or documents.newest_readable(place)
        or ""
    )


def _document(request: Request, question: str, conversation_id: str = "") -> str:
    """The file this question names, read and fenced, or nothing.

    **Off unless configured.** `workspace_path` is empty by default, because an
    install that was never asked to read a person's files should not do it, and
    a first request is a poor place to discover that it can.

    Every failure is answered rather than swallowed: outside the workspace, not
    there, and not text send a reader to three different places, and a silent
    empty reading would make all three look like the model deciding not to
    mention the file.
    """
    root = str(getattr(request.app.state.settings, "workspace_path", "") or "").strip()
    if not root:
        return ""

    target = _target(Path(root), question, conversation_id)
    if target is None:
        return ""
    if isinstance(target, str):
        return target
    where, name, chosen = target
    return _reading(where, name, chosen)


def _reading(where: Path, name: str, chosen: bool) -> str:
    """One file, read and fenced, or the refusal that says which kind it is.

    Every failure is answered rather than swallowed: outside the workspace, not
    there, and not readable send a person to three different places, and a
    silent empty reading would make all three look like the model deciding not
    to mention the file.
    """
    try:
        document = documents.read_document(where, name)
    except OutsideWorkspaceError as refusal:
        return f"The person named a file and it was refused: {refusal}. Say so plainly."
    except FileNotFoundError as absent:
        return f"The person named a file that is not there: {absent}. Say so rather than guessing."
    except ValueError as unreadable:
        return f"The person named a file chat cannot read: {unreadable}. Say which kinds it can."
    except OSError as failure:
        return f"The file could not be read ({type(failure).__name__}). Say so; do not invent it."

    if not chosen:
        return document.as_reading()
    # Said out loud, because NERVIS picked this file and the person did not. A
    # silently wrong pick is a confident answer about the wrong document, which
    # is the worst outcome available here.
    return (
        f"The person referred to an attached document without naming one. The"
        f" most recently attached readable file in this conversation is"
        f" {document.shown}, so that is what is below. Name it in the answer, so"
        f" they can tell if it is the one they meant.\n\n{document.as_reading()}"
    )
