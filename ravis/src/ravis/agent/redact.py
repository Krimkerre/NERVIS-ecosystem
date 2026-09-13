"""Command output that might hold a secret, hidden before RAVIS relays it (design §4.9).

Codex's commands run in the owner's projects, and a command can print a key: `cat .env`, a
config dump, a test that logs a token. The relay stream is content RAVIS passes through its memory
to a token-holding window (runbook §2.2 invariant 8), so what it relays must not be a secret.
**Output is hidden whole** — replaced by `[output hidden: it may contain a secret]` — when:

- the command, or one of Codex's `commandActions` paths, names a denied path (`roots.py`,
  `denied_paths`): its output is hidden from the first byte, whatever it turns out to print; or
- the output matches a token pattern: `sk-…`, `ghp_…`, a JWT's three dotted parts, or 32 or more
  base64url characters right after `token`, `key` or `secret`.

It applies to `command.output` deltas, to `item.completed`'s `output_tail` (at most 1,500
characters, the end of the output), and to the transcript. Clarvis redacts again before storing.

**A known gap:** a secret split across two streamed deltas can escape the pattern in each half. The
completed item's `output_tail` is redacted over the whole output, so the ledger never keeps it;
the live terminal may briefly have shown it. Codex's own history is outside RAVIS's control,
which is why its folder is denied to commands.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

HIDDEN = "[output hidden: it may contain a secret]"
TAIL_CHARACTERS = 1500
SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"|(?i:token|key|secret)[^A-Za-z0-9]{0,3}[A-Za-z0-9_-]{32,}"
)


class Redactor:
    """The rule above for one set of denied paths."""

    def __init__(self, denied: Iterable[Path], home: Path) -> None:
        # Each denied path as a command might spell it: absolute, or from the home folder.
        spellings: set[str] = set()
        for path in denied:
            spellings.add(str(path))
            if home in path.parents:
                spellings.add("~/" + str(path.relative_to(home)))
        self._spellings = tuple(sorted(spellings, key=len, reverse=True))

    def names_denied(self, command: object, actions: object = None) -> bool:
        """Whether a command's text, or any of its `commandActions` paths, names a denied path."""
        texts = [command if isinstance(command, str) else ""]
        for action in actions if isinstance(actions, list) else []:
            if isinstance(action, dict) and isinstance(action.get("path"), str):
                texts.append(action["path"])
        return any(spelling in text for text in texts for spelling in self._spellings)

    def output(self, text: object, *, hidden: bool) -> str:
        """Output as it may be relayed: itself, or the hidden line."""
        if not isinstance(text, str) or not text:
            return ""
        return HIDDEN if hidden or SECRET.search(text) else text

    def tail(self, text: object, *, hidden: bool) -> str:
        """The end of an output, at most 1,500 characters, judged over the whole output."""
        return self.output(text, hidden=hidden)[-TAIL_CHARACTERS:]


def command_hidden(redactor: Redactor, item: dict[str, Any]) -> bool:
    return redactor.names_denied(item.get("command"), item.get("commandActions"))
