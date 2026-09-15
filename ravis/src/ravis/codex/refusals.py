"""The refusals the Codex routes answer with, in the contract's words (`conventions.json`).

Every code, status and retry flag here is the contract's error catalogue
(`tests/fixtures/relay-contract/conventions.json` → `error_codes`), and the messages the fixtures
fix are copied as they stand. Clients match on the code, never on the wording (conventions,
`error_envelope.rules`), so a message may say more than the fixture where RAVIS knows more — the
state's own reason, say.

Each refusal is a `RavisError`, raised inside the Codex service or a route and turned into the MEP
error envelope once, at the edge (`errors.py`, runbook §14.4). `retryable` follows from the status
there: only a 503 here is worth retrying.
"""

from __future__ import annotations

from typing import Any

from ravis.errors import RavisError


class CodexRefusalError(RavisError):
    """One refusal: a catalogued code at its catalogued status."""

    def __init__(self, code: str, status: int, message: str, **details: Any) -> None:
        super().__init__(message, **details)
        self.code = code
        self.status = status


def admin_required() -> CodexRefusalError:
    return CodexRefusalError("FORBIDDEN", 403, "An admin credential is required.")


def named_caller_required() -> CodexRefusalError:
    return CodexRefusalError("FORBIDDEN", 403, "A RAVIS credential is required to read this.")


def sites_reader_required() -> CodexRefusalError:
    """The allowed sites are Clarvis's, NERVIS's and the owner's to read (R5)."""
    return CodexRefusalError(
        "FORBIDDEN", 403,
        "A Clarvis, NERVIS or admin credential is required to read the allowed sites.",
    )


def skills_reader_required() -> CodexRefusalError:
    """Codex's skills are for NERVIS's Codex card and the owner to read, never Clarvis's."""
    return CodexRefusalError(
        "FORBIDDEN", 403, "A NERVIS or admin credential is required to read Codex's skills.",
    )


def skill_not_found() -> CodexRefusalError:
    """A path that isn't a skill Codex lists just now: RAVIS switches nothing else."""
    return CodexRefusalError(
        "SKILL_NOT_FOUND", 404, "Codex doesn't list a skill at that path, so nothing was changed.",
    )


def skill_not_changed(name: str, reason: str) -> CodexRefusalError:
    """The owner's switch, not taken by Codex: the previous choice is kept and applied again."""
    return CodexRefusalError(
        "SKILL_NOT_CHANGED", 409, "Codex didn't take that change, so the skill stays as it was.",
        skill=name, reason=reason,
    )


# ── Skills for every engine (`/api/v1/skills`, RAVIS 0.27.0; `skills.json`) ──


def skills_board_reader_required() -> CodexRefusalError:
    """The Skills page's list is for NERVIS's GET relay and the owner, never Clarvis's."""
    return CodexRefusalError(
        "FORBIDDEN", 403, "A NERVIS or admin credential is required to read the skills.",
    )


def models_skills_reader_required() -> CodexRefusalError:
    """Skill text for the other models is for the programs calling them: Clarvis and NERVIS."""
    return CodexRefusalError(
        "FORBIDDEN", 403,
        "A Clarvis or NERVIS client credential is required to read skills for other models.",
    )


def skill_not_listed_for(engine: str) -> CodexRefusalError:
    """A switch naming a path RAVIS doesn't list for that engine just now: nothing is kept."""
    return CodexRefusalError(
        "SKILL_NOT_FOUND", 404,
        "RAVIS doesn't list a skill at that path for that engine, so nothing was changed.",
        engine=engine,
    )


def skill_not_served() -> CodexRefusalError:
    """A read naming no skill switched on for other models: unknown, off and unreadable alike."""
    return CodexRefusalError(
        "SKILL_NOT_FOUND", 404, "No skill with that identifier is switched on for other models.",
    )


