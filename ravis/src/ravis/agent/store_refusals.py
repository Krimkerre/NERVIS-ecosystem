"""The skill store's refusals, in its fixture's words (`tests/fixtures/skill-store/contract.json`).

**NERVIS's alone, on purpose.** The skill store's routes are for NERVIS's Skills page, never for
Clarvis, so their codes live in a fixture of their own beside `relay-contract/`, not in that
folder's `conventions.json`: that catalogue is Clarvis's contract, which Clarvis copies with a
manifest, and a code added there would change what Clarvis has to know. Two codes are the
contract's own and mean the same here: `FORBIDDEN` and `INVALID_REQUEST_BODY`.

Each refusal is a `RavisError`, turned into the MEP error envelope once, at the edge (`errors.py`);
`retryable` follows from the status there, so only a 503 — GitHub or a website not answering, or
rate-limiting RAVIS — is worth trying again. The page matches on the code and details; the message
is what it shows the owner, so it is written for the owner.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from ravis.agent.skill_package import PackageRefusedError
from ravis.agent.skill_web import (
    GITHUB_HOSTS,
    NotFoundError,
    RateLimitedError,
    RefusedError,
    UnreachableError,
    WebError,
)
from ravis.codex.lock_file import iso
from ravis.errors import RavisError


class SkillStoreRefusalError(RavisError):
    """One refusal: a code the skill store's fixture catalogues, at its status."""

    def __init__(self, code: str, status: int, message: str, **details: Any) -> None:
        super().__init__(message, **details)
        self.code = code
        self.status = status


@contextmanager
def translated() -> Iterator[None]:
    """Around a route's work: a package rule or a fetch that failed, as the store's refusal."""
    try:
        yield
    except PackageRefusedError as refused:
        raise package_refused(refused) from None
    except WebError as failure:
        raise fetch_failed(failure) from None


def invalid_body(message: str) -> SkillStoreRefusalError:
    return SkillStoreRefusalError("INVALID_REQUEST_BODY", 422, message)


def package_refused(refused: PackageRefusedError) -> SkillStoreRefusalError:
    """The skill broke a rule: the specification's, or RAVIS's own (`skill_package`)."""
    return SkillStoreRefusalError("SKILL_REFUSED", 422, refused.why, reason=refused.reason)


def fetch_failed(failure: WebError) -> SkillStoreRefusalError:
    """A fetch that didn't work, by what went wrong (`skill_web`)."""
    if isinstance(failure, RateLimitedError):
        return rate_limited(failure.host, failure.retry_at)
    if isinstance(failure, RefusedError):
        return SkillStoreRefusalError("SKILL_SOURCE_REFUSED", 422, f"{sentence(failure.why)}",
                                      reason=failure.reason)
    if isinstance(failure, NotFoundError):
        return SkillStoreRefusalError("SKILL_SOURCE_NOT_FOUND", 404, sentence(failure.why))
    assert isinstance(failure, UnreachableError)
    return SkillStoreRefusalError("SKILL_SOURCE_UNREACHABLE", 503, sentence(failure.why))


def rate_limited(host: str, retry_at: datetime) -> SkillStoreRefusalError:
    """"GitHub is rate-limiting, try again at HH:MM", in this Mac's own time."""
    return SkillStoreRefusalError(
        "SKILL_SOURCE_RATE_LIMITED", 503,
        f"{host_label(host)} is rate-limiting RAVIS; try again at {local_time(retry_at)}.",
        host=host, retry_at=iso(retry_at),
    )


def host_label(host: str) -> str:
    return "GitHub" if host in GITHUB_HOSTS else host


def local_time(moment: datetime) -> str:
    return moment.astimezone().strftime("%H:%M")


def not_a_github_link() -> SkillStoreRefusalError:
    return SkillStoreRefusalError(
        "SKILL_SOURCE_REFUSED", 422,
        "That isn't a link to a GitHub repository or to a folder in one "
        "(https://github.com/<owner>/<repository>/tree/<branch>/<folder>).",
        reason="not_a_github_link",
    )


def preview_not_found() -> SkillStoreRefusalError:
    return SkillStoreRefusalError(
        "SKILL_PREVIEW_NOT_FOUND", 404,
        "That review has expired or was already used; review the skill again.",
    )


def already_installed(name: str, installed_by_ravis: bool) -> SkillStoreRefusalError:
    how = (" Use Update on it instead." if installed_by_ravis
           else " It was put there by hand, so RAVIS leaves it alone.")
    return SkillStoreRefusalError(
        "SKILL_ALREADY_INSTALLED", 409,
        f"A skill named {name} is already in NERVIS's skills folder.{how}",
        name=name, installed_by_ravis=installed_by_ravis,
    )


def not_installed(name: str) -> SkillStoreRefusalError:
    return SkillStoreRefusalError(
        "SKILL_NOT_INSTALLED", 404,
        f"RAVIS didn't install a skill named {name}, so it can't update or remove it.",
        name=name,
    )


def not_updatable(name: str) -> SkillStoreRefusalError:
    return SkillStoreRefusalError(
        "SKILL_NOT_UPDATABLE", 409,
        f"The skill {name} was installed from a zip file, so there is nowhere to fetch an "
        "update from: choose the new zip file to update it.",
        name=name, origin="zip",
    )


def changed_since_review(name: str) -> SkillStoreRefusalError:
    return SkillStoreRefusalError(
        "SKILL_CHANGED_SINCE_REVIEW", 409,
        f"The folder of {name} changed after the review was made, so nothing was replaced; "
        "review the update again.",
        name=name,
    )


def folder_unusable(why: str) -> SkillStoreRefusalError:
    return SkillStoreRefusalError("SKILLS_FOLDER_UNUSABLE", 409,
                                  f"NERVIS's skills folder can't be used: {why}.")


def not_moved(why: str) -> SkillStoreRefusalError:
    return SkillStoreRefusalError("SKILL_NOT_MOVED", 409, sentence(why))


def source_exists(source: str) -> SkillStoreRefusalError:
    return SkillStoreRefusalError("MARKET_SOURCE_EXISTS", 409,
                                  "That source is already in the list.", source=source)


def source_not_found(source: str) -> SkillStoreRefusalError:
    return SkillStoreRefusalError("MARKET_SOURCE_NOT_FOUND", 404,
                                  "There is no such source in the list.", source=source)


def source_is_default(source: str) -> SkillStoreRefusalError:
    return SkillStoreRefusalError(
        "MARKET_SOURCE_IS_DEFAULT", 409,
        "A source RAVIS comes with can be hidden, not removed.", source=source,
    )


def sources_full(most: int) -> SkillStoreRefusalError:
    return SkillStoreRefusalError(
        "MARKET_SOURCES_FULL", 409,
        f"The list already holds {most} sources of your own; remove one first.", most=most,
    )


def sentence(text: str) -> str:
    """Plain words as a sentence: a full stop, and a capital first letter — unless the sentence
    starts with a host's or a repository's name, which keeps its own spelling (`skills.sh answered
    HTTP 500`, `anthropics/skills has no SKILL.md`)."""
    text = text.strip()
    if not text:
        return text
    first = text.split(" ", 1)[0]
    if "." not in first and "/" not in first:
        text = text[0].upper() + text[1:]
    return text if text.endswith((".", "?", "!")) else f"{text}."
