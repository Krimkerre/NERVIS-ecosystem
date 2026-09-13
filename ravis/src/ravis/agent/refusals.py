"""The relay's refusals, in the contract's words (`conventions.json` → `error_codes`).

Every code, status and message here is the contract fixtures' (`agent-sessions.json`,
`owner-stop.json`, `event-stream.json`). Clients match on the code and `details`, never on the
wording, but the fixtures fix the wording too, so it is copied as it stands.

Each is a `CodexRefusalError` — the same `RavisError` the Codex routes raise — turned into the MEP
error envelope once, at the edge (`errors.py`). The envelope's `retryable` follows from the status,
so only a 429 or 503 here says "try again".
"""

from __future__ import annotations

from typing import Any

from ravis.codex.refusals import CodexRefusalError

A_CLARVIS_CREDENTIAL = "A Clarvis credential is required."
ADMIN_REFUSED = "Administrative credentials may not start, read, steer, stop or answer Codex tasks."
#: The sentence every refused folder gets unless the contract fixes a more specific one.
FOLDER_REFUSED = "That folder can't hold a Codex task."


def client_not_allowed(message: str = A_CLARVIS_CREDENTIAL) -> CodexRefusalError:
    return CodexRefusalError("AGENT_CLIENT_NOT_ALLOWED", 403, message)


def session_not_found() -> CodexRefusalError:
    """Unknown id, missing token and wrong token all look the same, so nothing leaks."""
    return CodexRefusalError("AGENT_SESSION_NOT_FOUND", 404, "No such agent session.")


def owner_stop_not_allowed() -> CodexRefusalError:
    return CodexRefusalError(
        "OWNER_STOP_NOT_ALLOWED",
        403,
        "Only the owner's menu bar or the dashboard may stop a task this way; editors use "
        "interrupt.",
    )


def token_not_accepted() -> CodexRefusalError:
    return CodexRefusalError("TOKEN_NOT_ACCEPTED", 400, "The owner Stop takes no session token.")


def confirmation_mismatch() -> CodexRefusalError:
    return CodexRefusalError(
        "CONFIRMATION_MISMATCH", 409, "That task changed; refresh and try again."
    )


def nothing_running(state: str) -> CodexRefusalError:
    return CodexRefusalError("NOTHING_RUNNING", 409, "That task isn't running.", state=state)


def stop_rate_limited() -> CodexRefusalError:
    return CodexRefusalError(
        "RATE_LIMITED", 429, "Too many stop requests; try again shortly."
    )


def workspace_root_not_allowed(reason: str, message: str = FOLDER_REFUSED) -> CodexRefusalError:
    return CodexRefusalError("WORKSPACE_ROOT_NOT_ALLOWED", 422, message, reason=reason)


def git_dir_not_allowed() -> CodexRefusalError:
    return CodexRefusalError(
        "GIT_DIR_NOT_ALLOWED", 422, "git_dir is not this workspace's git folder."
    )


def project_locked(lock: dict[str, Any]) -> CodexRefusalError:
    return CodexRefusalError("PROJECT_LOCKED", 409, "Another engine holds this project.", lock=lock)


def nested_project_locked(lock: dict[str, Any]) -> CodexRefusalError:
    return CodexRefusalError(
        "NESTED_PROJECT_LOCKED", 409, "A folder inside or around this project is locked.", lock=lock
    )


def lock_superseded() -> CodexRefusalError:
    return CodexRefusalError(
        "LOCK_SUPERSEDED",
        409,
        "Another editor held this checkout when RAVIS restarted; wait until it releases.",
    )


def lock_transfer_invalid() -> CodexRefusalError:
    return CodexRefusalError(
        "LOCK_TRANSFER_INVALID",
        409,
        "That transfer token is unknown, expired or for another project.",
    )


def codex_not_ready(state: str, reason: str) -> CodexRefusalError:
    """Codex can't take work now: its state word, and why (a sentence, or the D2 reason word)."""
    return CodexRefusalError(
        "CODEX_NOT_READY", 409, "Codex isn't ready.", state=state, reason=reason
    )


def session_limit() -> CodexRefusalError:
    return CodexRefusalError(
        "CODEX_SESSION_LIMIT", 409, "Codex is already running the most tasks allowed at once."
    )


def runtime_unavailable(message: str) -> CodexRefusalError:
    return CodexRefusalError("CODEX_RUNTIME_UNAVAILABLE", 503, message)


def turn_active(
    message: str = "A turn is already running; send it as a steer.",
) -> CodexRefusalError:
    return CodexRefusalError("TURN_ACTIVE", 409, message)


def session_stopping() -> CodexRefusalError:
    return CodexRefusalError("SESSION_STOPPING", 409, "This task is stopping.")


def settle_first() -> CodexRefusalError:
    return CodexRefusalError("SETTLE_FIRST", 409, "Save the last turn's work first.")


def empty_steer() -> CodexRefusalError:
    return CodexRefusalError("EMPTY_STEER", 422, "There is nothing to pass on.")


def request_not_found() -> CodexRefusalError:
    return CodexRefusalError("REQUEST_NOT_FOUND", 404, "No such request.")


def request_already_resolved(by: str) -> CodexRefusalError:
    return CodexRefusalError("REQUEST_ALREADY_RESOLVED", 409, "Already answered.", by=by)


def decision_not_allowed(allowed: list[str]) -> CodexRefusalError:
    return CodexRefusalError(
        "DECISION_NOT_ALLOWED",
        422,
        "That decision isn't offered for this request.",
        allowed_decisions=allowed,
    )


def no_leftover() -> CodexRefusalError:
    return CodexRefusalError("NO_LEFTOVER", 409, "Nothing Codex started is still running.")


def settle_claimed(window: str) -> CodexRefusalError:
    return CodexRefusalError(
        "SETTLE_CLAIMED", 409, "Another editor is saving this task's work.", window=window
    )


def processes_not_confirmed_gone() -> CodexRefusalError:
    return CodexRefusalError(
        "PROCESSES_NOT_CONFIRMED_GONE", 409, "Something this task started is still running."
    )


def nothing_to_settle() -> CodexRefusalError:
    return CodexRefusalError("NOTHING_TO_SETTLE", 409, "This task has nothing to save.")


def claim_invalid() -> CodexRefusalError:
    return CodexRefusalError("CLAIM_INVALID", 409, "That settle claim is not the current one.")


def window_attached() -> CodexRefusalError:
    return CodexRefusalError(
        "WINDOW_ATTACHED", 409, "An editor is attached to this task; reconnect from there."
    )


def site_not_added(host: str, reason: str) -> CodexRefusalError:
    """Codex didn't add a site to its list (overridden, refused or silent): it stays blocked."""
    return CodexRefusalError(
        "SITE_NOT_ADDED", 409, "Codex didn't add that site, so it stays blocked.",
        host=host, reason=reason,
    )


def event_cursor_expired(oldest_event_id: int) -> CodexRefusalError:
    return CodexRefusalError(
        "EVENT_CURSOR_EXPIRED",
        409,
        "That event cursor is too old; read the snapshot.",
        oldest_event_id=oldest_event_id,
    )