#: What `SKILL_FILE_REFUSED` says, by its `reason` (`agent/skill_catalog.py`).
SKILL_FILE_REFUSALS = {
    "outside_skill": "That path isn't a file inside the skill's own folder, so RAVIS doesn't "
                     "serve it.",
    "hidden": "That is a hidden file, so RAVIS doesn't serve it.",
    "too_large": "That file is larger than 64 KB, so RAVIS doesn't serve it.",
    "not_text": "That file isn't UTF-8 text, so RAVIS doesn't serve it.",
}


def skill_file_refused(reason: str) -> CodexRefusalError:
    """A file of a switched-on skill RAVIS won't serve: outside it, hidden, too big or not text."""
    return CodexRefusalError("SKILL_FILE_REFUSED", 422, SKILL_FILE_REFUSALS[reason], reason=reason)


def skill_file_not_found() -> CodexRefusalError:
    return CodexRefusalError(
        "SKILL_FILE_NOT_FOUND", 404, "The skill has no file RAVIS may read at that path.",
    )


def reproof_not_allowed() -> CodexRefusalError:
    return CodexRefusalError(
        "REPROOF_NOT_ALLOWED",
        403,
        "Only the owner's command-line credential may start the file-rules re-test.",
    )


def not_available(reason: str) -> CodexRefusalError:
    return CodexRefusalError("CODEX_NOT_AVAILABLE", 409, f"Codex is not available here: {reason}")


def untested_version(reason: str) -> CodexRefusalError:
    return CodexRefusalError("CODEX_UNTESTED_VERSION", 409, reason)


def already_signed_in() -> CodexRefusalError:
    return CodexRefusalError("CODEX_ALREADY_SIGNED_IN", 409, "Sign out first.")


def run_in_progress(action: str) -> CodexRefusalError:
    """`action` is what waits: "sign in", "sign out", "re-test"."""
    return CodexRefusalError(
        "CODEX_RUN_IN_PROGRESS", 409, f"Codex is working on a task; {action} after it pauses."
    )


def sign_in_ports_busy() -> CodexRefusalError:
    return CodexRefusalError(
        "CODEX_SIGN_IN_PORT_BUSY",
        409,
        "Another program is holding the sign-in ports 1455 and 1457.",
    )


def runtime_unavailable(message: str) -> CodexRefusalError:
    return CodexRefusalError("CODEX_RUNTIME_UNAVAILABLE", 503, message)


def account_moved() -> CodexRefusalError:
    return CodexRefusalError(
        "CODEX_ACCOUNT_MOVED", 409, "The signed-in account changed again; check it and confirm."
    )


def hash_mismatch() -> CodexRefusalError:
    return CodexRefusalError(
        "CODEX_HASH_MISMATCH", 409, "That sha256 is not the installed Codex binary."
    )


def version_check_failed() -> CodexRefusalError:
    return CodexRefusalError(
        "CODEX_VERSION_CHECK_FAILED",
        409,
        "The version check failed, so this version can't be accepted.",
    )


def not_ready(reason: str) -> CodexRefusalError:
    """Codex can't take new work now. `details.reason` carries the state's own sentence."""
    return CodexRefusalError("CODEX_NOT_READY", 409, reason, reason=reason)


def idempotency_key_required() -> CodexRefusalError:
    return CodexRefusalError(
        "IDEMPOTENCY_KEY_REQUIRED", 428, "This route needs an Idempotency-Key header."
    )


def idempotency_key_reused() -> CodexRefusalError:
    return CodexRefusalError(
        "IDEMPOTENCY_KEY_REUSED",
        422,
        "That Idempotency-Key was already used with a different body.",
    )


def sign_in_method_not_supported(method: object) -> CodexRefusalError:
    return CodexRefusalError(
        "SIGN_IN_METHOD_NOT_SUPPORTED",
        422,
        f"RAVIS signs Codex in through the browser only; {method!r} is not a method it offers.",
    )


def invalid_body(message: str) -> CodexRefusalError:
    return CodexRefusalError("INVALID_REQUEST_BODY", 422, message)
