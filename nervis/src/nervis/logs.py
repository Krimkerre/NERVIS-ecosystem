"""M10 — the raw-log adapters (§11.3).

Fourth and last on §11.3's ladder: *structured event, management API, service
log, raw process log*, with the standing rule that a text log is **never**
parsed when structured data already exists. So this is a fallback, and its
existence is not permission to prefer it.

**Only documented sources.** §11.3 says adapters are added *"only for documented
sources, with format and version"*, and the documented one here is
`tools/run.py`, which spawns each service with its stdout and stderr appended to
`.run/{service}.log`. Nothing else in that directory is a log — it also holds
`*.token` files at mode 0600 — so this reads a fixed set of names rather than a
glob, and a glob would have swept credentials into a diagnostic screen.

**The format is JSON lines, and that is a fact about these files rather than an
assumption.** `JsonLineFormatter` writes one object per line with `level`,
`logger` and `message`, so a filter here is structural rather than a substring
hunt through prose. code-server is the exception and says so: it writes plain
text, and its adapter reports `text` as its format so nobody reads a `level`
that was never there.

**Rotation is copy-truncate, and the reason is the writer.** These files are
held open by a running service in append mode. Renaming one leaves the writer
appending to a file nobody can find any more — the classic rotation bug — so the
content is copied aside and the original truncated in place. With `O_APPEND` the
next write lands at the new end, which is zero, and no service has to be told
anything or restarted.

**This was overdue rather than theoretical.** On the machine this was written
for, `.run` held 102 MB across four logs, none of which had ever been rotated —
which is precisely §11.3's *"no ecosystem service may quietly fill the disk with
diagnostics"*, happening.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ecosystem_protocol.observability import REDACTED_KEYS

# §11.3's three rotation bounds. A size limit, a file count and a retention
# window: the first stops one log growing without end, the second stops the
# rotated copies doing the same, and the third clears what nobody came back for.
MAX_BYTES = 16 * 1024 * 1024
MAX_FILES = 3
RETENTION_DAYS = 14.0

# How much one read may return. A log with 368,000 lines in it is not something
# to hand a browser whole.
DEFAULT_LIMIT = 200
MAX_LIMIT = 2000

# How far back a *filter* looks, as opposed to how much it shows. Bounded
# because the file is not: these logs gain a line a second, so a search that
# only covered what fits on screen would report almost every real match as
# absent.
SEARCH_LINES = 20_000

# The documented sources, by service key. Named individually rather than
# globbed: `.run` also holds 0600 token files, and a glob is how those would
# reach a screen.
FILES = {
    "nervis": "nervis.log",
    "ravis": "ravis.log",
    "sirvis": "sirvis.log",
    "codeserver": "code-server.log",
}

# Which of those write JSON lines. code-server is somebody else's program and
# writes prose, so its adapter says `text` and offers no level filter rather
# than inventing one.
STRUCTURED = frozenset({"nervis", "ravis", "sirvis"})

# Secrets that survive as *values* in a line rather than as a named field.
# `REDACTED_KEYS` covers `{"api_key": "..."}`; these cover the same secret
# spelled into a message, a URL or a header dump, which is how one actually
# reaches a log file.
PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [redacted]"),
    (re.compile(r"\bsk-[A-Za-z0-9._-]{8,}"), "[redacted]"),
    # The launcher's own tokens: 44 characters of URL-safe base64, written to
    # `.run/*.token` and passed to services in their environment.
    (re.compile(r"\b[A-Za-z0-9_-]{43,}={0,2}\b"), "[redacted]"),
    (re.compile(r"(?i)\b(api[-_]?key|token|secret|password)([\"'\s:=]+)[^\s\"',}]{6,}"),
     r"\1\2[redacted]"),
)


@dataclass(frozen=True)
class Source:
    """One adapter, and what it can honestly offer."""

    service: str
    path: str
    present: bool
    format: str
    bytes: int
    modified: str
    rotated: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "path": self.path,
            "present": self.present,
            "format": self.format,
            "bytes": self.bytes,
            "modified": self.modified,
            "rotated": self.rotated,
            # §11.3 wants the format *and version* declared. There is one
            # version of each so far, and saying so beats implying a negotiation
            # that does not happen.
            "version": "1",
        }


def redact(line: str) -> str:
    """Blank anything in a line that looks like a credential.

    Applied to every line on the way out, not on the way in: NERVIS does not
    write these files and cannot redact at the producer, which is where the
    runbook puts the obligation. This is the second-best place and is honest
    about being second — a secret is already on disk by the time it is read
    here, and the value of blanking it is that the screen and any export do not
    spread it further.
    """
    for pattern, replacement in PATTERNS:
        line = pattern.sub(replacement, line)
    return line


def _redact_structured(record: dict[str, Any]) -> dict[str, Any]:
    """A parsed line, with named secrets blanked and the rest passed through."""
    return {
        key: ("[redacted]" if str(key).lower() in REDACTED_KEYS else
              redact(value) if isinstance(value, str) else value)
        for key, value in record.items()
    }


def shown(path: Path) -> str:
    """A path as an operator needs to read it, without the machine it sits on.

    §16 item 12 asks that evidence be inspectable without exposing private
    paths, and both halves of that matter here. The listing exists so somebody
    can see *which* file a screen is reading — `.run/nervis.log` says that as
    well as an absolute path does. What the absolute path adds is a home
    directory: a username and a layout, published on a surface NERVIS serves
    with no authentication of its own.

    So the answer is the file inside the directory the operator configured,
    named the way they named it, and nothing above that.
    """
    return f"{path.parent.name}/{path.name}" if path.parent.name else path.name


def sources(run_directory: Path) -> list[Source]:
    """Every documented adapter, present or not.

    **A missing file is a source that is absent, never an error.** M10's exit
    says outright that a missing file must not error globally, and the shape of
    that failure is worth naming: one service that has never been started should
    not blank a screen describing the other three.
    """
    found = []
    for service, name in FILES.items():
        path = run_directory / name
        exists = path.is_file()
        stat = path.stat() if exists else None
        found.append(Source(
            service=service,
            path=shown(path),
            present=exists,
            format="json-lines" if service in STRUCTURED else "text",
            bytes=stat.st_size if stat else 0,
            modified=(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime))
                      if stat else ""),
            rotated=len(_rotations(path)),
        ))
    return found


def _rotations(path: Path) -> list[Path]:
    """The rotated copies of one log, newest first."""
    return sorted(
        path.parent.glob(f"{path.name}.*"),
        key=lambda one: one.name,
    )


def _tail(path: Path, limit: int) -> Iterator[str]:
    """The last `limit` lines, without reading the file into memory.

    A 44 MB log read whole to show two hundred lines is how a diagnostics screen
    becomes the reason a machine swaps. Read backwards in blocks instead.
    """
    with path.open("rb") as handle:
        handle.seek(0, 2)
        end = handle.tell()
        block = 64 * 1024
        found: list[bytes] = []
        while end > 0 and len(found) <= limit:
            step = min(block, end)
            end -= step
            handle.seek(end)
            found = handle.read(step).split(b"\n") + found
        # **Empties are dropped before the slice, not after.** A log file ends
        # with a newline, so the split leaves a trailing empty string — and
        # taking the last `limit` elements first spent one of them on it and
        # returned `limit - 1` lines. Off by one, silently, and only on the
        # newest line, which is the one somebody is looking at.
        yield from (
            raw.decode("utf-8", "replace")
            for raw in [one for one in found if one.strip()][-limit:]
        )


def read(
    run_directory: Path,
    service: str,
    *,
    limit: int = DEFAULT_LIMIT,
    level: str = "",
    text: str = "",
    between: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Recent lines from one adapter, filtered and redacted.

    Filters are applied to the *parsed* record where the format allows it, so
    `level=ERROR` means the field rather than the word appearing anywhere in the
    line — a message quoting the string "ERROR" is not an error.
    """
    name = FILES.get(service)
    if name is None:
        return {"items": [], "present": False, "reason": f"{service} has no documented log"}
    path = run_directory / name
    if not path.is_file():
        return {"items": [], "present": False,
                "reason": "this service has not written a log here yet"}

    wanted = min(max(1, int(limit)), MAX_LIMIT)
    # **A filter searches further back than it displays, and says how far.**
    # Scanning only as many lines as are shown made "no match" mean "not in the
    # last twelve lines" while reading as "it never happened" — and on a log
    # that gains a line a second, that is almost always the wrong conclusion.
    # The window is bounded because the file is not, and `scanned` travels with
    # the answer so an empty result can say what was actually looked at.
    filtering = bool(level or text or between)
    window = SEARCH_LINES if filtering else wanted
    items: list[dict[str, Any]] = []
    scanned = 0
    for line in _tail(path, window):
        scanned += 1
        entry = _entry(line, structured=service in STRUCTURED)
        if level and str(entry.get("level", "")).upper() != level.upper():
            continue
        if text and text.lower() not in json.dumps(entry).lower():
            continue
        if between is not None and not _written_between(entry, between):
            continue
        items.append(entry)
    return {"items": items[-wanted:], "present": True, "reason": "",
            "scanned": scanned, "filtered": filtering,
            "format": "json-lines" if service in STRUCTURED else "text"}


