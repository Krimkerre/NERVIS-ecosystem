"""What the ecosystem is, as something chat can read.

**Why these files exist when the specifications already do.** The four canonical
specifications are contracts written for a coding agent building the thing,
milestone by milestone, and they describe what *should* be true. Retrieval over
them would have chat confidently explaining behaviour that was never built —
NERVIS.md alone still carries four unbuilt milestones. `STATUS.md` has the
opposite problem: it is an honest record and a chronological one, so a search
across it surfaces decisions that were true in August and reverted in September.

So `nervis/knowledge/` holds a short file per subject, written for the question
an operator actually asks — *how does RAVIS decide?* — and saying plainly what
is built rather than what is planned.

**They are gated, because a fourth kind of document is a fourth thing that can
rot.** `tools/knowledge_check.py` verifies that every pool, endpoint and
capability these files name exists in the running system. Prose can drift; a
checked claim cannot drift silently.

The reading they produce is a *source*, fenced like any other retrieved thing —
§11.5. Nothing in them can become an instruction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

#: Where the files live, beside the dashboard rather than inside the package.
#: They are documentation about the ecosystem, and an operator should be able to
#: read and edit them without going through `src/`.
KNOWLEDGE = Path(__file__).resolve().parents[2] / "knowledge"

#: How much of the knowledge base one answer may carry.
#:
#: Two or three sections. Enough to explain a mechanism, bounded so that a
#: question about routing cannot crowd out the live readings that sit beside it
#: in the same prompt — those are what the answer is *about*, and this is
#: background.
MAX_CHARACTERS = 6_000

#: The score a section must reach to be offered as background.
#:
#: **Measured, and the measurement says the two kinds of question overlap.**
#: Against a sample drawn from the real chat log, questions about how something
#: *works* scored 5.3 to 39.3, and questions about what something is *doing
#: right now* scored 0.0 to 13.8. There is no value that separates them, and
#: there was never going to be: "how does RAVIS decide" and "anything noteworthy
#: with RAVIS lately" are made of the same words, and differ in tense.
#:
#: So the bar is set where **every** design question clears it, and roughly half
#: the state questions come with background they did not need. That is the
#: deliberate direction of error. Background that arrives uninvited costs some
#: context and is marked as background, beside live figures the reading names as
#: the ones to trust. Background that fails to arrive is the failure actually
#: reported — a model explaining it had no readings on something written down in
#: this repository.
#:
#: A suppressor for "lately", "right now", "still" was written and thrown away:
#: it is a matcher whose misses remove real answers, which is the shape of bug
#: that took four attempts to get right elsewhere in this file's history.
MIN_SCORE = 5.0

#: Grammar, which is noise in any corpus.
#:
#: **Domain words are deliberately absent from this list**, and that is a
#: correction. It began with `model`, `service` and `request` in here on the
#: reasoning that they appear in nearly every section — which is true, and made
#: *"how does it decide which model to send a request to"* match nothing at all,
#: because after removing them the question had two words left. How common a
#: word is, is a measurement (see `_weight`), not a judgement to hard-code.
_TOO_COMMON = frozenset({
    "the", "a", "an", "is", "it", "and", "or", "of", "to", "in", "on", "for",
    "that", "this", "with", "as", "at", "by", "be", "are", "was", "not", "no",
    "what", "which", "how", "does", "do", "can", "you", "me", "my", "i",
    "one", "its", "about", "tell", "there", "them", "they", "from", "have",
})

_WORD = re.compile(r"[a-z0-9][a-z0-9_.-]*")


@dataclass(frozen=True)
class Section:
    """One heading and its body, from one file."""

    subject: str
    heading: str
    body: str

    @property
    def text(self) -> str:
        return f"## {self.heading}\n\n{self.body}"


#: Suffixes trimmed before comparing two words, longest first.
#:
#: **Crude on purpose.** This is not linguistics, it is making "optimization"
#: and "optimises" the same token — a real miss, since the question *does it do
#: cost optimization* scored one against a section headed *What it optimises
#: for*. British and American spellings of the same verb are the common case in
#: notes written by one person and questioned by another.
#: Longest first, because trimming a shorter ending first leaves two forms of
#: one verb at different lengths — and every entry past the obvious ones is here
#: because a pair failed to unify.
#:
#: `isations` before `ations`: "optimises" became `optim` and "optimisation"
#: `optimis`.
#:
#: **A bare `e` last, and it is the one that mattered most.** Without it every
#: verb ending in `e` split from its own inflections: `decide`/`decid`,
#: `route`/`rout`, `choose`/`choos`, `serve`/`serv` — eight of nine pairs tested,
#: and those are exactly this corpus's vocabulary. It showed up as `decide`
#: scoring zero against notes that say "decides" all the way through.
_ENDINGS = ("isations", "isation", "ations", "ation", "ising", "ises", "ised",
            "ise", "ing", "ers", "er", "ed", "es", "s", "e")


def _stem(word: str) -> str:
    normalised = word.replace("z", "s")
    for ending in _ENDINGS:
        # Three, not four: at four, "uses" kept its `s` while "use" had none, so
        # a four-letter word was the one length the stemmer could not unify.
        if normalised.endswith(ending) and len(normalised) - len(ending) >= 3:
            return normalised[: -len(ending)]
    return normalised


def _terms(text: str) -> set[str]:
    return {
        _stem(w) for w in _WORD.findall(text.lower())
        if w not in _TOO_COMMON and len(w) > 2
    }


@lru_cache(maxsize=1)
def _weight() -> dict[str, float]:
    """How much one term counts, from how rare it is across these files.

    **The measured version of the stop-word list this replaced.** A term in two
    sections out of thirty says something; one in twenty-eight says almost
    nothing, and says it without anybody having to decide in advance which words
    those are. `model` earns its low weight rather than being struck out by
    hand — which matters, because striking it out by hand removed the subject
    from *"how does it decide which model to send a request to"*.
    """
    from math import log

    everything = sections()
    total = len(everything) or 1
    seen: dict[str, int] = {}
    for section in everything:
        for term in _terms(f"{section.subject} {section.heading} {section.body}"):
            seen[term] = seen.get(term, 0) + 1
    # `1 + log(...)` so a term in every section is worth a little, not nothing:
    # the question is which section, and a universal word still breaks no ties.
    return {term: 1.0 + log(total / count) for term, count in seen.items()}


@lru_cache(maxsize=1)
def sections() -> tuple[Section, ...]:
    """Every section of every knowledge file, read once.

    Cached because these are small files that change when somebody edits them,
    not per request — and a filesystem read on the chat path for something this
    static would be a cost with no reader.
    """
    found: list[Section] = []
    if not KNOWLEDGE.is_dir():
        return ()
    for path in sorted(KNOWLEDGE.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        subject = path.stem
        # Split on `##`, keeping the title line as the first section's heading.
        parts = re.split(r"\n## ", text)
        title = parts[0].strip().lstrip("#").strip()
        if title:
            found.append(Section(subject, title.split("\n")[0], parts[0].strip()))
        for part in parts[1:]:
            heading, _, body = part.partition("\n")
            found.append(Section(subject, heading.strip(), body.strip()))
    return tuple(found)


def search(question: str, limit: int = 3) -> list[Section]:
    """The sections most likely to answer this question, best first.

    **Term overlap, not embeddings**, and that is a judgement about these
    documents rather than a shortcut. They are short, headed, and written in the
    same vocabulary the questions use — somebody asking how routing works says
    "routing", and the section is called *How a request is routed*. A vector
    index would be a dependency, a build step and an index to keep fresh, for a
    corpus of a few hundred lines.

    A heading match counts triple. It is the section's own summary of itself,
    and a word in it is a far stronger signal than the same word buried in a
    paragraph that merely mentions it.
    """
    wanted = _terms(question)
    if not wanted:
        return []
    weight = _weight()
    scored: list[tuple[float, int, Section]] = []
    for index, section in enumerate(sections()):
        heading = wanted & _terms(f"{section.subject} {section.heading}")
        body = wanted & _terms(section.body)
        score = sum(weight.get(t, 1.0) for t in heading) * 3
        score += sum(weight.get(t, 1.0) for t in body - heading)
        if score < MIN_SCORE:
            continue
        # Index breaks ties so the order is total and reproducible — §9.7's rule
        # about determinism applies to anything that shapes an answer.
        scored.append((score, -index, section))
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [section for _, _, section in scored[:limit]]


def reading(question: str) -> str:
    """The fenced background this question needs, or nothing.

    Says what it is. A specification and a running service are different things,
    and a model handed one without being told which will describe intentions as
    behaviour — the exact failure these files were written to avoid.
    """
    best = search(question)
    if not best:
        return ""
    body = ""
    for section in best:
        piece = f"\n\n--- from {section.subject} ---\n{section.text}"
        if len(body) + len(piece) > MAX_CHARACTERS:
            break
        body += piece
    if not body:
        return ""
    return (
        "Background on how this ecosystem works, from its own notes. This"
        " describes the design and is not a reading of the running system —"
        " where a live figure is available it is elsewhere in this prompt and it"
        " is the one to trust." + body
    )


__all__ = ["Section", "reading", "search", "sections", "KNOWLEDGE", "MAX_CHARACTERS"]
