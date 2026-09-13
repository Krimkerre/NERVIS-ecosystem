"""RAVIS's own record about its Codex, kept in `codex-state.json` (design §4.2).

The file sits in RAVIS's configuration folder (`~/.config/ravis/`), mode 0600, beside the other
state RAVIS keeps. It holds only RAVIS's decisions, never Codex's secrets:

- **the confirmed account**: its fingerprint, how strong it is, and its plan — never the email;
- **`signed_out_on_purpose`**, which tells "signed out" (the owner did it, or never signed in) from
  "sign-in expired" (the account went away without RAVIS ending it; design §3.3);
- **the accepted versions** the owner took without a test (§3.4), each recorded `strict_rules:
  unproven` because acceptance proves nothing about behaviour (review AM1);
- **which builds the file-rules re-test proved**, by sha256;
- **a sign-in in progress**, so a RAVIS restart can say honestly that it lost one (§3.4);
- **the re-test's last result**, so a restart during a re-test is reported rather than forgotten.

Codex's own home — its `auth.json`, its history — is elsewhere (`~/.local/share/ravis-codex`),
owned by Codex, and RAVIS never reads it.

**Reading is forgiving, writing is atomic.** A missing file is a fresh install. An unreadable one
is logged and read as empty — RAVIS without Codex is a working RAVIS (runbook §2.2) — and the next
change rewrites it whole: written to a temporary file with mode 0600 and moved over the old one,
so a crash mid-write never leaves half a record.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger("ravis")

_Entry = TypeVar("_Entry")

FILE_NAME = "codex-state.json"
FORMAT = 1


@dataclass(frozen=True)
class ConfirmedAccount:
    fingerprint: str
    strength: str
    plan: str | None
    confirmed_at: str


@dataclass(frozen=True)
class AcceptedBuild:
    """A build the owner accepted: the design's record (§4.2), `strict_rules` always unproven."""

    sha256: str
    version: str | None
    stable_tree: str | None
    experimental_tree: str | None
    accepted_at: str
    accepted_by: str
    protocol_summary: dict[str, Any]
    strict_rules: str = "unproven"


@dataclass(frozen=True)
class ReproofRecord:
    """The file-rules re-test's last run: `running`, or `finished` with its result word."""

    state: str
    result: str | None = None
    detail: str | None = None
    sha256: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


@dataclass
class CodexRecord:
    confirmed: ConfirmedAccount | None = None
    signed_out_on_purpose: bool = False
    accepted: list[AcceptedBuild] = field(default_factory=list)
    #: sha256 → when the re-test proved that build's file rules.
    proven: dict[str, str] = field(default_factory=dict)
    sign_in_started_at: str | None = None
    reproof: ReproofRecord | None = None


class CodexStateFile:
    """Reads and writes `codex-state.json`."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> CodexRecord:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return CodexRecord()
        except (OSError, ValueError) as failure:
            logger.warning("codex: %s can't be read, starting from empty: %s", self.path, failure)
            return CodexRecord()
        return _record(raw) if isinstance(raw, dict) else CodexRecord()

    def save(self, record: CodexRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        descriptor = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"format": FORMAT, **asdict(record)}, handle, indent=2, sort_keys=True)
        os.replace(temporary, self.path)


def _record(raw: dict[str, Any]) -> CodexRecord:
    """A record from the file's JSON: every entry that reads is kept, any that doesn't skipped."""
    return CodexRecord(
        confirmed=_built(ConfirmedAccount, raw.get("confirmed")),
        signed_out_on_purpose=raw.get("signed_out_on_purpose") is True,
        accepted=[
            build
            for entry in _list(raw.get("accepted"))
            if (build := _built(AcceptedBuild, entry)) is not None
        ],
        proven={
            str(sha): str(when) for sha, when in _mapping(raw.get("proven")).items()
        },
        sign_in_started_at=_text(raw.get("sign_in_started_at")),
        reproof=_built(ReproofRecord, raw.get("reproof")),
    )


def _built(kind: type[_Entry], raw: object) -> _Entry | None:
    if not isinstance(raw, dict):
        return None
    try:
        return kind(**raw)
    except TypeError:
        return None


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _mapping(value: object) -> dict[Any, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None
