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
from dataclasses import dataclass, replace
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

    @property
    def learned(self) -> bool:
        """Whether NERVIS was told this rather than shipped with it (M23).

        Derived from the filename rather than carried as a field, because the
        one thing that must never happen is a learned note that looks
        hand-written. A flag can be set wrongly; a file either is or is not the
        one NERVIS appends to.
        """
        return self.subject == "learned"


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
    # **Trailing punctuation stripped, inner punctuation kept.** The token
    # pattern allows dots and hyphens inside a word so that `nervis.registry`
    # and `ravis/chat` survive whole — which also swallowed the full stop at the
    # end of a sentence. "centre." and "centre" were two different words, so a
    # term matched only where it happened not to end a clause, and a question
    # about "the notification centre" scored 4.5 against a section that says
    # exactly that, and was dropped by a threshold of 5.
    return {
        _stem(w.strip("._-")) for w in _WORD.findall(text.lower())
        if w.strip("._-") not in _TOO_COMMON and len(w.strip("._-")) > 2
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


def forget_cached() -> None:
    """Drop the index, so the next question reads the directory again (M23).

    **The files stopped being static when NERVIS gained the ability to write
    one.** The cache was correct while every note shipped with the build: they
    changed when somebody edited them, which meant a restart. A learned note is
    appended by a running service, and without this NERVIS would agree to
    remember something, write it, and then not know it until the next restart —
    a bug that looks exactly like the feature not working, and that no test
    against a fresh process would ever show.

    Both caches, because the weights are derived from the corpus: a new section
    changes how rare every term in it is.
    """
    sections.cache_clear()
    _weight.cache_clear()


@lru_cache(maxsize=1)
def sections() -> tuple[Section, ...]:
    """Every section of every knowledge file, read once.

    Cached because a question asks for this on the chat path and the files
    change rarely — when somebody edits one, or when NERVIS appends a learned
    note. The second of those happens inside a running process, which is what
    `forget_cached` is for.
    """
    found: list[Section] = []
    if not KNOWLEDGE.is_dir():
        return ()
    for path in sorted(KNOWLEDGE.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        subject = path.stem
        # Split on `##` *and* `###`, keeping the title line as the first
        # section's heading. Level three matters: five planned features written
        # as sub-headings under one `##` were one 1,800-character block, so a
        # question about any of them retrieved all five and the other four were
        # padding.
        parts = re.split(r"\n#{2,3} ", text)
        title = parts[0].strip().lstrip("#").strip()
        if title:
            found.append(Section(subject, title.split("\n")[0], parts[0].strip()))
        for part in parts[1:]:
            heading, _, body = part.partition("\n")
            found.append(Section(subject, heading.strip(), body.strip()))
    return tuple(found)


def search(question: str, limit: int = 3, subject: str = "") -> list[Section]:
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
    def opening() -> list[Section]:
        """A named subject's own file, from the top.

        *"And you?"* carries no content word at all, so it cannot clear
        `MIN_SCORE` however the corpus is weighted — and by the time this is
        called the caller has already established which subject is meant.
        Returning nothing is the assistant saying it has no reading on itself,
        which is worse than opening its own file. Only when a subject was named:
        an unscoped question that matches nothing still matches nothing.
        """
        return _hand_written_wins([one for one in sections() if one.subject == subject])[:limit]

    wanted = _terms(question)
    if not wanted:
        return opening() if subject else []
    weight = _weight()
    scored: list[tuple[float, int, Section]] = []
    # **`subject` scopes, it does not merely boost.** A question about NERVIS
    # itself used to be answered by appending the word "nervis" to it, which
    # depended on that word being distinctive — and it is the single most common
    # word in this corpus, so its measured weight is near the floor. Every file
    # added since made it weaker, and documenting Clarvis's settings finally
    # pushed *"and you?"* below `MIN_SCORE` and returned nothing at all. The
    # term weighting was right; leaning on one term to mean "this subject" was
    # not. Naming the subject asks the question that was meant.
    for index, section in enumerate(sections()):
        if subject and section.subject != subject:
            continue
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
    if not scored and subject:
        return opening()
    return _hand_written_wins([section for _, _, section in scored])[:limit]


def _headed(section: Section) -> str:
    """A heading reduced to the words it is made of, for comparing two of them.

    Stems and sorts, so *The GPU box* and *the gpu boxes* are the same heading.
    Comparing the raw strings would make the conflict rule miss on
    capitalisation, which is exactly how two notes about one subject end up
    both being quoted as though they agreed.
    """
    return " ".join(sorted(_terms(section.heading)))


def _hand_written_wins(ranked: list[Section]) -> list[Section]:
    """M23's rule: a shipped note beats a learned one on the same heading.

    **Both are kept and the loser is renamed, not dropped.** A learned note that
    contradicts a shipped one is somebody having told NERVIS something the notes
    disagree with, and exactly one of those is wrong. Silently discarding the
    learned one hides a disagreement the person is the only one who can settle;
    silently preferring it lets a passing remark overwrite the documentation. So
    the shipped note is the answer, and the learned one is still shown, marked
    as overruled.

    Ordering only. Nothing is edited on disk — the file says what the person
    told NERVIS, whatever the shipped notes say about it.
    """
    written = {_headed(s) for s in ranked if not s.learned}
    if not written:
        return ranked
    resolved: list[Section] = []
    for section in ranked:
        if not section.learned or _headed(section) not in written:
            resolved.append(section)
            continue
        resolved.append(replace(
            section,
            heading=f"{section.heading} (overruled)",
            body=(
                "You told NERVIS this, and the note it shipped with says"
                " otherwise. The shipped note above is the one being used;"
                " this is here so the disagreement is visible rather than"
                f" settled quietly.\n\n{section.body}"
            ),
        ))
    # Overruled notes sink below everything they lost to, so a reader — and a
    # model — meets the answer before the contradiction of it.
    resolved.sort(key=lambda s: s.heading.endswith("(overruled)"))
    return resolved


#: Second person, as whole words. NERVIS is the assistant, so this is how people
#: ask about it — and the one subject in these files nobody calls by name.
_SECOND_PERSON = re.compile(r"\b(you|your|yours|yourself)\b", re.IGNORECASE)

#: Subjects that would mean the question is about a peer rather than about
#: NERVIS itself.
_PEERS = ("ravis", "sirvis", "clarvis")


def _about_itself(question: str) -> bool:
    """Whether "you" in this question means NERVIS.

    Asked in turn about RAVIS, SIRVIS and Clarvis, the next question is *"and
    you?"* — which names nothing, matched nothing, and got the persona reciting
    itself instead of anything about the service. Only when no peer is named,
    because "can you tell me about RAVIS" is a question about RAVIS that happens
    to contain the word.
    """
    lowered = (question or "").lower()
    if any(peer in lowered for peer in _PEERS):
        return False
    return bool(_SECOND_PERSON.search(lowered))


def reading(question: str) -> str:
    """The fenced background this question needs, or nothing.

    Says what it is. A specification and a running service are different things,
    and a model handed one without being told which will describe intentions as
    behaviour — the exact failure these files were written to avoid.
    """
    # A question about "you" is a question about NERVIS, and nothing in these
    # files says so — they are written in the third person, like notes.
    best = search(question, subject="nervis") if _about_itself(question) else search(question)
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


__all__ = ["Section", "forget_cached", "reading", "search", "sections",
           "KNOWLEDGE", "MAX_CHARACTERS"]