def _written_between(entry: dict[str, Any], between: tuple[float, float]) -> bool:
    """Whether a line's own `time` falls within `between` (epoch seconds). No time, no."""
    from nervis.traces import _moment

    at = _moment(entry.get("time"))
    return at is not None and between[0] <= at <= between[1]


def _entry(line: str, *, structured: bool) -> dict[str, Any]:
    """One line as a record, falling back to the raw text.

    A structured log with an unparseable line is not a broken adapter — a
    service can write a traceback straight to stderr, and dropping those would
    hide exactly the lines somebody came looking for.
    """
    if structured:
        try:
            parsed = json.loads(line)
        except ValueError:
            return {"level": "", "logger": "", "message": redact(line), "parsed": False}
        if isinstance(parsed, dict):
            return {**_redact_structured(parsed), "parsed": True}
    return {"level": "", "logger": "", "message": redact(line), "parsed": False}


def rotate(path: Path, *, max_bytes: int = MAX_BYTES, max_files: int = MAX_FILES) -> bool:
    """Rotate one log if it has outgrown the limit. True if it did.

    **Copy-truncate, because the writer holds the file open.** Renaming a log a
    running service is appending to leaves it writing into a file with no name
    anybody will look at, and the service never learns. Copying the content
    aside and truncating in place keeps the same inode, and an `O_APPEND` handle
    simply continues from the new end.

    Not atomic, and it does not need to be: a line written between the copy and
    the truncate is lost from a diagnostic log, which is a cost worth paying to
    avoid asking every service to reopen its output.
    """
    if not path.is_file() or path.stat().st_size <= max_bytes:
        return False
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    shutil.copy2(path, path.with_name(f"{path.name}.{stamp}"))
    with path.open("r+b") as handle:
        handle.truncate(0)
    for extra in _rotations(path)[:-max_files] if max_files else _rotations(path):
        extra.unlink(missing_ok=True)
    return True


def prune(run_directory: Path, *, days: float = RETENTION_DAYS) -> int:
    """Delete rotated copies older than `days`, returning how many went.

    Only rotated copies. The live log is what a service is writing to right now
    and is never deleted here however old it looks — a machine left alone for a
    month should come back to its logs, not to an empty directory.
    """
    cutoff = time.time() - days * 86400
    gone = 0
    for name in FILES.values():
        for rotated in _rotations(run_directory / name):
            if rotated.stat().st_mtime < cutoff:
                rotated.unlink(missing_ok=True)
                gone += 1
    return gone


def enforce(run_directory: Path) -> dict[str, Any]:
    """Apply every bound §11.3 names, and say what it did."""
    rotated = [name for name in FILES.values() if rotate(run_directory / name)]
    return {"rotated": rotated, "pruned": prune(run_directory)}


__all__ = [
    "DEFAULT_LIMIT", "FILES", "MAX_BYTES", "MAX_FILES", "MAX_LIMIT",
    "RETENTION_DAYS", "STRUCTURED", "Source", "enforce", "prune", "read",
    "redact", "rotate", "sources",
]
